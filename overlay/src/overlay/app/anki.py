"""AnkiConnect client + Lapis card builder + dedup for one-key mining.

Targets the collection's **Lapis** note type by default (the project's mining note type). The logical
→ real field map keeps it note-type-agnostic; only mapped fields are written. Dedup checks the deck for
an existing Expression before adding, so mining can't silently duplicate.
"""

from __future__ import annotations

import html
import json
import logging
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import stamina

from overlay.app.lookup import CardData

log = logging.getLogger(__name__)

ANKI_HOST = "http://127.0.0.1:8765"  # AnkiConnect stock default (webBindAddress:webBindPort)


def resolve_anki(cfg: dict | None = None) -> tuple[str, str | None]:
    """``(url, api_key)`` for AnkiConnect from the ``[anki]`` config table, defaulting to the stock
    ``http://127.0.0.1:8765`` with no key. Set ``[anki].url`` (or ``host``/``port``) if you changed
    AnkiConnect's ``webBindPort``/``webBindAddress``, and ``[anki].api_key`` if you set an ``apiKey``.
    Always 127.0.0.1 by default (not ``localhost``) to dodge IPv6/DNS resolution delays."""
    if cfg is None:
        from overlay.app.config import load_config

        cfg = load_config()
    raw = cfg.get("anki")
    a: dict = raw if isinstance(raw, dict) else {}
    url = a.get("url") or f"http://{a.get('host', '127.0.0.1')}:{a.get('port', 8765)}"
    return url, a.get("api_key")


def _ping_body(api_key: str | None) -> bytes:
    payload: dict = {"action": "version", "version": 6}
    if api_key:
        payload["key"] = api_key  # AnkiConnect's apiKey travels in the request body, not a header
    return json.dumps(payload).encode()


def anki_reachable(
    host: str | None = None, api_key: str | None = None, timeout: float = 2.0
) -> bool:
    """True if AnkiConnect answers a version ping. Host/key resolve from config when not given."""
    if host is None:
        host, api_key = resolve_anki()
    req = urllib.request.Request(host, _ping_body(api_key), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return b'"result"' in r.read()
    except Exception:
        return False


def ensure_anki_running(host: str | None = None, wait: float = 20.0) -> bool:
    """If AnkiConnect isn't answering, launch Anki and poll until it does (up to ``wait`` seconds).

    Returns True once reachable, False if it couldn't be started — the caller WARNS and degrades
    (mining/known-word coloring off) rather than failing. Non-blocking when Anki is already up."""
    if anki_reachable(host):
        return True
    if sys.platform == "darwin":
        launch = ["open", "-a", "Anki"]
    elif sys.platform.startswith("win"):
        launch = ["cmd", "/c", "start", "", "anki"]
    else:
        launch = ["anki"]
    log.info("AnkiConnect down — launching Anki (%s)", launch[0])
    try:
        subprocess.Popen(launch, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("could not launch Anki automatically: %s", e)
        return False
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if anki_reachable(host):
            log.info("Anki is up (AnkiConnect responding)")
            return True
        time.sleep(1.0)
    log.warning("Anki launched but AnkiConnect didn't come up within %.0fs", wait)
    return False


class AnkiError(RuntimeError):
    pass


class _AnkiRetryable(AnkiError):
    """A transient AnkiConnect failure (connection refused / timeout while Anki is briefly busy).
    ``stamina`` retries these ONCE, quickly — Anki being *not running* is a common steady state, so we
    keep the added latency tiny (a down call adds ~0.3s, not seconds). App errors (deck not found, …)
    are plain ``AnkiError`` and never retried."""


@dataclass
class MineConfig:
    deck: str = "Saitenka::Mining"
    model: str = "Lapis"
    tags: tuple[str, ...] = ("saitenka-overlay",)
    # logical name -> real field on the note type (Lapis defaults)
    fields: dict = field(
        default_factory=lambda: {
            "expression": "Expression",
            "reading": "ExpressionReading",
            "sentence": "Sentence",
            "glossary": "Glossary",
            "picture": "Picture",
            "audio": "SentenceAudio",
            "misc": "MiscInfo",
            "id": "ID",
            "freq": "Frequency",
            "freq_sort": "FreqSort",
        }
    )
    # non-empty flag fields Lapis uses to pick a card template
    flags: dict = field(default_factory=lambda: {"IsSentenceCard": "1"})


class Anki:
    def __init__(self, host: str | None = None, api_key: str | None = None):
        rh, rk = resolve_anki()
        self.host = host or rh
        self.api_key = api_key if api_key is not None else rk

    def _call(self, action: str, **params):
        payload: dict = {"action": action, "version": 6, "params": params}
        if self.api_key:
            payload["key"] = self.api_key  # AnkiConnect apiKey → request body
        body = json.dumps(payload).encode()
        req = urllib.request.Request(self.host, body, {"Content-Type": "application/json"})
        for attempt in stamina.retry_context(
            on=_AnkiRetryable, attempts=2, wait_initial=0.3, wait_max=1.0
        ):
            with attempt:
                try:
                    with urllib.request.urlopen(req, timeout=20) as r:
                        res = json.loads(r.read())
                except OSError as e:  # connection refused / timeout — transient, retry once
                    raise _AnkiRetryable(f"AnkiConnect unreachable at {self.host}: {e}") from e
                if res.get("error"):
                    raise AnkiError(res["error"])  # app error (deck/model not found) — do NOT retry
                return res.get("result")
        raise AnkiError(f"AnkiConnect call {action!r} failed after retries")  # unreachable

    def store_media(self, filename: str, path: str | Path) -> str:
        return self._call("storeMediaFile", filename=filename, path=str(Path(path).resolve()))

    def retrieve_media(self, filename: str) -> bytes | None:
        import base64

        data = self._call("retrieveMediaFile", filename=filename)
        return base64.b64decode(data) if data else None

    def find_notes(self, query: str) -> list[int]:
        return self._call("findNotes", query=query) or []

    def notes_info(self, ids: list[int]) -> list[dict]:
        return self._call("notesInfo", notes=ids) or []

    def can_add(self, note: dict) -> bool:
        return bool((self._call("canAddNotes", notes=[note]) or [False])[0])

    def add_note(self, note: dict) -> int:
        return self._call("addNote", note=note)

    def delete_notes(self, ids: list[int]) -> None:
        self._call("deleteNotes", notes=ids)


def _q(s: str) -> str:
    return s.replace('"', "")


def _esc_query(s: str) -> str:
    """Escape characters that have special meaning in Anki search queries (* ? : space _)."""
    return s.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?").replace(" ", "\\ ")


def dedupe(anki: Anki, cfg: MineConfig, expression: str) -> list[int]:
    """Existing note ids for this expression in the mining deck (empty = safe to add)."""
    field = cfg.fields["expression"]
    # Escape both the deck name (double-quote) and the expression (Anki wildcard chars) to avoid
    # query injection (e.g. an expression containing * would match all cards in the field).
    return anki.find_notes(f'deck:"{_q(cfg.deck)}" "{field}:{_esc_query(expression)}"')


def bold_word(sentence: str, surface: str) -> str:
    """Wrap the first occurrence of the mined surface in <b> for the Sentence field.

    The surrounding context is HTML-escaped so that subtitle text containing <, >, or &
    does not inject raw HTML into the Anki card's Sentence field."""
    esc = html.escape(sentence)
    esc_surface = html.escape(surface)
    i = esc.find(esc_surface)
    if i < 0:
        return esc
    return f"{esc[:i]}<b>{esc_surface}</b>{esc[i + len(esc_surface) :]}"


def build_note(
    cfg: MineConfig,
    card: CardData,
    sentence_html: str,
    picture: str = "",
    audio: str = "",
    misc: str = "",
    freq_html: str = "",
    freq_sort: str = "",
    tags=(),
) -> dict:
    """Assemble the AnkiConnect note dict from card data + media filenames. ``tags`` are extra per-card
    tags (source/episode) added to the config's static tags."""
    values = {
        "expression": card.expression,
        "reading": card.reading,
        "sentence": sentence_html,
        "glossary": card.glossary_html,
        "picture": f'<img src="{picture}">' if picture else "",
        "audio": f"[sound:{audio}]" if audio else "",
        "misc": misc,
        "id": card.idseq,
        "freq": freq_html,
        "freq_sort": freq_sort,
    }
    note_fields = {real: values.get(logical, "") for logical, real in cfg.fields.items()}
    note_fields.update(cfg.flags)
    all_tags = list(dict.fromkeys(list(cfg.tags) + list(tags)))  # dedupe, keep order
    return {
        "deckName": cfg.deck,
        "modelName": cfg.model,
        "fields": note_fields,
        "tags": all_tags,
        "options": {"allowDuplicate": False},
    }

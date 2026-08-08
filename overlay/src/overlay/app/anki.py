"""AnkiConnect client + Lapis card builder + dedup for one-key mining.

Targets the collection's **Lapis** note type by default (the project's mining note type). The logical
→ real field map keeps it note-type-agnostic; only mapped fields are written. Dedup checks the deck for
an existing Expression before adding, so mining can't silently duplicate.
"""

from __future__ import annotations

import html
import http.client
import importlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import stamina

from overlay.app.media import AnimatedClip

if TYPE_CHECKING:
    from overlay.app.lookup import CardData

log = logging.getLogger(__name__)

ANKI_HOST = "http://127.0.0.1:8765"  # AnkiConnect stock default (webBindAddress:webBindPort)


def _windows_anki_app_path() -> str | None:
    try:
        winreg: Any = importlib.import_module("winreg")
    except ModuleNotFoundError:
        return None

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for base in (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
        ):
            try:
                with winreg.OpenKey(hive, rf"{base}\anki.exe") as key:
                    value, _kind = winreg.QueryValueEx(key, "")
            except OSError:
                continue
            path = Path(str(value).strip('"'))
            if path.is_file():
                return str(path)
    return None


def find_anki(cfg: dict | None = None) -> str | None:
    """Resolve an installed Anki executable without relying on Explorer's shell lookup."""
    if cfg is None:
        from overlay.app.config import load_config

        cfg = load_config()
    raw = cfg.get("anki")
    settings: dict = raw if isinstance(raw, dict) else {}
    configured = settings.get("executable")
    if configured:
        path = Path(os.path.expandvars(str(configured))).expanduser()
        if path.is_file():
            return str(path)
    for name in ("anki.exe", "anki"):
        if found := shutil.which(name):
            return found
    if found := _windows_anki_app_path():
        return found
    env = os.environ
    roots = (
        (env.get("LOCALAPPDATA"), ("Programs", "Anki", "anki.exe")),
        (env.get("ProgramFiles"), ("Anki", "anki.exe")),
        (env.get("ProgramFiles(x86)"), ("Anki", "anki.exe")),
    )
    candidates = (Path(root).joinpath(*parts) for root, parts in roots if root)
    return next((str(path) for path in candidates if path.is_file()), None)


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


def anki_reachable(
    host: str | None = None, api_key: str | None = None, timeout: float = 2.0
) -> bool:
    """The single 'is AnkiConnect answering RIGHT NOW' gate — a fast, no-retry version ping through the
    one client (SSOT). Host/key resolve from config when not given. Callers that need a boolean
    (tooltip's ⊕ gate, setup, the startup watcher) use this; never re-implement a probe."""
    try:
        Anki(host, api_key)._call("version", timeout=timeout, attempts=1)
        return True
    except ANKI_DOWN_ERRORS:
        return False


def launch_anki() -> bool:
    """Fire-and-forget: start the Anki app (no polling). True if the launch was issued. Split out of
    :func:`ensure_anki_running` so a caller can kick Anki off the startup critical path and poll for it
    in the background (see reader_deps' Anki watcher)."""
    if sys.platform == "darwin":
        launch = ["open", "-a", "Anki"]
    elif sys.platform.startswith("win"):
        executable = find_anki()
        if executable is None:
            log.warning("could not find Anki to launch automatically")
            return False
        launch = [executable]
    else:
        launch = ["anki"]
    log.info("AnkiConnect down — launching Anki (%s)", launch[0])
    # macOS `open -a` hands off to LaunchServices and exits, so we can WAIT for it and surface a real
    # failure (e.g. "Unable to find application named 'Anki'") instead of a fire-and-forget Popen that
    # always looked like success. The app-process platforms (win/linux) must stay non-blocking.
    if launch[0] == "open":
        try:
            res = subprocess.run(launch, check=False, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("could not launch Anki automatically: %s", e)
            return False
        if res.returncode != 0:
            log.warning(
                "could not launch Anki automatically: %s",
                res.stderr.strip() or f"`open -a Anki` exited {res.returncode}",
            )
            return False
        return True
    try:
        subprocess.Popen(launch, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("could not launch Anki automatically: %s", e)
        return False
    return True


def wait_until_anki_up(host: str | None = None, wait: float = 20.0) -> bool:
    """Poll AnkiConnect until it answers or ``wait`` seconds elapse. Does NOT launch Anki — assumes it
    is already (being) started. True once reachable, False on timeout."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if anki_reachable(host):
            return True
        time.sleep(1.0)
    return False


def ensure_anki_running(host: str | None = None, wait: float = 20.0) -> bool:
    """If AnkiConnect isn't answering, launch Anki and poll until it does (up to ``wait`` seconds).

    Returns True once reachable, False if it couldn't be started — the caller WARNS and degrades
    (mining/known-word coloring off) rather than failing. Non-blocking when Anki is already up. This
    blocks for the poll; startup instead uses :func:`launch_anki` + a background :func:`wait_until_anki_up`
    so the wait never gates dictionary/coloring readiness."""
    if anki_reachable(host):
        return True
    if not launch_anki():
        return False
    if wait_until_anki_up(host, wait):
        log.info("Anki is up (AnkiConnect responding)")
        return True
    log.warning("Anki launched but AnkiConnect didn't come up within %.0fs", wait)
    return False


class AnkiError(RuntimeError):
    pass


class _AnkiRetryable(AnkiError):
    """A transient AnkiConnect failure (connection refused / timeout while Anki is briefly busy).
    ``stamina`` retries these ONCE, quickly — Anki being *not running* is a common steady state, so we
    keep the added latency tiny (a down call adds ~0.3s, not seconds). App errors (deck not found, …)
    are plain ``AnkiError`` and never retried."""


def is_unreachable(exc: BaseException) -> bool:
    """True when *exc* just means 'Anki isn't running' — connection refused / timeout, from either the
    :class:`Anki` client (``_AnkiRetryable``) or a raw ``urllib`` call (``OSError``/``URLError``).
    Anki can vanish at any moment; that's an expected steady state, so callers log it compactly
    (no traceback) and carry on."""
    return isinstance(exc, (_AnkiRetryable, OSError))


# SSOT: the exceptions a single AnkiConnect interaction can raise. Any caller for whom Anki is
# OPTIONAL (doctor probes, known-word coloring, mining) catches this to degrade instead of crashing.
# ``AnkiError`` covers both an app error (deck/model missing) and the ``_AnkiRetryable`` down case;
# the rest are transport/parse failures. Distinguish "just down" from "real fault" with is_unreachable.
ANKI_DOWN_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    http.client.HTTPException,
    json.JSONDecodeError,
    AnkiError,
)


# logical name -> real field on the note type (Lapis defaults). Kiku shares these names — SubMiner
# treats the two uniformly and its docs describe Kiku as inheriting Lapis's field settings.
LAPIS_FIELDS = {
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

# The mutually-exclusive card-template markers a note type may key off (Lapis/Kiku family). One of
# these, set non-empty, selects the front/back template. card_kind -> its marker (None = mark none).
KNOWN_MARKERS = ("IsSentenceCard", "IsWordAndSentenceCard", "IsClickCard", "IsAudioCard")
_CARD_KIND_MARKER: dict[str, str | None] = {
    "sentence": "IsSentenceCard",
    "word-and-sentence": "IsWordAndSentenceCard",
    "click": "IsClickCard",
    "audio": "IsAudioCard",
    "none": None,
}
_DEFAULT_CARD_KIND = "word-and-sentence"

# Known-good note types: (field map, default card kind). A preset spares the user spelling the map
# out; both Lapis and Kiku use the shared LAPIS_FIELDS names, differing only in card template.
PRESETS: dict[str, tuple[dict, str]] = {
    "Lapis": (LAPIS_FIELDS, _DEFAULT_CARD_KIND),
    "Kiku": (LAPIS_FIELDS, _DEFAULT_CARD_KIND),
}


def _flags_for(card_kind: str) -> dict:
    """The non-empty card-template marker(s) for a card kind: exactly one of :data:`KNOWN_MARKERS`
    set to ``"1"`` (mutual exclusion by construction), or ``{}`` for ``"none"``. An unrecognised kind
    warns and falls back to the default, so a ``[mine].card_kind`` typo can't silently disable mining."""
    if card_kind not in _CARD_KIND_MARKER:
        log.warning("unknown [mine].card_kind %r; using %r", card_kind, _DEFAULT_CARD_KIND)
        card_kind = _DEFAULT_CARD_KIND
    marker = _CARD_KIND_MARKER[card_kind]
    return {marker: "1"} if marker else {}


@dataclass
class MineConfig:
    deck: str = "Saitenka::Mining"
    model: str = "Lapis"
    tags: tuple[str, ...] = ("saitenka",)
    normalize_audio: bool = False  # opt-in −23 LUFS loudnorm on the mined clip
    # Opt-in animated (motion) screenshot instead of a still (config: [mine].animated_screenshot +
    # animated_height/fps/quality/max_secs/format). See media.AnimatedClip / animated_screenshot.
    animated: AnimatedClip = field(default_factory=AnimatedClip)
    # card template selector — one of _CARD_KIND_MARKER's keys. Default word-and-sentence (SubMiner's
    # default) is a deliberate change from the historical unconditional IsSentenceCard; set
    # [mine].card_kind = "sentence" to restore the old marker.
    card_kind: str = _DEFAULT_CARD_KIND
    fields: dict = field(default_factory=lambda: dict(LAPIS_FIELDS))
    # Yomitan-style field -> "{marker}" template map. When set it WINS wholesale over `fields` (only
    # these fields are written), letting one field combine markers / one entity fan out. See
    # card_markers.render_card_format / MARKERS.
    card_format: dict = field(default_factory=dict)
    # non-empty flag fields that pick a card template; derived from card_kind unless set explicitly
    flags: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.flags:
            self.flags = _flags_for(self.card_kind)

    def expression_field(self) -> str:
        """The real note field holding the mined expression — the dedup key. Under ``card_format`` it's
        the field whose template references ``{expression}`` (that's what actually gets written); else
        the entity map's ``expression`` target. ``""`` when ``card_format`` never surfaces the expression
        — no reliable dedup key, so the caller allows the add rather than querying an empty field."""
        if self.card_format:
            from overlay.app.card_markers import markers_in

            return next(
                (
                    real
                    for real, tmpl in self.card_format.items()
                    if "expression" in markers_in(str(tmpl))
                ),
                "",
            )
        return self.fields.get("expression", "Expression")

    @classmethod
    def from_preset(cls, name: str, **overrides) -> MineConfig:
        """A :class:`MineConfig` for a known note type (Lapis/Kiku): its field map + default card
        kind. An unknown name warns and falls back to the Lapis map. ``overrides`` win over the preset."""
        if name not in PRESETS:
            log.warning("unknown mining preset %r; using the Lapis field map", name)
        fields_map, card_kind = PRESETS.get(name, (LAPIS_FIELDS, _DEFAULT_CARD_KIND))
        params: dict = {"model": name, "fields": dict(fields_map), "card_kind": card_kind}
        params.update(overrides)
        return cls(**params)


class Anki:
    def __init__(self, host: str | None = None, api_key: str | None = None):
        rh, rk = resolve_anki()
        self.host = host or rh
        self.api_key = api_key if api_key is not None else rk

    def _urlopen_json(self, req, action: str, *, timeout: float, trace: bool) -> Any:
        """POST + parse one AnkiConnect response. ``trace`` splits the IO and CPU spans
        (``anki_http_call`` / ``anki_json_parse``) for the known-word coloring path's latency budget."""
        if not trace:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  # AnkiConnect on 127.0.0.1 - fixed localhost scheme
                return json.loads(r.read())
        from overlay import otel_metrics

        with (
            otel_metrics.traced("anki_http_call", action=action),
            urllib.request.urlopen(req, timeout=timeout) as r,  # noqa: S310  # AnkiConnect on 127.0.0.1 - fixed localhost scheme
        ):
            raw = r.read()
        with otel_metrics.traced("anki_json_parse", action=action):
            return json.loads(raw)

    def _call(
        self, action: str, *, timeout: float = 20, attempts: int = 2, trace: bool = False, **params
    ):
        """The single AnkiConnect JSON-RPC entry point (SSOT). ``timeout``/``attempts`` tune fast-fail
        (doctor probe, coloring) vs retry-once (mining); ``trace`` adds otel spans. Raises
        ``_AnkiRetryable`` when Anki is down (see :func:`is_unreachable`), ``AnkiError`` on an app error."""
        payload: dict = {"action": action, "version": 6, "params": params}
        if self.api_key:
            payload["key"] = self.api_key  # AnkiConnect apiKey → request body
        body = json.dumps(payload).encode()
        req = urllib.request.Request(  # noqa: S310  # AnkiConnect on 127.0.0.1 - fixed localhost scheme
            self.host, body, {"Content-Type": "application/json"}
        )
        for attempt in stamina.retry_context(
            on=_AnkiRetryable, attempts=attempts, wait_initial=0.3, wait_max=1.0
        ):
            with attempt:
                try:
                    res = self._urlopen_json(req, action, timeout=timeout, trace=trace)
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

    def model_field_names(self, model: str) -> list[str]:
        """The real field names of a note type — used to validate a configured mining field map."""
        return self._call("modelFieldNames", modelName=model) or []

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
    field = cfg.expression_field()
    if not field:  # a card_format with no {expression} field → no reliable dedup key, allow the add
        return []
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


def _entity_values(card, sentence_html, picture, audio, misc, freq_html, freq_sort) -> dict:
    """logical entity -> content, for the default ``[mine.fields]`` map (media wrapped Anki-ready)."""
    return {
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


def _card_format_fields(
    cfg, card, sentence_html, picture, audio, misc, freq_html, freq_sort, tags, markers
) -> dict:
    """Render ``cfg.card_format`` (field -> ``{marker}`` template). ``markers`` from the miner when it
    has one; otherwise a partial map from these args (pitch/pos/title empty)."""
    from overlay.app.card_markers import build_markers, render_card_format

    if markers is None:
        markers = build_markers(
            card,
            sentence_html=sentence_html,
            picture=picture,
            audio=audio,
            misc=misc,
            doc_title="",
            freq_html=freq_html,
            freq_rank=freq_sort,
            pos_en="",
            tags=tags,
        )
    return render_card_format(cfg.card_format, markers)


def build_note(  # noqa: PLR0913  # arg-clump — bundle into a config object (#216)
    cfg: MineConfig,
    card: CardData,
    sentence_html: str,
    picture: str = "",
    audio: str = "",
    misc: str = "",
    freq_html: str = "",
    freq_sort: str = "",
    tags=(),
    *,
    allow_duplicate: bool = False,
    markers: dict | None = None,
) -> dict:
    """Assemble the AnkiConnect note dict from card data + media filenames. ``tags`` are extra per-card
    tags (source/episode) added to the config's static tags. ``allow_duplicate`` lets an explicit
    "add anyway" mine a second card for an expression already in the deck (a different scene).

    ``markers`` is the full ``{marker} -> value`` map for the ``[mine.card_format]`` path — the miner
    builds it (it has the token/dict/video the pitch/pos/title markers need). Omitted, that path falls
    back to a partial map from these args (pitch/pos/title empty), so ``build_note`` stays usable alone."""
    if cfg.card_format:
        note_fields = _card_format_fields(
            cfg, card, sentence_html, picture, audio, misc, freq_html, freq_sort, tags, markers
        )
    else:
        values = _entity_values(card, sentence_html, picture, audio, misc, freq_html, freq_sort)
        note_fields = {real: values.get(logical, "") for logical, real in cfg.fields.items()}
    note_fields.update(cfg.flags)
    all_tags = list(dict.fromkeys(list(cfg.tags) + list(tags)))  # dedupe, keep order
    return {
        "deckName": cfg.deck,
        "modelName": cfg.model,
        "fields": note_fields,
        "tags": all_tags,
        "options": {"allowDuplicate": allow_duplicate},
    }

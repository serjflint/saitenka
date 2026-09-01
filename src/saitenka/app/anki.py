"""Reaching Anki: finding it, launching it, talking to it, and asking what is already there.

What a card *is* moved to `saitenka_card`. What stays is everything that needs a running Anki — the
launch policy, the AnkiConnect wrapper, the error taxonomy callers use to degrade when it is down,
and the dedup query, which needs both the client and the note's expression field.
"""

from __future__ import annotations

import http.client
import importlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ankiconnect_client import AnkiConnectClient, AnkiConnectError, AnkiConnectUnavailable

if TYPE_CHECKING:
    from saitenka_card import MineConfig

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
        from saitenka.app.config import load_config

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
        from saitenka.app.config import load_config

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
            res = subprocess.run(
                launch, check=False, capture_output=True, text=True, encoding="utf-8", timeout=15
            )
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


class AnkiError(AnkiConnectError):
    """Saitenka compatibility name for an AnkiConnect application error."""


class _AnkiRetryable(AnkiConnectUnavailable, AnkiError):
    """A transient AnkiConnect failure (connection refused / timeout while Anki is briefly busy).
    ``stamina`` retries these ONCE, quickly — Anki being *not running* is a common steady state, so we
    keep the added latency tiny (a down call adds ~0.3s, not seconds). App errors (deck not found, …)
    are plain ``AnkiError`` and never retried."""


def is_unreachable(exc: BaseException) -> bool:
    """True when *exc* just means 'Anki isn't running' — connection refused / timeout, from either the
    :class:`Anki` client (``_AnkiRetryable``) or a raw ``urllib`` call (``OSError``/``URLError``).
    Anki can vanish at any moment; that's an expected steady state, so callers log it compactly
    (no traceback) and carry on."""
    return isinstance(exc, (AnkiConnectUnavailable, OSError))


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


class Anki:
    def __init__(self, host: str | None = None, api_key: str | None = None):
        rh, rk = resolve_anki()
        self.host = host or rh
        self.api_key = api_key if api_key is not None else rk
        self._client = AnkiConnectClient(self.host, self.api_key)

    def _call(
        self, action: str, *, timeout: float = 20, attempts: int = 2, trace: bool = False, **params
    ):
        """Compatibility seam over the extracted AnkiConnect client."""
        try:
            if not trace:
                return self._client.call(action, timeout=timeout, attempts=attempts, **params)
            from saitenka import otel_metrics

            class _PhaseObserver:
                @staticmethod
                def phase(name: str, action: str):
                    return otel_metrics.traced(f"anki_{name}", action=action)

            return self._client.call(
                action,
                timeout=timeout,
                attempts=attempts,
                phase_observer=_PhaseObserver(),
                **params,
            )
        except AnkiConnectUnavailable as exc:
            raise _AnkiRetryable(str(exc)) from exc
        except AnkiConnectError as exc:
            raise AnkiError(str(exc)) from exc

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

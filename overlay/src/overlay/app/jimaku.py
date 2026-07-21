"""Fetch Japanese subtitles from jimaku.cc (the modern kitsunekko replacement).

For files without an embedded Japanese track. Needs a free API key (https://jimaku.cc/profile).
The key is resolved with precedence ``explicit (config/CLI) > $JIMAKU_API_KEY > macOS Keychain`` —
the Keychain is the one that works under a GUI-launched (plugin-mode) mpv, which doesn't inherit the
shell's env. Flow: search anime by title → pick the entry → list the episode's files → download the
best (.srt/.ass) next to the video.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import stamina

log = logging.getLogger(__name__)

BASE = "https://jimaku.cc/api"

# Shown at the interactive key prompt (CLI `set-jimaku-key` + the setup wizard) so the user knows where
# to get the token. jimaku.cc accounts are free and require no personal data.
KEY_HELP = (
    "Get a free jimaku.cc API key: sign in at https://jimaku.cc, then copy it from "
    "https://jimaku.cc/profile — API docs at https://jimaku.cc/api/docs."
)

# jimaku.cc keys are long tokens (~58 chars). A very short entered value almost always means a botched
# paste — and the specific trap is Python's HIDDEN prompt (getpass) on Windows: it reads the console
# char-by-char via msvcrt and does NOT accept Ctrl+V, which lands a single control character. (Ctrl+V
# works fine OUTSIDE the hidden prompt — this is not a general PowerShell issue.) Right-click, or
# Ctrl+Shift+V in Windows Terminal, pastes the whole key; or pass it as an argument on the normal line.
KEY_MIN_LEN = 20
PASTE_HINT = (
    "Note: this HIDDEN prompt won't accept Ctrl+V (it captures one control char). Right-click to "
    "paste, or use Ctrl+Shift+V in Windows Terminal. You can also cancel and pass the key on the "
    "normal command line, where Ctrl+V works: saitenka-overlay set-jimaku-key <key>"
)


def key_paste_warning(k: str) -> str | None:
    """A human warning when an entered key looks truncated (the classic hidden-prompt Ctrl+V that
    lands a single char on Windows), else ``None``. An empty string is handled separately by callers
    as "no key entered" — only a non-empty-but-short value trips this."""
    if 0 < len(k) < KEY_MIN_LEN:
        return f"Warning: that key is only {len(k)} character(s); jimaku keys are ~58. {PASTE_HINT}"
    return None


def prompt_for_key(getpass_fn, input_fn=input, out=print, tries=3) -> str:  # pragma: no cover — I/O
    """Read a jimaku key at a hidden prompt with a truncated-paste guard: show where to get it (plus
    the Windows paste caveat), read hidden input, and if it looks too short, warn and offer to
    re-enter. Returns the final stripped key (``""`` if the user enters nothing)."""
    out(KEY_HELP)
    if sys.platform == "win32":
        out(PASTE_HINT)
    k = ""
    for attempt in range(tries):
        k = getpass_fn("jimaku.cc API key (hidden): ").strip()
        warn = key_paste_warning(k)
        if not warn:
            return k
        out(warn)
        if attempt == tries - 1:
            break
        try:
            if input_fn("Re-enter the key? [Y/n] ").strip().lower() in ("n", "no"):
                break
        except EOFError:
            break
    return k


# OS secret-store coordinates for the jimaku key (keyring service/username).
KEYCHAIN_SERVICE = "saitenka-overlay"
KEYCHAIN_ACCOUNT = "jimaku"


class JimakuError(RuntimeError):
    pass


class _JimakuRetryable(JimakuError):
    """A TRANSIENT jimaku failure — HTTP 429 (rate limit), 5xx, or a network error. ``stamina`` retries
    these with backoff; a client error (400/401/404) raises plain ``JimakuError`` and is NOT retried."""


def _http_error_detail(e: urllib.error.HTTPError) -> str:
    """jimaku's own error body (it returns JSON like ``{"error": "..."}``) as a short suffix — a bare
    "Bad Request" is useless for debugging."""
    try:
        body = (e.read() or b"").decode("utf-8", "replace").strip()
    except Exception:  # pragma: no cover — best-effort; never mask the original error
        log.debug("reading jimaku error body failed", exc_info=True)
        return ""
    if not body:
        return ""
    try:
        body = json.loads(body).get("error", body)
    except (ValueError, AttributeError):
        pass
    return f" — {str(body)[:300]}"


def keychain_get() -> str | None:
    """Read the jimaku key from the OS secret store via ``keyring`` (macOS Keychain / Windows
    Credential Locker / Linux Secret Service). None if unset or no backend is available (headless
    Linux) — the caller then falls back to config/env."""
    try:
        import keyring
        import keyring.errors

        return keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT) or None
    except keyring.errors.KeyringError:
        return None
    except Exception:  # pragma: no cover — keyring import/backend selection edge cases
        return None


def keychain_set(key: str) -> bool:
    """Store the jimaku key in the OS secret store via ``keyring``. False if no backend is available
    (the caller then persists to the config file instead). The OS store is readable by a GUI-launched
    (plugin-mode) mpv, unlike a shell env var."""
    try:
        import keyring
        import keyring.errors

        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, key)
        return True
    except keyring.errors.KeyringError:
        return False
    except Exception:  # pragma: no cover
        return False


def resolve_jimaku_key(explicit: str | None = None) -> tuple[str | None, str]:
    """Return ``(key, source)`` with precedence explicit (config/CLI) > ``$JIMAKU_API_KEY`` > macOS
    Keychain. ``source`` is ``config``/``env``/``keychain``/``none`` — reported by doctor.

    Every source is ``.strip()``-ed: a stray trailing newline/space (easy to introduce when pasting a
    key, or reading it back from a store) would otherwise make urllib reject the ``Authorization``
    header outright (``ValueError: Invalid header value``)."""
    for value, source in (
        (explicit, "config"),
        (os.environ.get("JIMAKU_API_KEY"), "env"),
        (keychain_get(), "keychain"),
    ):
        cleaned = (value or "").strip()
        if cleaned:
            return cleaned, source
    return None, "none"


@dataclass
class JimakuFile:
    name: str
    url: str
    size: int = 0

    @property
    def ext(self) -> str:
        return Path(self.name).suffix.lower()


def _ssl_context():
    """A TLS context backed by certifi's CA bundle — reliable HTTPS even where the OS/Python trust
    store is missing or stale (frozen apps, older macOS Pythons)."""
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # pragma: no cover — certifi is a declared dep
        return ssl.create_default_context()


class JimakuClient:
    def __init__(self, api_key: str | None = None, base: str = BASE):
        self.api_key = resolve_jimaku_key(api_key)[0] or ""
        self.base = base
        if not self.api_key:
            raise JimakuError(
                "no jimaku API key — run `saitenka-overlay set-jimaku-key` (stored in the OS secret "
                "store, readable by plugin-mode mpv), or set $JIMAKU_API_KEY. Free key: https://jimaku.cc/profile"
            )

    def _get(self, path: str, **params):
        url = f"{self.base}{path}"
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if q:
            url += "?" + q
        req = urllib.request.Request(url, headers={"Authorization": self.api_key})
        # Retry transient failures (429 / 5xx / network) with backoff; client errors (400/401/404) are
        # raised immediately with jimaku's error body (retrying them can't help).
        for attempt in stamina.retry_context(
            on=_JimakuRetryable, attempts=4, wait_initial=1.0, wait_max=8.0
        ):
            with attempt:
                try:
                    with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as r:
                        return json.loads(r.read())
                except urllib.error.HTTPError as e:  # 400/401/404 client · 429/5xx transient
                    detail = _http_error_detail(e)
                    if e.code == 429 or e.code >= 500:
                        raise _JimakuRetryable(
                            f"jimaku {e.code} for {path}: {e.reason}{detail}"
                        ) from e
                    hint = (
                        "  (check your API key: `saitenka-overlay set-jimaku-key`)"
                        if e.code == 401
                        else ""
                    )
                    raise JimakuError(
                        f"jimaku {e.code} for {path}: {e.reason}{detail}{hint}"
                    ) from e
                except urllib.error.URLError as e:  # DNS / timeout / connection reset — transient
                    raise _JimakuRetryable(f"jimaku network error for {path}: {e.reason}") from e
                except ValueError as e:  # illegal Authorization header — a stray char in the key
                    raise JimakuError(
                        f"jimaku request build failed for {path}: {e} — re-set the key with "
                        "`saitenka-overlay set-jimaku-key`"
                    ) from e
        raise JimakuError(f"jimaku request to {path} failed after retries")  # unreachable

    def search(self, query: str, anime: bool = True) -> list[dict]:
        return self._get("/entries/search", query=query, anime=str(anime).lower())

    def files(self, entry_id: int, episode: int | None = None) -> list[JimakuFile]:
        data = self._get(f"/entries/{entry_id}/files", episode=episode)
        return [JimakuFile(f["name"], f["url"], f.get("size", 0)) for f in data]

    def download(self, jf: JimakuFile, dest_dir: str | Path) -> Path:
        dest = Path(dest_dir) / jf.name
        req = urllib.request.Request(jf.url, headers={"Authorization": self.api_key})
        with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as r:
            dest.write_bytes(r.read())
        return dest

    def fetch(self, title: str, episode: int | None, dest_dir: str | Path) -> Path:
        """Search → best entry → best file for the episode → download. Returns the local path."""
        entries = self.search(title)
        if not entries:
            raise JimakuError(f"no jimaku entry for {title!r}")
        entry = entries[0]
        files = self.files(entry["id"], episode)
        if not files:
            raise JimakuError(f"no files for entry {entry.get('name')} ep {episode}")

        # prefer the episode number in the name, then .srt over .ass, then largest
        def score(f: JimakuFile) -> tuple:
            ep_hit = episode is not None and re.search(
                rf"(?<!\d){episode:02d}(?!\d)|(?<!\d){episode}(?!\d)", f.name
            )
            return (bool(ep_hit), f.ext in (".srt", ".ass"), f.ext == ".srt", f.size)

        best = max(files, key=score)
        return self.download(best, dest_dir)


_FN_EP = re.compile(r"[-_ ]\s*(?:e|ep|episode)?\s*(\d{1,3})\b", re.IGNORECASE)


def parse_filename(path: str | Path) -> tuple[str, int | None]:
    """Best-effort (title, episode) from an anime filename.

    '[Erai-raws] Nippon Sangoku - 10 [1080p …].mkv' → ('Nippon Sangoku', 10).
    """
    stem = Path(path).stem
    stem = re.sub(r"\[[^\]]*\]", " ", stem)  # drop [group]/[quality] tags
    stem = re.sub(r"\([^)]*\)", " ", stem)
    episode = None
    m = list(_FN_EP.finditer(stem))
    if m:
        episode = int(m[-1].group(1))
        stem = stem[: m[-1].start()]
    title = re.sub(r"[-_.]+", " ", stem)
    title = re.sub(r"\s+", " ", title).strip(" -–—")
    return title, episode

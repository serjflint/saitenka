"""Fetch Japanese subtitles from jimaku.cc (the modern kitsunekko replacement).

For files without an embedded Japanese track. Needs a free API key (https://jimaku.cc/account).
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
import urllib.error
import urllib.parse
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
    "https://jimaku.cc/account — API docs at https://jimaku.cc/api/docs."
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
    "normal command line, where Ctrl+V works: saitenka set-jimaku-key <key>"
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
            if input_fn("Re-enter the key? [Y/n] ").strip().lower() in {"n", "no"}:
                break
        except EOFError:
            break
    return k


# OS secret-store coordinates for the jimaku key (keyring service/username).
KEYCHAIN_SERVICE = "saitenka"
KEYCHAIN_ACCOUNT = "jimaku"


def keyring_enabled() -> bool:
    """Whether to use the OS keyring at all — gates BOTH storing and reading the key (a knob that only
    threads one seam would half-apply: still triggering the read it was meant to avoid).

    Some Windows AV heuristics flag the first Credential Locker read from a fresh process; opting out
    stores the key in the owner-only ``jimaku.key`` file and never issues the keyring syscall. Set via
    ``$SAITENKA_JIMAKU_KEYRING=0`` (a one-off override) or ``[jimaku].keyring = false`` (persistent —
    what ``set-jimaku-key --file`` writes). Default True."""
    env = os.environ.get("SAITENKA_JIMAKU_KEYRING")
    if env is not None:
        return env.strip().lower() not in {"0", "false", "no", "off"}
    try:
        from overlay.app.config import load_config

        jm = load_config().get("jimaku")
    except Exception:  # pragma: no cover — config load edge cases; default to enabled
        log.debug("reading [jimaku].keyring failed", exc_info=True)
        return True
    return bool(jm["keyring"]) if isinstance(jm, dict) and "keyring" in jm else True


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
    except ImportError:
        log.debug("keyring is not installed", exc_info=True)
        return None
    try:
        return keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT) or None
    except keyring.errors.KeyringError:
        return None
    except Exception:  # pragma: no cover — keyring import/backend selection edge cases
        log.debug("keyring backend unavailable", exc_info=True)
        return None


def keychain_set(key: str) -> bool:
    """Store the jimaku key in the OS secret store via ``keyring``. False if no backend is available
    (the caller then persists to the config file instead). The OS store is readable by a GUI-launched
    (plugin-mode) mpv, unlike a shell env var."""
    try:
        import keyring
        import keyring.errors
    except ImportError:
        log.debug("keyring is not installed", exc_info=True)
        return False
    try:
        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, key)
        return True
    except keyring.errors.KeyringError:
        return False
    except Exception:  # pragma: no cover — keyring import/backend selection edge cases
        log.debug("keyring backend unavailable", exc_info=True)
        return False


def key_file_path() -> Path:
    """Private plaintext fallback next to the platform-native Saitenka config."""
    from overlay.app.config import config_path

    return config_path().with_name("jimaku.key")


def key_file_get() -> str | None:
    try:
        return key_file_path().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def key_file_set(key: str) -> Path:
    from overlay.app.paths import atomic_write_text

    return atomic_write_text(key_file_path(), f"{key.strip()}\n")


def resolve_jimaku_key(explicit: str | None = None) -> tuple[str | None, str]:
    """Return ``(key, source)`` with precedence explicit (config/CLI) > ``$JIMAKU_API_KEY`` > OS
    keyring > private file. ``source`` is reported by doctor.

    Every source is ``.strip()``-ed: a stray trailing newline/space (easy to introduce when pasting a
    key, or reading it back from a store) would otherwise make urllib reject the ``Authorization``
    header outright (``ValueError: Invalid header value``)."""
    keychain = keychain_get() if keyring_enabled() else None  # skip the keyring read when opted out
    for value, source in (
        (explicit, "config"),
        (os.environ.get("JIMAKU_API_KEY"), "env"),
        (keychain, "keychain"),
        (key_file_get(), "file"),
    ):
        cleaned = (value or "").strip()
        if cleaned:
            return cleaned, source
    return None, "none"


def subs_cache_dir() -> Path:
    from overlay.app.subtitle_cache import subs_cache_dir as shared_cache_dir

    return shared_cache_dir()


def subs_cache_key(video: str | os.PathLike, title: str, episode, *, resync: bool = True) -> str:
    from overlay.app.subtitle_cache import subs_cache_key as shared_cache_key

    return shared_cache_key(video, title, episode, resync=resync)


def cached_subs(
    video: str | os.PathLike, title: str, episode, *, resync: bool = True
) -> Path | None:
    from overlay.app.subtitle_cache import cached_subs as shared_cached_subs

    return shared_cached_subs(video, title, episode, resync=resync)


def store_subs(
    video: str | os.PathLike,
    title: str,
    episode,
    sub_path: str | os.PathLike,
    *,
    resync: bool = True,
) -> Path:
    from overlay.app.subtitle_cache import store_subs as shared_store_subs

    return shared_store_subs(video, title, episode, sub_path, resync=resync)


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
    except ImportError:  # pragma: no cover — certifi is a declared dep
        return ssl.create_default_context()


class JimakuClient:
    def __init__(self, api_key: str | None = None, base: str = BASE):
        self.api_key = resolve_jimaku_key(api_key)[0] or ""
        self.base = base
        if not self.api_key:
            raise JimakuError(
                "no jimaku API key — run `saitenka set-jimaku-key` (persistent and readable by "
                "plugin-mode mpv), or set $JIMAKU_API_KEY. Free key: https://jimaku.cc/account"
            )

    @staticmethod
    def _http_error_exc(e: urllib.error.HTTPError, path: str) -> JimakuError:
        """400/401/404 → plain :class:`JimakuError` (not retried); 429/5xx → :class:`_JimakuRetryable`."""
        detail = _http_error_detail(e)
        if e.code == 429 or e.code >= 500:
            return _JimakuRetryable(f"jimaku {e.code} for {path}: {e.reason}{detail}")
        hint = "  (check your API key: `saitenka set-jimaku-key`)" if e.code == 401 else ""
        return JimakuError(f"jimaku {e.code} for {path}: {e.reason}{detail}{hint}")

    def _get(self, path: str, **params):
        url = f"{self.base}{path}"
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if q:
            url += "?" + q
        req = urllib.request.Request(  # noqa: S310  # jimaku.moe HTTPS API - fixed scheme
            url, headers={"Authorization": self.api_key}
        )
        # Retry transient failures (429 / 5xx / network) with backoff; client errors (400/401/404) are
        # raised immediately with jimaku's error body (retrying them can't help).
        for attempt in stamina.retry_context(
            on=_JimakuRetryable, attempts=4, wait_initial=1.0, wait_max=8.0
        ):
            with attempt:
                try:
                    with urllib.request.urlopen(  # noqa: S310  # jimaku.moe HTTPS API - fixed scheme
                        req, timeout=20, context=_ssl_context()
                    ) as r:
                        return json.loads(r.read())
                except urllib.error.HTTPError as e:  # 400/401/404 client · 429/5xx transient
                    raise self._http_error_exc(e, path) from e
                except urllib.error.URLError as e:  # DNS / timeout / connection reset — transient
                    raise _JimakuRetryable(f"jimaku network error for {path}: {e.reason}") from e
                except ValueError as e:  # illegal Authorization header — a stray char in the key
                    raise JimakuError(
                        f"jimaku request build failed for {path}: {e} — re-set the key with "
                        "`saitenka set-jimaku-key`"
                    ) from e
        raise JimakuError(f"jimaku request to {path} failed after retries")  # unreachable

    def search(self, query: str, *, anime: bool = True) -> list[dict]:
        return self._get("/entries/search", query=query, anime=str(anime).lower())

    def files(self, entry_id: int, episode: int | None = None) -> list[JimakuFile]:
        data = self._get(f"/entries/{entry_id}/files", episode=episode)
        return [JimakuFile(f["name"], f["url"], f.get("size", 0)) for f in data]

    def download(self, jf: JimakuFile, dest_dir: str | Path) -> Path:
        dest = Path(dest_dir) / jf.name
        req = urllib.request.Request(  # noqa: S310  # jimaku.moe HTTPS API - fixed scheme
            jf.url, headers={"Authorization": self.api_key}
        )
        with urllib.request.urlopen(  # noqa: S310  # jimaku.moe HTTPS API - fixed scheme
            req, timeout=60, context=_ssl_context()
        ) as r:
            dest.write_bytes(r.read())
        return dest

    def episode_files(
        self, title: str, episode: int | None, *, video: str | None = None
    ) -> list[JimakuFile]:
        """Every subtitle file for the episode, best-match first — the source list Window 1's picker
        shows. Same ordering as :meth:`fetch`'s auto-pick (so ``[0]`` is what fetch would grab); the
        user overrides it by choosing a differently-timed source from the list."""
        entries = self.search(title)
        if not entries:
            raise JimakuError(f"no jimaku entry for {title!r}")
        entry = entries[0]
        files = self.files(entry["id"], episode)
        if not files:
            raise JimakuError(f"no files for entry {entry.get('name')} ep {episode}")
        return sorted(files, key=lambda f: _candidate_score(f, episode, video), reverse=True)

    def fetch(
        self, title: str, episode: int | None, dest_dir: str | Path, *, video: str | None = None
    ) -> Path:
        """Search → best entry → best file for the episode → download. Returns the local path.

        ``video`` (the media filename) steers selection toward the sub whose release MATCHES this
        encode — an entry commonly carries several sources (AT-X / EX-TV / WebRip) whose cue timing
        differs by tens of seconds, so grabbing the biggest ``.srt`` mistimes everything (found live:
        ep03's AT-X sub put the opening 30s late on a CR WebRip). Resolution match is the tiebreaker
        BEFORE size."""
        candidates = self.episode_files(title, episode, video=video)
        best = candidates[0]
        match = _resolution_match(video, best.name)
        log.info(
            "jimaku: picked %s (candidates=%d, resolution_match=%s)",
            best.name,
            len(candidates),
            match,
        )
        # One span per fetch records WHICH release won and why — the "wrong release picked by size"
        # class (live: an AT-X rip chosen over the matching EX source) is invisible in a report otherwise.
        from overlay import otel_metrics

        with otel_metrics.traced("subtitle.fetch", provider="jimaku") as span:
            span.set("episode", episode if episode is not None else -1)
            span.set("candidates", len(candidates))
            span.set("resolution_match", match)
            span.set("ext", best.ext or "")
            span.set("picked", best.name)
            return self.download(best, dest_dir)


# A real, always-present jimaku entry — an empty result set then means a genuine failure (bad key /
# server), not "no such anime".
PROBE_QUERY = "Spy x Family"


def verify_key(key: str, query: str = PROBE_QUERY) -> tuple[str, str]:
    """Best-effort liveness probe for a jimaku key: one test search, classified so a wrong-but-full-length
    key is caught at save time (the length guard only catches a truncated paste), not mid-video.

    Returns ``(status, message)`` where status is:

    - ``"ok"``      — search succeeded, the key works;
    - ``"bad"``     — jimaku rejected it (401 bad key / 400) — the key is wrong, re-set it;
    - ``"unknown"`` — network/transient (offline, timeout, 5xx) — can't tell; the caller must NOT treat
      this as a bad key (never fail a correct save on a flaky network)."""
    try:
        entries = JimakuClient(api_key=key).search(query)
    except _JimakuRetryable as e:  # network / 429 / 5xx — indeterminate, not the key's fault
        return "unknown", f"couldn't verify (network/transient): {e}"
    except JimakuError as e:  # 401/400 — a client error is the key/request itself
        return "bad", str(e)
    head = f" — first: {entries[0].get('name')!r}" if entries else ""
    return "ok", f"{len(entries)} entrie(s){head}"


# Season+episode forms take precedence over a bare number and yield the E part: S01E05 / s1e5 /
# S01.E05 → 5, and 1x08 → 8. `\d{1,2}` on the left half keeps a resolution like 1920x1080 from
# matching (no 1–2-digit run sits directly before the `x`/`e`).
_FN_SXXEXX = re.compile(r"s\d{1,2}[\s._-]*e(\d{1,3})(?!\d)", re.IGNORECASE)
_FN_NXNN = re.compile(r"\b\d{1,2}x(\d{1,3})(?!\d)", re.IGNORECASE)
# Bare number, optionally prefixed e/ep/episode. `(?!\d)` (not `\b`) so a trailing word char like the
# '_' in 'Show_ep05_1080p' still terminates the episode — `\b` failed there ('5' and '_' are both \w).
_FN_EP = re.compile(r"[-_ ]\s*(?:e|ep|episode)?\s*(\d{1,3})(?!\d)", re.IGNORECASE)


# A "1080p" tag implies the standard raster; a per-broadcaster rip that states an anamorphic size
# (AT-X's 1440x1080) is NOT the same source as a 1080p WebRip and its cue timing drifts (different
# ad-breaks / eyecatch). Matching resolution is the cheap signal that a sub belongs to THIS encode.
_STD_WIDTH = {2160: 3840, 1080: 1920, 720: 1280, 576: 1024, 480: 640}
_RES_WXH = re.compile(r"(\d{3,4})\s*[x×]\s*(\d{3,4})")
_RES_P = re.compile(r"(?<!\d)(\d{3,4})p(?!\d)", re.IGNORECASE)


def _resolutions(name: str) -> set[tuple[int, int]]:
    """The (width, height) pairs a filename declares — explicit ``WxH`` plus ``Np`` normalised to its
    standard width (``1080p`` → ``1920x1080``), so a 1080p video matches a ``1920x1080`` sub but not an
    anamorphic ``1440x1080`` one."""
    out = {(int(w), int(h)) for w, h in _RES_WXH.findall(name)}
    out |= {(_STD_WIDTH.get(int(h), 0), int(h)) for h in _RES_P.findall(name)}
    return out


def _resolution_match(video: str | None, sub_name: str) -> bool:
    """True when the sub's declared resolution overlaps the video's — the strongest cheap signal that
    it's the matching release (so we don't grab an AT-X rip for a 1080p WebRip just because it's bigger)."""
    if not video:
        return False
    return bool(_resolutions(Path(video).name) & _resolutions(sub_name))


def _candidate_score(f: JimakuFile, episode: int | None, video: str | None) -> tuple:
    """Rank a candidate: episode number → matching release (resolution) → subtitle ext → .srt → size.
    Shared by :meth:`JimakuClient.fetch` (auto-pick = max) and :meth:`episode_files` (the picker's
    best-first order), so the picker's top row is exactly what fetch would have grabbed."""
    ep_hit = episode is not None and re.search(
        rf"(?<!\d){episode:02d}(?!\d)|(?<!\d){episode}(?!\d)", f.name
    )
    return (
        bool(ep_hit),
        _resolution_match(video, f.name),
        f.ext in {".srt", ".ass"},
        f.ext == ".srt",
        f.size,
    )


def parse_filename(path: str | Path) -> tuple[str, int | None]:
    """Best-effort (title, episode) from an anime filename.

    '[Erai-raws] Nippon Sangoku - 10 [1080p …].mkv' → ('Nippon Sangoku', 10).
    'Show S01E05 [x265].mkv' → ('Show', 5).
    """
    stem = Path(path).stem
    stem = re.sub(r"\[[^\]]*\]", " ", stem)  # drop [group]/[quality] tags
    stem = re.sub(r"\([^)]*\)", " ", stem)
    episode: int | None = None
    cut: int | None = None
    for rx in (_FN_SXXEXX, _FN_NXNN, _FN_EP):
        m = list(rx.finditer(stem))
        if m:
            episode = int(m[-1].group(1))
            cut = m[-1].start()
            break  # SxxExx / NxNN win over a bare number; the title is what precedes the match
    if cut is not None:
        stem = stem[:cut]
    title = re.sub(r"[-_.]+", " ", stem)
    title = re.sub(r"\s+", " ", title).strip(" -–—")
    return title, episode

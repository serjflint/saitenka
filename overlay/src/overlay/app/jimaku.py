"""Fetch Japanese subtitles from jimaku.cc (the modern kitsunekko replacement).

For files without an embedded Japanese track. Needs a free API key (jimaku.cc → account → API key).
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
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

BASE = "https://jimaku.cc/api"

# OS secret-store coordinates for the jimaku key (keyring service/username).
KEYCHAIN_SERVICE = "saitenka-overlay"
KEYCHAIN_ACCOUNT = "jimaku"


class JimakuError(RuntimeError):
    pass


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
    Keychain. ``source`` is ``config``/``env``/``keychain``/``none`` — reported by doctor."""
    if explicit:
        return explicit, "config"
    env = os.environ.get("JIMAKU_API_KEY")
    if env:
        return env, "env"
    kc = keychain_get()
    if kc:
        return kc, "keychain"
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
                "store, readable by plugin-mode mpv), or set $JIMAKU_API_KEY. Free key: jimaku.cc → account"
            )

    def _get(self, path: str, **params):
        url = f"{self.base}{path}"
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if q:
            url += "?" + q
        req = urllib.request.Request(url, headers={"Authorization": self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:  # 401 (bad key), 404, …
            raise JimakuError(f"jimaku {e.code} for {path}: {e.reason}") from e

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

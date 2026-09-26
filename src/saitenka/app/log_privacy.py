"""What the file log may not carry verbatim: the user's media identity and subtitle content.

Two mechanisms, because the two kinds of data reach the log differently. Content — a cue, a word,
a dictionary query — is unbounded, so the call site that logs it passes it through `text_label`.
Media identity — a video or subtitle path, the title parsed from it — is known once and then
reappears inside status strings, provider errors and exception text that no call site controls, so
it is registered where it is resolved and replaced in every file-log record by `scrub`.

Console output is not scrubbed: the user watching the terminal is looking at their own files.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from urllib.parse import quote, quote_plus

from saitenka.app.subtitle_geometry_diagnostics import cue_digest

#: Bumped whenever the file log stops carrying a kind of private data it used to. A report ships a
#: session's log and trace only if its records carry the current value, because lines written by an
#: older build still hold what this one scrubs.
LOG_FORMAT = 2

#: Shorter tokens would scrub ordinary words out of unrelated records.
_MIN_TOKEN = 4
#: A bare stem or a one-word title is often a word the log uses itself (`English.ass`, `video.mp4`);
#: only a distinctive one is registered. Full names and paths always are.
_MIN_DISTINCTIVE = 8
#: Episodes change within one long session; the oldest identities are the least likely to recur.
_CAPACITY = 256

_lock = threading.Lock()
_labels: dict[str, str] = {}
#: Rebuilt under the lock and swapped whole, so `scrub` — on every file-log record — never locks.
_snapshot: tuple[re.Pattern[str], dict[str, str]] | None = None


def text_label(text: object) -> str:
    """A cue, word or query as a join key and a length, never the text."""
    if not isinstance(text, str):
        return repr(text)
    return f"<text:{cue_digest(text)} len={len(text)}>"


def media_label(path: str | Path) -> str:
    """A media or subtitle file as a digest of its name plus its extension, which stays diagnostic."""
    name = Path(str(path)).name
    return f"<media:{cue_digest(name)}{Path(name).suffix.lower()}>"


def _distinctive(text: str) -> bool:
    return len(text) >= _MIN_DISTINCTIVE or len(text.split()) >= 2


def register_media(path: str | Path, *, title: str | None = None) -> None:
    """Scrub this file's path, name and stem — and the title parsed from it — from the file log."""
    raw = str(path)
    label = media_label(raw)
    # `repr` is how an OSError quotes its filename: doubled backslashes, non-ASCII kept — which the
    # JSON form escapes instead. mpv may report a Windows path with forward slashes.
    forms = {raw, repr(raw)[1:-1], json.dumps(raw)[1:-1], raw.replace("\\", "/"), Path(raw).name}
    stem = Path(raw).stem
    if len(stem) >= _MIN_DISTINCTIVE:
        forms.add(stem)
    tokens = dict.fromkeys(forms, label)
    if title:
        tokens.update(_title_tokens(title))
    _register(tokens)


def register_title(title: str) -> None:
    """Scrub a series or release name a provider returned, where it is not a file Saitenka opened."""
    _register(_title_tokens(title))


def _title_tokens(title: str) -> dict[str, str]:
    if not _distinctive(title):
        return {}
    label = f"<title:{cue_digest(title)}>"
    return dict.fromkeys((title, repr(title)[1:-1], quote(title), quote_plus(title)), label)


def _register(tokens: dict[str, str]) -> None:
    from saitenka.app.report import _scrub_home

    global _snapshot
    # The file sink redacts the home directory before this scrub runs, so a path under it has to be
    # matched in its redacted spelling as well.
    tokens = {**{_scrub_home(t): label for t, label in tokens.items()}, **tokens}
    with _lock:
        for token, label in tokens.items():
            if len(token) < _MIN_TOKEN or token.isdigit():
                continue
            _labels.pop(token, None)
            _labels[token] = label
        while len(_labels) > _CAPACITY:
            del _labels[next(iter(_labels))]
        # Longest first, so a full path is replaced whole rather than around its registered name;
        # bounded by non-word characters, so a title never rewrites part of a longer word.
        ordered = sorted(_labels, key=len, reverse=True)
        _snapshot = (
            (
                re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, ordered)) + r")(?!\w)"),
                dict(_labels),
            )
            if ordered
            else None
        )


def reset() -> None:
    """Forget every registered identity — for a test that registers, in its own teardown."""
    global _snapshot
    with _lock:
        _labels.clear()
        _snapshot = None


def scrub(text: str) -> str:
    """Replace every registered media identity in ``text`` with its label."""
    snapshot = _snapshot
    if snapshot is None:
        return text
    pattern, labels = snapshot
    return pattern.sub(lambda match: labels[match.group(0)], text)

"""Per-cue tokenization cache: a source subtitle line → its tokenized + scored result.

A repeated line (a loop, a review re-watch, an Alt+← nav back, a full-episode prefetch line that
later becomes the on-screen cue) skips the whole tokenize path — fugashi parse, the dict
compound-merge probe, and scoring — and annotates at cue time with no plain-then-upgrade flicker.

Two hard rules enforced at the insertion point:

* **Never memoize an empty result.** Storing a blank tokenization would permanently strip
  annotations from every later occurrence of that line, so `put` drops it and the line re-tokenizes.
* **Only memoize a COMPLETE annotation.** A line tokenized before the dictionaries/scorer finished
  loading (no compound merge, no colors) is a transient — caching it would strip annotations from
  every later occurrence. The caller passes ``complete`` so the same line re-attempts once deps land.

Distinct from :mod:`overlay.app.subtitle_cache`, which caches resynced ``.srt`` *files* on disk.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from overlay.app.tokenize import Token
    from overlay.model import Style


@dataclass(frozen=True, slots=True)
class TokenizedCue:
    """One cue's fully-resolved render inputs: the per-source-line token lists (row-major), the flat
    token list, and the per-token styles (``None`` when no scorer ran)."""

    lines: list[list[Token]]
    tokens: list[Token]
    styles: list[Style] | None


class TokenCache:
    """Thread-safe LRU of ``source text → TokenizedCue``. Shared by the main thread (cue redraw) and
    the prefetch workers, so — like :class:`~overlay.app.dictionary.Dictionary._entry_cache` — every
    mutation holds the lock (``OrderedDict`` get/move_to_end/setitem/popitem aren't atomic no-GIL)."""

    def __init__(self, maxsize: int = 512) -> None:
        self._data: OrderedDict[str, TokenizedCue] = OrderedDict()
        self._max = max(1, maxsize)
        self._lock = threading.Lock()

    def get(self, text: str) -> TokenizedCue | None:
        with self._lock:
            cue = self._data.get(text)
            if cue is not None:
                self._data.move_to_end(text)
            return cue

    def put(self, text: str, cue: TokenizedCue, *, complete: bool = True) -> None:
        # Never store an empty or incomplete tokenization — a later identical line must get
        # another shot at annotations (empty = a miss re-tries; incomplete = re-tries once deps load).
        if not cue.tokens or not complete:
            return
        with self._lock:
            self._data[text] = cue
            self._data.move_to_end(text)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

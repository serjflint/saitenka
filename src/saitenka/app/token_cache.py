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

Distinct from :mod:`saitenka.app.subtitle_cache`, which caches resynced ``.srt`` *files* on disk.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka_tokenize.japanese import Token

    from saitenka.app.scoring import TokenStyle


def cue_key(text: str) -> str:
    """A cue's cache key: mpv's sub-text with ASS/CR line breaks normalized to ``\\n``.

    The SAME transform for a live cue and a warmed one, or an episode-prefetched line misses.
    """
    return text.replace("\\N", "\n").replace("\r", "")


@dataclass(frozen=True, slots=True)
class TokenizedCue:
    """One cue's fully-resolved render inputs: the per-source-line token lists (row-major), the flat
    token list, and the per-token styles (``None`` when no scorer ran)."""

    lines: list[list[Token]]
    tokens: list[Token]
    styles: list[TokenStyle] | None


class TokenCache:
    """Thread-safe LRU of ``source text → TokenizedCue``. Shared by the main thread (cue redraw) and
    the prefetch workers, so every mutation holds the lock (``OrderedDict`` get/move_to_end/setitem/popitem aren't atomic no-GIL)."""

    def __init__(self, maxsize: int = 512) -> None:
        self._data: OrderedDict[str, TokenizedCue] = OrderedDict()
        self._max = max(1, maxsize)
        self._lock = threading.Lock()
        # Bumped on every clear() so a background worker that captured the generation before a profile
        # swap can't land a stale-language entry AFTER the swap cleared the cache (#254 D8 race). The
        # check and the swap's clear share this lock, so the compare-then-store is atomic vs the clear.
        self._gen = 0

    @property
    def generation(self) -> int:
        """The current cache generation — a background warm captures this at its start and passes it to
        :meth:`put`, so a profile swap (which bumps it via :meth:`clear`) drops the warm's in-flight puts."""
        with self._lock:
            return self._gen

    def get(self, text: str) -> TokenizedCue | None:
        with self._lock:
            cue = self._data.get(text)
            if cue is not None:
                self._data.move_to_end(text)
            return cue

    def put(
        self, text: str, cue: TokenizedCue, *, complete: bool = True, generation: int | None = None
    ) -> None:
        # Never store an empty or incomplete tokenization — a later identical line must get
        # another shot at annotations (empty = a miss re-tries; incomplete = re-tries once deps load).
        if not cue.tokens or not complete:
            return
        with self._lock:
            if generation is not None and generation != self._gen:
                return  # a profile swap cleared+bumped the cache after this cue was tokenized → drop it
            self._data[text] = cue
            self._data.move_to_end(text)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._gen += (
                1  # invalidate any generation captured before this clear (see put/generation)
            )

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

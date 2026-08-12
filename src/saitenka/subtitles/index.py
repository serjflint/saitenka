"""Time-ordered subtitle cue lookup and navigation."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.subtitles.model import Cue


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\\N", "\n")).strip()


class CueIndex:
    """A sorted cue list with lookup and navigation operations."""

    def __init__(self, cues: list[Cue]):
        self.cues = sorted(cues, key=lambda cue: cue.start)
        by_text: defaultdict[str, list[int]] = defaultdict(list)
        for position, cue in enumerate(self.cues):
            by_text[_normalize(cue.text)].append(position)
        self._by_text = dict(by_text)

    def __len__(self) -> int:
        return len(self.cues)

    def _at(self, timestamp: float) -> int | None:
        return next(
            (
                position
                for position, cue in enumerate(self.cues)
                if cue.start <= timestamp < cue.end
            ),
            None,
        )

    def _from_text(self, text: str, preferred: int, sub_start: float | None) -> int | None:
        matches = self._by_text.get(_normalize(text), [])
        if not matches:
            return None
        if preferred >= 0:
            return min(matches, key=lambda position: abs(position - preferred))
        if sub_start is not None:
            timed = next(
                (
                    position
                    for position in matches
                    if self.cues[position].start <= sub_start < self.cues[position].end
                ),
                None,
            )
            if timed is not None:
                return timed
        return matches[0]

    def _from_playback_position(self, timestamp: float) -> int | None:
        return next(
            (position for position, cue in enumerate(self.cues) if cue.end > timestamp),
            None,
        )

    def locate(
        self,
        *,
        text: str | None = None,
        sub_start: float | None = None,
        time_pos: float | None = None,
        preferred: int = -1,
    ) -> int:
        """Return the current cue index, or ``-1`` when none can be identified."""
        if text:
            position = self._from_text(text, preferred, sub_start)
            if position is not None:
                return position
        if sub_start is not None:
            position = self._at(sub_start)
            if position is not None:
                return position
        if time_pos is not None:
            position = self._from_playback_position(time_pos)
            if position is not None:
                return position
        return -1

    def target(self, current: int, delta: int, *, inside: bool = True) -> int:
        """Return the cue reached by a previous, replay, or next navigation step."""
        if current < 0:
            candidate = 0 if delta > 0 else -1
        elif inside:
            candidate = current + delta
        elif delta > 0:
            candidate = current
        elif delta < 0:
            candidate = current - 1
        else:
            candidate = -1
        return candidate if 0 <= candidate < len(self.cues) else -1

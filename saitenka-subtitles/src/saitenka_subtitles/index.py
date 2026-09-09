"""Time-ordered subtitle cue lookup and navigation."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import islice
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from saitenka_subtitles.model import Cue


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\\N", "\n")).strip()


@dataclass(frozen=True, slots=True)
class ActiveCue:
    """Which cue is current, and how contested that choice was.

    ``overlapping`` counts the *other* cues that were on screen at the same moment, and is only
    populated when a timestamp decided the choice — a text match is answering a different question,
    so it reports nothing rather than a number that means something else.
    """

    position: int
    overlapping: int = 0

    @property
    def located(self) -> bool:
        return self.position >= 0


class CueIndex:
    """A sorted cue list with lookup and navigation operations."""

    def __init__(self, cues: list[Cue]):
        # Stable, and on `start` alone, so co-timed cues keep the order the document gave them —
        # `frame_text` joins in this order and the renderer reads the document in that same order,
        # so a different tie-break here would make the two disagree about a line no one moved.
        self.cues = sorted(cues, key=lambda cue: cue.start)
        self._frames = self._compute_frames()
        by_text: defaultdict[str, list[int]] = defaultdict(list)
        for position, cue in enumerate(self.cues):
            by_text[_normalize(cue.text)].append(position)
        # A frame's joined text under every position in it. After a navigation the drawn line is
        # the frame, not the event, so a lookup by what is on screen has to find its way back —
        # without this, next/next/next stops chaining the moment it steps into an overlap.
        for position, frame in enumerate(self._frames):
            if len(frame) > 1:
                by_text[_normalize(self.frame_text(position))].append(position)
        self._by_text = {text: sorted(set(hits)) for text, hits in by_text.items()}
        self._boundaries = tuple(
            sorted({point for cue in self.cues for point in (cue.start, cue.end)})
        )

    def _compute_frames(self) -> tuple[tuple[int, ...], ...]:
        """For each cue, every cue on screen at the instant it appears — itself included.

        The instant is the renderer's, not a fresh choice: `authored_ass_rows_at` is asked at
        ``round(start * 1000) + 1`` ms, so anything else here would build a set the document
        disagrees with, which is the whole defect this exists to close.

        One sweep with a heap of open ends rather than a scan per cue, because a sign held over a
        scene makes the scan quadratic exactly where the overlap it is looking for lives.
        """
        frames: list[tuple[int, ...]] = []
        opened: list[tuple[float, int]] = []
        cursor = 0
        for cue in self.cues:
            instant = (round(cue.start * 1_000) + 1) / 1_000
            while cursor < len(self.cues) and self.cues[cursor].start <= instant:
                heappush(opened, (self.cues[cursor].end, cursor))
                cursor += 1
            while opened and opened[0][0] <= instant:
                heappop(opened)
            frames.append(tuple(sorted(position for _end, position in opened)))
        return tuple(frames)

    def frame_at_position(self, position: int) -> tuple[int, ...]:
        """The cues drawn together with ``position``, in document order."""
        if not 0 <= position < len(self._frames):
            return ()
        return self._frames[position]

    def frame_text(self, position: int) -> str:
        """What is actually on screen when ``position`` is: every co-timed cue, joined.

        mpv draws the set active at an instant; a cue is one authored event. Navigating to the
        event and drawing only its text hands the geometry a line the document does not have at
        that timestamp, which it refuses (`subtitle-hint-text-mismatch`) — so color waits for mpv
        instead of painting from the index.
        """
        return "\n".join(self.cues[index].text for index in self.frame_at_position(position))

    def __len__(self) -> int:
        return len(self.cues)

    def boundaries_after(self, timestamp: float) -> Iterator[float]:
        """Return authored visibility-change times strictly after ``timestamp``."""
        return islice(self._boundaries, bisect_right(self._boundaries, timestamp), None)

    def active_at(self, timestamp: float) -> ActiveCue:
        """The cue a viewer would call current at ``timestamp``, and how many share the moment.

        Overlap is ordinary in authored subtitles — a sign held over a scene, two speakers — and
        the list is sorted by start, so "the first active cue" quietly means "the one that has been
        on screen longest". A sign spanning a scene then answers for every moment inside it, and
        prev/next step relative to the sign rather than the dialogue being read. The most recently
        revealed line is the one the viewer is on, so that is the choice. Ties break on the later
        end and then the lower position, which keeps the order total: an unstable pick would make
        next/next/next non-deterministic.
        """
        active = [
            position for position, cue in enumerate(self.cues) if cue.start <= timestamp < cue.end
        ]
        if not active:
            return ActiveCue(-1)
        chosen = max(
            active,
            key=lambda position: (self.cues[position].start, self.cues[position].end, -position),
        )
        return ActiveCue(chosen, len(active) - 1)

    def _at(self, timestamp: float) -> int | None:
        active = self.active_at(timestamp)
        return active.position if active.located else None

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
        """The active cue, or the upcoming one across a gap.

        Split rather than one "first cue still to end" scan, because that scan answers the active
        case with the longest-running cue — the same overlap trap `active_at` exists to close.
        With nothing active the two are equivalent: a remaining cue that has not ended has not
        started either.
        """
        active = self._at(timestamp)
        if active is not None:
            return active
        return next(
            (position for position, cue in enumerate(self.cues) if cue.end > timestamp),
            None,
        )

    def locate_active(
        self,
        *,
        text: str | None = None,
        sub_start: float | None = None,
        time_pos: float | None = None,
        preferred: int = -1,
    ) -> ActiveCue:
        """Locate the current cue, reporting how contested the choice was.

        Text is the strongest evidence and answers on its own; the timings are what have to resolve
        an overlap, so only they can report one.
        """
        if text:
            position = self._from_text(text, preferred, sub_start)
            if position is not None:
                return ActiveCue(position)
        if sub_start is not None:
            active = self.active_at(sub_start)
            if active.located:
                return active
        if time_pos is not None:
            active = self.active_at(time_pos)
            if active.located:
                return active
            position = self._from_playback_position(time_pos)
            if position is not None:
                return ActiveCue(position)
        return ActiveCue(-1)

    def locate(
        self,
        *,
        text: str | None = None,
        sub_start: float | None = None,
        time_pos: float | None = None,
        preferred: int = -1,
    ) -> int:
        """Return the current cue index, or ``-1`` when none can be identified."""
        return self.locate_active(
            text=text, sub_start=sub_start, time_pos=time_pos, preferred=preferred
        ).position

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

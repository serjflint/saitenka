from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
from saitenka_subtitles import Cue, CueIndex

from saitenka.app.subnav_policy import resolve_target


@given(st.lists(st.integers(min_value=1, max_value=4), min_size=2, max_size=8))
def test_next_visits_distinct_authored_starts_regardless_of_event_multiplicity(multiplicities):
    cues = [
        Cue(10.0 * group, 10.0 * group + 2 + event / 10, "猫")
        for group, count in enumerate(multiplicities, 1)
        for event in range(count)
    ]
    index = CueIndex(cues)
    position = 0
    destinations = []
    for _ in range(len(multiplicities) - 1):
        chosen = resolve_target(
            index,
            delta=1,
            text="猫",
            sub_start=cues[position].start,
            time_pos=cues[position].start + 1,
            nav_idx=position,
        )
        assert chosen is not None
        position = chosen.index
        destinations.append(index.cues[position].start)
    assert destinations == [10.0 * group for group in range(2, len(multiplicities) + 1)]

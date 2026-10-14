"""WP4.1 gates: the projection is the sole interpreter of ordered mpv observations."""

from __future__ import annotations

from dataclasses import replace

from hypothesis import example, given
from hypothesis import strategies as st

from saitenka.runtime.playback import (
    CueIdentityRetired,
    CueObservationChanged,
    FactDomain,
    ObservedCue,
    PlaybackProjection,
    PlaybackState,
    Revision,
    SubtitleSelectionChanged,
)


def installed(
    projection: PlaybackProjection,
    *,
    text: str = "猫を見る",
    start: object = 1.0,
    end: object = 3.0,
) -> PlaybackState:
    """A state whose cue identity is installed, as the subtitle owner leaves it."""
    state = PlaybackState()
    for name, data in (("sub-text", text), ("sub-start", start), ("sub-end", end)):
        state = projection.seed(state, name, data)
    return projection.install(state, start=start, end=end)


def kinds(deltas: tuple[object, ...]) -> list[type]:
    return [type(delta) for delta in deltas]


# --- gate: the first conflicting observation retires before the next command ------------------


# --- gate: joined and split sequences converge -------------------------------------------------

_OBSERVATIONS = st.sampled_from(
    (
        ("sub-text", "猫を見る"),
        ("sub-text", "犬も見る"),
        ("sub-text", ""),
        ("sub-start", 1.0),
        ("sub-start", 4.0),
        ("sub-end", 3.0),
        ("sid", 1),
        ("sid", 2),
        ("sub-delay", 0.5),
        ("osd-dimensions", {"w": 1920, "h": 1080}),
        ("pause", True),
        ("mouse-pos", {"x": 4, "y": 8}),
        ("secondary-sub-text", "English"),
    )
)


@given(st.lists(_OBSERVATIONS, min_size=1, max_size=16))
@example([("sub-text", "猫を見る")])
def test_reobserving_the_latest_values_emits_nothing(
    observations: list[tuple[str, object]],
) -> None:
    projection = PlaybackProjection()
    state = PlaybackState()
    for name, data in observations:
        state = projection.observe(state, name, data).state

    for name, data in dict(observations).items():
        projected = projection.observe(state, name, data)
        assert projected.deltas == ()
        assert replace(projected.state, sid_observations=state.sid_observations) == state
        assert projected.state.sid_observations == state.sid_observations + (name == "sid")


# --- gate: identical text under a new source/track/role/cue is a new identity -----------------


# --- gate: an old epoch or incomplete observation cannot resurrect retired state ---------------


def test_an_unnamed_observation_is_inert() -> None:
    projection = PlaybackProjection()
    state = installed(projection)

    projected = projection.observe(state, "", "anything")

    assert projected.state == state
    assert projected.deltas == ()


# --- gate: source, role, and render-space revisions are explicit and immutable -----------------


# --- gate: every cue observation publishes the identity it implies -----------------------------


def observed(deltas: tuple[object, ...]) -> list[ObservedCue]:
    return [d.cue for d in deltas if isinstance(d, CueObservationChanged)]


def test_a_split_cue_burst_settles_on_the_same_identity_as_a_joined_one() -> None:
    projection = PlaybackProjection()
    cue = ("猫を見る", 1.0, 3.0)

    split = PlaybackState()
    for name, data in zip(
        ("sub-start", "sub-text", "sub-end"), (cue[1], cue[0], cue[2]), strict=True
    ):
        split = projection.observe(split, name, data).state
    joined = PlaybackState()
    for name, data in zip(("sub-text", "sub-start", "sub-end"), cue, strict=True):
        joined = projection.observe(joined, name, data).state

    assert split.identity() == joined.identity()


def test_each_changed_cue_fact_publishes_the_identity_it_implies() -> None:
    projection = PlaybackProjection()
    state = PlaybackState()

    seen = []
    for name, data in (("sub-start", 1.0), ("sub-text", "猫を見る"), ("sub-end", 3.0)):
        projected = projection.observe(state, name, data)
        state = projected.state
        seen.extend(observed(projected.deltas))

    # The reducer has no batch, so a split burst is three deltas; the last carries the whole cue and
    # the drain coalesces by equality. Only sub-text advances the cue revision.
    assert [(c.text, c.start, c.end) for c in seen] == [
        ("", 1.0, None),
        ("猫を見る", 1.0, None),
        ("猫を見る", 1.0, 3.0),
    ]
    assert [c.cue for c in seen] == [Revision(), Revision(1), Revision(1)]


def test_observed_echo_confirms_a_locally_replaced_cue_once_without_new_identity() -> None:
    projection = PlaybackProjection()
    state = projection.cue_replaced(PlaybackState(), "猫を見る")
    identity = state.identity()

    confirmed = projection.observe(state, "sub-text", "猫を見る")

    assert observed(confirmed.deltas) == [identity]
    assert confirmed.state.identity() == identity
    assert projection.observe(confirmed.state, "sub-text", "猫を見る").deltas == ()


def test_identical_text_under_a_new_track_changes_identity_without_a_cue_delta() -> None:
    projection = PlaybackProjection()
    state = projection.observe(PlaybackState(), "sub-text", "猫を見る").state
    before = state.identity()

    state = projection.observe(state, "sid", 2).state
    after = projection.observe(state, "sub-text", "猫を見る")

    # observe()'s guard is keyed on the property value, so mpv re-sending the same text under a new
    # track publishes no cue delta even though the identity moved. A delta-driven consumer must take
    # the track change from SubtitleSelectionChanged; subscribing to cue deltas alone would miss it.
    assert observed(after.deltas) == []
    assert after.state.identity() != before
    assert SubtitleSelectionChanged in kinds(projection.observe(state, "sid", 3).deltas)


# --- gate: projected playback domains -----------------------------------------------------------


def test_every_fact_domain_is_reachable_from_the_delta_vocabulary() -> None:
    from saitenka.runtime.playback import _DELTA_DOMAIN

    assert set(_DELTA_DOMAIN.values()) == set(FactDomain)


@given(start=st.integers(min_value=0, max_value=10000), end_first=st.booleans())
def test_unknown_timing_completes_once_but_conflicting_known_timing_retires(start, end_first):
    projection = PlaybackProjection()
    state = installed(projection, start=None, end=None)
    fields = [("sub-start", start), ("sub-end", start + 2)]
    if end_first:
        fields.reverse()
    for name, value in fields:
        observed = projection.observe(state, name, value)
        assert CueIdentityRetired not in kinds(observed.deltas)
        state = observed.state
        observed = projection.observe(state, name, None)
        assert CueIdentityRetired not in kinds(observed.deltas)
        state = observed.state
    assert state.cue.installed is not None
    assert (state.cue.installed.start, state.cue.installed.end) == (start, start + 2)

    conflict = projection.observe(state, fields[0][0], fields[0][1] + 10)

    assert CueIdentityRetired in kinds(conflict.deltas)
    assert conflict.state.cue.installed is None
    repeated = projection.observe(conflict.state, fields[1][0], fields[1][1] + 10)
    assert CueIdentityRetired not in kinds(repeated.deltas)

"""WP4.1 gates: the projection is the sole interpreter of ordered mpv observations."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from saitenka.runtime.playback import (
    AuthoredCueStale,
    ConnectionChanged,
    CueIdentityRetired,
    FactDomain,
    GeometryInputChanged,
    PauseChanged,
    PlaybackProjection,
    PlaybackState,
    PointerMoved,
    RenderSpaceChanged,
    RetireReason,
    Revision,
    SourceChanged,
    SubtitleSelectionChanged,
    SubtitleTimingChanged,
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


def test_changed_cue_text_retires_the_installed_identity_in_the_same_observation() -> None:
    projection = PlaybackProjection()
    state = installed(projection)

    projected = projection.observe(state, "sub-text", "犬も見る")

    retired = projected.deltas[0]
    assert isinstance(retired, CueIdentityRetired)
    assert retired.reason is RetireReason.CUE_TEXT
    assert projected.state.cue.installed is None


def test_a_second_conflicting_observation_does_not_retire_twice() -> None:
    projection = PlaybackProjection()
    state = projection.observe(installed(projection), "sub-text", "犬も見る").state

    projected = projection.observe(state, "sub-start", 9.0)

    assert CueIdentityRetired not in kinds(projected.deltas)


def test_a_changed_track_retires_the_identity_before_the_selection_delta() -> None:
    projection = PlaybackProjection()
    state = projection.seed(installed(projection), "sid", 1)
    state = projection.install(state, start=1.0, end=3.0)

    projected = projection.observe(state, "sid", 2)

    assert kinds(projected.deltas)[:2] == [CueIdentityRetired, AuthoredCueStale]
    assert SubtitleSelectionChanged in kinds(projected.deltas)


def test_repeated_timing_equal_to_the_installed_identity_is_not_a_conflict() -> None:
    projection = PlaybackProjection()
    state = projection.seed(installed(projection), "sub-start", 0.0)
    # The observer replays the value the identity was installed with.
    state = projection.install(state, start=1.0, end=3.0)

    projected = projection.observe(state, "sub-start", 1.0)

    assert CueIdentityRetired not in kinds(projected.deltas)
    assert projected.state.cue.installed is not None


def test_a_null_timing_observation_cannot_retire_the_identity() -> None:
    projection = PlaybackProjection()

    projected = projection.observe(installed(projection), "sub-end", None)

    assert CueIdentityRetired not in kinds(projected.deltas)
    assert projected.state.cue.installed is not None


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
    )
)


@given(st.lists(_OBSERVATIONS, min_size=1, max_size=24), st.integers(min_value=1, max_value=6))
def test_split_and_joined_delivery_converge_to_the_same_state(
    observations: list[tuple[str, object]], chunk: int
) -> None:
    """A transport burst has no semantic meaning: only the observation order matters."""
    projection = PlaybackProjection()

    one_at_a_time = PlaybackState()
    for name, data in observations:
        one_at_a_time = projection.observe(one_at_a_time, name, data).state

    batched = PlaybackState()
    for index in range(0, len(observations), chunk):
        for name, data in observations[index : index + chunk]:
            batched = projection.observe(batched, name, data).state

    assert one_at_a_time == batched


@given(st.lists(_OBSERVATIONS, min_size=1, max_size=16))
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
        assert projected.state == state


# --- gate: identical text under a new source/track/role/cue is a new identity -----------------


def test_identical_text_on_a_new_cue_receives_a_new_identity() -> None:
    projection = PlaybackProjection()
    state = projection.seed(PlaybackState(), "sub-text", "猫を見る")
    first = state.identity()

    state = projection.observe(state, "sub-text", "").state
    state = projection.observe(state, "sub-text", "猫を見る").state

    assert state.identity() != first
    assert state.identity().text == first.text


def test_identical_text_on_a_new_source_receives_a_new_identity() -> None:
    projection = PlaybackProjection()
    state = projection.seed(PlaybackState(), "sub-text", "猫を見る")
    first = state.identity()

    state = projection.source_replaced(state, "/media/next.mkv").state

    assert state.identity() != first
    assert state.identity().source == first.source.advance()


def test_identical_text_on_a_new_track_receives_a_new_identity() -> None:
    projection = PlaybackProjection()
    state = projection.seed(PlaybackState(), "sub-text", "猫を見る")
    state = projection.seed(state, "sid", 1)
    first = state.identity()

    state = projection.observe(state, "sid", 2).state

    assert state.identity() != first


def test_a_same_sid_role_change_retires_the_old_identity() -> None:
    projection = PlaybackProjection()
    state = projection.seed(installed(projection), "sid", 1)
    state = projection.install(state, start=1.0, end=3.0)
    first = state.identity()

    projected = projection.role_changed(state, "second")

    assert kinds(projected.deltas) == [SubtitleSelectionChanged, CueIdentityRetired]
    assert projected.state.identity() != first
    assert projected.state.track.sid == 1


def test_an_unchanged_role_is_not_a_transition() -> None:
    projection = PlaybackProjection()
    state = projection.role_changed(PlaybackState(), "jp").state

    assert projection.role_changed(state, "jp") == projection.role_changed(state, "jp")
    assert projection.role_changed(state, "jp").deltas == ()


# --- gate: an old epoch or incomplete observation cannot resurrect retired state ---------------


def test_an_older_epoch_observation_changes_nothing() -> None:
    projection = PlaybackProjection()
    state = projection.connection_changed(installed(projection), epoch=2, ready=True).state

    projected = projection.observe(state, "sub-text", "犬も見る", connection_epoch=1)

    assert projected.state == state
    assert projected.deltas == ()


def test_a_replacement_epoch_retires_before_it_reports_ready() -> None:
    projection = PlaybackProjection()

    projected = projection.connection_changed(installed(projection), epoch=1, ready=True)

    assert kinds(projected.deltas) == [CueIdentityRetired, ConnectionChanged]
    retired = projected.deltas[0]
    assert isinstance(retired, CueIdentityRetired)
    assert retired.reason is RetireReason.CONNECTION


def test_connection_loss_retires_the_installed_identity() -> None:
    projection = PlaybackProjection()

    projected = projection.connection_changed(installed(projection), epoch=0, ready=False)

    assert kinds(projected.deltas) == [CueIdentityRetired, ConnectionChanged]
    assert projected.state.connection.ready is False


def test_a_retired_identity_is_not_resurrected_by_a_replayed_observation() -> None:
    projection = PlaybackProjection()
    state = projection.connection_changed(installed(projection), epoch=0, ready=False).state

    # The gateway replays every observer after reconnect, including the pre-loss cue.
    for name, data in (("sub-text", "猫を見る"), ("sub-start", 1.0), ("sub-end", 3.0)):
        state = projection.observe(state, name, data).state

    assert state.cue.installed is None


def test_an_unnamed_observation_is_inert() -> None:
    projection = PlaybackProjection()
    state = installed(projection)

    projected = projection.observe(state, "", "anything")

    assert projected.state == state
    assert projected.deltas == ()


# --- gate: source, role, and render-space revisions are explicit and immutable -----------------


def test_revisions_are_immutable_values() -> None:
    revision = Revision(3)

    assert revision.advance() == Revision(4)
    assert revision == Revision(3)
    assert Revision(1) < Revision(2)


def test_a_render_space_observation_revises_the_render_space() -> None:
    projection = PlaybackProjection()
    state = PlaybackState()

    projected = projection.observe(state, "osd-dimensions", {"w": 1920, "h": 1080})

    assert projected.state.render_space.render_space == Revision(1)
    assert RenderSpaceChanged in kinds(projected.deltas)
    assert GeometryInputChanged in kinds(projected.deltas)


def test_a_source_replacement_revises_the_source_and_stales_the_authored_probe() -> None:
    projection = PlaybackProjection()

    projected = projection.source_replaced(PlaybackState(), "/media/next.mkv")

    assert kinds(projected.deltas) == [SourceChanged]
    assert projected.state.media == type(projected.state.media)(Revision(1), "/media/next.mkv")
    assert projected.state.cue.authored_stale is True


def test_cue_timing_is_a_geometry_input_but_not_a_render_space_revision() -> None:
    projection = PlaybackProjection()

    projected = projection.observe(PlaybackState(), "sub-start", 4.0)

    assert kinds(projected.deltas) == [GeometryInputChanged]
    assert projected.state.render_space.render_space == Revision()


def test_sub_delay_reports_timing_without_revising_the_render_space() -> None:
    projection = PlaybackProjection()

    projected = projection.observe(PlaybackState(), "sub-delay", 0.5)

    assert kinds(projected.deltas) == [SubtitleTimingChanged]
    assert projected.state.timing.delay == 0.5
    assert projected.state.render_space.render_space == Revision()


# --- gate: pointer and pause stay legacy-owned in production -----------------------------------


def test_pointer_and_pause_deltas_are_not_published_while_legacy_owns_them() -> None:
    projection = PlaybackProjection()

    pointer = projection.observe(PlaybackState(), "mouse-pos", {"x": 1, "y": 2})
    pause = projection.observe(PlaybackState(), "pause", data=True)

    assert pointer.deltas == ()
    assert pause.deltas == ()
    # The facts are still projected, so composition can read them.
    assert pointer.state.pointer.position == {"x": 1, "y": 2}
    assert pause.state.paused is True


def test_a_composition_projection_publishes_pointer_and_pause() -> None:
    projection = PlaybackProjection(legacy_owned=frozenset())

    pointer = projection.observe(PlaybackState(), "mouse-pos", {"x": 1, "y": 2})
    pause = projection.observe(PlaybackState(), "pause", data=True)

    assert kinds(pointer.deltas) == [PointerMoved]
    assert kinds(pause.deltas) == [PauseChanged]


def test_unknown_legacy_owned_domains_are_rejected() -> None:
    try:
        PlaybackProjection(legacy_owned=frozenset({"tooltip"}))  # type: ignore[arg-type]
    except ValueError as error:
        assert "legacy-owned" in str(error)
    else:  # pragma: no cover - the constructor must reject an unknown domain
        raise AssertionError("an unknown legacy-owned domain must be rejected")


def test_every_fact_domain_is_reachable_from_the_delta_vocabulary() -> None:
    from saitenka.runtime.playback import _DELTA_DOMAIN

    assert set(_DELTA_DOMAIN.values()) == set(FactDomain)

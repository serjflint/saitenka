"""WP4.3: the reducer picks plain vs styled; a provider only prepares the raster."""

from __future__ import annotations

import pytest
from session_builder import build_session
from util import FakeIPC

from saitenka.app.session.factory import SessionServices
from saitenka.app.subtitle_raster import (
    AnnotationOverlay,
    PillowRasterProvider,
    RasterContent,
    RasterStyle,
    SubtitleRasterRequest,
    annotation_visible,
    build_request,
    raster_style,
)


def token(surface: str = "猫"):
    from saitenka.app.tokenize import Token

    return Token(surface=surface, lemma=surface, reading="", pos="", start=0, end=len(surface))


BACKGROUND = (0, 0, 0, 128)


def request(**overrides: object) -> SubtitleRasterRequest:
    values: dict = {
        "style": RasterStyle.STYLED,
        "text": "猫を見る",
        "lines": [[token()]],
        "width": 1920,
        "size": 40,
        "annotated": True,
        "hover": -1,
        "hover_span": None,
        "styles": ["scored"],
        **overrides,
    }
    content = RasterContent(
        values["text"], values["lines"], values["width"], values["size"], BACKGROUND
    )
    overlay = AnnotationOverlay(
        values["annotated"], values["hover"], values["hover_span"], values["styles"]
    )
    return build_request(values["style"], content, overlay)


# --- the plain/styled decision -----------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    ["secondary_role", "upgrade_pending", "annotation_degraded"],
)
def test_a_cue_with_no_annotation_to_show_publishes_plain(reason: str) -> None:
    flags = dict.fromkeys(("secondary_role", "upgrade_pending", "annotation_degraded"), False)
    flags[reason] = True

    assert raster_style(**flags) is RasterStyle.PLAIN


def test_an_annotated_target_cue_publishes_styled() -> None:
    style = raster_style(secondary_role=False, upgrade_pending=False, annotation_degraded=False)

    assert style is RasterStyle.STYLED


def test_annotations_show_in_full_mode_or_while_hovering() -> None:
    assert annotation_visible(mode="full", hover_annotation=False)
    assert annotation_visible(mode="hover", hover_annotation=True)
    assert not annotation_visible(mode="hover", hover_annotation=False)


# --- request assembly --------------------------------------------------------------------------


def test_a_plain_request_carries_text_and_no_annotation_inputs() -> None:
    built = request(style=RasterStyle.PLAIN)

    assert built.text == "猫を見る"
    assert built.lines == ()
    assert (built.hover, built.hover_end, built.styles) == (None, None, None)


def test_an_unannotated_styled_request_drops_hover_and_styles() -> None:
    built = request(annotated=False, hover=2, styles=["scored"])

    assert (built.hover, built.hover_end, built.styles) == (None, None, None)


def test_a_hover_span_drives_the_underline_over_the_hovered_token() -> None:
    """A phrase span can start before the hovered token (a leading お in お休み)."""
    built = request(hover=3, hover_span=(2, 4))

    assert (built.hover, built.hover_end) == (2, 4)


def test_without_a_span_the_hovered_token_underlines() -> None:
    built = request(hover=3, hover_span=None)

    assert (built.hover, built.hover_end) == (3, None)


def test_no_hover_underlines_nothing() -> None:
    built = request(hover=-1, hover_span=None)

    assert built.hover is None


def test_a_request_is_immutable_and_holds_no_live_sequence() -> None:
    lines = [[token()]]
    built = request(lines=lines)
    lines.append([token("犬")])

    assert len(built.lines) == 1
    with pytest.raises(AttributeError):
        built.style = RasterStyle.PLAIN  # type: ignore[misc]


# --- provider neutrality -----------------------------------------------------------------------


def test_the_pillow_provider_satisfies_the_same_contract_as_a_fake() -> None:
    from util import RecordingRasterProvider

    built = request(style=RasterStyle.PLAIN)
    shipped = PillowRasterProvider().render(built)
    faked = RecordingRasterProvider().render(built)

    for result in (shipped, faked):
        assert result.image.width > 0
        assert isinstance(result.boxes, tuple)
    assert faked is not None


def test_the_pillow_provider_renders_both_styles() -> None:
    provider = PillowRasterProvider()

    plain = provider.render(request(style=RasterStyle.PLAIN))
    styled = provider.render(request(style=RasterStyle.STYLED, styles=None))

    assert plain.image.width > 0
    assert styled.image.width > 0


# --- the surface: what a SessionController publishes, under either provider -------------------------------
#
# Every test below is parametrized over both providers, which is WP4.3's neutrality gate stated the
# way it matters: not "each provider renders something" but "the same trace decides the same thing
# whichever one is installed". The recorder wraps Pillow rather than replacing it, so the shipping
# path is the one under assertion.


@pytest.fixture(params=["fake", "pillow"])
def recorder(request: pytest.FixtureRequest):
    from util import RecordingRasterProvider

    if request.param == "fake":
        return RecordingRasterProvider(size=(20, 10))
    return RecordingRasterProvider(delegate=PillowRasterProvider())


class _ExistsDS:
    """A dict set exposing only ``terms_exist`` — its presence is what makes a tokenization
    complete, and therefore what turns a plain cue into a styled one."""

    def terms_exist(self, _forms):
        return set()


class _CapturingAnnotationIPC(FakeIPC):
    def __init__(self) -> None:
        super().__init__()
        self.annotation_handler = None
        self.annotation_jobs = []

    def register_runtime_job_lane(self, name, policy, handler) -> bool:  # noqa: ARG002
        if name != "cue-annotation":
            return False
        self.annotation_handler = handler
        return True

    def submit_runtime_job(self, **kwargs) -> bool:
        if kwargs["lane"] != "cue-annotation":
            return False
        self.annotation_jobs.append(kwargs)
        return True


def _finish_captured_annotation(ipc) -> None:
    import threading

    from saitenka.runtime import EffectFinished, EffectId, EffectOutcome

    job = ipc.annotation_jobs.pop(0)
    result = ipc.annotation_handler(job["request"], threading.Event())
    job["on_finished"](
        EffectFinished(
            EffectId(1),
            job["owner"],
            job["identity"],
            EffectOutcome.SUCCEEDED,
            result=result,
        )
    )


def _reader(recorder, *, dict_set=None, annotation_async: bool = False):
    from saitenka.app.subtitle_render import SubtitleRenderer

    ipc = _CapturingAnnotationIPC() if annotation_async else FakeIPC()
    reader = build_session(ipc, services=SessionServices(dictionaries=dict_set))
    reader.turn.screen.osd = (1920, 1080)
    reader.turn.subtitle_presentation.renderer = SubtitleRenderer(recorder)
    if annotation_async:
        reader.turn.profile_integration.enable_async_annotation()
        reader.turn.profile_integration.dependencies_changed()
    return reader


def test_a_cue_publishes_plain_immediately_and_styled_onto_the_same_identity(recorder) -> None:
    """WP4.3: plain pixels publish at cue time and the later styled result replaces only the same
    identity. Drives the production path — a cue arriving before the dictionaries are ready, then
    the same cue once they land."""
    reader = _reader(recorder)

    reader.turn.cue_coordinator.set_subtitle("猫を見る")
    reader.turn.profile_session.profile.replace_dictionary_set(_ExistsDS())
    reader.turn.cue_coordinator.set_subtitle("猫を見る")

    assert recorder.styles == ["plain", "styled"]
    assert {published.text for published in recorder.requests} == {"猫を見る"}


def test_an_annotation_for_a_replaced_cue_never_restyles_the_current_one(recorder) -> None:
    """The other half of "only the same identity": a result that completes after its cue is gone.

    The identity guard lives in the annotation disposition and nowhere else — a second copy on the
    surface would be a second representation of the same fact, which is what invariant 13 forbids.
    So this asserts the guard from the surface's side rather than duplicating it there.
    """
    reader = _reader(recorder, dict_set=_ExistsDS(), annotation_async=True)
    reader.turn.cue_coordinator.set_subtitle("猫を見る")
    reader.turn.cue_coordinator.set_subtitle("犬を見る")
    published = len(recorder.requests)

    _finish_captured_annotation(reader.turn.ipc)
    reader.turn.interaction.settle()

    assert len(recorder.requests) == published
    assert reader.turn.playback_observation.cue.text == "犬を見る"


def test_a_closed_subtitle_surface_publishes_no_pixels_and_releases_its_provider(recorder) -> None:
    """The close participant. A cue that arrives after close — a late annotation publishing its
    upgrade — must not stage pixels onto a slot the close path has already emptied."""
    reader = _reader(recorder, dict_set=_ExistsDS())
    reader.turn.cue_coordinator.set_subtitle("猫を見る")
    published = len(recorder.requests)

    reader.turn.subtitle_presentation.pipeline.close()
    reader.turn.cue_coordinator.set_subtitle("犬を見る")

    assert len(recorder.requests) == published
    assert recorder.closed


def test_a_draw_onto_a_closed_surface_settles_its_caller_as_uncommitted(recorder) -> None:
    """Negative control for the quarantine: staging must learn the pixels never landed. Silently
    returning no transaction would leave legacy ownership waiting on an answer that never comes."""
    from saitenka.app.subtitle_render import SubtitleRenderer

    reader = _reader(recorder, dict_set=_ExistsDS())
    reader.turn.cue_coordinator.set_subtitle("猫を見る")
    renderer = reader.turn.subtitle_presentation.renderer
    assert isinstance(renderer, SubtitleRenderer)
    settled: list[bool] = []

    renderer.close()

    result = renderer.draw(
        reader.turn.cue_coordinator.draw_request(),
        reader.turn.lifecycle_surfaces,
        reader.turn.ipc,
        on_settled=settled.append,
    )
    transaction = None if result is None else result.transaction

    assert transaction is None
    assert settled == [False]

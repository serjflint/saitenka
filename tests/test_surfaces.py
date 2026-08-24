"""The ordered OSD-surface registry (saitenka.app.surfaces).

Two layers: the registry *contract* (short-circuit routing, no-op defaults, capture-from-``open``) tested
with synthetic specs, and a guard that the real ``SURFACES`` tuple keeps its documented z-order and that
every surface's state exposes the uniform ``open`` predicate (the anti-occlusion invariant — a surface
that forgets to declare shown-ness is exactly the #100 picker click-through bug).
"""

from __future__ import annotations

import pytest
import util

from saitenka.app import sidebar, surfaces
from saitenka.app.bindings import SCROLL_DOWN_MSG
from saitenka.app.controller import Reader
from saitenka.app.subselect import SubtitleCandidate
from saitenka.app.surfaces import SurfaceSpec
from saitenka.runtime import UserCommand, events
from saitenka.runtime import sidebar as runtime_sidebar


class _FakeState:
    def __init__(self, *, open: bool = False):  # noqa: A002  # matches the SurfaceState.open contract
        self.open = open


class _FakeIPC(util.FakeIPC):
    def __init__(self, **props):
        super().__init__()
        self.props.update({"osd-dimensions": {"w": 1920, "h": 1080}, **props})


def _spec(name: str, *, claims: set[str], calls: list[str], open: bool = False) -> SurfaceSpec:  # noqa: A002
    """A synthetic surface that records each chain call and claims the events named in ``claims``."""
    state = _FakeState(open=open)

    def _mk(chan: str):
        def _fn(_reader, *_args):
            calls.append(f"{name}.{chan}")
            return chan in claims

        return _fn

    return SurfaceSpec(
        name,
        state_of=lambda _r: state,
        suppress_hover=_mk("suppress_hover"),
        scroll=_mk("scroll"),
        on_click=_mk("on_click"),
    )


def test_default_predicates_are_no_ops():
    spec = SurfaceSpec("bare", state_of=lambda _r: _FakeState(open=True))
    assert spec.captures(None) is True  # derived from state.open
    assert spec.suppress_hover(None) is False
    assert spec.scroll(None, 1) is False
    assert spec.on_click(None, 0, 0) is False


def test_route_click_stops_at_the_first_claiming_surface(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        surfaces,
        "SURFACES",
        (
            _spec("top", claims=set(), calls=calls),
            _spec("mid", claims={"on_click"}, calls=calls),
            _spec("bot", claims={"on_click"}, calls=calls),
        ),
    )

    assert surfaces.route_click(object(), 5, 5) is True
    assert calls == ["top.on_click", "mid.on_click"]  # stops at mid; bot never consulted


def test_route_scroll_falls_through_to_the_terminal_surface(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        surfaces,
        "SURFACES",
        (_spec("a", claims=set(), calls=calls), _spec("terminal", claims={"scroll"}, calls=calls)),
    )

    assert surfaces.route_scroll(object(), 1) is True
    assert calls == ["a.scroll", "terminal.scroll"]


def test_unclaimed_scroll_returns_false(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(surfaces, "SURFACES", (_spec("a", claims=set(), calls=calls),))
    assert surfaces.route_scroll(object(), 1) is False


def test_wants_mouse_capture_is_any_open(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        surfaces,
        "SURFACES",
        (
            _spec("a", claims=set(), calls=calls, open=False),
            _spec("b", claims=set(), calls=calls, open=True),
        ),
    )
    assert surfaces.wants_mouse_capture(object()) is True


def test_real_registry_z_order():
    """Topmost-first, and the exact set — a reordering or a dropped surface is a deliberate re-bless."""
    assert [s.name for s in surfaces.SURFACES] == [
        "help",
        "sub_picker",
        "sidebar",
        "preview",
        "tooltip",
    ]


@pytest.mark.parametrize("spec", surfaces.SURFACES, ids=lambda s: s.name)
def test_every_surface_state_exposes_open(spec):
    """Anti-occlusion invariant: each surface's state object exposes ``open`` (bool) on a real Reader, so
    it participates in the forced-mouse-section OR and can never be shown-but-click-through (#100 picker)."""
    reader = Reader(_FakeIPC())
    assert isinstance(spec.captures(reader), bool)


def test_scroll_command_routes_to_open_help(monkeypatch):
    from saitenka.runtime.help import HelpCommand

    reader = Reader(_FakeIPC())
    reader._help_store.dispatch(
        HelpCommand.TOGGLE
    )  # the slice owns "open"; nothing else may set it
    steps: list[int] = []
    # The help surface routes the wheel into the help *command*; which page that is is the slice's.
    monkeypatch.setattr(reader, "_run_help_command", lambda command: steps.append(command))

    reader._handle(UserCommand(SCROLL_DOWN_MSG))

    assert steps == [HelpCommand.NEXT]


def test_scroll_command_routes_to_open_picker(monkeypatch):
    from saitenka.app.sub_picker import ListingResult
    from saitenka.runtime import events, picker

    reader = Reader(_FakeIPC(**{"mouse-pos": {"x": 10, "y": 10}}))
    candidate = SubtitleCandidate(
        "provider", "name", 1, match=False, download=lambda: ("path", "ok")
    )
    reader._picker_store.dispatch(events.PickerOpened())
    reader._picker_store.dispatch(
        events.PickerListed(reader.sub_picker.generation, ListingResult((candidate,) * 20, ()))
    )
    reader.interaction.picker_panel.rect = (0, 0, 100, 100)
    monkeypatch.setattr(reader, "redraw_sub_picker", lambda: None)

    reader._handle(UserCommand(SCROLL_DOWN_MSG))

    assert reader.sub_picker.scroll == picker.ROWS_PER_WHEEL_STEP


def test_scroll_command_routes_to_open_sidebar(monkeypatch):
    reader = Reader(_FakeIPC(**{"mouse-pos": {"x": 10, "y": 10}}))
    reader._sidebar_store.dispatch(events.SidebarShown(active=0, capacity=10))
    reader.interaction.sidebar_panel.rect = (0, 0, 100, 100)
    reader.interaction.sidebar_panel.total = 100
    monkeypatch.setattr(sidebar, "draw", lambda _view: None)

    reader._handle(UserCommand(SCROLL_DOWN_MSG))

    assert reader.sidebar.scroll == runtime_sidebar.ROWS_PER_WHEEL_STEP


def test_the_registry_reads_shown_ness_without_a_reader() -> None:
    """Occlusion is answerable from the INTERACTION context alone — no host anywhere in the chain.

    `state_of` used to take the whole `Reader` to return one field, and because `SurfaceSpec` is one
    shared signature, every accessor plus `captures` and `wants_mouse_capture` was a host-taking row
    for it. Gathering the five surface states onto `InteractionContext` converted all of them at once.
    Constructing the context directly is the proof: if any hook still reached past it, this cannot run.
    """
    from saitenka.app.card_preview import PreviewPanel
    from saitenka.app.popups import TooltipState
    from saitenka.app.reader_context import InteractionContext
    from saitenka.app.sidebar import SidebarPanel
    from saitenka.app.sub_picker import PickerPanel
    from saitenka.app.surfaces import wants_mouse_capture
    from saitenka.runtime.interaction_slice import (
        HelpStore,
        PickerStore,
        PreviewStore,
        SidebarStore,
    )
    from saitenka.runtime.jobs import NoSessionRuntime

    class TooltipOwner:
        def __init__(self) -> None:
            self.state = TooltipState()

    interaction = InteractionContext()
    # Two of the five are slice features now, so the context is given their stores rather than
    # their states — and `NoSessionRuntime` is how a transport-less stand-in says "no reactor here".
    interaction.help_store = HelpStore(NoSessionRuntime())
    interaction.picker_store = PickerStore(NoSessionRuntime())
    interaction.picker_panel = PickerPanel()
    interaction.sidebar_store = SidebarStore(NoSessionRuntime())
    interaction.sidebar_panel = SidebarPanel()
    interaction.preview_store = PreviewStore(NoSessionRuntime())
    interaction.preview_panel = PreviewPanel()
    interaction.tooltip = TooltipOwner()

    assert wants_mouse_capture(interaction) is False

    interaction.sidebar_store.dispatch(events.SidebarShown(active=0, capacity=10))
    assert wants_mouse_capture(interaction) is True

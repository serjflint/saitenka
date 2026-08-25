"""The installed OSD-surface router (saitenka.app.session.surfaces).

Two layers: the registry *contract* (short-circuit routing, no-op defaults, capture-from-``open``) tested
with synthetic specs, and a guard that the production router keeps its documented z-order and that
every surface's state exposes the uniform ``open`` predicate (the anti-occlusion invariant — a surface
that forgets to declare shown-ness is exactly the #100 picker click-through bug).
"""

from __future__ import annotations

import pytest
import util

from saitenka.app.bindings import SCROLL_DOWN_MSG
from saitenka.app.features.sidebar import sidebar
from saitenka.app.session.controller import SessionController
from saitenka.app.session.surfaces import SurfaceRouter, SurfaceSpec
from saitenka.app.subselect import SubtitleCandidate
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
        def _fn(*_args):
            calls.append(f"{name}.{chan}")
            return chan in claims

        return _fn

    return SurfaceSpec(
        name,
        state_of=lambda: state,
        suppress_hover=_mk("suppress_hover"),
        scroll=_mk("scroll"),
        on_click=_mk("on_click"),
    )


def _router(*specs: SurfaceSpec) -> SurfaceRouter:
    return SurfaceRouter(specs, order=tuple(spec.name for spec in specs))


def test_default_predicates_are_no_ops():
    spec = SurfaceSpec("bare", state_of=lambda: _FakeState(open=True))
    assert spec.captures() is True
    assert spec.suppress_hover(None) is False
    assert spec.scroll(None, 1) is False
    assert spec.on_click(None, 0, 0) is False


def test_route_click_stops_at_the_first_claiming_surface():
    calls: list[str] = []
    router = _router(
        _spec("top", claims=set(), calls=calls),
        _spec("mid", claims={"on_click"}, calls=calls),
        _spec("bot", claims={"on_click"}, calls=calls),
    )

    assert router.route_click(object(), 5, 5) is True
    assert calls == ["top.on_click", "mid.on_click"]  # stops at mid; bot never consulted


def test_route_scroll_falls_through_to_the_terminal_surface():
    calls: list[str] = []
    router = _router(
        _spec("a", claims=set(), calls=calls),
        _spec("terminal", claims={"scroll"}, calls=calls),
    )

    assert router.route_scroll(object(), 1) is True
    assert calls == ["a.scroll", "terminal.scroll"]


def test_unclaimed_scroll_returns_false():
    calls: list[str] = []
    router = _router(_spec("a", claims=set(), calls=calls))
    assert router.route_scroll(object(), 1) is False


def test_wants_mouse_capture_is_any_open():
    calls: list[str] = []
    router = _router(
        _spec("a", claims=set(), calls=calls, open=False),
        _spec("b", claims=set(), calls=calls, open=True),
    )
    assert router.wants_mouse_capture() is True


def test_real_registry_z_order():
    """Topmost-first, and the exact set — a reordering or a dropped surface is a deliberate re-bless."""
    reader = SessionController(_FakeIPC())
    assert [s.name for s in reader.surface_router.specs] == [
        "help",
        "sub_picker",
        "sidebar",
        "preview",
        "tooltip",
    ]


def test_every_surface_state_exposes_open():
    """Anti-occlusion invariant: each surface's state object exposes ``open`` (bool) on a real SessionController, so
    it participates in the forced-mouse-section OR and can never be shown-but-click-through (#100 picker)."""
    reader = SessionController(_FakeIPC())
    assert all(isinstance(spec.captures(), bool) for spec in reader.surface_router.specs)


def test_surface_router_rejects_duplicate_names():
    spec = _spec("same", claims=set(), calls=[])
    with pytest.raises(ValueError, match="unique"):
        SurfaceRouter((spec, spec), order=("same", "same"))


def test_surface_router_rejects_implicit_order():
    top = _spec("top", claims=set(), calls=[])
    bottom = _spec("bottom", claims=set(), calls=[])
    with pytest.raises(ValueError, match="order mismatch"):
        SurfaceRouter((bottom, top), order=("top", "bottom"))


def test_scroll_command_routes_to_open_help():
    from saitenka.runtime.help import HelpCommand

    reader = SessionController(_FakeIPC())
    reader.osd = (480, 220)
    reader.help_controller.store.dispatch(
        HelpCommand.TOGGLE
    )  # the slice owns "open"; nothing else may set it

    reader._handle(UserCommand(SCROLL_DOWN_MSG))

    assert reader.help.page == 1


def test_scroll_command_routes_to_open_picker(monkeypatch):
    from saitenka.app.features.picker.sub_picker import ListingResult
    from saitenka.runtime import events, picker

    reader = SessionController(_FakeIPC(**{"mouse-pos": {"x": 10, "y": 10}}))
    candidate = SubtitleCandidate(
        "provider", "name", 1, match=False, download=lambda: ("path", "ok")
    )
    reader.picker_controller.store.dispatch(events.PickerOpened())
    reader.picker_controller.store.dispatch(
        events.PickerListed(reader.sub_picker.generation, ListingResult((candidate,) * 20, ()))
    )
    reader.picker_controller.panel.rect = (0, 0, 100, 100)
    monkeypatch.setattr(reader.picker_controller, "redraw", lambda: None)

    reader._handle(UserCommand(SCROLL_DOWN_MSG))

    assert reader.sub_picker.scroll == picker.ROWS_PER_WHEEL_STEP


def test_scroll_command_routes_to_open_sidebar(monkeypatch):
    reader = SessionController(_FakeIPC(**{"mouse-pos": {"x": 10, "y": 10}}))
    reader.sidebar_controller.store.dispatch(events.SidebarShown(active=0, capacity=10))
    reader.sidebar_controller.panel.rect = (0, 0, 100, 100)
    reader.sidebar_controller.panel.total = 100
    monkeypatch.setattr(sidebar, "draw", lambda _view: None)

    reader._handle(UserCommand(SCROLL_DOWN_MSG))

    assert reader.sidebar.scroll == runtime_sidebar.ROWS_PER_WHEEL_STEP


def test_the_registry_reads_shown_ness_from_feature_owners() -> None:
    reader = SessionController(_FakeIPC())
    router = reader.surface_router
    assert router.wants_mouse_capture() is False

    reader.sidebar_controller.store.dispatch(events.SidebarShown(active=0, capacity=10))
    assert router.wants_mouse_capture() is True

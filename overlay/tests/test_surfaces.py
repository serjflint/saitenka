"""The ordered OSD-surface registry (overlay.app.surfaces).

Two layers: the registry *contract* (short-circuit routing, no-op defaults, capture-from-``open``) tested
with synthetic specs, and a guard that the real ``SURFACES`` tuple keeps its documented z-order and that
every surface's state exposes the uniform ``open`` predicate (the anti-occlusion invariant — a surface
that forgets to declare shown-ness is exactly the #100 picker click-through bug).
"""

from __future__ import annotations

import pytest

from overlay.app import surfaces
from overlay.app.controller import Reader
from overlay.app.surfaces import SurfaceSpec


class _FakeState:
    def __init__(self, *, open: bool = False):  # noqa: A002  # matches the SurfaceState.open contract
        self.open = open


class _FakeIPC:
    def command(self, *args):
        if args[0] == "get_property":
            return {"data": {"osd-dimensions": {"w": 1920, "h": 1080}}.get(args[1])}
        return {"error": "success"}


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

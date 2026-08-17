"""Overlay-draw telemetry: every ``Overlay.show``/``show_bgra`` tags its ``upload`` span with the
overlay's logical id and its actual on-screen geometry.

This is the drift-proof coverage seam: the ``OverlayId`` enum is the registry of overlay events, and
every draw flows through the ONE ``Overlay.show``/``show_bgra`` chokepoint, so instrumenting it once
covers every current and future saitenka. The recorded ``w``/``h`` are the ACTUAL uploaded pixels, so
they encode the effective ``ui_scale`` — the metamorphic test below is the oracle that a chrome overlay
silently reverting to scale 1.0 (the help/stats/sidebar regression) would have tripped.
"""

from __future__ import annotations

import contextlib
import threading

import numpy as np
import pytest
from PIL import Image

from saitenka import otel_metrics
from saitenka.app.overlay_ids import OverlayId
from saitenka.mpvio.ipc import MpvIPC
from saitenka.mpvio.osd import Overlay


class _FakeIPC:
    def command(self, *_args):
        return {"error": "success"}


def _record_spans(monkeypatch) -> list[dict]:
    """Capture every ``traced(...)`` span (name + static attrs + in-block ``.set`` attrs) without
    standing up an OTel provider — ``instrumented`` composes ``traced``, so this sees the real path."""
    spans: list[dict] = []

    @contextlib.contextmanager
    def _fake_traced(name, **attrs):
        rec = {"name": name, "attrs": dict(attrs)}
        spans.append(rec)

        class _Setter:
            def set(self, key, value):
                rec["attrs"][key] = value

        yield _Setter()

    monkeypatch.setattr(otel_metrics, "traced", _fake_traced)
    return spans


def _uploads(spans: list[dict]) -> list[dict]:
    return [s["attrs"] for s in spans if s["name"] == "upload"]


def test_show_tags_the_upload_span_with_oid_and_geometry(monkeypatch):
    spans = _record_spans(monkeypatch)
    ov = Overlay(_FakeIPC())
    ov.show(Image.new("RGBA", (300, 200), (0, 0, 0, 0)), x=10, y=20, oid=OverlayId.HELP)
    (attrs,) = _uploads(spans)
    assert attrs["oid"] == "HELP"
    assert (attrs["w"], attrs["h"]) == (300, 200)
    assert (attrs["x"], attrs["y"]) == (10, 20)


def test_show_bgra_tags_the_upload_span_with_oid_and_geometry(monkeypatch):
    spans = _record_spans(monkeypatch)
    ov = Overlay(_FakeIPC())
    ov.show_bgra(np.zeros((120, 340, 4), dtype=np.uint8), x=1, y=2, oid=OverlayId.SIDEBAR)
    (attrs,) = _uploads(spans)
    assert attrs["oid"] == "SIDEBAR"
    assert (attrs["w"], attrs["h"]) == (340, 120)  # numpy is (H, W, 4)


def test_every_overlay_id_is_labelled_by_the_seam(monkeypatch):
    """The registry (OverlayId) is the SSOT of overlay events; each one drawn through the seam must
    surface under its own name, so adding a slot needs no new instrumentation and can't go dark."""
    spans = _record_spans(monkeypatch)
    ov = Overlay(_FakeIPC())
    img = Image.new("RGBA", (64, 48), (0, 0, 0, 0))
    for member in OverlayId:
        ov.show(img, oid=member)
    labels = [a["oid"] for a in _uploads(spans)]
    assert labels == [m.name for m in OverlayId]  # every slot, in order, under its own name


def test_geometry_tracks_ui_scale(monkeypatch):
    """The regression oracle: the help overlay built at ui_scale 1.5 uploads visibly larger than at 1.0,
    so a silent revert to scale 1.0 shows up directly as too-small ``w``/``h`` in the trace."""
    from saitenka.render.help import HelpEntry, build_document, render_page

    entries = (HelpEntry("Nav", "j", "down", None, "x"),)

    def _draw(scale: float) -> dict:
        spans = _record_spans(monkeypatch)
        doc = build_document(entries, osd=(1920, 1080), footer="f", scale=scale)
        page = render_page(
            doc.pages[0], width=doc.width, height=doc.height, index=0, total=1, scale=scale
        )
        Overlay(_FakeIPC()).show(page, oid=OverlayId.HELP)
        (attrs,) = _uploads(spans)
        return attrs

    small, big = _draw(1.0), _draw(1.5)
    assert (
        big["w"] > small["w"] and big["h"] > small["h"]
    )  # scale is visible in the uploaded pixels


def test_a_bare_int_oid_falls_back_to_its_digits(monkeypatch):
    """A caller that passes a raw int (not an OverlayId) still gets a stable label, never a crash."""
    spans = _record_spans(monkeypatch)
    Overlay(_FakeIPC()).show(Image.new("RGBA", (10, 10)), oid=7)
    assert _uploads(spans)[0]["oid"] == "7"


@pytest.mark.timeout(5)
def test_interaction_presenter_keeps_submission_nonblocking_and_publishes_newest(monkeypatch):
    ipc = MpvIPC("unused")
    ov = Overlay(ipc)
    entered = threading.Event()
    release = threading.Event()
    newest_painted = threading.Event()
    calls: list[int] = []

    def blocked_show(bgra, _x=0, _y=0, oid=0):
        del oid
        value = int(bgra[0, 0, 0])
        calls.append(value)
        if value == 1:
            entered.set()
            release.wait(2)
        else:
            newest_painted.set()
        return {"error": "success"}

    monkeypatch.setattr(ov, "show_bgra", blocked_show)
    try:
        ov.show_bgra_interactive(np.full((1, 1, 4), 1, dtype=np.uint8), oid=OverlayId.TIP)
        assert entered.wait(1)

        submitted = threading.Event()

        def submit_newest() -> None:
            ov.show_bgra_interactive(np.full((1, 1, 4), 2, dtype=np.uint8), oid=OverlayId.TIP)
            submitted.set()

        submitter = threading.Thread(target=submit_newest)
        submitter.start()
        assert submitted.wait(1)
        release.set()
        assert newest_painted.wait(1)
        submitter.join(1)
        assert calls == [1, 2]
    finally:
        release.set()
        ov.close()
        ipc.close()


@pytest.mark.timeout(5)
def test_interaction_presenter_survives_a_failed_paint(monkeypatch):
    ipc = MpvIPC("unused")
    ov = Overlay(ipc)
    failed = threading.Event()
    recovered = threading.Event()

    def flaky_show(bgra, _x=0, _y=0, oid=0):
        del oid
        if int(bgra[0, 0, 0]) == 1:
            raise OSError("paint failed")
        recovered.set()
        return {"error": "success"}

    monkeypatch.setattr(ov, "show_bgra", flaky_show)
    try:
        ov.show_bgra_interactive(
            np.full((1, 1, 4), 1, dtype=np.uint8),
            oid=OverlayId.TIP,
            on_presented=lambda result: failed.set() if result["error"] == "failed" else None,
        )
        assert failed.wait(1)
        ov.show_bgra_interactive(np.full((1, 1, 4), 2, dtype=np.uint8), oid=OverlayId.TIP)
        assert recovered.wait(1)
    finally:
        ov.close()
        ipc.close()


@pytest.mark.timeout(5)
def test_visibility_off_removes_an_inflight_interaction_paint(monkeypatch):
    ipc = MpvIPC("unused")
    ov = Overlay(ipc)
    entered = threading.Event()
    release = threading.Event()
    removed = threading.Event()
    commands: list[tuple] = []

    def blocked_show(_bgra, _x=0, _y=0, oid=0):
        commands.append(("overlay-add", oid))
        entered.set()
        release.wait(2)
        return {"error": "success"}

    def command(*args, **_kwargs):
        commands.append(args)
        if args[0] == "overlay-remove":
            removed.set()
        return {"error": "success"}

    monkeypatch.setattr(ov, "show_bgra", blocked_show)
    monkeypatch.setattr(ipc, "command", command)
    try:
        ov.show_bgra_interactive(np.zeros((1, 1, 4), dtype=np.uint8), oid=OverlayId.TIP)
        assert entered.wait(1)
        ov.set_visible(visible=False)
        release.set()
        assert removed.wait(1)
        assert commands[-1][0] == "overlay-remove"
    finally:
        release.set()
        ov.close()
        ipc.close()

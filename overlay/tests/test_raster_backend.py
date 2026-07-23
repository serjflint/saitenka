"""Stage 8c: the cosmic-text seam — the RasterBackend protocol.

Layering enforcement (PIL-agnostic core, GPL chokepoint, import cycles) moved to the
``.importlinter`` dependency-contract engine (``uv run poe arch``) — see
``overlay/.importlinter`` and ``AGENTS.md``. This file keeps the behavioral tests: hit geometry
(ScanBox/LinkBox) is produced by LAYOUT, not by the raster backend, and the raster backends must
reproduce identical bytes.
"""

from pathlib import Path

import numpy as np

SRC = Path(__file__).resolve().parent.parent / "src" / "overlay"


def test_raster_backend_protocol_shape():
    from overlay.raster.protocol import RasterBackend, RasterResult

    assert hasattr(RasterBackend, "raster_rows")
    # RasterResult carries premultiplied BGRA + height + layout-produced hit geometry
    fields = set(RasterResult.__dataclass_fields__)
    assert {"bgra", "height", "scan_boxes", "link_boxes"} <= fields


def test_pillow_backend_matches_lazy_panel_bytes():
    """PillowBackend.raster_rows must be byte-identical to the existing LazyPanel full render +
    to_bgra_array — the canonical interchange a future cosmic-text backend must reproduce."""
    from overlay.mpvio.osd import to_bgra_array
    from overlay.panel import Definition, Entry, LazyPanel, panel_rows
    from overlay.raster.pillow_backend import PillowBackend

    e = Entry(
        headword=["本命", {"tag": "rt", "content": "ほんめい"}],
        defs=[
            Definition("MonoC", ["追いかけること。また、その人。"]),
            Definition("JMdict", ["favourite; front runner"]),
        ],
    )
    width = 384
    rows = panel_rows(e, width)
    res = PillowBackend().raster_rows(panel_rows(e, width), width)

    lp = LazyPanel(rows, width)
    want = to_bgra_array(lp.finish())
    assert res.height == want.shape[0]
    assert (res.bgra == want).all()  # premultiplied BGRA, byte-identical


def test_hit_geometry_is_produced_by_layout_not_raster():
    """ScanBox/LinkBox come from the LAYOUT pass (model.py types, PIL-free) and the backend must
    return exactly what layout computed — a raster swap cannot change hit geometry."""
    import overlay.model as model
    from overlay.panel import Definition, Entry, LazyPanel, panel_rows
    from overlay.raster.pillow_backend import PillowBackend

    body = ["同義語は", {"tag": "a", "href": "?query=見る", "content": "見る"}, "。"]
    e = Entry(headword=["観る"], defs=[Definition("MonoA", body)])
    width = 384
    res = PillowBackend().raster_rows(panel_rows(e, width), width)
    lp = LazyPanel(panel_rows(e, width), width)
    lp.finish()
    assert res.scan_boxes == lp.scan_boxes
    assert res.link_boxes == lp.link_boxes
    assert res.link_boxes and isinstance(res.link_boxes[0], model.LinkBox)
    assert res.scan_boxes and isinstance(res.scan_boxes[0], model.ScanBox)


def test_rust_backend_reserved():
    """rust/ is reserved for the future PyO3 cosmic-text backend; its README must pin the
    free-threading requirement so the GIL stays off."""
    readme = SRC.parent.parent / "rust" / "README.md"
    assert readme.exists(), "rust/README.md missing"
    text = readme.read_text()
    assert "cosmic-text" in text
    assert "free-threading" in text or "free-threaded" in text


def _unused(x):  # keep numpy imported for future byte assertions without ruff noise
    return np.asarray(x)

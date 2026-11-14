"""Foreground reads consume prepared pixels; preparation belongs to workers."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import numpy as np
import pytest
from PIL import Image

from saitenka import bgra
from saitenka.model import Theme
from saitenka.panel import Row
from saitenka.render.banded import BandedTuning, PackedPixels, WindowedPanel
from saitenka.render.window import LazyOffsets


def _solid(*_args, scale=1.0, **_kwargs):
    return Image.new("RGBA", (round(64 * scale), round(600 * scale)), (10, 20, 30, 255)), [], []


def _forbidden(*_args, **_kwargs):
    raise AssertionError("foreground read performed preparation")


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_unknown_layout_is_a_readiness_miss_without_preparation(scale):
    panel = WindowedPanel([Row(0, _forbidden)], 64)

    ready = panel.viewport_warm(0, 100) if scale == 1 else panel.native_viewport_warm(0, 100, scale)

    assert not ready


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_unknown_layout_read_returns_background_without_preparation(scale):
    panel = WindowedPanel([Row(0, _forbidden)], 64)
    background = WindowedPanel([], 64).viewport_bgra(0, 100, scale=scale, warm_only=True)

    actual = panel.viewport_bgra(0, 100, overscan=500, scale=scale, warm_only=True)

    assert panel.missed_last_assemble
    assert np.array_equal(actual, background)


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_worker_warm_leaves_no_foreground_conversion_or_inflation(monkeypatch, scale):
    panel = WindowedPanel([Row(0, _solid)], 64, tuning=BandedTuning(compress=True))
    panel.warm_viewport(0, 100)
    if scale != 1:
        panel.warm_native_viewport(0, 100, scale)
    expected = WindowedPanel([Row(0, _solid)], 64).viewport_bgra(0, 100, scale=scale)
    monkeypatch.setattr(bgra, "to_bgra_array", _forbidden)
    monkeypatch.setattr(PackedPixels, "inflate", _forbidden)
    monkeypatch.setattr(LazyOffsets, "estimated_table", _forbidden)

    actual = panel.viewport_bgra(0, 100, overscan=500, scale=scale, warm_only=True)

    assert not panel.missed_last_assemble
    assert np.array_equal(actual, expected)


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_offscreen_unknown_row_cannot_be_prepared_by_overscan(scale):
    panel = WindowedPanel([Row(0, _solid), Row(0, _forbidden)], 64)
    panel.warm_viewport(0, 100)
    if scale != 1:
        panel.warm_native_viewport(0, 100, scale)
    expected = panel.viewport_bgra(0, 100, scale=scale, warm_only=True)

    actual = panel.viewport_bgra(0, 100, overscan=1000, scale=scale, warm_only=True)

    assert not panel.missed_last_assemble
    assert np.array_equal(actual, expected)


def test_installed_head_readiness_is_independent_of_overscan():
    expected = WindowedPanel([Row(0, _solid)], 64).viewport_bgra(0, 100)
    panel = WindowedPanel([Row(0, _forbidden)], 64)
    panel.install_first_view(100, 0, expected)

    actual = panel.viewport_bgra(0, 100, overscan=100, warm_only=True)

    assert panel.viewport_warm(0, 100)
    assert not panel.missed_last_assemble
    assert np.array_equal(actual, expected)
    actual[:] = 0
    assert np.array_equal(panel.viewport_bgra(0, 100, warm_only=True), expected)


@pytest.mark.timeout(5)
@pytest.mark.parametrize("operation", ["measure", "precompose", "convert", "pack"])
def test_stalled_head_preparation_cannot_block_a_ready_read(monkeypatch, operation):
    entered, release = Event(), Event()

    def second(*args, **kwargs):
        if operation in {"measure", "precompose"}:
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test worker was not released")
        return _solid(*args, **kwargs)

    panel = WindowedPanel([Row(0, _solid), Row(0, second)], 64, tuning=BandedTuning(compress=True))
    panel.warm_viewport(0, 100)
    expected = panel.viewport_bgra(0, 100, warm_only=True)
    if operation in {"convert", "pack"}:
        owner, name = (bgra, "to_bgra_array") if operation == "convert" else (PackedPixels, "pack")
        original = getattr(owner, name)

        def stalled(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test worker was not released")
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, stalled)
    with ThreadPoolExecutor(max_workers=2) as pool:
        if operation == "precompose":
            worker = pool.submit(panel.precompose, 1000)
        elif operation == "measure":
            worker = pool.submit(panel.measure_to, 750)
        else:
            worker = pool.submit(panel.warm_viewport, 650, 100)
        try:
            assert entered.wait(1)
            actual = pool.submit(panel.viewport_bgra, 0, 100, warm_only=True).result(timeout=1)
            assert np.array_equal(actual, expected)
        finally:
            release.set()
        worker.result(timeout=1)


@pytest.mark.timeout(5)
def test_a_pinned_frame_survives_eviction_before_display_copy(monkeypatch):
    from saitenka.render import banded

    panel = WindowedPanel([Row(0, _solid) for _ in range(6)], 64)
    panel.warm_viewport(0, 100)
    expected = panel.viewport_bgra(0, 100, warm_only=True)
    entered, release = Event(), Event()
    compose = banded._ViewportSnapshot.compose

    def stalled(snapshot):
        entered.set()
        if not release.wait(3):
            raise TimeoutError("display copy was not released")
        return compose(snapshot)

    monkeypatch.setattr(banded._ViewportSnapshot, "compose", stalled)
    with ThreadPoolExecutor(max_workers=1) as pool:
        frame = pool.submit(panel.viewport_bgra, 0, 100, warm_only=True)
        try:
            assert entered.wait(1)
            panel.warm_viewport(3000, 100)
            assert not panel.viewport_warm(0, 100)
        finally:
            release.set()
        actual = frame.result(timeout=1)
    assert np.array_equal(actual, expected)


@pytest.mark.parametrize("scale", [1.5, 2.0])
def test_rgba_only_native_band_is_a_readiness_miss(monkeypatch, scale):
    def render_window(y0, y1, *, scale=1.0):
        return (
            Image.new("RGBA", (round(64 * scale), round((y1 - y0) * scale)), (10, 20, 30, 255)),
            [],
            [],
        )

    row = Row(0, _solid, measure=lambda: 4096, render_window=render_window)
    panel = WindowedPanel([row], 64)
    panel.viewport_bgra(0, 100, overscan=1000, scale=scale)
    background = WindowedPanel([], 64).viewport_bgra(600, 100, scale=scale, warm_only=True)
    monkeypatch.setattr(bgra, "to_bgra_array", _forbidden)

    assert not panel.native_viewport_warm(600, 100, scale)
    actual = panel.viewport_bgra(600, 100, scale=scale, warm_only=True)

    assert panel.missed_last_assemble
    assert np.array_equal(actual, background)


@pytest.mark.parametrize("scale", [1.5, 2.0])
def test_retained_bytes_include_both_pixel_representations_at_each_scale(scale):
    panel = WindowedPanel([Row(0, _solid)], 64)
    panel.warm_viewport(0, 100)
    panel.warm_native_viewport(0, 100, scale)

    reference_bytes = 64 * 600 * 4
    native_bytes = round(64 * scale) * round(600 * scale) * 4
    assert panel.retained_nbytes == 2 * (reference_bytes + native_bytes)


@pytest.mark.parametrize("scale", [0.65, 1.0, 1.3, 1.5, 1.7, 2.0])
@pytest.mark.parametrize("scroll", [156, 412, 512, 255, 256, 257, 1791, 1792, 1793, 1948, 2048])
def test_worker_warm_matches_cold_at_fractional_scale_band_edges(scale, scroll):
    def window(y0, y1, *, scale=1.0):
        image = Image.new(
            "RGBA", (round(64 * scale), round((y1 - y0) * scale)), (y0 // 256, 20, 30, 255)
        )
        return image, [], []

    row = Row(0, _solid, measure=lambda: 4096, render_window=window)
    theme = Theme()
    scroll += theme.margin
    panel, reference = WindowedPanel([row], 64, theme), WindowedPanel([row], 64, theme)
    panel.warm_viewport(scroll, 100)
    if scale != 1:
        panel.warm_native_viewport(scroll, 100, scale)

    actual = panel.viewport_bgra(scroll, 100, overscan=100, scale=scale, warm_only=True)
    expected = reference.viewport_bgra(scroll, 100, scale=scale)

    assert not panel.missed_last_assemble
    assert np.array_equal(actual, expected)

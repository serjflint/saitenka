from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest
from saitenka_subtitles import (
    FontProvider,
    FontSetup,
    GeometryPaletteEntry,
    GeometryRequest,
    SubtitleEventId,
    SubtitleFrameId,
    SubtitleTrackId,
)
from saitenka_subtitles.geometry import MAX_GEOMETRY_TOKENS
from saitenka_subtitles.libass_backend import LibassGeometryBackend, extract_token_geometry


@dataclass(frozen=True)
class Layer:
    width: int
    height: int
    bitmap: bytes
    color: int
    dst_x: int
    dst_y: int
    image_type: int = 0


@dataclass(frozen=True)
class Result:
    layers: tuple[Layer, ...]


def request(
    *,
    ass: bytes = b"ass",
    generation: int = 0,
    margins: tuple[int, int, int, int] = (0, 0, 0, 0),
    use_margins: bool = False,
    render_profile: tuple[tuple[str, str], ...] = (),
    font_setup: FontSetup | None = None,
    palette_size: int = 2,
    keep_coverage: bool = False,
) -> GeometryRequest:
    track = SubtitleTrackId("track")
    event = SubtitleEventId(track, 1_000, 2_000, 0, 0)
    return GeometryRequest(
        generation,
        track,
        SubtitleFrameId(track, (event,)),
        1_250,
        (1280, 720),
        (1280, 720),
        ass,
        margins=margins,
        use_margins=use_margins,
        palette=(
            GeometryPaletteEntry(event, 0, 0x010203),
            GeometryPaletteEntry(event, 1, 0x040506),
        )[:palette_size],
        reserved_rgb=(0xFFFFFF,),
        attachments=(("font.ttf", b"font"),),
        font_setup=font_setup or FontSetup(),
        render_profile=render_profile,
        keep_coverage=keep_coverage,
    )


def probeable_request(**kwargs) -> GeometryRequest:
    """A request whose palette carries what the anchor probe needs: text, face and size.

    Separate from `request` rather than a flag on it, because "the overprint may redraw this token"
    is what the three fields together mean — a request missing any of them is the ordinary case.
    """
    base = request(**kwargs)
    return replace(
        base,
        palette=tuple(
            replace(entry, font_name="Sans", font_size=40.0, text=text)
            for entry, text in zip(base.palette, ("猫", "犬"), strict=False)
        ),
    )


def test_extractor_unions_segments_and_ignores_non_character_layers() -> None:
    rendered = Result(
        (
            Layer(2, 1, b"\xff\x00", 0x01020300, 10, 20),
            Layer(2, 1, b"\x00\xff", 0x01020300, 12, 20),
            Layer(1, 1, b"\xff", 0x04050600, 30, 40),
            Layer(9, 9, b"\xff" * 81, 0x99999900, 0, 0, image_type=1),
        )
    )

    geometry = extract_token_geometry(rendered, request())

    assert [(item.token_index, item.bounds) for item in geometry] == [
        (0, type(geometry[0].bounds)(10, 20, 4, 1)),
        (1, type(geometry[1].bounds)(30, 40, 1, 1)),
    ]
    assert len(geometry[0].regions) == 2


def test_extractor_orders_flattened_tokens_across_distinct_authored_events() -> None:
    baseline = request()
    first = baseline.frame_id.active_event_ids[0]
    second = SubtitleEventId(baseline.track_id, 1_100, 1_900, 1, 1)
    multi = replace(
        baseline,
        frame_id=SubtitleFrameId(baseline.track_id, (first, second)),
        palette=(
            GeometryPaletteEntry(second, 1, 0x040506),
            GeometryPaletteEntry(first, 0, 0x010203),
        ),
    )

    geometry = extract_token_geometry(
        Result(
            (
                Layer(1, 1, b"\xff", 0x04050600, 30, 40),
                Layer(1, 1, b"\xff", 0x01020300, 10, 20),
            )
        ),
        multi,
    )

    assert [(item.event_id, item.token_index) for item in geometry] == [(first, 0), (second, 1)]


@pytest.mark.parametrize(
    ("layers", "message"),
    [
        ((Layer(1, 1, b"\xff", 0x99999900, 0, 0),), "unknown"),
        ((Layer(1, 1, b"", 0x01020300, 0, 0),), "invalid size"),
        ((Layer(1, 1, b"\x00", 0x01020300, 0, 0),), "missing"),
    ],
)
def test_extractor_fails_closed_on_unusable_layers(layers, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        extract_token_geometry(Result(layers), request())


def test_extractor_rejects_two_token_colors_on_the_same_pixel() -> None:
    rendered = Result(
        (
            Layer(1, 1, b"\xff", 0x01020300, 10, 20),
            Layer(1, 1, b"\xff", 0x04050600, 10, 20),
        )
    )

    with pytest.raises(ValueError, match="overlap"):
        extract_token_geometry(rendered, request())


def test_extractor_rejects_character_pixels_outside_frame() -> None:
    rendered = Result(
        (
            Layer(1, 1, b"\xff", 0x01020300, 1280, 20),
            Layer(1, 1, b"\xff", 0x04050600, 30, 40),
        )
    )

    with pytest.raises(ValueError, match="outside the frame"):
        extract_token_geometry(rendered, request())


def test_request_cache_identity_covers_render_inputs_but_not_generation() -> None:
    baseline = request()

    assert baseline.cache_key() == request(generation=9).cache_key()
    assert baseline.cache_key() != request(ass=b"changed").cache_key()
    assert baseline.cache_key() != request(margins=(10, 20, 30, 40)).cache_key()
    assert baseline.cache_key() != request(use_margins=True).cache_key()
    assert baseline.cache_key() != request(render_profile=(("sub-scale", "1.2"),)).cache_key()
    # Not a render input but a content one: a hit served maskless to a caller that wanted coverage
    # drops the cue to the plainest color device with nothing saying why.
    assert baseline.cache_key() != request(keep_coverage=True).cache_key()


@pytest.mark.parametrize(
    "margins",
    [(-1, 0, 0, 0), (360, 360, 0, 0), (0, 0, 640, 640)],
)
def test_request_rejects_invalid_frame_margins(margins: tuple[int, int, int, int]) -> None:
    with pytest.raises(ValueError, match="margins"):
        request(margins=margins)


def test_request_rejects_non_boolean_margin_policy() -> None:
    with pytest.raises(TypeError, match="use_margins"):
        request(use_margins=1)  # type: ignore[arg-type]


def test_request_rejects_cross_event_palette() -> None:
    baseline = request()
    other_track = SubtitleTrackId("other")
    other_event = SubtitleEventId(other_track, 1_000, 2_000, 0, 0)

    with pytest.raises(ValueError, match="requested frame"):
        GeometryRequest(
            baseline.generation,
            baseline.track_id,
            baseline.frame_id,
            baseline.timestamp_ms,
            baseline.frame_size,
            baseline.storage_size,
            baseline.ass,
            palette=(GeometryPaletteEntry(other_event, 0, 1),),
        )


def test_request_rejects_frame_pixel_budget_before_rendering() -> None:
    assert replace(request(), frame_size=(4096, 4096)).frame_size == (4096, 4096)

    with pytest.raises(ValueError, match="frame pixel limit"):
        replace(request(), frame_size=(4097, 4097))


def test_request_rejects_palette_budget_before_rendering() -> None:
    baseline = request()
    event = baseline.frame_id.active_event_ids[0]
    accepted = tuple(
        GeometryPaletteEntry(event, index, index + 1) for index in range(MAX_GEOMETRY_TOKENS)
    )
    assert len(replace(baseline, palette=accepted).palette) == MAX_GEOMETRY_TOKENS
    palette = tuple(
        GeometryPaletteEntry(event, index, index + 1) for index in range(MAX_GEOMETRY_TOKENS + 1)
    )

    with pytest.raises(ValueError, match="palette entry limit"):
        replace(baseline, palette=palette)


class FakeRenderer:
    def __init__(
        self, result: Result, ass: bytes = b"", probe_result: Result | None = None
    ) -> None:
        self.result = result
        self.probe_result = probe_result or result
        self.closed = False
        self.calls: list[tuple[tuple, dict]] = []
        self.documents: list[bytes] = [ass]

    def render(self, *args, **kwargs) -> Result:
        self.calls.append((args, kwargs))
        # A real renderer answers the document it was given. A fake that always returns the cue's
        # colours would make the anchor probe — which renders its own document in its own palette —
        # unrecoverable, and hide the probe wiring behind a swallowed exception.
        if b"\\an7\\pos" in self.documents[-1]:
            return self.probe_result
        return self.result

    def set_document(self, ass: bytes, _features=()) -> None:
        self.documents.append(ass)

    def close(self) -> None:
        assert not self.closed
        self.closed = True

    def library_version(self) -> int:
        return 0x1705000


def _recording_factory(created: list[FakeRenderer]):
    layers = Result(
        (Layer(1, 1, b"\xff", 0x01020300, 10, 20), Layer(1, 1, b"\xff", 0x04050600, 30, 40))
    )

    # The probe draws each token alone at its own anchor, in `_probe_rgbs` colours; these land one
    # pixel right and five below the anchors `probe_document` hands out, which is the shape of a real
    # ascent gap.
    probe = Result(
        (
            Layer(1, 1, b"\xff", (0x010000) << 8, 65, 5),
            Layer(1, 1, b"\xff", (0x010001) << 8, 65, 49),
        )
    )

    def factory(ass, **_kwargs):
        renderer = FakeRenderer(layers, ass, probe)
        created.append(renderer)
        return renderer

    return factory


def test_a_new_cue_swaps_the_document_instead_of_rebuilding_libass() -> None:
    """A renderer is identified by its font environment, not by the cue it happens to hold.

    Keying it on the snapshot identity — which hashes the timestamp, the palette and the document
    — meant it could never be reused: one live session built a renderer for 16 of 18 renders, each
    a libass init and a font-directory rescan, and threw away the glyph cache it had just filled
    for the same fonts.
    """
    created: list[FakeRenderer] = []
    backend = LibassGeometryBackend(renderer_factory=_recording_factory(created))

    backend.render(request())
    backend.render(request(ass=b"the next cue"))
    backend.close()

    assert len(created) == 1, "a second cue rebuilt the whole library"
    assert created[0].documents == [b"ass", b"the next cue"]


def test_the_swapped_in_document_is_the_one_the_render_is_measured_against() -> None:
    """The hazard the reuse opens, and the reason the swap is not left to the caller: a cached
    renderer still holding the previous cue's track answers with that cue's boxes, silently — hit
    regions beside the words, which is the failure this whole path exists to prevent."""
    created: list[FakeRenderer] = []
    backend = LibassGeometryBackend(renderer_factory=_recording_factory(created))

    backend.render(request())
    backend.render(request(ass=b"the next cue"))
    rendered_under = created[0].documents[-1]
    backend.close()

    assert rendered_under == b"the next cue"


def test_a_different_font_environment_gets_its_own_renderer() -> None:
    """The other half of the identity: fonts are what a renderer is built around, so a track that
    brings its own must not be measured against the previous track's font set."""
    created: list[FakeRenderer] = []
    backend = LibassGeometryBackend(renderer_factory=_recording_factory(created))

    backend.render(request())
    backend.render(request(font_setup=FontSetup(default_family="Hiragino Sans")))
    backend.close()

    assert len(created) == 2


def test_backend_bounds_renderer_cache_and_closes_evictions() -> None:
    created: list[FakeRenderer] = []
    backend = LibassGeometryBackend(
        renderer_cache_max=1, renderer_factory=_recording_factory(created)
    )

    assert len(backend.render(request()).tokens) == 2
    assert len(backend.render(request(font_setup=FontSetup(default_family="other"))).tokens) == 2
    assert len(created) == 2 and created[0].closed and not created[1].closed

    backend.close()
    backend.close()
    assert created[1].closed


def test_building_a_renderer_is_timed_and_reusing_one_is_not() -> None:
    """The interval nothing measured. `render_ms` times `renderer.render()` and `extract_ms` a
    pure-Python walk, so a library init and a font scan fell between the two spans and read as
    free — which is how a per-cue cost got argued about from console noise instead of a number.
    Zero on a reuse, so the histogram measures construction rather than diluting it.
    """
    spans: list[dict[str, object]] = []

    class _Span:
        def __init__(self, values: dict[str, object]) -> None:
            self.values = values

        def set(self, key: str, value: object) -> None:
            self.values[key] = value

    class _RecordingTelemetry:
        @contextmanager
        def span(self, _name: str):
            values: dict[str, object] = {}
            spans.append(values)
            yield _Span(values)

        def record(self, metric: str, milliseconds: float) -> None:
            pass

    layers = Result((Layer(1, 1, b"\xff", 0x01020300, 10, 20),))
    backend = LibassGeometryBackend(
        renderer_factory=lambda *_a, **_k: FakeRenderer(layers),
        telemetry=_RecordingTelemetry(),
    )
    try:
        backend.render(request(palette_size=1))
        backend.render(request(palette_size=1))
    finally:
        backend.close()

    built = [span["renderer_built_ms"] for span in spans if "renderer_built_ms" in span]
    assert len(built) == 2
    assert built[0] >= 0.0
    assert built[1] == 0.0, "a cached renderer was billed for a construction that did not happen"


def test_backend_reports_each_phase_it_times_to_the_injected_sink() -> None:
    """The three histograms are the point of timing the phases at all. They used to be recorded by
    name against module globals; behind a port a dropped one is silent, so the names are asserted."""
    recorded: list[tuple[str, float]] = []

    class _Telemetry:
        @contextmanager
        def span(self, _name: str):
            yield SimpleNamespace(set=lambda _k, _v: None)

        def record(self, metric: str, milliseconds: float) -> None:
            recorded.append((metric, milliseconds))

    layers = Result((Layer(1, 1, b"\xff", 0x01020300, 10, 20),))
    backend = LibassGeometryBackend(
        renderer_factory=lambda *_a, **_k: FakeRenderer(layers), telemetry=_Telemetry()
    )
    try:
        backend.render(request(palette_size=1))
        backend.render(request(palette_size=1))
    finally:
        backend.close()

    names = [metric for metric, _ms in recorded]
    assert names.count("renderer_build_ms") == 1, "the cached second render billed a construction"
    assert names.count("render_ms") == 2
    assert names.count("extract_ms") == 2
    assert all(milliseconds >= 0.0 for _metric, milliseconds in recorded)


def test_the_telemetry_adapter_reaches_the_histogram_each_name_stands_for() -> None:
    """`otel_metrics` satisfies the port structurally — nothing type-checks the mapping, so a typo
    would silently record nothing. Reads the globals per call, since `configure` rebinds them."""
    from saitenka import otel_metrics

    class _Histogram:
        def __init__(self) -> None:
            self.values: list[float] = []

        def record(self, value: float) -> None:
            self.values.append(value)

    histograms = {name: _Histogram() for name in ("renderer_build", "render", "extract")}
    with pytest.MonkeyPatch.context() as patch:
        for name, histogram in histograms.items():
            patch.setattr(otel_metrics, f"subtitle_geometry_{name}_ms", histogram)
        for name in histograms:
            otel_metrics.geometry_telemetry.record(f"{name}_ms", 1.5)

    assert {name: histogram.values for name, histogram in histograms.items()} == {
        "renderer_build": [1.5],
        "render": [1.5],
        "extract": [1.5],
    }
    with pytest.raises(ValueError, match="unknown geometry metric"):
        otel_metrics.geometry_telemetry.record("prepare_ms", 1.0)


def test_backend_forwards_every_font_source_to_the_renderer() -> None:
    """The measuring renderer has to be handed the same four sources mpv's was, or it lays the cue
    out in whatever the system happens to offer and every box comes out the wrong width."""
    seen: list[dict] = []

    def factory(_ass, **kwargs):
        seen.append(kwargs)
        return FakeRenderer(Result((Layer(1, 1, b"\xff", 0x01020300, 10, 20),)))

    backend = LibassGeometryBackend(renderer_factory=factory)
    setup = FontSetup(
        fonts_dir="/config/fonts",
        extract_fonts=True,
        default_font="/config/subfont.ttf",
        default_family="sans-serif",
        fontconfig_config="/config/fonts.conf",
        font_provider=FontProvider.FONTCONFIG,
    )

    backend.render(request(font_setup=setup, palette_size=1))

    assert seen == [
        {
            "fonts": [("font.ttf", b"font")],
            "library_path": None,
            "fonts_dir": "/config/fonts",
            "extract_fonts": True,
            "default_font": "/config/subfont.ttf",
            "default_family": "sans-serif",
            "fontconfig_config": "/config/fonts.conf",
            "font_provider": 3,
            "features": [],
        }
    ]


def test_a_font_source_change_is_a_different_cached_renderer() -> None:
    """Two tracks whose only difference is the font set must not share a renderer: libass reads the
    directory and the extraction flag before it parses, so a reused one is measuring the old set."""
    plain = request(palette_size=1)
    attached = request(palette_size=1, font_setup=FontSetup(fonts_dir="/config/fonts"))

    assert plain.cache_key() != attached.cache_key()


def test_backend_forwards_mpv_margin_contract() -> None:
    native = FakeRenderer(
        Result(
            (
                Layer(1, 1, b"\xff", 0x01020300, 10, 20),
                Layer(1, 1, b"\xff", 0x04050600, 30, 40),
            )
        )
    )
    backend = LibassGeometryBackend(renderer_factory=lambda *_args, **_kwargs: native)

    backend.render(request(margins=(98, 99, 0, 0), use_margins=True))

    assert native.calls == [
        (
            (1_250, (1280, 720), (1280, 720)),
            {
                "pixel_aspect": 1.0,
                "margins": (98, 99, 0, 0),
                "use_margins": True,
                "max_bitmap_bytes": 1_843_200,
                "style": None,
            },
        )
    ]


# --- anchor probe -------------------------------------------------------------------------------
# The overprint redraws a token as its own `\an7\pos` event, which anchors the line box while the
# measured rectangle is ink. The offset between them is measured by rendering that fragmented
# layout; these cover the wiring, since the assertion that it lands on the glyphs is `live`-tier and
# does not run in CI.


def test_a_probeable_token_carries_the_offset_its_redraw_needs() -> None:
    created: list[FakeRenderer] = []
    backend = LibassGeometryBackend(renderer_factory=_recording_factory(created))

    snapshot = backend.render(probeable_request())
    backend.close()

    # The fake renders both tokens' ink at the same two places whatever document it is given, so the
    # probe resolves a definite offset from its own anchors rather than a placeholder.
    assert all(token.anchor_dx or token.anchor_dy for token in snapshot.tokens)


def test_a_token_with_no_face_or_text_is_left_where_it_was() -> None:
    """The palette carries neither for a token the overprint will not draw, and an unprobed token
    must keep the origin it has rather than acquire someone else's offset."""
    created: list[FakeRenderer] = []
    backend = LibassGeometryBackend(renderer_factory=_recording_factory(created))

    snapshot = backend.render(request())
    backend.close()

    assert [(t.anchor_dx, t.anchor_dy) for t in snapshot.tokens] == [(0, 0), (0, 0)]


def test_the_same_words_are_measured_once_however_often_the_cue_is_drawn() -> None:
    """The probe is a second libass render; paying it per redraw would put it on the hot path."""
    created: list[FakeRenderer] = []
    backend = LibassGeometryBackend(renderer_factory=_recording_factory(created))

    backend.render(probeable_request())
    first = len(created[-1].documents)
    backend.render(probeable_request())
    backend.render(probeable_request())
    backend.close()

    assert len(created[-1].documents) == first, "a repeated cue re-measured its anchors"


def test_the_probe_never_points_the_cue_renderer_at_its_own_document() -> None:
    """A probe that swapped the shared renderer's document would leave the next cue measured against
    the probe — the silent wrong-boxes failure the document swap exists to prevent."""
    created: list[FakeRenderer] = []
    backend = LibassGeometryBackend(renderer_factory=_recording_factory(created))

    backend.render(probeable_request())
    cue_renderer = created[0]
    backend.close()

    assert len(created) == 2, "the probe did not build a renderer of its own"
    assert all(b"\\an7\\pos" not in document for document in cue_renderer.documents)


def test_every_probe_renderer_is_closed_with_the_backend() -> None:
    created: list[FakeRenderer] = []
    backend = LibassGeometryBackend(renderer_factory=_recording_factory(created))

    backend.render(probeable_request())
    backend.close()

    assert all(renderer.closed for renderer in created)


def test_measured_anchors_are_bounded_so_a_resize_cannot_grow_them_forever() -> None:
    """The key holds the FRAME's font size, not the document's: `_palette_in_frame_units` scales by
    `frame_height / play_res_y`, so dragging a window edge mints a distinct float per pixel of height
    and every one of them misses. Unbounded, a resize would leave an entry per (word, transient size)
    for the life of the session."""
    from saitenka_subtitles.libass_backend import ANCHOR_CACHE_MAX, _Anchor

    created: list[FakeRenderer] = []
    backend = LibassGeometryBackend(renderer_factory=_recording_factory(created))
    stale = {
        (f"word{index}", "Sans", float(index), 0.0, 100.0, False, False): _Anchor(1, 1)
        for index in range(ANCHOR_CACHE_MAX)
    }
    backend._anchors.update(stale)

    backend.render(probeable_request())  # two genuinely new words, over the bound
    size = len(backend._anchors)
    backend.close()

    assert size == ANCHOR_CACHE_MAX


def test_two_events_do_not_exchange_their_first_tokens_corrections() -> None:
    """A frame holds several events and `_validate_palette` only requires the (event, index) PAIR to
    be unique, so two events whose first tokens differ both answer to index 0. Keyed on the index
    alone the last one measured wins, and the other word is redrawn by a correction taken from
    different glyphs."""
    track = SubtitleTrackId("track")
    first = SubtitleEventId(track, 1_000, 2_000, 0, 0)
    second = SubtitleEventId(track, 1_000, 2_000, 0, 1)
    created: list[FakeRenderer] = []
    backend = LibassGeometryBackend(renderer_factory=_recording_factory(created))
    from saitenka_subtitles.libass_backend import _Anchor

    backend._anchors.update(
        {
            ("猫", "Sans", 40.0, 0.0, 100.0, False, False): _Anchor(6, 6),
            ("犬", "Sans", 40.0, 0.0, 100.0, False, False): _Anchor(0, -14),
        }
    )
    base = request()
    shared = replace(
        base,
        frame_id=SubtitleFrameId(track, (first, second)),
        palette=(
            # BOTH at index 0 — that is the collision. `_validate_palette` requires only the
            # (event, index) pair to be unique, and a frame with two events routinely has two
            # first tokens.
            GeometryPaletteEntry(first, 0, 0x010203, "Sans", 40.0, "猫"),
            GeometryPaletteEntry(second, 0, 0x040506, "Sans", 40.0, "犬"),
        ),
    )

    tokens = backend.render(shared).tokens
    backend.close()

    by_event = {token.event_id: (token.anchor_dx, token.anchor_dy) for token in tokens}
    assert by_event[first] == (6, 6)
    assert by_event[second] == (0, -14)


def test_a_batch_the_probe_cannot_measure_is_not_re_rendered_every_frame() -> None:
    """`extract_token_geometry` refuses a palette colour that drew nothing, so one inkless token —
    text the overprint would refuse anyway — fails the whole batch. Nothing records the attempt, so
    without a memo of failures the probe re-renders once per geometry render for the session."""
    created: list[FakeRenderer] = []

    def failing_factory(ass, **kwargs):
        renderer = _recording_factory(created)(ass, **kwargs)
        renderer.probe_result = Result(())  # the probe drew nothing at all
        return renderer

    backend = LibassGeometryBackend(renderer_factory=failing_factory)

    for _ in range(3):
        backend.render(probeable_request())
    probes = sum(1 for renderer in created for doc in renderer.documents if b"\\an7\\pos" in doc)
    backend.close()

    assert probes == 1, f"a permanent probe failure re-rendered {probes} times"

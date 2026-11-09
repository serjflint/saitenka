"""Character attribution is optional and must preserve the semantic subtitle geometry."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from saitenka_subtitles import (
    FontSetup,
    GeometryRequest,
    SubtitleTrackId,
    TokenAnnotation,
    prepare_ass_hit_map_frame,
)
from saitenka_subtitles.character_masks import same_support
from saitenka_subtitles.character_scan import character_spans, prepare_character_map
from saitenka_subtitles.geometry import CharacterMap, GeometryPaletteEntry, PaintQualification
from saitenka_subtitles.libass_backend import LibassGeometryBackend
from saitenka_tokenize.japanese import tokenize
from test_ass_geometry import ASS
from test_libass_geometry_backend import FakeRenderer, Layer, Result, request
from util import requires_libass

from saitenka.app.mpv_layout_source import layout_boxes
from saitenka.app.native_subtitles import GeometryObservation
from saitenka.app.subtitles import render_subtitle


def component_request(text="やり遂げたい"):
    source = ASS.replace("猫を見る".encode(), text.encode())
    track = SubtitleTrackId("component")
    rows = source.decode().splitlines()[-2]
    spans = (TokenAnnotation(0, 0, len(text)),)
    prepared = prepare_ass_hit_map_frame(source, track, active_rows=rows, text=text, tokens=spans)
    return GeometryRequest(
        1,
        track,
        prepared.frame_id,
        1500,
        (1280, 720),
        (1280, 720),
        prepared.ass,
        palette=prepared.palette,
        reserved_rgb=prepared.reserved_rgb,
        native_ass=source,
        paint_qualification=prepared.paint_qualification,
        font_setup=FontSetup(
            default_font=str(Path("src/saitenka/assets/fonts/NotoSansJP.ttf").resolve()),
            default_family="Noto Sans JP Thin",
        ),
        character_map=prepare_character_map(
            source, track, active_rows=rows, text=text, tokens=spans
        ),
    )


def test_character_spans_keep_combining_marks_with_their_base():
    assert character_spans("やり遂け\u3099たい") == ((0, 1), (1, 2), (2, 3), (3, 5), (5, 6), (6, 7))


@pytest.mark.parametrize("text", ["a猫", "fi", "猫", "猫\u200d犬"])
def test_unsupported_shaping_has_no_manufactured_character_cells(text):
    assert character_spans(text) == ()


def test_fine_map_changes_snapshot_identity_but_keeps_renderer_reuse():
    fine = component_request()
    coarse = replace(fine, character_map=None)

    assert fine.cache_key() != coarse.cache_key()
    assert fine.renderer_key() == coarse.renderer_key()


def test_equal_outer_bounds_do_not_prove_equal_internal_support():
    a = np.zeros((9, 9), dtype=bool)
    a[0, 0] = a[8, 8] = a[4, 2] = True
    b = a.copy()
    b[4, 2], b[4, 6] = False, True

    assert not same_support(a, b)


def test_pillow_measures_the_component_inside_the_original_token():
    tokens = tokenize("やり遂げたい")
    rendered = render_subtitle([tokens], 1280)
    [box] = rendered.boxes

    assert tokens[0].surface == "やり遂げたい"
    assert [char.offset for char in box.characters] == list(range(6))
    char = box.characters[2]
    assert tokens[0].surface[char.offset :] == "遂げたい"
    assert box.contains(
        char.bounds.x + char.bounds.width / 2, char.bounds.y + char.bounds.height / 2
    )


@pytest.mark.parametrize("case", ["disjoint", "cluster", "overlap"])
def test_mpv_character_cells_preserve_renderer_cluster_boundaries(case):
    from saitenka_subtitles.mpv_layout import decode_layout
    from saitenka_tokenize.japanese import Token
    from test_mpv_layout import payload

    data = payload()
    if case == "cluster":
        data["events"][0]["units"] = [
            {
                "start": 0,
                "end": 6,
                "line": 0,
                "logical_rects": [{"x": 100, "y": 100, "w": 80, "h": 40}],
            }
        ]
    elif case == "overlap":
        data["events"][0]["units"][1]["logical_rects"] = [{"x": 100, "y": 100, "w": 40, "h": 40}]
    token = Token("猫犬", "猫犬", "ねこいぬ", "名詞", 0, 2)
    seen = GeometryObservation(
        prop=lambda _: None,
        osd=(1280, 720),
        text="猫犬",
        tokens=[token],
        lines=[[token]],
        index=None,
        normalise=str,
        nav_index=-1,
        cue_hint=None,
        cue_revision=1,
        is_skippable=lambda _: False,
    )

    [box] = layout_boxes(decode_layout(data), seen)

    assert bool(box.characters) is (case == "disjoint")
    assert box.index == 0
    if case == "disjoint":
        assert [char.offset for char in box.characters] == [0, 1]
        assert box.characters[1].regions[0].x == 200


def test_real_libass_characters_preserve_the_semantic_ink():
    requires_libass()
    asked = component_request()
    backend = LibassGeometryBackend()
    try:
        plain = backend.render(replace(asked, character_map=None))
        fine = backend.render(asked)
    finally:
        backend.close()

    assert [replace(item, characters=()) for item in fine.tokens] == list(plain.tokens)
    assert [char.offset for char in fine.tokens[0].characters] == list(range(6))


def test_real_libass_preserves_component_identity_across_cotimed_lines():
    requires_libass()
    baseline = component_request()
    source = baseline.native_ass.replace(b"0:00:04.00,0:00:06.00", b"0:00:01.00,0:00:03.00")
    rows = "\n".join(line for line in source.decode().splitlines() if line.startswith("Dialogue:"))
    text = "やり遂げたい\n犬も見る"
    spans = (TokenAnnotation(0, 0, 6), TokenAnnotation(1, 7, 11))
    prepared = prepare_ass_hit_map_frame(
        source, baseline.track_id, active_rows=rows, text=text, tokens=spans
    )
    asked = replace(
        baseline,
        ass=prepared.ass,
        native_ass=source,
        frame_id=prepared.frame_id,
        palette=prepared.palette,
        reserved_rgb=prepared.reserved_rgb,
        character_map=prepare_character_map(
            source, baseline.track_id, active_rows=rows, text=text, tokens=spans
        ),
    )
    backend = LibassGeometryBackend()
    try:
        snapshot = backend.render(asked)
    finally:
        backend.close()

    assert [(item.token_index, len(item.characters)) for item in snapshot.tokens] == [
        (0, 6),
        (1, 4),
    ]
    assert snapshot.tokens[0].event_id != snapshot.tokens[1].event_id


@pytest.mark.parametrize("failure", ["occluded", "overlap", "missing", "runtime"])
def test_fine_map_failure_keeps_the_original_semantic_geometry(failure):
    base = request(palette_size=1)
    event = base.frame_id.active_event_ids[0]
    original = Result((Layer(3, 1, b"\xff\x00\xff", 0x01020300, 10, 20),))
    fine = Result(
        (Layer(1, 1, b"\xff", 0x11111100, 10, 20), Layer(1, 1, b"\xff", 0x22222200, 12, 20))
    )
    if failure == "occluded":
        original = Result((*original.layers, Layer(1, 1, b"\xff", 0xFFFFFF00, 10, 20)))
    if failure == "overlap":
        fine = Result((fine.layers[0], replace(fine.layers[1], dst_x=10)))
    if failure == "missing":
        fine = Result(fine.layers[:1])

    class Renderer(FakeRenderer):
        def render(self, *_args, **_kwargs):
            if self.documents[-1] == b"fine":
                if failure == "runtime":
                    raise RuntimeError("fine renderer unavailable")
                return fine
            return original

    backend = LibassGeometryBackend(renderer_factory=lambda ass, **_kw: Renderer(original, ass))
    mapping = CharacterMap(
        b"fine",
        (GeometryPaletteEntry(event, 0, 0x111111), GeometryPaletteEntry(event, 1, 0x222222)),
        (0xFFFFFF,),
        (TokenAnnotation(0, 0, 2),),
    )
    try:
        plain = backend.render(base)
        result = backend.render(
            replace(base, paint_qualification=PaintQualification.STATIC, character_map=mapping)
        )
    finally:
        backend.close()

    assert result.tokens == plain.tokens

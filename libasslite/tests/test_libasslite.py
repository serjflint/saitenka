"""Assembly tests against the system libass selected for this platform."""

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import libasslite
import libasslite as public_api

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\c&H112233&}猫{\\c&H445566&}を見る{\\c&H778899&}猫
""".encode()


#: A font the repo already ships, used as a family no installed provider offers under that name.
REPO_FONT = Path(__file__).resolve().parents[2] / "src/saitenka/assets/fonts/NotoSans.ttf"
REPO_FONT_FAMILY = "Noto Sans"


def noto_ass() -> bytes:
    return ASS.replace(b"Default,Arial,48", f"Default,{REPO_FONT_FAMILY},48".encode())


def uuencoded(data: bytes) -> str:
    """The ASS `[Fonts]` encoding: three bytes to four printable characters, 80 to a line."""
    digits = []
    for offset in range(0, len(data), 3):
        chunk = data[offset : offset + 3]
        value = int.from_bytes(chunk.ljust(3, b"\0"), "big")
        quad = [(value >> 18) & 63, (value >> 12) & 63, (value >> 6) & 63, value & 63]
        digits.extend(chr(digit + 33) for digit in quad[: len(chunk) + 1])
    return "\n".join("".join(digits[at : at + 80]) for at in range(0, len(digits), 80))


def painted_characters(result: libasslite.AssRenderResult) -> int:
    return len([layer for layer in result.layers if layer.image_type == 0 and layer.width])


def frame(ass_renderer: libasslite.AssRenderer, **kwargs: object) -> libasslite.AssRenderResult:
    return ass_renderer.render(1_500, (1280, 720), (1280, 720), **kwargs)  # type: ignore[arg-type]


def configured_library() -> Path | None:
    configured = os.environ.get("LIBASSLITE_LIBRARY")
    if configured:
        return Path(configured)
    if sys.platform == "darwin":
        return Path("/opt/homebrew/lib/libass.dylib")
    return None


def renderer(ass: bytes = ASS, **kwargs: object) -> libasslite.AssRenderer:
    return libasslite.AssRenderer(ass, library_path=configured_library(), **kwargs)  # type: ignore[arg-type]


def character_bounds(result: libasslite.AssRenderResult, rgb: int) -> tuple[int, int, int, int]:
    layers = [layer for layer in result.layers if layer.image_type == 0 and layer.color >> 8 == rgb]
    assert layers
    return (
        min(layer.dst_x for layer in layers),
        min(layer.dst_y for layer in layers),
        max(layer.dst_x + layer.width for layer in layers),
        max(layer.dst_y + layer.height for layer in layers),
    )


def geometry_signature(result: libasslite.AssRenderResult) -> list[tuple]:
    return [
        (
            layer.image_type,
            layer.width,
            layer.height,
            layer.dst_x,
            layer.dst_y,
            layer.bitmap,
        )
        for layer in result.layers
    ]


def test_loads_public_abi_and_recovers_repeated_token_bounds() -> None:
    ass_renderer = renderer()

    result = ass_renderer.render(1_500, (1280, 720), (1280, 720))

    assert ass_renderer.library_version() & 0xFFFF0000 == 0x01700000
    assert ass_renderer.library_path()
    assert {layer.image_type for layer in result.layers} >= {0, 1, 2}
    assert all(len(layer.bitmap) == layer.width * layer.height for layer in result.layers)
    first_cat = character_bounds(result, 0x332211)
    middle = character_bounds(result, 0x665544)
    second_cat = character_bounds(result, 0x998877)
    centers = [(bounds[0] + bounds[2]) / 2 for bounds in (first_cat, middle, second_cat)]
    assert centers == sorted(centers)
    assert len(set(centers)) == 3


def test_color_only_variants_preserve_plain_fixture_geometry() -> None:
    visible = renderer()
    hit_map_ass = (
        ASS.replace(b"112233", b"010101")
        .replace(b"445566", b"020202")
        .replace(b"778899", b"030303")
    )
    hit_map = renderer(hit_map_ass)

    visible_result = visible.render(1_500, (1280, 720), (1280, 720))
    hit_map_result = hit_map.render(1_500, (1280, 720), (1280, 720))

    assert geometry_signature(visible_result) == geometry_signature(hit_map_result)


def test_geometry_oracle_detects_layout_change() -> None:
    baseline = renderer()
    changed = renderer(ASS.replace(b"Arial,48", b"Arial,60"))

    baseline_result = baseline.render(1_500, (1280, 720), (1280, 720))
    changed_result = changed.render(1_500, (1280, 720), (1280, 720))

    assert geometry_signature(baseline_result) != geometry_signature(changed_result)


def test_full_width_space_produces_no_requested_character_layer() -> None:
    spacing = ASS.replace(
        b"{\\c&H112233&}\xe7\x8c\xab{\\c&H445566&}\xe3\x82\x92\xe8\xa6\x8b\xe3\x82\x8b{\\c&H778899&}\xe7\x8c\xab",
        b"{\\c&H010101&}\xe7\x8c\xab{\\c&H020202&}\xe3\x80\x80{\\c&H030303&}\xe7\x8a\xac",
    )
    ass_renderer = renderer(spacing)

    result = ass_renderer.render(1_500, (1280, 720), (1280, 720))
    character_colors = {
        layer.color >> 8 for layer in result.layers if layer.image_type == 0 and any(layer.bitmap)
    }

    assert 0x010101 in character_colors
    assert 0x030303 in character_colors
    assert 0x020202 not in character_colors


def test_one_renderer_serializes_cross_thread_calls() -> None:
    ass_renderer = renderer()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda timestamp: ass_renderer.render(timestamp, (1280, 720), (1280, 720)),
                [1_500] * 16,
            )
        )

    signatures = [geometry_signature(result) for result in results]
    assert signatures and all(signature == signatures[0] for signature in signatures)


def test_explicit_path_does_not_silently_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = configured_library()
    if configured is None:
        pytest.skip("test requires a configured system libass path")
    monkeypatch.setenv("LIBASSLITE_LIBRARY", str(configured))

    with pytest.raises(RuntimeError, match="could not load libass"):
        libasslite.AssRenderer(ASS, library_path=configured.with_name("missing-libass"))


def test_explicit_path_wins_over_installed_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    chosen = Path("chosen-libass")
    monkeypatch.setattr(public_api, "_bundle_library", lambda: "bundled-libass")

    assert public_api._selected_library(chosen) == chosen


def test_environment_path_skips_bundle_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIBASSLITE_LIBRARY", "system-libass")
    monkeypatch.setattr(
        public_api,
        "import_module",
        lambda _name: pytest.fail("bundle import must not run"),
    )

    assert public_api._bundle_library() is None


def test_bundle_can_be_disabled_without_uninstall(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIBASSLITE_LIBRARY", raising=False)
    monkeypatch.setenv("LIBASSLITE_BUNDLE", "off")
    monkeypatch.setattr(
        public_api,
        "import_module",
        lambda _name: pytest.fail("disabled bundle must not be imported"),
    )

    assert public_api._bundle_library() is None


def test_installed_bundle_supplies_default_library(monkeypatch: pytest.MonkeyPatch) -> None:
    class Bundle:
        @staticmethod
        def library_path() -> Path:
            return Path("bundle/libass")

    monkeypatch.delenv("LIBASSLITE_LIBRARY", raising=False)
    monkeypatch.delenv("LIBASSLITE_BUNDLE", raising=False)
    monkeypatch.setattr(public_api, "import_module", lambda _name: Bundle)

    assert Path(public_api._bundle_library()) == Path("bundle/libass")


def test_invalid_geometry_is_rejected_before_native_render() -> None:
    ass_renderer = renderer()

    with pytest.raises(ValueError, match="frame_size must be positive"):
        ass_renderer.render(1_500, (0, 720), (1280, 720))

    with pytest.raises(ValueError, match="storage_size must be positive"):
        ass_renderer.render(1_500, (1280, 720), (1280, -1))

    with pytest.raises(ValueError, match="margins must be non-negative"):
        ass_renderer.render(1_500, (1280, 720), (1280, 720), margins=(-1, 0, 0, 0))

    with pytest.raises(ValueError, match="positive video rectangle"):
        ass_renderer.render(
            1_500,
            (1280, 720),
            (1280, 720),
            margins=(360, 360, 0, 0),
        )

    with pytest.raises(ValueError, match="max_bitmap_bytes must be positive"):
        ass_renderer.render(1_500, (1280, 720), (1280, 720), max_bitmap_bytes=0)


def test_bitmap_budget_is_exact_and_checked_before_returning_layers() -> None:
    ass_renderer = renderer()
    baseline = ass_renderer.render(1_500, (1280, 720), (1280, 720))
    bitmap_bytes = sum(len(layer.bitmap) for layer in baseline.layers)
    assert bitmap_bytes > 1

    accepted = ass_renderer.render(
        1_500,
        (1280, 720),
        (1280, 720),
        max_bitmap_bytes=bitmap_bytes,
    )
    assert sum(len(layer.bitmap) for layer in accepted.layers) == bitmap_bytes

    with pytest.raises(RuntimeError, match="bitmap budget exceeded"):
        ass_renderer.render(
            1_500,
            (1280, 720),
            (1280, 720),
            max_bitmap_bytes=bitmap_bytes - 1,
        )

    with pytest.raises(ValueError, match="positive video rectangle"):
        ass_renderer.render(
            1_500,
            (1280, 720),
            (1280, 720),
            margins=(2_147_483_647, 2_147_483_647, 0, 0),
        )


@pytest.mark.parametrize("pixel_aspect", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_pixel_aspect_is_rejected_before_native_render(pixel_aspect: float) -> None:
    ass_renderer = renderer()

    with pytest.raises(ValueError, match="pixel_aspect must be finite and positive"):
        ass_renderer.render(
            1_500,
            (1280, 720),
            (960, 720),
            pixel_aspect=pixel_aspect,
        )


def test_explicit_pixel_aspect_changes_only_horizontal_geometry() -> None:
    square = renderer()
    wide = renderer()

    square_bounds = character_bounds(
        square.render(1_500, (1280, 720), (960, 720), pixel_aspect=1.0),
        0x332211,
    )
    wide_bounds = character_bounds(
        wide.render(1_500, (1280, 720), (960, 720), pixel_aspect=4 / 3),
        0x332211,
    )

    assert wide_bounds[0::2] != square_bounds[0::2]
    assert wide_bounds[1::2] == square_bounds[1::2]


def test_use_margins_moves_bottom_aligned_authored_ass_into_video_rectangle() -> None:
    multiline = ("猫\\N" * 8 + "猫").encode()
    ass_renderer = renderer(ASS.replace("猫".encode(), multiline))

    ignored = character_bounds(
        ass_renderer.render(
            1_500,
            (1280, 720),
            (1280, 720),
            margins=(250, 250, 0, 0),
            use_margins=False,
        ),
        0x332211,
    )
    applied = character_bounds(
        ass_renderer.render(
            1_500,
            (1280, 720),
            (1280, 720),
            margins=(250, 250, 0, 0),
            use_margins=True,
        ),
        0x332211,
    )

    assert ignored[1] >= 250
    assert applied[1] < ignored[1]
    assert applied[3] == ignored[3]


def test_no_font_provider_confines_lookup_to_what_the_caller_loaded() -> None:
    ass_renderer = renderer(noto_ass(), font_provider=libasslite.FontProvider.NONE)

    assert painted_characters(frame(ass_renderer)) == 0


def test_fonts_directory_supplies_a_family_no_provider_offers(tmp_path: Path) -> None:
    if not REPO_FONT.exists():
        pytest.skip("test requires the repository's bundled font asset")
    (tmp_path / REPO_FONT.name).write_bytes(REPO_FONT.read_bytes())
    ass_renderer = renderer(
        noto_ass(),
        fonts_dir=str(tmp_path),
        font_provider=libasslite.FontProvider.NONE,
    )

    assert painted_characters(frame(ass_renderer)) > 0


def test_attached_font_bytes_supply_a_family_no_provider_offers() -> None:
    if not REPO_FONT.exists():
        pytest.skip("test requires the repository's bundled font asset")
    ass_renderer = renderer(
        noto_ass(),
        fonts=[(REPO_FONT.name, REPO_FONT.read_bytes())],
        font_provider=libasslite.FontProvider.NONE,
    )

    assert painted_characters(frame(ass_renderer)) > 0


@pytest.mark.parametrize("extract_fonts", [True, False])
def test_in_file_font_section_is_read_only_when_extraction_is_asked_for(
    *, extract_fonts: bool
) -> None:
    if not REPO_FONT.exists():
        pytest.skip("test requires the repository's bundled font asset")
    section = f"\n[Fonts]\nfontname: {REPO_FONT.name}\n{uuencoded(REPO_FONT.read_bytes())}\n"
    ass_renderer = renderer(
        noto_ass() + section.encode(),
        extract_fonts=extract_fonts,
        font_provider=libasslite.FontProvider.NONE,
    )

    assert (painted_characters(frame(ass_renderer)) > 0) is extract_fonts


def test_render_style_never_carries_into_the_next_frame() -> None:
    ass_renderer = renderer()

    plain = geometry_signature(frame(ass_renderer))
    scaled = geometry_signature(frame(ass_renderer, style=libasslite.RenderStyle(font_scale=2.0)))
    again = geometry_signature(frame(ass_renderer))

    assert scaled != plain
    assert again == plain


def test_line_position_lifts_a_bottom_aligned_event_to_the_top() -> None:
    ass_renderer = renderer()

    bottom = character_bounds(frame(ass_renderer), 0x332211)
    top = character_bounds(
        frame(ass_renderer, style=libasslite.RenderStyle(line_position=100.0)), 0x332211
    )

    assert top[1] < bottom[1]


def test_selective_style_override_replaces_the_authored_font_size() -> None:
    ass_renderer = renderer()
    override = libasslite.RenderStyle(
        override_bits=libasslite.OverrideBits.FONT_SIZE_FIELDS,
        override_style=libasslite.AssStyle(font_name="Arial", font_size=96.0),
    )

    authored = character_bounds(frame(ass_renderer), 0x332211)
    overridden = character_bounds(frame(ass_renderer, style=override), 0x332211)

    assert overridden[3] - overridden[1] > authored[3] - authored[1]


def test_override_bits_without_a_style_are_rejected_rather_than_zeroing_the_style() -> None:
    with pytest.raises(ValueError, match="override_bits requires an override_style"):
        libasslite.RenderStyle(override_bits=libasslite.OverrideBits.COLORS)


def test_track_features_report_what_this_build_cannot_apply() -> None:
    ass_renderer = renderer(
        features=[
            (libasslite.Feature.WRAP_UNICODE, True),
            (libasslite.Feature.BIDI_BRACKETS, True),
            (libasslite.Feature.WHOLE_TEXT_LAYOUT, True),
        ]
    )

    assert set(ass_renderer.unsupported_features()) <= {
        libasslite.Feature.WRAP_UNICODE,
        libasslite.Feature.BIDI_BRACKETS,
    }


def test_an_unknown_track_feature_is_reported_not_silently_applied() -> None:
    ass_renderer = renderer(features=[(999, True)])

    assert ass_renderer.unsupported_features() == [999]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"font_provider": 5}, "font_provider must be between 0 and 4"),
        ({"fonts_dir": "with\0nul"}, "fonts_dir contains a NUL byte"),
        ({"default_family": "with\0nul"}, "default_family contains a NUL byte"),
    ],
)
def test_invalid_font_setup_is_rejected_before_libass_is_initialized(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        renderer(**kwargs)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"hinting": 4}, "hinting must be between 0 and 3"),
        ({"shaper": 2}, "shaper must be between 0 and 1"),
        ({"font_scale": 0.0}, "font_scale must be finite and positive"),
        ({"line_position": 101.0}, "line_position must be between 0 and 100"),
    ],
)
def test_invalid_render_style_is_rejected_at_construction(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        libasslite.RenderStyle(**kwargs)  # type: ignore[arg-type]


def test_close_is_idempotent_and_blocks_render() -> None:
    ass_renderer = renderer()

    ass_renderer.close()
    ass_renderer.close()

    with pytest.raises(RuntimeError, match="closed"):
        ass_renderer.render(1_500, (1280, 720), (1280, 720))

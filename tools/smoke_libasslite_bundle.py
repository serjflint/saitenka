"""Installed-wheel lifecycle smoke for the optional native bundle."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import libasslite_bundle

import libasslite  # noqa: TID251 -- this installed-wheel smoke is the reviewed boundary itself

ASS = b"""[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,bundle
"""


def render_once() -> tuple[str, int]:
    renderer = libasslite.AssRenderer(ASS)
    try:
        result = renderer.render(1_000, (640, 360), (640, 360))
        assert result.layers
        return renderer.library_path(), renderer.library_version()
    finally:
        renderer.close()


def main() -> None:
    bundled = libasslite_bundle.library_path().resolve()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: render_once(), range(64)))
    assert {version for _, version in results} == {0x01705000}
    assert {os.path.realpath(path) for path, _ in results} == {os.path.realpath(bundled)}

    os.environ["LIBASSLITE_LIBRARY"] = str(bundled.with_name("missing-libass"))
    try:
        render_once()
    except RuntimeError:
        pass
    else:
        raise AssertionError("an invalid explicit path fell back to the bundle")
    del os.environ["LIBASSLITE_LIBRARY"]

    os.environ["LIBASSLITE_LIBRARY"] = str(bundled)
    explicit_path, _ = render_once()
    assert os.path.realpath(explicit_path) == os.path.realpath(bundled)
    del os.environ["LIBASSLITE_LIBRARY"]

    os.environ["LIBASSLITE_BUNDLE"] = "0"
    try:
        selected, _ = render_once()
    except RuntimeError:
        pass
    else:
        assert os.path.realpath(selected) != os.path.realpath(bundled)


if __name__ == "__main__":
    main()

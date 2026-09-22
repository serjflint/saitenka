"""Fail closed when a required native-render test environment cannot render text."""

from __future__ import annotations

import argparse
import json
import os
from importlib import import_module
from pathlib import Path

import libasslite  # noqa: TID251 -- native capability qualification boundary


def preflight(provider: str) -> dict[str, str | int]:
    if os.environ.get("LIBASSLITE_LIBRARY") is not None:
        raise RuntimeError("unset LIBASSLITE_LIBRARY to qualify the selected provider")
    if provider == "system" and os.environ.get("LIBASSLITE_BUNDLE") != "0":
        raise RuntimeError("system qualification requires LIBASSLITE_BUNDLE=0")
    expected = (
        Path(import_module("libasslite_bundle").library_path()).resolve()
        if provider == "bundle"
        else None
    )
    frame = (320, 180)
    document = """[Script Info]
PlayResX: 320
PlayResY: 180
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: P,Noto Sans JP Thin,36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,P,,0,0,0,,{\\pos(32,32)}だ漢
"""
    font = Path(__file__).resolve().parents[1] / "src/saitenka/assets/fonts/NotoSansJP.ttf"
    renderer = libasslite.AssRenderer(
        document.encode(), [(font.name, font.read_bytes())], font_provider=0
    )
    try:
        selected = renderer.library_path()
        if expected is not None and Path(selected).resolve() != expected:
            raise RuntimeError(f"expected bundle {expected}, loaded {selected}")
        result = renderer.render(1000, frame, frame)
        ink = sum(bool(value) for layer in result.layers for value in layer.bitmap)
        if not ink:
            raise RuntimeError("native runtime loaded but rendered no text coverage")
        return {
            "provider": provider,
            "library": selected,
            "version": renderer.library_version(),
            "ink_bytes": ink,
        }
    finally:
        renderer.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("system", "bundle"), required=True)
    args = parser.parse_args()
    print(json.dumps(preflight(args.provider), sort_keys=True))


if __name__ == "__main__":
    main()

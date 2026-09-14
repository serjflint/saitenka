"""Fail closed when a required native-render test environment cannot render text."""

from __future__ import annotations

import argparse
import json
import os
from importlib import import_module
from pathlib import Path

from saitenka_subtitles.fragments import probe_document
from saitenka_subtitles.overprint import TokenPaint, event_lines

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
    paint = TokenPaint("だ漢", 32, 32, "Noto Sans JP Thin", 36, 0xFFFFFF)
    document = probe_document((), (), frame).document + "\n".join(
        f"Dialogue: 0,0:00:00.00,0:00:02.00,P,,0,0,0,,{line}" for line in event_lines(paint)
    )
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

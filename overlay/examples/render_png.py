"""CLI: render a string or a Yomitan-entry fixture JSON to a PNG for eyeballing.

uv run python examples/render_png.py --text "Saitenka" -o out.png
uv run python examples/render_png.py --entry tests/fixtures/yomu.json -o panel.png
"""

from __future__ import annotations

import argparse

from overlay.render.text import TextOpts, rasterize


def main() -> None:
    ap = argparse.ArgumentParser(description="Render text or an entry fixture to a PNG.")
    ap.add_argument("--text", default="Saitenka", help="text to render")
    ap.add_argument("--entry", help="path to an entry fixture JSON (renders the full panel)")
    ap.add_argument("--width", type=int, default=384)
    ap.add_argument("--size", type=int, default=32)
    ap.add_argument("--weight", type=int, default=400)
    ap.add_argument("--bg", help="solid background, e.g. 'white' (default transparent)")
    ap.add_argument("-o", "--out", default="out.png")
    args = ap.parse_args()

    if args.entry:
        from overlay.panel import load_entry, render_panel

        img = render_panel(load_entry(args.entry), width=args.width)
    else:
        img = rasterize(args.text, TextOpts(size=args.size, weight=args.weight))

    if args.bg:
        from PIL import Image

        bg = Image.new("RGBA", img.size, args.bg)
        bg.alpha_composite(img)
        img = bg
    img.save(args.out)
    print(f"wrote {args.out}  ({img.width}x{img.height})")


if __name__ == "__main__":
    main()

"""Generate Saitenka-vs-SubMiner tooltip comparison images.

For each case in ``cases.py``: render OUR tooltip from the real configured dictionaries, crop the
SubMiner tooltip from its reference screenshot, and compose them side-by-side (+ a contact sheet).
SubMiner isn't changing, so its side is a fixed reference; ours re-renders each run.

    cd overlay && uv run python compare/generate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cases import CASES  # noqa: E402

from overlay import fonts  # noqa: E402
from overlay.app.config import expand_paths, load_config  # noqa: E402
from overlay.app.dictionary import DictionarySet  # noqa: E402
from overlay.app.tokenize import Token  # noqa: E402
from overlay.panel import render_panel  # noqa: E402

REFS = HERE / "refs"
OUT = HERE / "out"
PANEL_W = 520
PANEL_H = 900          # SubMiner refs only show the top of the tooltip; match that height
BG = (24, 26, 32, 255)
FG = (230, 232, 238, 255)
MUTED = (150, 156, 168, 255)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(fonts.ASSETS / fonts.FONT_FILES[0]), size)


def render_ours(case: dict, ds: DictionarySet) -> Image.Image:
    tok = Token(surface=case["surface"], lemma=case["lemma"], reading=case["reading"],
                pos=case["pos"], start=0, end=len(case["surface"]))
    entry = ds.entry_for(tok)
    return render_panel(entry, width=PANEL_W, max_height=None)


def _labeled(img: Image.Image, label: str, w: int, h: int) -> Image.Image:
    """Scale img to width ``w`` and show its TOP ``h`` px (both tooltips are cut off at the bottom in
    the SubMiner screenshots, so top-align rather than shrink-to-fit — keeps text readable)."""
    lab_h = 34
    sw, sh = w, max(1, round(img.height * (w / img.width)))
    scaled = img.convert("RGBA").resize((sw, sh))
    card = Image.new("RGBA", (w, h + lab_h), (36, 38, 46, 255))
    d = ImageDraw.Draw(card)
    d.text((10, 7), label, font=_font(20), fill=FG)
    card.alpha_composite(scaled.crop((0, 0, w, min(sh, h))), (0, lab_h))
    return card


def _reference(case: dict) -> tuple[Image.Image, str] | None:
    """The reference (SubMiner-engine) side: prefer a live Yomitan render if captured, else the
    cropped static SubMiner screenshot."""
    yom = OUT / "yomitan" / f"{case['surface']}.png"
    if yom.exists():
        return Image.open(yom).convert("RGBA"), "Yomitan (SubMiner engine)"
    ref_path = REFS / case["ref"]
    if ref_path.exists():
        ref = Image.open(ref_path).convert("RGBA")
        x0, y0, x1, y1 = case["crop"]
        rw, rh = ref.size
        return ref.crop((int(rw * x0), int(rh * y0), int(rw * x1), int(rh * y1))), "SubMiner (screenshot)"
    return None


def compose(case: dict, ours: Image.Image) -> Image.Image:
    col_w, col_h = 520, PANEL_H
    left = _labeled(ours, "Saitenka", col_w, col_h)
    ref = _reference(case)
    if ref is not None:
        right = _labeled(ref[0], ref[1], col_w, col_h)
    else:
        right = _labeled(Image.new("RGBA", (10, 10), BG), "reference (missing)", col_w, col_h)

    pad, head = 20, 64
    W = pad * 3 + left.width + right.width
    H = head + max(left.height, right.height) + pad
    canvas = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(canvas)
    d.text((pad, 12), f"{case['word']}  ({case['reading']})", font=_font(28), fill=FG)
    chain = " « ".join(case["expect_chain"]) or "—"
    d.text((pad, 44), f"{case['ts']}   {case['line']}    🧩 {chain}", font=_font(17), fill=MUTED)
    canvas.alpha_composite(left, (pad, head))
    canvas.alpha_composite(right, (pad * 2 + left.width, head))
    return canvas


def main() -> int:
    cfg = load_config()
    if not cfg.get("dicts"):
        print("no dicts in ~/.config/saitenka/overlay.toml — see overlay.example.toml")
        return 2
    ds = DictionarySet.load(expand_paths(cfg["dicts"]), freq_paths=expand_paths(cfg.get("freq")),
                            pitch_paths=expand_paths(cfg.get("pitch")))
    OUT.mkdir(exist_ok=True)
    tiles = []
    for case in CASES:
        img = compose(case, render_ours(case, ds))
        p = OUT / f"{case['word']}.png"
        img.save(p)
        tiles.append(img)
        print("wrote", p.relative_to(HERE.parent))
    # contact sheet: stack cases vertically
    if tiles:
        W = max(t.width for t in tiles)
        H = sum(t.height for t in tiles) + 12 * (len(tiles) - 1)
        sheet = Image.new("RGBA", (W, H), BG)
        y = 0
        for t in tiles:
            sheet.alpha_composite(t, (0, y))
            y += t.height + 12
        sheet.save(OUT / "_sheet.png")
        print("wrote", (OUT / "_sheet.png").relative_to(HERE.parent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Record what mpv actually reports for a converted SubRip track, as the converter's oracle.

`saitenka_subtitles.subrip` predicts the ASS event mpv builds for a `.srt` cue. mpv is the only
authority on that — it links libavcodec's `srtdec` and then serialises the row itself — so the
expected values here are read out of a real mpv rather than written by hand.

    uv run tools/subrip_oracle.py record    # refresh tests/fixtures/subrip_rows.json
    uv run tools/subrip_oracle.py check     # compare the fixture with a live mpv

`record` needs `mpv` and `ffmpeg` on PATH. The fixture is committed so the unit tests have the
oracle without either.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "subrip_rows.json"

#: One entry per rule the converter claims, plus the ones it must DECLINE — a corpus that only held
#: the easy cases would pass with the declines removed.
CASES: tuple[tuple[str, str], ...] = (
    ("plain", "Hello world"),
    ("italic", "Hello <i>world</i>"),
    ("bold-underline", "<b>Bold</b> and <u>under</u>"),
    ("two-lines", "line one\nline two"),
    ("three-lines", "a\nb\nc"),
    ("tag-across-lines", "<i>x\ny</i>"),
    ("adjacent-tags", "<i>a</i><i>b</i>"),
    ("uppercase-tags", "<B>C</B>"),
    ("empty-tag-pair", "<i></i>empty"),
    ("unclosed-open", "<i>unclosed and <b>nested</i>"),
    ("stray-close", "<i>a</b> mismatched </i>"),
    ("font-hex", '<font color="#FF0000">red</font> text'),
    ("font-hex-black", '<font color="#000000">k</font>'),
    ("font-hex-mixed", '<font color="#123456">m</font>'),
    ("font-hex-unquoted", "<font color=#00ff00>g</font>"),
    ("braces-and-backslash", "{braces} and back\\slash"),
    ("leading-an", "{\\an8}top of screen"),
    ("line-whitespace", "  padded  \n  again  "),
    ("non-ascii", '猫を見る — dash… "quotes"'),
    ("ampersand", "a &amp; b"),
    # Declined by the converter; recorded anyway, so the test can assert it declines the cases it
    # would otherwise get wrong rather than merely that it agrees where it answers.
    ("stray-angle", "a < b > c"),
    ("unknown-tag", "<unknown>tag</unknown>"),
    ("font-named-color", '<font color="red">r</font>'),
    ("font-size", '<font size="20">s</font>'),
    ("trailing-an", "{\\an1}bottom{\\an2}"),
    ("mid-an", "text {\\an8} more"),
)

_ROW = re.compile(r"ROW>>(?P<row>.*?)<<", re.DOTALL)


def _srt(directory: Path) -> Path:
    path = directory / "cases.srt"
    blocks = []
    for index, (_name, body) in enumerate(CASES):
        start, end = 1 + index * 4, 3 + index * 4
        blocks.append(f"{index + 1}\n00:00:{start:02d},000 --> 00:00:{end:02d},000\n{body}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def _video(directory: Path) -> Path:
    path = directory / "black.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=320x240:d={len(CASES) * 4 + 4}:r=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def _row_at(video: Path, srt: Path, seconds: int) -> str:
    result = subprocess.run(
        [
            "mpv",
            "--no-config",
            "--vo=null",
            "--ao=null",
            f"--sub-file={srt}",
            f"--start={seconds}",
            "--frames=2",
            "--term-status-msg=ROW>>${sub-text/ass-full}<<",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    match = _ROW.search(result.stdout + result.stderr)
    return match.group("row").strip() if match else ""


def record() -> int:
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        srt, video = _srt(directory), _video(directory)
        rows = {
            name: _row_at(video, srt, 2 + index * 4) for index, (name, _body) in enumerate(CASES)
        }
    FIXTURE.write_text(
        json.dumps(
            {
                "note": "Recorded from a live mpv by tools/subrip_oracle.py; do not hand-edit.",
                "cases": [
                    {"name": name, "markup": body, "row": rows[name]} for name, body in CASES
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"recorded {len(rows)} rows into {FIXTURE}")
    return 0


def check() -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    recorded = {case["name"]: case["row"] for case in fixture["cases"]}
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        srt, video = _srt(directory), _video(directory)
        drifted = [
            name
            for index, (name, _body) in enumerate(CASES)
            if _row_at(video, srt, 2 + index * 4) != recorded.get(name)
        ]
    if drifted:
        print("mpv no longer reports the recorded rows for: " + ", ".join(drifted))
        return 1
    print(f"subrip oracle: OK ({len(recorded)} rows still match a live mpv)")
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    raise SystemExit(record() if command == "record" else check())

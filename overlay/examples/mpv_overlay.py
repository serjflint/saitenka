"""Live end-to-end: render the 読む panel and push it into mpv's OSD over real video.

This is the "airspace proof": the panel is drawn inside mpv's own surface (``overlay-add``), so it
stays visible and correctly placed even in fullscreen — no second window, unlike the Electron
overlay. Use ``--fullscreen`` and press ``f`` in mpv to confirm the panel survives the transition.

    # play a file, show the panel top-left, keep it up for 20s
    uv run python examples/mpv_overlay.py /path/to/video.mkv

    # no file? generate a test clip with ffmpeg and screenshot the composited window, then quit
    uv run python examples/mpv_overlay.py --screenshot /tmp/shot.png --seconds 2
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from overlay.mpvio.ipc import MpvIPC
from overlay.mpvio.osd import Overlay
from overlay.panel import load_entry, render_panel

FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "yomu.json"


def _make_test_video(path: Path, seconds: int = 5) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=1280x720:rate=30:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Show the Saitenka panel inside mpv's OSD.")
    ap.add_argument("video", nargs="?", help="video file (a test clip is generated if omitted)")
    ap.add_argument("--entry", default=str(FIX))
    ap.add_argument("--width", type=int, default=384)
    ap.add_argument("--x", type=int, default=40)
    ap.add_argument("--y", type=int, default=24)
    ap.add_argument("--max-height", type=int, default=660)
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument(
        "--use-config",
        action="store_true",
        help="load your real mpv config (default: --no-config, isolated demo)",
    )
    ap.add_argument("--screenshot", help="capture the composited window to this PNG, then quit")
    ap.add_argument("--seconds", type=float, default=20.0, help="how long to keep the panel up")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="saitenka-mpv-"))
    video = Path(args.video) if args.video else tmp / "test.mp4"
    if not args.video:
        print("no video given — generating a test clip with ffmpeg…")
        _make_test_video(video, seconds=max(6, int(args.seconds) + 2))

    sock = str(tmp / "mpv.sock")
    mpv_cmd = [
        "mpv",
        f"--input-ipc-server={sock}",
        "--force-window=yes",
        "--keep-open=yes",
        "--osd-level=0",
        "--pause" if args.screenshot else "--loop-file=inf",
        str(video),
    ]
    # Isolated by default so the demo doesn't pull in the animecards/subtitleminer rig (which
    # spams the OSD and races on /tmp/mpv-socket). Pass --use-config to run under your real config.
    if not args.use_config:
        mpv_cmd.insert(1, "--no-config")
    if args.fullscreen:
        mpv_cmd.insert(1, "--fullscreen")
    print("launching:", " ".join(mpv_cmd))
    proc = subprocess.Popen(mpv_cmd)

    try:
        ipc = MpvIPC(sock).connect(timeout=15)
    except TimeoutError as e:
        print("could not reach mpv IPC:", e, file=sys.stderr)
        proc.terminate()
        return 2

    panel = render_panel(load_entry(args.entry), width=args.width, max_height=args.max_height)
    ov = Overlay(ipc)
    reply = ov.show(panel, x=args.x, y=args.y, oid=0)
    print("overlay-add reply:", reply)

    try:
        if args.screenshot:
            time.sleep(1.0)  # let a frame render
            r = ipc.command("screenshot-to-file", args.screenshot, "window")
            print("screenshot reply:", r)
            time.sleep(0.3)
            print("wrote", args.screenshot, "exists:", os.path.exists(args.screenshot))
        else:
            print(f"panel is up for {args.seconds}s — press 'f' in mpv to test fullscreen.")
            time.sleep(args.seconds)
    finally:
        try:
            ov.close()
            ipc.command("quit")
            ipc.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

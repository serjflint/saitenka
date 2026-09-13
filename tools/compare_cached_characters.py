"""Opt-in local corpus runner: mpv reference masks versus production overprint/overpaint.

Run with --execute only in a desktop session. No subtitle extraction or downloading occurs.
All document variants and pixel artifacts remain in the explicitly selected output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import platform
import subprocess
import time
from contextlib import contextmanager
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pysubs2
from character_masks import (
    clusters,
    compare_mask,
    digest,
    green_coverage,
    manifest,
    reference_mask,
    results_summary,
)
from PIL import Image
from saitenka_subtitles import (
    Cue,
    GeometryRequest,
    SubtitleTrackId,
    converted,
    font_names,
    subrip,
)
from saitenka_subtitles.ass import decode_ass_event, parse_ass_event_line
from saitenka_subtitles.ass_geometry import authored_ass_rows_at, prepare_ass_hit_map_frame
from saitenka_subtitles.libass_backend import LibassGeometryBackend
from saitenka_tokenize.japanese import tokenize

from saitenka.app import native_subtitles, subtitle_fonts
from saitenka.app.session.routes import install_session_runtime
from saitenka.app.subtitle_render import (
    DrawRequest,
    color_ladder,
    overpaint_image,
    overprint_payload,
)
from saitenka.app.subtitles import WordBox
from saitenka.mpvio.discover import find_mpv
from saitenka.mpvio.ipc import MpvIPC, default_ipc_path
from saitenka.mpvio.osd import Overlay
from saitenka.version import overlay_version

PROFILE = (
    "--osc=no",
    "--load-scripts=no",
    "--osd-bar=no",
    "--sub-ass-override=no",
    "--sub-ass-scale-with-window=no",
    "--sub-scale=1",
    "--sub-pos=100",
    "--sub-use-margins=yes",
    "--sub-ass-force-margins=no",
    "--sub-ass-video-aspect-override=0",
    "--sub-ass-use-video-data=all",
    "--sub-ass-style-overrides=",
    "--sub-ass-vsfilter-color-compat=no",
    "--blend-subtitles=no",
    "--sub-filter-sdh=no",
    "--target-prim=bt.709",
    "--target-trc=srgb",
    "--icc-profile-auto=no",
    "--dither-depth=no",
)
TRACK = SubtitleTrackId("character-corpus")


def fingerprint(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def implementation_digest() -> str:
    root = Path(__file__).resolve().parents[1]
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return digest(
        {
            name: fingerprint(root / name)
            for name in sorted(set(files.stdout.splitlines()))
            if name.endswith((".py", "uv.lock")) and (root / name).is_file()
        }
    )


def font_inventory() -> dict:
    if platform.system() != "Darwin":
        return {"status": "unqualified-provider-inventory", "platform": platform.platform()}
    roots = [
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path.home() / "Library/Fonts",
        Path.home() / ".config/mpv/fonts",
    ]
    fonts = {
        str(path): fingerprint(path)
        for root in roots
        for path in root.rglob("*")
        if path.suffix.lower() in subtitle_fonts.FONT_EXTENSIONS and path.is_file()
    }
    return {"status": "ambient-font-inventory", "platform": platform.platform(), "files": fonts}


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def source_events(source: str) -> tuple[list[str], list[tuple[int, object]]]:
    lines = source.splitlines(keepends=True)
    events = []
    for index, line in enumerate(lines):
        if line.startswith("Dialogue:"):
            event = parse_ass_event_line(line.rstrip("\r\n"), TRACK, len(events))
            events.append((index, decode_ass_event(event)))
    return lines, events


def recolor(source: str, *, target: tuple[int, int, int] | None = None) -> str:
    """Color only primary fills, retaining all text, override tags and neighboring events."""
    lines, events = source_events(source)
    for event_index, (line_index, decoded) in enumerate(events):
        raw = decoded.source.raw_text
        if decoded.raw_spans is None or decoded.drawings:
            raise ValueError("exact text attribution unavailable")
        insertions = []
        for index, _end in clusters(decoded.text):
            span = decoded.raw_spans[index]
            blue = (
                target is not None and event_index == target[0] and target[1] <= index < target[2]
            )
            insertions.append((span.start, r"{\1c&HFF0000&}" if blue else r"{\1c&H0000FF&}"))
        for offset, tag in reversed(insertions):
            raw = raw[:offset] + tag + raw[offset:]
        prefix = (
            lines[line_index].rstrip("\r\n").rsplit(decoded.source.raw_text, 1)[0]
            if decoded.source.raw_text
            else lines[line_index].rstrip("\r\n")
        )
        lines[line_index] = prefix + raw + "\n"
    return "".join(lines)


def request_for(
    source: str, sample_ms: int, size: tuple[int, int], fonts: subtitle_fonts.FontEnvironment
) -> DrawRequest:
    rows, text = authored_ass_rows_at(source.encode(), TRACK, sample_ms)
    tokens = tokenize(text, strip_furigana=False)
    prepared = prepare_ass_hit_map_frame(
        source.encode(),
        TRACK,
        active_rows=rows,
        text=text,
        tokens=[
            annotation
            for i, token in enumerate(tokens)
            if (
                annotation := native_subtitles._annotation_for_token(
                    token, i, text, 0, lambda _token: False
                )[0]
            )
            is not None
        ],
    )
    palette = native_subtitles._palette_in_frame_units(
        prepared,
        size[1],
        1.0,
        unreachable=fonts.osd_unreachable(font_names.in_document(source.encode())),
    )
    backend = LibassGeometryBackend()
    try:
        snapshot = backend.render(
            GeometryRequest(
                1,
                TRACK,
                prepared.frame_id,
                sample_ms,
                size,
                size,
                prepared.ass,
                palette=palette,
                reserved_rgb=prepared.reserved_rgb,
                keep_coverage=True,
                font_setup=fonts.setup,
                attachments=fonts.attachments,
            )
        )
    finally:
        backend.close()
    boxes = []
    for token in snapshot.tokens:
        renamed = {
            "index": token.token_index,
            "x": token.bounds.x,
            "y": token.bounds.y,
            "w": token.bounds.width,
            "h": token.bounds.height,
        }
        boxes.append(
            WordBox(
                **{
                    field.name: renamed[field.name]
                    if field.name in renamed
                    else getattr(token, field.name)
                    for field in fields(WordBox)
                }
            )
        )
    return DrawRequest(
        text=text,
        lines=[tokens],
        osd=size,
        sub_size=40,
        bg_opacity=0,
        bottom_margin=30,
        secondary_role=False,
        upgrade_pending=False,
        annotation_degraded=False,
        annotation_visible=True,
        hover=-1,
        hover_span=None,
        boxes=boxes,
        styles=[SimpleNamespace(color=(0, 255, 0, 255), underline=None) for _ in tokens],
    )


class Captures:
    def __init__(self, directory: Path, ipc, size: tuple[int, int]):
        self.directory, self.ipc, self.size = directory, ipc, size
        self.overlay = Overlay(ipc)
        self.fonts = subtitle_fonts.resolve(
            expand=ipc.expand_path,
            settings={name: ipc.query(f"options/{name}") for name in subtitle_fonts.FONT_OPTIONS},
            video=directory / "black.mkv",
            cache_dir=directory / "fonts",
        )

    def command(self, *args):
        response = self.ipc.command(*args, timeout=5)
        if response.get("error") != "success":
            raise RuntimeError(f"mpv {args[0]}: {response.get('error', 'missing-reply')}")
        return response.get("data")

    def frame(
        self,
        source: str,
        sample_ms: int,
        request: DrawRequest | None = None,
        *,
        suffix: str = ".ass",
        reference_coverage: np.ndarray | None = None,
    ) -> np.ndarray:
        source_path = self.directory / f"capture{suffix}"
        source_path.write_text(source, encoding="utf-8")
        self.ipc.command("osd-overlay", 61, "none", "")
        self.overlay.hide(62)
        old = self.ipc.query("sid")
        if isinstance(old, int):
            self.ipc.command("sub-remove", old)
        self.command("sub-add", str(source_path), "select")
        self.command("set_property", "sub-delay", 2 - sample_ms / 1000)
        self.command("seek", 2, "absolute+exact")
        deadline = time.monotonic() + 5
        while self.command("get_property", "seeking"):
            if time.monotonic() >= deadline:
                raise TimeoutError("seek did not settle")
            time.sleep(0.005)
        if request is not None:
            payload = overprint_payload(request)
            self.command("osd-overlay", 61, "ass-events", payload, *self.size, 1)
            raster = overpaint_image(request)
            if raster is not None:
                self.overlay.show(Image.fromarray(raster.rgba), raster.x, raster.y, oid=62)
        if reference_coverage is not None:
            rgba = np.zeros((*reference_coverage.shape, 4), dtype=np.uint8)
            rgba[:, :, 1] = 255
            rgba[:, :, 3] = reference_coverage
            self.overlay.show(Image.fromarray(rgba), 0, 0, oid=62)
        target = self.directory / "capture.png"
        response = self.ipc.command("screenshot-to-file", str(target), "window")
        if response.get("error") != "success":
            raise RuntimeError("screenshot refused")
        with Image.open(target) as image:
            result = np.array(image.convert("RGB"))
        if result.shape[:2] != (self.size[1], self.size[0]):
            raise ValueError("actual screenshot dimensions differ from pinned geometry")
        return result


@contextmanager
def captures(directory: Path, size: tuple[int, int], video: Path):
    clip = directory / "black.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={size[0]}x{size[1]}:d=8:r=2",
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-map",
            "1:t?",
            "-c:t",
            "copy",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    socket = default_ipc_path("character-" + digest(str(directory))[:12])
    stderr = (directory / "mpv-stderr.log").open("wb")
    process = subprocess.Popen(
        [
            find_mpv() or "mpv",
            "--no-config",
            "--pause",
            "--keep-open=yes",
            "--force-window=yes",
            "--osd-level=0",
            "--sub-visibility=yes",
            f"--log-file={directory / 'mpv.log'}",
            f"--geometry={size[0]}x{size[1]}",
            "--hidpi-window-scale=no",
            *PROFILE,
            f"--input-ipc-server={socket}",
            str(clip),
        ],
        stdout=subprocess.DEVNULL,
        stderr=stderr,
    )
    ipc = gateway = capture = None
    try:
        ipc = MpvIPC(socket).connect(timeout=10)
        gateway = install_session_runtime(ipc, startup_hint=False)
        capture = Captures(directory, ipc, size)
        yield capture
    finally:
        if capture is not None:
            capture.overlay.close()
        if gateway is not None:
            gateway.close()
        if ipc is not None:
            ipc.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        stderr.close()


def converted_source(source: str, size: tuple[int, int]) -> str:
    parsed = pysubs2.SSAFile.from_string(source, format_="srt")
    markup = subrip.markup_by_cue(source)
    rows = []
    for event in parsed:
        raw = markup.get((event.start, event.end))
        if raw is None:
            raise ValueError("SubRip event attribution is ambiguous")
        row = subrip.dialogue_row(Cue(event.start / 1000, event.end / 1000, event.plaintext), raw)
        if row is None:
            raise ValueError("SubRip markup conversion is unsupported")
        rows.append(row)
    return converted.document("\n".join(rows), converted.RenderSpace(*size)).decode()


def coordinate_devices(source: str, coordinate: dict, request: DrawRequest) -> dict:
    _lines, events = source_events(source)
    offset = 0
    selected = None
    texts = []
    for index, (_line, event) in enumerate(events):
        identity = event.source.identity
        if not identity.start_ms <= coordinate["sample_ms"] < identity.end_ms:
            continue
        if index == coordinate["event"]:
            selected = offset + coordinate["start"], offset + coordinate["end"]
        texts.append(event.text)
        offset += len(event.text) + 1
    if selected is None or "\n".join(texts) != request.text:
        return {"device_scope": "unknown-semantic-projection", "devices": []}
    tokens = [token for line in request.lines for token in line]
    owners = {
        index
        for index, token in enumerate(tokens)
        if token.start < selected[1] and selected[0] < token.end
    }
    boxes = [box for box in request.boxes if box.index in owners]
    return {
        "device_scope": "cluster-to-production-token",
        "token_indices": sorted(owners),
        "devices": list(color_ladder(replace(request, boxes=boxes)).devices),
        "font_families": sorted({box.font_name for box in boxes}),
    }


def coordinate_result(
    capture: Captures, source: str, coordinate: dict, *, suffix: str = ".ass"
) -> tuple[dict, tuple]:
    if coordinate["reason"]:
        return {"verdict": "unsupported", "reason": coordinate["reason"]}, ()
    native = source
    if suffix == ".srt":
        source = converted_source(source, capture.size)
    active, _text = authored_ass_rows_at(source.encode(), TRACK, coordinate["sample_ms"])
    if any(tag in active for tag in (r"\t(", r"\k", r"\K", r"\move(", r"\fad")):
        return {"verdict": "inconclusive", "reason": "animated-document-needs-state-sampling"}, ()
    time_ms = coordinate["sample_ms"]
    original = capture.frame(native, time_ms, suffix=suffix)
    repeated = capture.frame(native, time_ms, suffix=suffix)
    if not np.array_equal(original, repeated):
        return {"verdict": "inconclusive", "reason": "reference-capture-not-repeatable"}, ()
    if suffix == ".srt" and not np.array_equal(original, capture.frame(source, time_ms)):
        return {
            "verdict": "inconclusive",
            "reason": "converted-document-pixels-disagree-with-mpv",
        }, ()
    request = request_for(source, time_ms, capture.size, capture.fonts)
    ours = capture.frame(native, time_ms, request, suffix=suffix)
    red = capture.frame(recolor(source), time_ms)
    blue = capture.frame(
        recolor(source, target=(coordinate["event"], coordinate["start"], coordinate["end"])),
        time_ms,
    )
    mask, reason = reference_mask(original, red, blue)
    if reason:
        return {"verdict": "inconclusive", "reason": reason}, (original, ours, mask)
    expected = capture.frame(native, time_ms, suffix=suffix, reference_coverage=original[:, :, 0])
    context = green_coverage(expected)
    if not np.array_equal(context > 32, original[:, :, 0] > 32):
        return {
            "verdict": "inconclusive",
            "reason": "calibration-does-not-preserve-native-ink",
        }, (original, ours, mask)
    verdict = compare_mask(np.where(mask > 0, context, 0), green_coverage(ours), context)
    verdict["ownership"] = "isolated-native-ink-cell-with-whole-cue-check"
    verdict.update(coordinate_devices(source, coordinate, request))
    return verdict, (original, ours, mask)


def write_index(directory: Path, summary: dict) -> None:
    rows = [
        f"<tr><td>{html.escape(row['key'])}</td><td>{html.escape(row.get('text', ''))}</td>"
        f"<td>{row['verdict']}</td><td>{html.escape(row.get('reason', ''))}</td>"
        f"<td>{' '.join('<a href="./' + html.escape(name, quote=True) + '">' + html.escape(name) + '</a>' for name in row.get('artifacts', []))}</td></tr>"
        for row in summary["results"]
    ]
    (directory / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Character comparison</title>"
        "<h1>Local character comparison</h1><pre>"
        + html.escape(json.dumps(summary["counts"]))
        + "</pre><table>"
        + "".join(rows)
        + "</table>",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cached-subtitles", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", nargs=2, type=int, default=(1280, 720))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--only-kanji",
        action="store_true",
        help="pilot selection; other coordinates remain in the denominator",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="bounded pilot; other coordinates stay unattempted"
    )
    args = parser.parse_args()
    if (
        args.output.exists()
        and any(args.output.iterdir())
        and not (args.output / "manifest.json").is_file()
    ):
        parser.error("output must be new, empty, or a matching corpus checkpoint")
    if min(args.size) <= 0 or args.size[0] * args.size[1] > 16_777_216 or args.limit < 0:
        parser.error("invalid geometry or pilot limit")
    source = args.cached_subtitles.read_text(encoding="utf-8-sig")
    parsed = pysubs2.SSAFile.from_string(source, format_=args.cached_subtitles.suffix.lstrip("."))
    events = [(event.start, event.end, event.plaintext) for event in parsed]
    if args.cached_subtitles.suffix.lower() == ".ass":
        _lines, decoded = source_events(source)
        events = [
            (event.source.identity.start_ms, event.source.identity.end_ms, event.text)
            for _line, event in decoded
        ]
    version = subprocess.run(
        [find_mpv() or "mpv", "--version"], capture_output=True, text=True, check=True, timeout=10
    ).stdout
    frozen = manifest(
        events,
        {
            "subtitle_sha256": fingerprint(args.cached_subtitles),
            "video_sha256": fingerprint(args.video),
            "format": args.cached_subtitles.suffix,
            "variant": args.cached_subtitles.name,
            "track_metadata": "unknown; explicit caller selection",
            "mpv": version,
            "saitenka": overlay_version(),
            "surface": args.size,
            "profile": list(PROFILE),
            "runner_sha256": fingerprint(Path(__file__)),
            "implementation_sha256": implementation_digest(),
            "fonts": font_inventory(),
            "attachments": "copied from fingerprinted video to black reference container",
            "qualification": "local midpoint profile; not whole-session fidelity certification",
        },
    )
    args.output.mkdir(parents=True, exist_ok=True)
    frozen_path = args.output / "manifest.json"
    if frozen_path.exists() and json.loads(frozen_path.read_text(encoding="utf-8")) != frozen:
        parser.error("resume manifest differs; choose a new output directory")
    atomic_json(frozen_path, frozen)
    result_path = args.output / "results.json"
    previous = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    if previous and previous.get("manifest_sha256") != frozen["manifest_sha256"]:
        parser.error("checkpoint manifest digest differs")
    results = {
        row["key"]: row for row in previous.get("results", []) if row["verdict"] != "unattempted"
    }
    if args.execute:
        try:
            execute(args, source, frozen, results)
        except (
            OSError,
            ValueError,
            RuntimeError,
            TimeoutError,
            subprocess.SubprocessError,
        ) as error:
            pending = [row for row in frozen["coordinates"] if row["key"] not in results]
            if args.only_kanji:
                pending = [row for row in pending if row["kanji"]]
            for row in pending[: args.limit] if args.limit else pending:
                results[row["key"]] = {
                    "verdict": "inconclusive",
                    "reason": f"capture-startup-{type(error).__name__}",
                }
        finally:
            atomic_json(result_path, results_summary(frozen, results))
    summary = results_summary(frozen, results)
    atomic_json(result_path, summary)
    write_index(args.output, summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, indent=2))
    return int(
        bool(
            not summary["counts"]["passed"]
            or frozen["provenance"]["fonts"]["status"] != "ambient-font-inventory"
            or summary["counts"]["failed"]
            or summary["counts"]["inconclusive"]
            or summary["counts"]["unattempted"]
        )
    )


def execute(args, source: str, frozen: dict, results: dict) -> None:
    pending = [row for row in frozen["coordinates"] if row["key"] not in results]
    if args.only_kanji:
        pending = [row for row in pending if row["kanji"]]
    selected = pending[: args.limit] if args.limit else pending
    if args.cached_subtitles.suffix.lower() not in {".ass", ".srt"}:
        for row in selected:
            results[row["key"]] = {
                "verdict": "inconclusive",
                "reason": "unsupported-subtitle-format",
            }
        return
    with captures(args.output, tuple(args.size), args.video) as capture:
        for row in selected:
            try:
                verdict, images = coordinate_result(
                    capture, source, row, suffix=args.cached_subtitles.suffix.lower()
                )
            except (OSError, ValueError, RuntimeError, TimeoutError) as error:
                verdict, images = (
                    {
                        "verdict": "inconclusive",
                        "reason": type(error).__name__,
                        "detail": str(error)[:200],
                    },
                    (),
                )
            results[row["key"]] = verdict
            if images and len(list(args.output.glob("*-reference.png"))) < 20:
                verdict["artifacts"] = []
                for name, pixels in zip(("reference", "ours", "mask"), images, strict=True):
                    filename = f"{row['key'].replace(':', '-')}-{name}.png"
                    Image.fromarray(pixels).save(args.output / filename)
                    verdict["artifacts"].append(filename)
            atomic_json(args.output / "results.json", results_summary(frozen, results))


if __name__ == "__main__":
    raise SystemExit(main())

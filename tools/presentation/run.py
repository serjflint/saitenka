"""Qualify the installed CLI on a redistributable fixture, from time zero and without focus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from saitenka.app.frame_presentation import (
    check_provenance,
    frames,
    publication_records,
    qualify,
    read_events,
)
from saitenka.app.report_schema import startup_source_identity

FIXTURE = Path(__file__).with_name("synthetic.ass")
POPULATION = (
    (6000, 8000, "猫"),
    (8000, 10000, "犬"),
    (12000, 14000, "猫\n犬"),
    (16000, 18000, "猫"),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(output: Path, binary: Path, source: str) -> Path:
    config = output / "overlay.toml"
    config.write_text(
        f"mpv_path = {json.dumps(str(binary))}\nprefetch_lookahead = 4\n"
        "[telemetry]\nenabled = true\n[subtitle_geometry]\nnative_visible = true\n"
        f'source = "{source}"\ncoloring = "whole-cue-osd"\n',
        encoding="utf-8",
    )
    media = output / "fixture.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1280x720:r=24",
            "-t",
            "20",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-y",
            str(media),
        ],
        check=True,
        timeout=30,
    )
    return config


def launch(
    output: Path,
    binary: Path,
    cli: Path,
    config: Path,
    delay: float,
    *,
    navigation: bool = False,
    input_changes: bool = False,
) -> dict:
    import saitenka

    assert saitenka.__file__ is not None
    fonts = Path(saitenka.__file__).parent / "assets/fonts"
    command = [
        str(cli),
        "run",
        "--use-config",
        "--config",
        str(config),
        "--known",
        "猫,犬",
        "--no-mine",
        "--sub-file",
        str(FIXTURE.resolve()),
        "--start",
        "0",
    ]
    options = [
        "--no-config",
        "--focus-on=never",
        "--input-terminal=no",
        "--keep-open=no",
        "--resume-playback=no",
        "--save-position-on-quit=no",
        "--sub-ass-override=no",
        "--blend-subtitles=no",
        "--aid=no",
        "--vo=gpu-next",
        "--hwdec=no",
        f"--sub-delay={delay}",
        f"--sub-fonts-dir={fonts}",
        f"--osd-fonts-dir={fonts}",
    ]
    if navigation:
        options.append(f"--script={Path(__file__).with_name('navigation.lua').resolve()}")
    if input_changes:
        options.append(f"--script={Path(__file__).with_name('input-changes.lua').resolve()}")
    command.extend(f"--mpv-arg={option}" for option in options)
    command.append(str(output / "fixture.mkv"))
    env = dict(
        os.environ,
        SAITENKA_CACHE_DIR=str(output / "cache"),
        SAITENKA_DATA_DIR=str(output / "data"),
        SAITENKA_CONFIG=str(config),
        PYTHON_GIL="0",
    )
    receipt = {
        "command": command,
        "binary_sha256": digest(binary),
        "config_sha256": digest(config),
        "fixture_sha256": digest(FIXTURE),
        "font_sha256": digest(fonts / "NotoSansJP.ttf"),
        "consumer_source": startup_source_identity(),
        "installed_entrypoint": True,
        "navigation": navigation,
        "input_changes": input_changes,
        "wall_start_ns": time.time_ns(),
        "runner_sha256": digest(Path(__file__)),
    }
    with (output / "console.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            receipt["returncode"] = process.wait(timeout=40)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            receipt["timeout"] = True
    receipt["wall_end_ns"] = time.time_ns()
    return receipt


def evidence(output: Path) -> tuple[dict, dict, list[dict], dict]:
    cache = output / "cache"
    receipt = json.loads(next(cache.glob("mpv-frame-*.json")).read_text(encoding="utf-8"))
    trace_path = next(
        p for p in (cache / "telemetry").glob("trace-*.json") if not p.name.endswith(".health.json")
    )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    health = json.loads(trace_path.with_suffix(".health.json").read_text(encoding="utf-8"))
    spans = trace["traceEvents"]
    stages = publication_records(spans)
    return receipt, health, stages, trace


def analyze(output: Path, run: dict, *, stock: bool, delay: float) -> dict:
    receipt, health, stages, trace = evidence(output)
    if trace.get("otherData", {}).get("session") != receipt["session"]:
        raise ValueError("consumer trace session mismatch")
    if (
        receipt["consumer_source"]["sha256"] != run["consumer_source"]["sha256"]
        or receipt["binary_sha256"] != run["binary_sha256"]
    ):
        raise ValueError("installed consumer or binary differs from requested source")
    check_provenance(
        receipt,
        health,
        {
            "session": receipt["session"],
            "binary_sha256": run["binary_sha256"],
            "source_sha256": run["consumer_source"]["sha256"],
        },
    )
    if stock:
        outcomes = [
            e["args"] for e in trace["traceEvents"] if e.get("name") == "subtitle_color_outcome"
        ]
        expected = {
            (hashlib.blake2s(text.encode(), digest_size=16).hexdigest(), start)
            for start, _, text in POPULATION
        }
        completed = {
            (r["text_hash"], r.get("cue_start_ms"))
            for r in outcomes
            if r.get("color_status") == "complete"
        }
        scanning = scan_coverage(
            trace,
            [
                {
                    "text_hash": h,
                    "start_ms": start,
                    "wall_start_ns": run["wall_start_ns"],
                    "wall_end_ns": run["wall_end_ns"],
                }
                for h, start in expected
            ],
        )
        return {
            "qualified": expected <= completed and scanning and not receipt["timed_osd"],
            "scanning": scanning,
            "endpoint": "reactive-command-acceptance-not-frame-alignment",
        }
    api = receipt.get("timed_osd_api", [])
    expected_args = [
        ("id", "Integer64"),
        ("start_ms", "Integer64"),
        ("end_ms", "Integer64"),
        ("data", "String"),
        ("res_x", "Integer"),
        ("res_y", "Integer"),
        ("z", "Integer"),
    ]
    if (
        len(api) != 1
        or [(a.get("name"), a.get("type")) for a in api[0].get("args", [])] != expected_args
    ):
        raise ValueError("incompatible timed OSD API")
    raw = (output / "cache" / receipt["trace"]).read_text(encoding="utf-8")
    observed = frames(read_events(raw))
    contexts = [s for s in stages if s["event"] == "context"]
    if len(contexts) != 1 and not run.get("input_changes"):
        raise ValueError("unexpected source/clock transition in natural-playback scenario")
    context = contexts[0]
    manifest = {
        "schema": 1,
        "evidence_complete": True,
        "provenance": {
            "session": receipt["session"],
            "binary_sha256": receipt["binary_sha256"],
            "source_sha256": receipt["consumer_source"]["sha256"],
        },
        "appearances": [
            {
                "occurrence": i,
                "text_hash": hashlib.blake2s(text.encode(), digest_size=16).hexdigest(),
                "start_ms": start,
                "end_ms": end,
                "video_start_ms": start + round(delay * 1000),
                "video_end_ms": end + round(delay * 1000),
                "epoch": context["epoch"],
                "connection_epoch": context["connection_epoch"],
                "owner": receipt["ipc_peer"],
                "wall_start_ns": run["wall_start_ns"],
                "wall_end_ns": run["wall_end_ns"],
                "requested": 1,
                "required_warm": True,
            }
            for i, (start, end, text) in enumerate(POPULATION)
        ],
    }
    if run.get("navigation"):
        manifest["appearances"] = navigation_appearances(manifest["appearances"], observed, trace)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    result = qualify(raw, manifest, stages)
    result["scanning"] = scan_coverage(trace, manifest["appearances"])
    result["libass"] = producer_identity(receipt)
    result["qualified"] = result["qualified"] and result["scanning"]
    if run.get("input_changes"):
        result["scenario_complete"] = input_changes_complete(
            run, stages, observed, (output / "console.log").read_text(encoding="utf-8")
        )
    return result


def input_changes_complete(run: dict, stages: list[dict], observed, console: str) -> bool:
    first_ack = min((r["captured_ns"] for r in stages if r["event"] == "ack"), default=float("inf"))
    reasons = {
        r.get("reason")
        for r in stages
        if r["event"] == "invalidate" and r["captured_ns"] > first_ack
    }
    return (
        run.get("returncode") == 0
        and "saitenka-presentation-inputs-complete" in console
        and any(f.video_ms >= 19000 for f in observed)
        and {"sub-delay", "suspend", "osd-dimensions", "source-replaced"} <= reasons
    )


def producer_identity(receipt: dict) -> dict:
    binary = Path(receipt["binary"]).resolve()
    build = json.loads((binary.parents[2] / "receipt.json").read_text(encoding="utf-8"))
    if (
        build.get("libass_linkage") != "static"
        or build["binary_sha256"] != receipt["binary_sha256"]
        or build["libass_library"] not in build.get("link_command", "")
    ):
        raise ValueError("static libass producer identity differs from build")
    return {"sha256": build["libass_sha256"], "scope": "statically-linked-archive"}


def scan_coverage(trace: dict, appearances: list[dict]) -> bool:
    events = trace["traceEvents"]
    targets = {
        e["args"]["occurrence"]: e["args"]
        for e in events
        if e.get("name") == "subtitle_color_target"
    }
    draws = [
        e
        for e in events
        if e.get("name") == "subtitle_draw"
        and e["args"].get("path") == "native"
        and e["args"].get("scan_tokens", 0) == e["args"].get("tokens", -1)
        and e["args"].get("tokens", 0) > 0
    ]
    return all(
        any(
            appearance["wall_start_ns"] <= e["ts"] * 1000 < appearance["wall_end_ns"]
            and (target := targets.get(e["args"].get("occurrence"), {})).get("text_hash")
            == appearance["text_hash"]
            and target.get("cue_start_ms") == appearance["start_ms"]
            for e in draws
        )
        for appearance in appearances
    )


def navigation_appearances(population: list[dict], observed, trace: dict) -> list[dict]:
    intents = [
        e
        for e in trace["traceEvents"]
        if e.get("name") == "sub_seek" and "target_start" in e.get("args", {})
    ]
    expected = [8.0, 8.0, 6.0]
    if [e["args"]["target_start"] for e in intents] != expected:
        raise ValueError("Next/Replay/Previous destinations differ from declared scenario")
    landings = []
    for i, (intent, target) in enumerate(zip(intents, expected, strict=True)):
        end = intents[i + 1]["ts"] * 1000 if i + 1 < len(intents) else float("inf")
        landing = next(
            (
                f
                for f in observed
                if intent["ts"] * 1000 <= f.wall < end
                and target * 1000 <= f.video_ms <= target * 1000 + 100
            ),
            None,
        )
        if landing is None:
            raise ValueError("navigation did not reach its declared video frame")
        landings.append(landing)
    starts = [observed[0], *landings]
    rows = []
    for i, begin in enumerate(starts):
        finish = starts[i + 1] if i + 1 < len(starts) else None
        expected_end = (6500, 8500, 9000, None)[i]
        for cue in population:
            if cue["video_end_ms"] <= begin.video_ms or (
                expected_end is not None and cue["video_start_ms"] > expected_end
            ):
                continue
            rows.append(
                {
                    **cue,
                    "occurrence": len(rows),
                    "wall_start_ns": begin.wall,
                    "wall_end_ns": finish.wall if finish else cue["wall_end_ns"],
                    "entry_composition": begin.composition if i else None,
                    "exit_composition": finish.composition if finish else None,
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mpv", type=Path, required=True)
    parser.add_argument("--cli", type=Path, default=Path.home() / ".local/bin/saitenka")
    parser.add_argument("--source", choices=("auto", "shadow"), default="auto")
    parser.add_argument("--stock", action="store_true")
    parser.add_argument("--navigation", action="store_true")
    parser.add_argument("--input-changes", action="store_true")
    parser.add_argument("--delay", type=float, default=0)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    config = prepare(output, args.mpv.resolve(), args.source)
    receipt = launch(
        output,
        args.mpv.resolve(),
        args.cli.resolve(),
        config,
        args.delay,
        navigation=args.navigation,
        input_changes=args.input_changes,
    )
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    try:
        result = analyze(output, receipt, stock=args.stock, delay=args.delay)
    except (OSError, ValueError, KeyError, StopIteration) as error:
        result = {"qualified": False, "status": "unknown", "reason": str(error)}
    result["qualified"] = result["qualified"] and receipt.get("returncode") == 0
    (output / "qualification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

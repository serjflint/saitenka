"""Real-mpv cue replay with phase checkpoints and the production color-accounting endpoint.

Run via ``poe cue-replay`` or ``poe cue-pyspy``. This diagnostic uses the live harness's small
synthetic dictionary/scorer, not the user's dictionary configuration. No pytest runner is involved.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shlex
import signal
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))


def navigation_steps(scenario: str, repetitions: int) -> list[int]:
    cycle: tuple[int, ...] = (0,) if scenario == "replay" else (1, -1)
    return list[int](cycle) * repetitions


def summarize(attempts: list[dict]) -> dict:
    statuses = Counter(row.get("status", "interrupted") for row in attempts)
    complete = sorted(
        row["accounting"]["complete_ms"] for row in attempts if row.get("status") == "complete"
    )
    command_complete = sorted(
        row["command_to_complete_ack_ms"]
        for row in attempts
        if row.get("status") == "complete" and "command_to_complete_ack_ms" in row
    )
    return {
        "attempts": len(attempts),
        "outcomes": dict(statuses),
        "complete_ack_p95_ms_among_completed": (
            complete[math.ceil(len(complete) * 0.95) - 1] if complete else None
        ),
        "budget_qualification": False,
        "command_to_complete_ack_p95_ms": (
            command_complete[math.ceil(len(command_complete) * 0.95) - 1]
            if command_complete
            else None
        ),
    }


class ReplayTimeout(BaseException):
    """Escape application recovery handlers without turning a deadline into a silent retry."""


class Receipt:
    """Checkpoint before blocking operations so interruption cannot leave an older run's result."""

    def __init__(self, directory: Path, metadata: dict) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "result.json"
        self.started = time.monotonic()
        self.data: dict = {"metadata": metadata, "status": "running", "phases": [], "attempts": []}
        self.save()

    def save(self) -> None:
        self.data["summary"] = summarize(self.data["attempts"])
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def phase(self, name: str, **fields) -> None:
        self.data["phases"].append(
            {"name": name, "elapsed_ms": (time.monotonic() - self.started) * 1000, **fields}
        )
        self.save()
        print(f"{name}: {self.path}", flush=True)


@contextmanager
def operation_deadline(seconds: float, receipt: Receipt | None = None):
    def expired(_signal, _frame):
        if receipt is not None:
            receipt.data.update(status="failed", error="benchmark operation timed out")
            receipt.save()
        raise ReplayTimeout("benchmark operation timed out")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def pump_for(session, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        session.pump()
        time.sleep(0.001)


def await_first_color(session, receipt: Receipt) -> None:
    tracker = session.graph.subtitle_presentation.color_telemetry
    while True:
        session.pump()
        current = tracker.current
        if current is not None and current.snapshot(time.monotonic()).status == "complete":
            receipt.phase("first-cue-ack", accounting=asdict(current.snapshot(time.monotonic())))
            return
        time.sleep(0.001)


def replay(session, ipc, args, receipt: Receipt) -> None:
    from saitenka.app.subtitle_intents import SubtitleCommand

    tracker = session.graph.subtitle_presentation.color_telemetry
    receipt.phase("replay-start", runtime=runtime_state(session, ipc))
    for delta in navigation_steps(args.scenario, args.repetitions):
        row = {"index": len(receipt.data["attempts"]), "delta": delta, "status": "interrupted"}
        receipt.data["attempts"].append(row)
        receipt.save()
        started = time.monotonic()
        with operation_deadline(10 + args.dwell, receipt):
            command = {
                -1: SubtitleCommand.NAVIGATE_PREVIOUS,
                0: SubtitleCommand.REPLAY_CUE,
                1: SubtitleCommand.NAVIGATE_NEXT,
            }[delta]
            session.graph.stateless_commands.run(command)
            current = tracker.current
            occurrence = None if current is None else current.occurrence
            row.update(command=command.value, occurrence=occurrence)
            pump_for(session, args.dwell)
            current = tracker.current
            if current is not None and current.occurrence == occurrence:
                outcome = asdict(current.snapshot(time.monotonic()))
                row.update(status=outcome["status"], accounting=outcome)
                if outcome["complete_ms"] is not None:
                    row["command_to_complete_ack_ms"] = (
                        current.started - started
                    ) * 1000 + outcome["complete_ms"]
            else:
                row["status"] = "superseded"
        row["window_ms"] = (time.monotonic() - started) * 1000
        receipt.save()
    receipt.phase("replay-end", runtime=runtime_state(session, ipc))


def runtime_state(session, ipc) -> dict:
    return {
        "routing_census": ipc.session_runtime_census(),
        "tooltip_prefetch": asdict(session.graph.tooltip.prefetch_snapshot),
    }


def require_prefetch_success(snapshot) -> None:
    if snapshot.failed or not snapshot.succeeded:
        raise RuntimeError("prefetch did not complete successfully; inspect telemetry")


def await_prefetch(session, receipt: Receipt) -> None:
    while True:
        snapshot = session.graph.tooltip.prefetch_snapshot
        if snapshot.failed or snapshot.succeeded:
            receipt.phase("prefetch-readiness", prefetch=asdict(snapshot))
            require_prefetch_success(snapshot)
            return
        session.pump()
        time.sleep(0.001)


def profiler_pause(args, receipt: Receipt) -> None:
    profiler = Path(sys.executable).parent / "py-spy"
    command = [
        "sudo",
        str(profiler),
        "record",
        "--pid",
        str(os.getpid()),
        "--threads",
        "--rate",
        "100",
        "--format",
        "speedscope",
        "--output",
        str(args.output / "profile.speedscope.json"),
    ]
    receipt.phase("awaiting-profiler")
    print("In another terminal:\n" + shlex.join(command), flush=True)
    input("Press Enter once py-spy is recording. ")


def run(args, receipt: Receipt) -> None:
    from live_harness import LayoutLiveOptions, live_reader
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords

    from saitenka.app.scoring import Coloring, Palette

    def phase(name):
        receipt.phase(name)
        if name == "cleanup":
            signal.setitimer(signal.ITIMER_REAL, 10)

    layout = LayoutLiveOptions(
        source=args.source,
        coloring=args.coloring,
        mpv_path=str(args.mpv),
        extra_args=("--aid=no", "--sub-ass-override=no", "--blend-subtitles=no", "--sub-scale=1"),
        media=args.media,
        subtitles=args.subtitles,
        start_seconds=args.start,
        prefetch=args.prefetch == "on",
        phase=phase,
    )
    scorer = Coloring(
        Scorer(known=KnownWords.from_set(["猫"]), enable_freq=False, enable_jlpt=False), Palette()
    )
    receipt.phase("startup")
    with (
        operation_deadline(30, receipt),
        live_reader(
            native_visible=True,
            scorer=scorer,
            layout=layout,
        ) as (_tmp, session, ipc),
    ):
        await_first_color(session, receipt)
        if layout.prefetch:
            await_prefetch(session, receipt)
        pump_for(session, 1.0)
        signal.setitimer(signal.ITIMER_REAL, 0)
        receipt.phase(
            "ready",
            runtime=runtime_state(session, ipc),
            gil_enabled=getattr(sys, "_is_gil_enabled", lambda: True)(),
        )
        if args.profile:
            profiler_pause(args, receipt)
        replay(session, ipc, args, receipt)
        if layout.prefetch:
            require_prefetch_success(session.graph.tooltip.prefetch_snapshot)
        if args.profile:
            input("Replay finished. Stop py-spy in its terminal, then press Enter for cleanup. ")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--media", type=Path, required=True)
    result.add_argument("--subtitles", type=Path, required=True)
    result.add_argument("--mpv", type=Path, required=True)
    result.add_argument(
        "--start", type=float, required=True, help="Playback position inside a colorable cue."
    )
    result.add_argument(
        "--coloring",
        choices=("legacy", "whole-cue-osd", "whole-cue-auto", "whole-cue-overpaint"),
        default="legacy",
    )
    result.add_argument("--source", choices=("auto", "shadow"), default="auto")
    result.add_argument("--scenario", choices=("replay", "back-forth"), default="replay")
    result.add_argument(
        "--repetitions", type=int, default=30, help="Replay count or forward/backward pair count."
    )
    result.add_argument(
        "--dwell",
        type=float,
        default=0.8,
        help="Observation window after each navigation, seconds.",
    )
    result.add_argument("--prefetch", choices=("on", "off"), default="on")
    result.add_argument(
        "--profile",
        action="store_true",
        help="Wait for profiler attachment after first-cue readiness.",
    )
    result.add_argument(
        "--output", type=Path, help="New output directory; existing results are never overwritten."
    )
    return result


def main(argv: list[str] | None = None) -> int:
    options = parser()
    args = options.parse_args(argv)
    if args.repetitions < 1 or not math.isfinite(args.dwell) or args.dwell <= 0:
        options.error("repetitions and dwell must be positive")
    if not math.isfinite(args.start) or args.start < 0:
        options.error("start must be finite and nonnegative")
    for name in ("media", "subtitles", "mpv"):
        path = getattr(args, name).expanduser().resolve()
        if not path.is_file():
            options.error(f"{name} is not a file: {path}")
        setattr(args, name, path)
    if not os.access(args.mpv, os.X_OK):
        options.error(f"mpv is not executable: {args.mpv}")
    args.output = (
        args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="cue-replay-"))
    )
    if (args.output / "result.json").exists():
        options.error("output already contains result.json; choose a new directory")
    receipt = Receipt(
        args.output,
        {
            **{
                key: str(value) if isinstance(value, Path) else value
                for key, value in vars(args).items()
            },
            "python": sys.version,
            "platform": platform.platform(),
            "gil_enabled": getattr(sys, "_is_gil_enabled", lambda: True)(),
            "endpoint": "mpv-acknowledgment",
            "dictionary": "live-harness MiniDS",
        },
    )
    from saitenka.app.config import TelemetryOptions
    from saitenka.app.telemetry import configure, shutdown

    try:
        configure(TelemetryOptions(enabled=True, export_dir=str(args.output / "telemetry")))
        run(args, receipt)
        if receipt.data["status"] != "failed":
            receipt.data["status"] = "finished"
    except (Exception, KeyboardInterrupt, ReplayTimeout) as error:
        receipt.data.update(status="failed", error=f"{type(error).__name__}: {error}")
        print(receipt.data["error"], file=sys.stderr)
    finally:
        receipt.save()
        shutdown()
    print(json.dumps(receipt.data["summary"], indent=2), flush=True)
    return 0 if receipt.data["status"] == "finished" else 1


if __name__ == "__main__":
    raise SystemExit(main())

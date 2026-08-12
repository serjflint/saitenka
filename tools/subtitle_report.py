# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Distil the SUBTITLE-PIPELINE story out of one or more `saitenka report` bundles.

Where `trace_report.py` is the perf/rendering view, this is the subtitle-timing view: it pulls the
`subtitle.fetch` / `subtitle.reslot` / `subtitle.resync` spans (and the matching `overlay.log` lines)
and turns each into a one-line diagnosis — which release was picked, which track the re-slot selected,
and whether a resync actually moved the cues onto the embedded reference or shipped a silent no-op.

The `subtitle.resync` spans carry TEXT-FREE integer cue fingerprints (`src_cue_ms` / `out_cue_ms` /
`ref_cue_ms`), so `--json` emits exactly the vectors needed to seed an offline, copyright-free
regression test from a real failure — one record per resync. Pass any number of report zips or
unzipped dirs (or a bare `trace.json`):

    uv run tools/subtitle_report.py ~/.local/share/saitenka/reports/saitenka-report-*.zip
    uv run tools/subtitle_report.py --json report-A.zip report-B.zip > seeds.json

There's also a REPRODUCE mode: point it at a local episode + its cached sub (a file, or the subtitles
cache dir to auto-match by video name) and it re-runs the REAL resync pipeline — extract the embedded
reference, align, and print the same record a trace would carry, plus the exact-encode ground truth. It
imports the `overlay` package, so run that mode in the project env with `uv run python`:

    uv run python tools/subtitle_report.py --video EP.mkv --sub ~/Library/Caches/saitenka/subtitles

The report mode is stdlib-only (reuses the loaders from the sibling `trace_report.py`); reproduce lazily
imports `saitenka.app.resync` so there is one alignment implementation, never a drifting copy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import itertools

import trace_report as tr

_SUB_SPAN_NAMES = ("subtitle.fetch", "subtitle.reslot", "subtitle.resync")


def subtitle_spans(events: list[dict]) -> list[dict]:
    """Every complete (ph==X) subtitle-pipeline span, in start-time order."""
    spans = [e for e in events if e.get("ph") == "X" and e.get("name") in _SUB_SPAN_NAMES]
    return sorted(spans, key=lambda s: s.get("ts", 0.0))


def _first(v) -> int | None:
    return v[0] if v else None


def diagnose_resync(a: dict) -> str:
    """One-line verdict for a `subtitle.resync` span from its attributes alone."""
    outcome = a.get("outcome")
    if outcome == "unavailable":
        return "NO ALIGNER — install alass or ensure uvx (subs left raw)"
    tool, reference = a.get("tool", "?"), a.get("reference", "?")
    if outcome == "failed":
        where = f"{tool} vs {reference}{a.get('reference_fmt', '')}"
        return f"ALIGNER FAILED ({where}) — {a.get('fail_reason', 'subs left raw')}"
    src, out = a.get("src_cue_ms") or [], a.get("out_cue_ms") or []
    shift = a.get("shift_ms")
    shift_txt = f"{shift:+d}ms" if isinstance(shift, int) else "?ms"
    head = f"{tool} vs {reference}"
    if src and out and src == out:
        return (
            f"{head} — NO-OP: output == input (shift {shift_txt}) — already aligned OR a tool no-op"
        )
    # NOTE: we deliberately do NOT compare the first src/ref cue — the two tracks often caption
    # different opening events (JP SFX vs the EN's first spoken line), so cue-1↔cue-1 is a false metric.
    # A uniform shift that's right post-OP but early pre-OP is a SPLIT issue, invisible to a single number.
    if isinstance(shift, int) and shift != 0:
        return f"{head} — shifted {shift_txt} (uniform; a wrong-before/right-after-OP feel means it under-split)"
    if isinstance(shift, int):
        return f"{head} — shift 0ms (already aligned, or the aligner found no offset)"
    return head


def diagnose_fetch(a: dict) -> str:
    match = a.get("resolution_match")
    flag = "resolution MATCH" if match else "no resolution match"
    return (
        f"picked {a.get('picked', '?')} — {flag}"
        f" (ep {a.get('episode', '?')}, {a.get('candidates', '?')} candidates, {a.get('ext', '?')})"
    )


def diagnose_reslot(a: dict) -> str:
    return (
        f"selected jp_sid={a.get('jp_sid', '?')} en_sid={a.get('en_sid', '?')}"
        f" (dropped {a.get('externals_dropped', 0)} carried-over external(s), active={a.get('active', '?')})"
    )


_DIAGNOSE = {
    "subtitle.fetch": diagnose_fetch,
    "subtitle.reslot": diagnose_reslot,
    "subtitle.resync": diagnose_resync,
}


def retry_deltas(resyncs: list[dict]) -> list[int | None]:
    """First-cue movement between each resync and the previous one (ms) — the convergence signal for a
    user hammering the re-sync key: 0 = no further change, alternating signs = OSCILLATING (re-syncing
    its own output). ``None`` where a fingerprint is missing."""
    firsts = [_first(r.get("args", {}).get("out_cue_ms") or []) for r in resyncs]
    out: list[int | None] = [None]
    for prev, cur in itertools.pairwise(firsts):
        out.append(cur - prev if prev is not None and cur is not None else None)
    return out


def extract(events: list[dict]) -> dict:
    """Structured, TEXT-FREE payload for `--json`: the fingerprint vectors + attributes per resync,
    plus the fetch/reslot facts — everything needed to seed an offline regression, nothing copyrighted."""
    spans = subtitle_spans(events)

    def record(s: dict) -> dict:
        a = dict(s.get("args", {}))
        a.pop("span_id", None)
        a.pop("trace_id", None)
        a.pop("thread.id", None)
        return {"name": s["name"], "ts": s.get("ts"), "args": a}

    return {
        "fetches": [record(s) for s in spans if s["name"] == "subtitle.fetch"],
        "reslots": [record(s) for s in spans if s["name"] == "subtitle.reslot"],
        "resyncs": [record(s) for s in spans if s["name"] == "subtitle.resync"],
    }


# --- log correlation ------------------------------------------------------------------------------

# The overlay.log substrings that carry the file/tool/reference context spans can't (paths, stderr).
_LOG_KEYS = ("jimaku: picked", "resync: running", "resync:", "subtitle", "unknown language",
             "sub-add", "Japanese subtitles", "Re-syncing")  # fmt: skip


def notable_log(log: list[dict]) -> list[tuple[str, str, str]]:
    """(hh:mm:ss, level, event) for the subtitle-relevant log lines, in order."""
    out = []
    for r in log:
        e = r.get("event", "")
        if any(k in e for k in _LOG_KEYS):
            out.append((r.get("timestamp", "")[11:19], r.get("level", ""), e[:160]))
    return out


# --- printing -------------------------------------------------------------------------------------


def _session(events: list[dict]) -> str | None:
    """The run's session id (stamped on every span) — ties this report to a specific `saitenka run`."""
    for e in events:
        sid = e.get("args", {}).get("session")
        if sid:
            return str(sid)
    return None


def print_report(src: Path, events: list[dict], log: list[dict]) -> None:
    spans = subtitle_spans(events)
    resyncs = [s for s in spans if s["name"] == "subtitle.resync"]
    session = _session(events)
    print(
        f"# saitenka subtitle report — {src.name}" + (f"  (session {session})" if session else "")
    )
    fetches = sum(s["name"] == "subtitle.fetch" for s in spans)
    reslots = sum(s["name"] == "subtitle.reslot" for s in spans)
    print(f"  {fetches} fetch · {reslots} reslot · {len(resyncs)} resync span(s)\n")

    if not spans:
        print("  (no subtitle.* spans — was telemetry enabled for this session?)\n")
    else:
        t0 = spans[0]["ts"]
        print("## pipeline timeline (t+ from first subtitle event)")
        deltas = {id(r): d for r, d in zip(resyncs, retry_deltas(resyncs), strict=False)}
        for s in spans:
            label = s["name"].split(".", 1)[1]
            line = _DIAGNOSE[s["name"]](s.get("args", {}))
            tplus = (s["ts"] - t0) / 1000
            print(f"  t+{tplus:7.1f}s  {label:<7} {line}")
            if s["name"] == "subtitle.resync":
                d = deltas.get(id(s))
                if d is not None:
                    kind = "no further change" if d == 0 else f"moved {d:+d}ms vs previous retry"
                    print(f"                        ↳ {kind}")
        print()

        # Convergence verdict across a retry burst.
        moves = [d for d in retry_deltas(resyncs) if d]
        if moves:
            osc = any(a * b < 0 for a, b in itertools.pairwise(moves))
            print("## retry convergence")
            print(f"  per-retry first-cue moves: {moves}")
            print(
                "  → OSCILLATING — re-syncing its own output; the SOURCE/reference is wrong, not the offset"
                if osc
                else "  → monotone — each retry refined the previous"
            )
            print()

    log_lines = notable_log(log)
    if log_lines:
        print("## subtitle log lines (paths / tool / reference)")
        for ts, level, event in log_lines:
            print(f"  {ts} {level:<5} {event}")
        print()


# --- reproduce mode (runs the REAL resync on a local video+sub pair) ------------------------------


def find_cached_sub(video: Path, cache_dir: Path) -> Path | None:
    """The RAW (not already-synced) cached subtitle whose name matches *video* in *cache_dir* — the
    cache names files after the video stem. Skips ``.synced`` outputs so we re-align from scratch;
    largest wins on ties. None if the dir has no match."""
    stem = video.stem
    cands = [
        p
        for p in cache_dir.iterdir()
        if p.suffix in {".srt", ".ass"} and ".synced" not in p.name and p.name.startswith(stem)
    ]
    return max(cands, key=lambda p: p.stat().st_size) if cands else None


def reproduce(video: Path, sub: Path) -> dict:
    """Re-run the REAL resync pipeline on a local (video, sub) pair and return a record shaped exactly
    like a trace `subtitle.resync` span — the fixture-builder / verifier path. Reuses
    ``saitenka.app.resync`` (one alignment implementation), so run it in the project env with
    ``uv run python``."""
    try:
        from saitenka.app import resync
    except ModuleNotFoundError as exc:  # isolated `uv run script.py` has no overlay on the path
        raise SystemExit(
            "--video/--sub (reproduce) needs the overlay package; run it in the project env:\n"
            "  uv run python tools/subtitle_report.py --video EP.mkv --sub CACHE"
        ) from exc
    import tempfile

    out = Path(tempfile.mkdtemp()) / (sub.stem + ".synced.srt")
    details: dict = {}
    outcome, reason = "synced", ""
    try:
        resync.resync(video, sub, out, force=True, details=details)
    except resync.ResyncUnavailable as exc:
        outcome, reason = "unavailable", str(exc)
    except resync.ResyncFailed as exc:
        outcome, reason = "failed", str(exc)
    src_cue = resync._cue_starts_ms(sub)
    out_cue = resync._cue_starts_ms(out) if out.exists() else []
    shift = out_cue[0] - src_cue[0] if src_cue and out_cue else None
    return {
        "video": video.name,
        "sub": sub.name,
        "outcome": outcome,
        "fail_reason": reason,
        "tool": details.get("tool"),
        "reference": details.get("reference"),
        "reference_fmt": details.get("reference_fmt", ""),
        "shift_ms": shift,
        "src_cue_ms": src_cue,
        "out_cue_ms": out_cue,
        "ref_cue_ms": details.get("ref_cue_ms", []),
    }


def print_reproduce(rec: dict) -> None:
    print(f"# saitenka subtitle reproduce — {rec['video']}")
    print(f"  sub: {rec['sub']}")
    print(f"  {diagnose_resync(rec)}")
    for key in ("src_cue_ms", "out_cue_ms", "ref_cue_ms"):
        print(f"  {key} = {rec[key]}")
    print()


def _load(path: Path) -> tuple[list[dict], list[dict]]:
    """Trace events + log lines from a report zip/dir, or a bare trace.json file."""
    if path.is_file() and path.suffix == ".json":
        doc = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        events = doc.get("traceEvents", doc) if isinstance(doc, dict) else doc
        return events, []
    return tr._load_trace(path), tr._load_log(path)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Distil the subtitle pipeline out of saitenka report(s)."
    )
    ap.add_argument("reports", type=Path, nargs="*",
                    help="one or more saitenka-report-*.zip / unzipped dirs / bare trace.json")  # fmt: skip
    ap.add_argument("--video", type=Path,
                    help="reproduce mode: an episode video to re-run the real resync against")  # fmt: skip
    ap.add_argument("--sub", type=Path,
                    help="reproduce mode: the cached subtitle FILE, or the subtitles cache DIR to match by video name")  # fmt: skip
    ap.add_argument("--json", action="store_true",
                    help="emit the TEXT-FREE fingerprint records (seed for a regression test) instead of a report")  # fmt: skip
    args = ap.parse_args()

    # Reproduce mode: --video + --sub re-run the real aligner locally (fixture-builder / verifier).
    if args.video or args.sub:
        if not (args.video and args.sub):
            ap.error("--video and --sub must be given together")
        sub = find_cached_sub(args.video, args.sub) if args.sub.is_dir() else args.sub
        if sub is None:
            raise SystemExit(f"no cached .srt/.ass for {args.video.name} in {args.sub}")
        rec = reproduce(args.video, sub)
        if args.json:
            json.dump(rec, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print_reproduce(rec)
        return

    if not args.reports:
        ap.error("give one or more report paths, or --video/--sub for reproduce mode")

    if args.json:
        payload = {}
        for report in args.reports:
            events, _log = _load(report)
            payload[report.name] = extract(events)
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    for report in args.reports:
        events, log = _load(report)
        if not events:
            print(f"# {report.name}: no telemetry/trace.json found\n")
            continue
        print_report(report, events, log)


if __name__ == "__main__":
    main()

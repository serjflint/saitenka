"""Headless responsiveness benchmark for the in-mpv tooltip, on the fixed smoke line.

Measures the CPU + our-code latency of every interaction that gates how snappy the overlay *feels* —
first tooltip paint, time-to-complete, warm (prefetched) hover, a scroll frame, the nested popup, a
horizontal sweep across the line, and the per-tick hover hit-test. It talks to a fake mpv IPC, so the
numbers exclude mpv's own compositing + socket round-trip (a small, ~constant add on top), but include
the real dictionary lookups, structured-content layout, BGRA conversion and the temp-file upload write.

    uv run python examples/bench_responsiveness.py            # uses ~/.config/saitenka/overlay.toml dicts
    uv run python examples/bench_responsiveness.py --reps 12

Why these metrics: for a bitmap tooltip the perceived speed is (1) time to first pixels on a *cold*
hover, (2) near-zero time on a *warm* (prefetched) hover, and (3) a scroll/sweep frame that stays under
one display frame (~16 ms). Time-to-complete matters less because the tail streams in behind the head.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import sysconfig
import time

from pathlib import Path

from overlay.app.config import load_config
from overlay.app.controller import Reader
from overlay.app.tokenize import Token, tokenize
from overlay.mpvio.osd import to_bgra, to_bgra_array
from overlay.panel import Definition, Entry, LazyPanel, panel_rows

LINE = "門前の小僧習わぬ経を読む"  # the fixed smoke line (examples/mpv_reader.DEMO_LINE)
OSD = (1920, 1080)
_STRESS_CACHE_CAP = (
    24  # --stress forces reader.panel_cache_max to this — a test control, independent
)
# of the user's own [tooltip].panel_cache_max — so eviction thrash is exercised deterministically

# Hand-picked multi-sense words Serj still sees pathological first lookups on: very polysemous
# common words whose monolingual entries are enormous (手 alone is ~100 senses in a big monolingual dict).
HAND_PICKED: list[tuple[str, str]] = [
    ("手", "て"),
    ("気", "き"),
    ("出る", "でる"),
    ("かける", "かける"),
    ("上げる", "あげる"),
    ("見る", "みる"),
    ("行く", "いく"),
    ("いい", "いい"),
]


class FakeIPC:
    """Minimal mpv stand-in: fixed osd, no socket. overlay-add just writes a temp file (as mpv wants)."""

    def __init__(self):
        self.props = {
            "osd-dimensions": {"w": OSD[0], "h": OSD[1]},
            "pause": False,
            "mouse-pos": {"hover": False, "x": -1, "y": -1},
        }

    def command(self, *args):
        if args and args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        return {"data": None}

    def drain_events(self):
        return []


def _stats(samples: list[float]) -> dict:
    """Latency summary. p99 = the jank tail (a p99 over the 16.7/33 ms frame budget drops a frame);
    cv (stdev/mean) = run-to-run stability — a metric with high cv can't be regression-gated because
    the noise swamps the signal."""
    s = sorted(samples)
    p = lambda q: s[min(len(s) - 1, int(q * len(s)))]  # noqa: E731
    mean = statistics.fmean(s)
    stdev = statistics.stdev(s) if len(s) > 1 else 0.0
    return {
        "p50": p(0.50),
        "p95": p(0.95),
        "p99": p(0.99),
        "mean": mean,
        "min": s[0],
        "max": s[-1],
        "stdev": stdev,
        "cv": (stdev / mean) if mean else 0.0,
        "n": len(s),
    }


def measure(fn, reps: int, warmup: int = 2) -> dict:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return _stats(samples)


def runtime_info() -> dict:
    """The runtime facts that make a benchmark number meaningful: whether this is a free-threaded
    build and whether the GIL is actually OFF right now (a C-extension like fugashi silently
    re-enables it on import — see AGENTS.md — which collapses worker scaling without any error), plus
    the worker capacity that scaling depends on. Recorded in every run so a result is never ambiguous
    about which runtime produced it."""
    gil_enabled = getattr(sys, "_is_gil_enabled", lambda: True)()
    return {
        "python": sys.version.split()[0],
        "freethreaded_build": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        "gil_enabled": bool(gil_enabled),
        "cpu_count": os.cpu_count() or 1,
        "prefetch_workers": min(8, (os.cpu_count() or 1) - 2) if not gil_enabled else 2,
    }


def finalize_runtime(rt: dict, require_ft: bool) -> int:
    """Re-read the LIVE GIL state after the workload (fugashi re-enables the GIL on first use, not at
    import) and fold it back into ``rt``. Warn on a free-threaded build whose GIL got re-enabled — the
    silent scaling-killer — and fail when ``--require-ft`` demanded it stay off. Returns an exit code
    delta (2 to abort, 0 otherwise)."""
    live = getattr(sys, "_is_gil_enabled", lambda: True)()
    rt["gil_enabled"] = bool(live)
    rt["prefetch_workers"] = 2 if live else min(8, (os.cpu_count() or 1) - 2)
    if rt["freethreaded_build"] and live:
        print(
            "\nWARNING: free-threaded build but the GIL is RE-ENABLED (a C-extension like fugashi "
            "re-enabled it) — worker scaling is collapsed. Run with PYTHON_GIL=0.",
            file=sys.stderr,
        )
        if require_ft:
            return 2
    return 0


def format_runtime(rt: dict) -> str:
    mode = (
        "free-threaded (GIL OFF)"
        if rt["freethreaded_build"] and not rt["gil_enabled"]
        else ("free-threaded BUILD but GIL RE-ENABLED" if rt["freethreaded_build"] else "GIL")
    )
    return (
        f"runtime: Python {rt['python']} · {mode} · {rt['cpu_count']} cores · "
        f"~{rt['prefetch_workers']} prefetch workers"
    )


def discover_pathological(db, dict_id: int, n: int = 5) -> list[tuple[str, str, int]]:
    """The ``n`` entries with the LARGEST glossary payloads for one dictionary in the consolidated
    DB — the worst cold-first-paint candidates (longest structured-content JSON = tallest render).
    Returns ``(term, reading, payload_bytes)`` rows, biggest first."""
    rows = (
        db._conn()
        .execute(
            "SELECT term, reading, length(glossary) FROM entries WHERE dict_id=? "
            "ORDER BY length(glossary) DESC LIMIT ?",
            (dict_id, n),
        )
        .fetchall()
    )
    return [(t, r, s) for t, r, s in rows]


def _load_dict_set():
    """Resolve the configured dict/freq/pitch **titles** against the consolidated DB (built once by
    ``saitenka-overlay import`` — see ``app/config.py``, dicts are titles, not zip paths)."""
    cfg = load_config()
    dict_titles = list(cfg.get("dicts") or [])
    if not dict_titles:
        return None, "no dicts in overlay.toml — falling back to a synthetic 6-dict entry"
    from overlay.app.dictdb import DictionaryDb
    from overlay.app.dictionary import DictionarySet

    db = DictionaryDb.open()
    ds = DictionarySet.from_db(
        db, dict_titles, list(cfg.get("freq") or []), list(cfg.get("pitch") or [])
    )
    tag = f"{len(ds.dicts)} dicts + {len(ds.freqs)} freq + {len(ds.pitches)} pitch"
    return ds, tag


class _SyntheticDS:
    """Fallback when no real dicts are configured: a tall multi-section CJK entry."""

    def entry_for(self, tok, inflected=None):
        para = "とても長い定義の本文でありスクロールが必要になるほど縦に伸びる説明文です。" * 3
        return Entry(
            headword=tok.surface,
            reading=tok.reading,
            defs=[Definition(f"辞書{i}", [para]) for i in range(6)],
        )


def _content_indices(reader) -> list[int]:
    from overlay.app.controller import SKIP_POS

    return [
        i
        for i, t in enumerate(reader.tokens)
        if t.is_content and t.pos not in SKIP_POS and t.surface.strip()
    ]


def _tallest(reader, idxs) -> int:
    """Index whose full panel is tallest — the best target for scroll/nested measurements."""
    best, best_h = idxs[0], 0
    for i in idxs:
        tok = reader.tokens[i]
        entry = reader.dict_set.entry_for(tok, reader._inflected_surface(i))
        h = LazyPanel(panel_rows(entry, reader.tip_width), reader.tip_width).finish().height
        if h > best_h:
            best, best_h = i, h
    return best


def _pathological_corpus(ds, per_dict: int = 3) -> list[tuple[str, str, str]]:
    """(source, term, reading) for the worst first-lookup words: auto-discovered largest entries per
    dict + the hand-picked multi-sense words. Deduped by term, discovery order preserved."""
    corpus: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for d in getattr(ds, "dicts", []):
        for term, reading, _size in discover_pathological(d.db, d.dict_id, n=per_dict):
            if term not in seen:
                seen.add(term)
                corpus.append((d.title, term, reading))
    for term, reading in HAND_PICKED:
        if term not in seen:
            seen.add(term)
            corpus.append(("hand-picked", term, reading))
    return corpus


def _cold_reader(ds):
    """A fresh Reader on a fake IPC, head-path forced (as a live run with workers would)."""
    reader = Reader(FakeIPC(), dict_set=ds, prefetch=False)
    reader.osd = OSD
    reader._finish_available = lambda: True
    return reader


def _bench_word(reader, term: str, reading: str, reps: int) -> dict:
    """Cold first-paint for one word through the real reader path (head render + BGRA + upload)."""
    from overlay.app.subtitles import WordBox

    tok = Token(term, term, reading, "名詞", 0, len(term))
    reader.tokens = [tok]
    reader.boxes = [WordBox(0, 400, 800, 60, 60)]
    reader.sub_origin = (0, 0)

    def cold():
        reader._panel_cache.clear()
        reader._tip_state = None
        reader.hover = 0
        reader._show_tooltip(0)
        reader._finish_q.queue.clear()

    return measure(cold, reps, warmup=1)


def run_pathological(
    reps: int, rt: dict, require_ft: bool = False, json_path: str | None = None
) -> int:
    ds, tag = _load_dict_set()
    if ds is None:
        print("pathological corpus needs the real dict set (overlay.toml) — nothing to measure")
        return 1
    corpus = _pathological_corpus(ds)
    reader = _cold_reader(ds)

    # First-hover-after-launch: a brand-new DictionaryDb (fresh process ≈ cold page cache for the
    # non-mmap'd portions; the OS file cache may still be warm — note in the output).
    from overlay.app.dictdb import DictionaryDb
    from overlay.app.dictionary import DictionarySet

    fresh_db = DictionaryDb.open(ds.dicts[0].db.path)
    fresh = DictionarySet.from_db(fresh_db, [d.title for d in ds.dicts])
    fresh_reader = _cold_reader(fresh)
    t0 = time.perf_counter()
    first_term, first_reading = corpus[0][1], corpus[0][2]
    ftok = Token(first_term, first_term, first_reading, "名詞", 0, len(first_term))
    from overlay.app.subtitles import WordBox

    fresh_reader.tokens = [ftok]
    fresh_reader.boxes = [WordBox(0, 400, 800, 60, 60)]
    fresh_reader.sub_origin = (0, 0)
    fresh_reader.hover = 0
    fresh_reader._show_tooltip(0)
    first_hover_ms = (time.perf_counter() - t0) * 1000.0

    gil_rc = finalize_runtime(rt, require_ft)
    print(f"\nSaitenka overlay — PATHOLOGICAL cold-first-paint benchmark   ({tag})")
    print(format_runtime(rt))
    print(
        f"osd: {OSD[0]}x{OSD[1]}   tip_width: {reader.tip_width}   cap: {reader._tip_cap()}px   "
        f"reps/word: {reps}"
    )
    print(f"first-hover-after-launch (fresh connections, {first_term}): {first_hover_ms:.1f} ms\n")
    hdr = f"{'word':8} {'source':34} {'p50':>7} {'p95':>7} {'p99':>7} {'max':>7}   (ms)"
    print(hdr)
    print("-" * len(hdr))
    all_p50, all_p95, all_p99, all_max = [], [], [], []
    collected: dict[str, dict] = {}
    for source, term, reading in corpus:
        m = _bench_word(reader, term, reading, reps)
        collected[f"{term} ({source})"] = m
        all_p50.append(m["p50"])
        all_p95.append(m["p95"])
        all_p99.append(m["p99"])
        all_max.append(m["max"])
        print(
            f"{term:8} {source[:34]:34} {m['p50']:7.1f} {m['p95']:7.1f} {m['p99']:7.1f} "
            f"{m['max']:7.1f}"
        )
    print("-" * len(hdr))
    print(
        f"{'WORST':8} {'over all words':34} {max(all_p50):7.1f} {max(all_p95):7.1f} "
        f"{max(all_p99):7.1f} {max(all_max):7.1f}"
    )
    print("\ntargets: cold p95 < 150 ms per word · first-hover-after-launch < 300 ms")
    print(
        "note: OS file cache may still be warm for first-hover (no sudo purge); "
        "fresh SQLite connections only."
    )
    if json_path:
        Path(json_path).write_text(
            json.dumps(
                {"runtime": rt, "osd": OSD, "first_hover_ms": first_hover_ms, "metrics": collected},
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote metrics baseline → {json_path}")
    return gil_rc


def _rss_mb() -> float:
    """Resident set size in MB (cross-platform via psutil, a runtime dep) — captures the numpy/Pillow
    C buffers that tracemalloc, being Python-only, misses. The primary memory signal for the stress."""
    import psutil

    return psutil.Process().memory_info().rss / 1e6


def run_stress(
    reps: int,
    rt: dict,
    require_ft: bool = False,
    json_path: str | None = None,
    max_frame_ms: float | None = None,
    max_rss_mb: float | None = None,
) -> int:
    """A sustained, DETERMINISTIC chained session — cold hover → scroll → nested popup → scroll →
    dismiss — over a corpus of distinct heavy entries, repeated ``reps`` rounds. Unlike the isolated
    micro-benchmarks it exercises what only shows under load: panel-cache eviction thrash, nested-state
    churn, and memory growth across a long session. Reports the per-op frame-latency tail (MAX is the
    jank signal) + peak RSS + growth, and can gate on ``--max-frame-ms`` / ``--max-rss-mb``."""
    import tracemalloc

    from overlay.app.subtitles import WordBox

    ds, tag = _load_dict_set()
    if ds is None:
        ds, tag = _SyntheticDS(), tag
    reader = _cold_reader(ds)
    # The cache cap is a TEST CONTROL, not the user's live [tooltip].panel_cache_max — fix it small
    # and deterministic so eviction is exercised the same way regardless of how many dicts are
    # configured or what the user's own cap is. Scaling the corpus to chase a large live cap instead
    # blows up wall time / memory for reasons unrelated to what's being measured.
    reader.panel_cache_max = _STRESS_CACHE_CAP
    if hasattr(ds, "dicts") and ds.dicts:
        # A fixed corpus comfortably larger than the fixed cap — forces real eviction thrash without
        # depending on the live config.
        per_dict = max(3, _STRESS_CACHE_CAP // len(ds.dicts) + 2)
        corpus = [(t, r) for _s, t, r in _pathological_corpus(ds, per_dict=per_dict)]
    else:
        corpus = [(w, w) for w in ("手", "気", "出る", "見る", "行く", "上げる")]
    step = round(OSD[1] * 0.12)
    frames: list[float] = []

    def timed(fn) -> None:
        t0 = time.perf_counter()
        fn()
        frames.append((time.perf_counter() - t0) * 1000.0)

    def one_word(term: str, reading: str) -> None:
        tok = Token(term, term, reading, "名詞", 0, len(term))
        reader.tokens = [tok]
        reader.boxes = [WordBox(0, 400, 800, 60, 60)]
        reader.sub_origin = (0, 0)
        reader.hover = 0
        timed(lambda: reader._show_tooltip(0))
        for _ in range(4):  # scroll toward the bottom of a tall entry
            timed(lambda: reader._scroll_tip(step))
        st = reader._tip_state
        boxes = st.lazy.scan_boxes if st else []
        if boxes:
            sb = boxes[len(boxes) // 3]  # a deterministic cell well inside the body
            timed(lambda: reader._show_nested(sb))
            timed(lambda: reader._scroll_tip(step))  # scroll while the nested popup is up
            timed(reader._hide_nested)
        timed(lambda: reader.set_hover(-1))  # dismiss the whole stack
        reader._finish_q.queue.clear()  # model the worker draining it (no worker in --prefetch off)

    for term, reading in corpus:  # one warmup round before the memory baseline
        one_word(term, reading)
    frames.clear()
    tracemalloc.start()
    rss_base = _rss_mb()
    rss_peak = rss_base
    for _ in range(max(1, reps)):
        for term, reading in corpus:
            one_word(term, reading)
        rss_peak = max(rss_peak, _rss_mb())
    _cur, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    m = _stats(frames)
    cache_len = len(reader._panel_cache)
    growth = rss_peak - rss_base
    gil_rc = finalize_runtime(rt, require_ft)

    print(
        f"\nSaitenka overlay — STRESS: chained scan/scroll/nested over {len(corpus)} distinct heavy "
        f"entries × {reps} rounds   ({tag})"
    )
    print(format_runtime(rt))
    print(f"osd: {OSD[0]}x{OSD[1]}   tip_width: {reader.tip_width}   ops timed: {m['n']}")
    print(
        f"\nper-op frame latency:  p50 {m['p50']:.1f}  p95 {m['p95']:.1f}  p99 {m['p99']:.1f}  "
        f"MAX {m['max']:.1f} ms  (cv {m['cv']:.2f})"
    )
    print(
        f"panel cache: {cache_len}/{reader.panel_cache_max} entries "
        "(LRU-capped — steady state means eviction is working)"
    )
    print(
        f"memory: peak RSS {rss_peak:.0f} MB · growth over rounds {growth:+.1f} MB · "
        f"python-obj peak {py_peak / 1e6:.1f} MB"
    )
    print(
        "\nMAX frame is the jank signal (a single op over the 16.7/33 ms budget can drop a video "
        "frame under load); growth ≫ 0 across rounds of fixed work ⇒ a leak."
    )
    rc = gil_rc
    if max_frame_ms is not None and m["max"] > max_frame_ms:
        print(
            f"FAIL: MAX frame {m['max']:.1f} ms exceeds --max-frame-ms {max_frame_ms}",
            file=sys.stderr,
        )
        rc = rc or 1
    if max_rss_mb is not None and rss_peak > max_rss_mb:
        print(
            f"FAIL: peak RSS {rss_peak:.0f} MB exceeds --max-rss-mb {max_rss_mb}", file=sys.stderr
        )
        rc = rc or 1
    if json_path:
        Path(json_path).write_text(
            json.dumps(
                {
                    "runtime": rt,
                    "osd": OSD,
                    "rounds": reps,
                    "corpus_size": len(corpus),
                    "frame_latency_ms": m,
                    "panel_cache_len": cache_len,
                    "panel_cache_max": reader.panel_cache_max,
                    "rss_peak_mb": rss_peak,
                    "rss_growth_mb": growth,
                    "py_obj_peak_mb": py_peak / 1e6,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote stress baseline → {json_path}")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument(
        "--pathological",
        action="store_true",
        help="run the pathological cold-first-paint corpus (largest entries per dict "
        "+ hand-picked multi-sense words)",
    )
    ap.add_argument(
        "--json",
        metavar="PATH",
        help="also write the metrics (with runtime info) as JSON, for baseline diffing over time",
    )
    ap.add_argument(
        "--require-ft",
        action="store_true",
        help="fail if the GIL is enabled (a C-extension re-enabled it) — for free-threaded runs",
    )
    ap.add_argument(
        "--stress",
        action="store_true",
        help="sustained chained session (scan→scroll→nested→dismiss over many heavy entries) — "
        "surfaces cache-eviction thrash, memory growth, and the frame-latency tail under load",
    )
    ap.add_argument(
        "--max-frame-ms",
        type=float,
        help="stress: fail if any single op exceeds this frame budget (ms)",
    )
    ap.add_argument(
        "--max-rss-mb", type=float, help="stress: fail if peak resident memory exceeds this (MB)"
    )
    args = ap.parse_args()

    # Snapshot the runtime; the GIL state is re-read AFTER the workload (finalize_runtime), because
    # fugashi re-enables the GIL only when first USED, not at startup — a start-of-run check would
    # miss exactly the regression --require-ft is meant to catch.
    rt = runtime_info()

    if args.stress:
        return run_stress(
            args.reps, rt, args.require_ft, args.json, args.max_frame_ms, args.max_rss_mb
        )
    if args.pathological:
        return run_pathological(args.reps, rt, args.require_ft, args.json)

    ds, tag = _load_dict_set()
    if ds is None:
        ds, tag = _SyntheticDS(), tag

    reader = Reader(FakeIPC(), dict_set=ds, prefetch=False)
    reader.osd = OSD
    reader.set_subtitle(LINE)
    idxs = _content_indices(reader)
    words = [reader.tokens[i].surface for i in idxs]
    cap = reader._tip_cap()

    # Force the viewport-first head path (pretend a prefetch worker exists to finish the tail later),
    # so "first paint" measures head render + BGRA + upload — not the whole entry.
    reader._finish_available = lambda: True

    rows = []
    cyc = {"cold": 0, "warm": 0}  # cycle through the words so each sample times ONE tooltip

    def show_cold(i):
        reader._panel_cache.clear()
        reader.set_hover(-1)
        reader._show_tooltip(i)
        reader._finish_q.queue.clear()  # drop the enqueued finish job (no worker running)

    def show_warm(i):
        reader._show_tooltip(i)  # panel already fully cached → upload only

    def cold_one():
        i = idxs[cyc["cold"] % len(idxs)]
        cyc["cold"] += 1
        show_cold(i)

    def warm_one():
        i = idxs[cyc["warm"] % len(idxs)]
        cyc["warm"] += 1
        show_warm(i)

    # 1) cold first paint (head + upload) — per word, pooled across the content words
    rows.append(
        ("first paint  (cold: head render + upload)", measure(cold_one, args.reps * len(idxs)))
    )

    # 2) time to complete: render the deferred tail of a cold head
    def complete_one():
        reader._panel_cache.clear()
        i = idxs[0]
        reader.set_hover(-1)
        reader._show_tooltip(i)
        st = reader._tip_state
        reader._finish_q.queue.clear()
        st.finish()  # what a worker does; then the loop refreshes

    rows.append(("time-to-complete  (finish deferred tail)", measure(complete_one, args.reps)))

    # 3) warm hover: panel prefetched/cached → just re-slice + upload
    for i in idxs:  # warm the cache fully first
        reader._panel_for(reader.tokens[i], reader._inflected_surface(i), finish=True)
    rows.append(
        ("warm hover  (prefetched → upload only)", measure(warm_one, args.reps * len(idxs)))
    )

    # 4) scroll frame: one wheel step re-slice + scrollbar + upload on the tallest tooltip
    tall = _tallest(reader, idxs)
    reader._panel_for(reader.tokens[tall], reader._inflected_surface(tall), finish=True)
    show_warm(tall)
    step = round(OSD[1] * 0.12)

    def scroll_frame():
        reader._tip_scroll = 0
        reader._scroll_tip(step)  # down one step (re-render)

    rows.append((f"scroll frame  (one {step}px step)", measure(scroll_frame, args.reps * 3)))

    # 5) nested popup first paint: hover a word inside the tooltip
    show_warm(tall)
    boxes = reader._tip_state.lazy.scan_boxes
    if boxes:
        sb = boxes[len(boxes) // 3]  # a cell well inside the body

        def nested_cold():
            reader._hide_nested()
            # drop only the inner word's cached panel so we measure a cold nested paint
            reader._panel_cache.pop(
                reader._panel_key(tokenize(sb.text)[0], tokenize(sb.text)[0].surface), None
            )
            reader._show_nested(sb)
            reader._finish_q.queue.clear()

        rows.append(("nested popup first paint  (inner word)", measure(nested_cold, args.reps)))

    # 6) per-tick hover hit-test: the poll-loop cost while the cursor sits on the tooltip body
    show_warm(tall)
    tx, ty, tw, th = reader._tip_rect
    reader.ipc.props["mouse-pos"] = {"hover": True, "x": tx + tw / 2, "y": ty + th - 8}
    reader.scan_delay = 1e9  # isolate the hit-test; don't actually open a nested popup
    rows.append(
        ("poll tick hover hit-test  (_update_hover)", measure(reader._update_hover, args.reps * 5))
    )
    reader.scan_delay = 0.25

    # 7) horizontal sweep across the line — cold vs warm (shows what prefetch buys you)
    sweep_cold = measure(lambda: [show_cold(i) for i in idxs], max(4, args.reps // 2))
    sweep_warm = measure(lambda: [show_warm(i) for i in idxs], max(4, args.reps // 2))

    # 8) components, for diagnosis
    def comp_lookup():
        for i in idxs:
            reader.dict_set.entry_for(reader.tokens[i], reader._inflected_surface(i))

    def comp_headrender():
        for i in idxs:
            e = reader.dict_set.entry_for(reader.tokens[i], reader._inflected_surface(i))
            LazyPanel(panel_rows(e, reader.tip_width), reader.tip_width).render_to(cap)

    _tall_head = LazyPanel(
        panel_rows(reader.dict_set.entry_for(reader.tokens[tall]), reader.tip_width),
        reader.tip_width,
    ).render_to(cap)  # pre-rendered once, outside the timer

    def comp_bgra():
        to_bgra_array(_tall_head)  # isolate the RGBA→premultiplied-BGRA conversion

    # Isolate the temp-file UPLOAD write (the last hop before mpv reads the bitmap) from the render.
    # Two variants expose the OS page-cache trap that hides the suspected ~55 ms floor: reusing ONE
    # path — mpv's real per-overlay-id behaviour, inode stays hot — vs a FRESH file each iteration
    # with fsync, which forces inode creation + a real device write (the true cold cost). If warm≈cold
    # the floor was a page-cache artifact; if cold ≫ warm, moving to mmap/shared memory is justified.
    import shutil
    import tempfile

    _up_data, _up_w, _up_h, _up_stride = to_bgra(_tall_head)
    _up_dir = Path(tempfile.mkdtemp(prefix="saitenka-bench-upload-"))
    _up_path = _up_dir / "reuse.bgra"
    _up_cold = {"i": 0}

    def comp_upload_warm():
        _up_path.write_bytes(_up_data)  # overwrite one inode (no fsync — matches osd.Overlay.show)

    def comp_upload_cold():
        p = _up_dir / f"cold-{_up_cold['i']}.bgra"  # a new inode each time → not page-cached
        _up_cold["i"] += 1
        fd = os.open(p, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            os.write(fd, _up_data)
            os.fsync(fd)  # force to the device: measure real I/O, not just the write buffer
        finally:
            os.close(fd)

    n = len(idxs)
    collected: dict[str, dict] = {}

    def prow(label: str, m: dict) -> None:
        collected[label] = m
        print(
            f"{label:44} {m['p50']:7.1f} {m['p95']:7.1f} {m['p99']:7.1f} {m['mean']:7.1f} "
            f"{m['cv']:6.2f}"
        )

    gil_rc = finalize_runtime(rt, args.require_ft)
    print(f"\nSaitenka overlay — responsiveness benchmark   ({tag})")
    print(format_runtime(rt))
    print(f"line: {LINE}   osd: {OSD[0]}x{OSD[1]}   tip_width: {reader.tip_width}   cap: {cap}px")
    print(f"content words: {' '.join(words)}\n")
    # p99 = the jank tail (a p99 over the 16.7/33 ms frame budget drops a frame); cv = run-to-run
    # stability (a metric with high cv can't be regression-gated — the noise swamps the signal).
    hdr = f"{'metric':44} {'p50':>7} {'p95':>7} {'p99':>7} {'mean':>7} {'cv':>6}   (ms)"
    print(hdr)
    print("-" * len(hdr))
    for label, m in rows:
        prow(label, m)
    print("-" * len(hdr))
    for label, m in [
        (f"horizontal sweep: cold, {n} words (total)", sweep_cold),
        (f"horizontal sweep: warm, {n} words (total)", sweep_warm),
    ]:
        prow(label, m)
    print("-" * len(hdr))
    for label, fn in [
        (f"component: dict lookup, {n} words", comp_lookup),
        (f"component: head render, {n} words", comp_headrender),
        ("component: BGRA convert, tallest", comp_bgra),
        ("component: upload write, warm (reuse inode)", comp_upload_warm),
        ("component: upload write, cold (fresh+fsync)", comp_upload_cold),
    ]:
        prow(label, measure(fn, args.reps))
    shutil.rmtree(_up_dir, ignore_errors=True)
    print(
        f"\nupload payload: {_up_w}x{_up_h} BGRA ≈ {len(_up_data) / 1e6:.1f} MB. cold≈warm ⇒ the "
        "~55 ms floor was a page-cache artifact; cold ≫ warm ⇒ mmap/shared-mem is worth it."
    )
    print(
        "note: excludes mpv's own compositing + IPC round-trip (a small, ~constant add). "
        "cold = OS/SQLite page cache warm, our panel cache cleared (a fresh word mid-session)."
    )
    if args.json:
        Path(args.json).write_text(
            json.dumps({"runtime": rt, "osd": OSD, "metrics": collected}, indent=2),
            encoding="utf-8",
        )
        print(f"\nwrote metrics baseline → {args.json}")
    return gil_rc


if __name__ == "__main__":
    raise SystemExit(main())

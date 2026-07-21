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
import sqlite3
import statistics
import time

from overlay.app.config import expand_paths, load_config
from overlay.app.controller import Reader
from overlay.app.tokenize import Token, tokenize
from overlay.mpvio.osd import to_bgra_array
from overlay.panel import Definition, Entry, LazyPanel, panel_rows

LINE = "門前の小僧習わぬ経を読む"  # the fixed smoke line (examples/mpv_reader.DEMO_LINE)
OSD = (1920, 1080)

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


def measure(fn, reps: int, warmup: int = 2) -> dict:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    p = lambda q: samples[min(len(samples) - 1, int(q * len(samples)))]  # noqa: E731
    return {
        "p50": p(0.50),
        "p95": p(0.95),
        "mean": statistics.fmean(samples),
        "min": samples[0],
        "max": samples[-1],
        "n": len(samples),
    }


def discover_pathological(db_path: str, n: int = 5) -> list[tuple[str, str, int]]:
    """The ``n`` entries with the LARGEST glossary payloads in a built dict index — the worst
    cold-first-paint candidates (longest structured-content JSON = tallest render). Returns
    ``(term, reading, payload_bytes)`` rows, biggest first."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT term, reading, length(glossary) FROM entries "
            "ORDER BY length(glossary) DESC LIMIT ?",
            (n,),
        ).fetchall()
    finally:
        conn.close()
    return [(t, r, s) for t, r, s in rows]


def _load_dict_set():
    cfg = load_config()
    dicts = expand_paths(cfg.get("dicts"))
    if not dicts:
        return None, "no dicts in overlay.toml — falling back to a synthetic 6-dict entry"
    from overlay.app.dictionary import DictionarySet

    ds = DictionarySet.load(
        dicts, freq_paths=expand_paths(cfg.get("freq")), pitch_paths=expand_paths(cfg.get("pitch"))
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
        for term, reading, _size in discover_pathological(d._db_path, n=per_dict):
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


def run_pathological(reps: int) -> int:
    ds, tag = _load_dict_set()
    if ds is None:
        print("pathological corpus needs the real dict set (overlay.toml) — nothing to measure")
        return 1
    corpus = _pathological_corpus(ds)
    reader = _cold_reader(ds)

    # First-hover-after-launch: brand-new SQLite connections (fresh process ≈ cold page cache for
    # the non-mmap'd portions; the OS file cache may still be warm — note in the output).
    from overlay.app.dictionary import Dictionary, DictionarySet

    fresh = DictionarySet(
        [Dictionary(d.title, d._db_path, d.tags) for d in ds.dicts], ds.freqs, ds.pitches
    )
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

    print(f"\nSaitenka overlay — PATHOLOGICAL cold-first-paint benchmark   ({tag})")
    print(
        f"osd: {OSD[0]}x{OSD[1]}   tip_width: {reader.tip_width}   cap: {reader._tip_cap()}px   "
        f"reps/word: {reps}"
    )
    print(f"first-hover-after-launch (fresh connections, {first_term}): {first_hover_ms:.1f} ms\n")
    hdr = f"{'word':8} {'source':34} {'p50':>8} {'p95':>8} {'max':>8}   (ms)"
    print(hdr)
    print("-" * len(hdr))
    all_p50, all_p95, all_max = [], [], []
    for source, term, reading in corpus:
        m = _bench_word(reader, term, reading, reps)
        all_p50.append(m["p50"])
        all_p95.append(m["p95"])
        all_max.append(m["max"])
        print(f"{term:8} {source[:34]:34} {m['p50']:8.1f} {m['p95']:8.1f} {m['max']:8.1f}")
    print("-" * len(hdr))
    print(
        f"{'WORST':8} {'over all words':34} {max(all_p50):8.1f} {max(all_p95):8.1f} "
        f"{max(all_max):8.1f}"
    )
    print("\ntargets: cold p95 < 150 ms per word · first-hover-after-launch < 300 ms")
    print(
        "note: OS file cache may still be warm for first-hover (no sudo purge); "
        "fresh SQLite connections only."
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument(
        "--pathological",
        action="store_true",
        help="run the pathological cold-first-paint corpus (largest entries per dict "
        "+ hand-picked multi-sense words)",
    )
    args = ap.parse_args()

    if args.pathological:
        return run_pathological(args.reps)

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

    print(f"\nSaitenka overlay — responsiveness benchmark   ({tag})")
    print(f"line: {LINE}   osd: {OSD[0]}x{OSD[1]}   tip_width: {reader.tip_width}   cap: {cap}px")
    print(f"content words: {' '.join(words)}\n")
    hdr = f"{'metric':44} {'p50':>8} {'p95':>8} {'mean':>8} {'min':>8}   (ms)"
    print(hdr)
    print("-" * len(hdr))
    for label, m in rows:
        print(f"{label:44} {m['p50']:8.1f} {m['p95']:8.1f} {m['mean']:8.1f} {m['min']:8.1f}")
    print("-" * len(hdr))
    n = len(idxs)
    for label, m in [
        (f"horizontal sweep: cold, {n} words (total)", sweep_cold),
        (f"horizontal sweep: warm, {n} words (total)", sweep_warm),
    ]:
        print(f"{label:44} {m['p50']:8.1f} {m['p95']:8.1f} {m['mean']:8.1f} {m['min']:8.1f}")
    print("-" * len(hdr))
    for label, fn in [
        (f"component: dict lookup, {n} words", comp_lookup),
        (f"component: head render, {n} words", comp_headrender),
        ("component: BGRA convert, tallest", comp_bgra),
    ]:
        m = measure(fn, args.reps)
        print(f"{label:44} {m['p50']:8.1f} {m['p95']:8.1f} {m['mean']:8.1f} {m['min']:8.1f}")
    print(
        "\nnote: excludes mpv's own compositing + IPC round-trip (a small, ~constant add). "
        "cold = OS/SQLite page cache warm, our panel cache cleared (a fresh word mid-session)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

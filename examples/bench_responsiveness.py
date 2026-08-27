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
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from saitenka.app.config import load_config
from saitenka.app.features.tooltip import nested_popup, tooltip, tooltip_panel
from saitenka.app.session.controller import SessionController
from saitenka.app.tokenize import Token, tokenize
from saitenka.mpvio.osd import to_bgra, to_bgra_array
from saitenka.panel import Definition, Entry, LazyPanel, panel_rows
from saitenka.runtime.jobs import NoSessionRuntime
from saitenka.subtitles import Cue, CueIndex

if TYPE_CHECKING:
    from saitenka.mpvio.gateway import MpvGateway
    from saitenka.mpvio.ipc import MpvIPC

LINE = "門前の小僧習わぬ経を読む"  # the fixed smoke line (examples/mpv_reader.DEMO_LINE)
OSD = (1920, 1080)
# --stress uses a fixed cache cap so eviction pressure is independent of the user's configuration.
_STRESS_CACHE_CAP = 24
_SCROLL_JANK_STEPS = (
    40  # --scroll-jank: wheel steps DOWN into each entry (bounds tall-entry render time)
)

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


class FakeIPC(NoSessionRuntime):
    """Minimal mpv stand-in: fixed osd, no socket. overlay-add just writes a temp file (as mpv wants).

    Refuses job lanes while no gateway is installed — the synchronous bench paths drive the SessionController
    themselves and want no background work. A path that MEASURES background work installs one (see
    :func:`_runtime_ipc`), and then the ports delegate for real: refusing with a gateway present
    would silently disable the very thing being measured, which is exactly what happened to
    ``--timeline`` when prefetch moved onto registered job lanes.
    """

    def __init__(self):
        self.props: dict[str, object] = {
            "osd-dimensions": {"w": OSD[0], "h": OSD[1]},
            "pause": False,
            "mouse-pos": {"hover": False, "x": -1, "y": -1},
        }
        self._runtime_gateway = None

    def install_runtime_ingress(self, _event_sink, _connection_sink, _session_loop, gateway):
        self._runtime_gateway = gateway

    def register_runtime_job_lane(self, name, policy, handler) -> bool:
        if self._runtime_gateway is None:
            return False
        self._runtime_gateway.register_job_lane(name, policy, handler)
        return True

    def submit_runtime_job(self, **kwargs) -> bool:
        if self._runtime_gateway is None:
            return False
        return self._runtime_gateway.submit_job(**kwargs)

    def close_runtime_job_lane(self, name, timeout: float = 2.0) -> bool:
        if self._runtime_gateway is None:
            return False
        return self._runtime_gateway.close_job_lane(name, timeout)

    def wake_session_runtime(self) -> bool:
        if self._runtime_gateway is None:
            return False
        self._runtime_gateway.mailbox.wake()
        return True

    def command(self, *args):
        if args and args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        return {"data": None}

    # Delegates rather than answering alongside `command`: two write paths for one channel is how a
    # fake starts disagreeing with production about what was sent.
    def command_async(self, *args):
        return self.command(*args)

    def query(self, name: str) -> object | None:
        return self.props.get(name)

    def receive_session(self, _timeout, _handle) -> None:
        """No socket, so a turn observes nothing. The bench drives the SessionController by calling it."""


def _fake_ipc() -> MpvIPC:
    """``FakeIPC`` duck-types the ``command``/``drain_events`` surface ``SessionController`` calls on ``MpvIPC``
    (no socket, headless bench) but isn't a subclass — cast documents that at the one boundary instead
    of a per-call-site ``# type: ignore``."""
    return cast("MpvIPC", FakeIPC())


def _runtime_ipc() -> tuple[MpvIPC, MpvGateway]:
    """A fake with a REAL gateway behind it, for a bench that measures BACKGROUND work.

    Prefetch runs on a registered job lane, and `start_prefetch` returns early when the lane is
    refused — so a bench that omits the gateway measures a SessionController whose prefetch never starts, and
    reports every hover as "the worker fell behind" when no worker exists. Close the gateway when
    the run ends; its threads outlive the SessionController otherwise.
    """
    from saitenka.mpvio.gateway import MpvGateway
    from saitenka.runtime.mailbox import SessionMailbox

    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox())
    return cast("MpvIPC", ipc), gateway


def _stats(samples: list[float]) -> dict:
    """Latency summary. p99 = the jank tail (a p99 over the 16.7/33 ms frame budget drops a frame);
    cv (stdev/mean) = run-to-run stability — a metric with high cv can't be regression-gated because
    the noise swamps the signal."""
    s = sorted(samples)
    p = lambda q: s[min(len(s) - 1, int(q * len(s)))]
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


def _gil_enabled() -> bool:
    """Whether the GIL is on RIGHT NOW. Worth re-reading after the tokenizer loads: fugashi
    re-enables it on import, so a reading taken at startup describes a runtime that no longer
    exists."""
    return bool(getattr(sys, "_is_gil_enabled", lambda: True)())


def runtime_info() -> dict:
    """The runtime facts that make a benchmark number meaningful: whether this is a free-threaded
    build and whether the GIL is actually OFF right now (a C-extension like fugashi silently
    re-enables it on import — see AGENTS.md — which collapses worker scaling without any error), plus
    the worker capacity that scaling depends on. Recorded in every run so a result is never ambiguous
    about which runtime produced it."""
    gil_enabled = _gil_enabled()
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
    ``saitenka import`` — see ``app/config.py``, dicts are titles, not zip paths)."""
    cfg = load_config()
    dict_titles = list(cfg.get("dicts") or [])
    if not dict_titles:
        return None, "no dicts in overlay.toml — falling back to a synthetic 6-dict entry"
    from saitenka.app.dictdb import DictionaryDb
    from saitenka.app.dictionary import DictionarySet

    db = DictionaryDb.open()
    ds = DictionarySet.from_db(
        db, dict_titles, list(cfg.get("freq") or []), list(cfg.get("pitch") or [])
    )
    tag = f"{len(ds.dicts)} dicts + {len(ds.freqs)} freq + {len(ds.pitches)} pitch"
    return ds, tag


class _SyntheticDS:
    """Fallback when no real dicts are configured: a tall multi-section CJK entry."""

    def entry_for(self, tok, _inflected=None, **_kwargs):
        para = "とても長い定義の本文でありスクロールが必要になるほど縦に伸びる説明文です。" * 3
        return Entry(
            headword=tok.surface,
            reading=tok.reading,
            defs=[Definition(f"辞書{i}", [para]) for i in range(6)],
        )


# Deterministic CJK headword pool for the dict-free synth corpus — a fixed string, no randomness, so the
# same corpus (and the same numbers) come out on every machine and every commit.
_SYNTH_POOL = "見門経読語手気道時人山川花水火木金土空海雨風雪月日火"
_SYNTH_PARA = "とても長い定義の本文でありスクロールが必要になるほど縦に伸びる説明文です。"


def synth_corpus(n: int = 60) -> list[tuple[str, Entry]]:
    """A deterministic, dict-free corpus of ``(headword, Entry)`` spanning the render cost space — short
    single-def entries, medium multi-def, and tall scrolling ones. No ``overlay.toml``, no ``random``:
    byte-identical every run, so it is the CI/asv-safe gate target that ``--vocab`` (which needs real
    dicts) can't be. The cost tier cycles short/medium/tall and the body length grows within a tier."""
    out: list[tuple[str, Entry]] = []
    for i in range(n):
        hw = _SYNTH_POOL[i % len(_SYNTH_POOL)] + _SYNTH_POOL[(i * 7 + 3) % len(_SYNTH_POOL)]
        tier = i % 3
        n_defs, body_reps = ((1, 1), (3, 2), (6, 3))[tier]
        defs = [Definition(f"辞書{j}", [_SYNTH_PARA * body_reps]) for j in range(n_defs)]
        out.append((hw, Entry(headword=hw, reading="かな", defs=defs)))
    return out


def _content_indices(reader) -> list[int]:
    from saitenka.app.tokenize import SKIP_POS

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
        entry = reader.profile_controller.dict_set.entry_for(tok, reader._inflected_surface(i))
        h = (
            LazyPanel(panel_rows(entry, reader.tip_scale.width), reader.tip_scale.width)
            .finish()
            .height
        )
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


def _load_vocab_words() -> list[list[str]]:
    """Raw ``[surface, lemma, reading, pos]`` rows from ``examples/vocab.json`` (608 unique content
    words from one real Nippon Sangoku episode) — the shared corpus for ``--timeline``'s cues and,
    with ``--timeline-head-prefetch``, its synthetic known-word set."""
    return json.loads(Path(__file__).with_name("vocab.json").read_text(encoding="utf-8"))


def _timeline_cues(
    words: list[list[str]], cue_words: int, max_cues: int, dwell_s: float
) -> list[Cue]:
    """Synthetic subtitle cues built from the REAL episode vocabulary, grouped into short cue-sized
    chunks in original appearance order. Not grammatical sentences — just concatenated surfaces the
    real tokenizer re-segments — but real per-episode word content and frequency distribution, unlike
    an isolated word list (``--vocab``) or a hand-picked heavy-word churn (``--stress``)."""
    groups = [words[i : i + cue_words] for i in range(0, len(words), cue_words)]
    if max_cues > 0:
        groups = groups[:max_cues]
    return [
        Cue(start=i * dwell_s, end=(i + 1) * dwell_s, text="".join(w[0] for w in g))
        for i, g in enumerate(groups)
    ]


def _timeline_scorer(words: list[list[str]]):
    """A :class:`~saitenka.app.scoring.Scorer` for ``--timeline-head-prefetch``: marks every 8th
    vocabulary word as the lone "unknown" one and the rest ``known``, so ``mark_n_plus_one``'s
    exactly-one-unknown-content-word rule can actually fire on our punctuation-less synthetic cues
    (a real subtitle line has sentence breaks; ours doesn't) — a realistic mostly-known viewer with
    occasional n+1 gaps, not "nothing is known" (which would never trigger n+1 at all) or "everything
    is content" (which would trigger it on almost every word). ``min_sentence_words=1`` because our
    cues are short chunks, not real multi-clause sentences."""
    from saitenka.app.scoring import Scorer
    from saitenka.app.wordlists import KnownWords

    known = {w[0] for i, w in enumerate(words) if i % 8 != 0}
    # Surface-known with the "" (no-reading-taught) sentinel → matches any reading, i.e. the old flat
    # set's unconditional-known semantics under the reading-aware KnownWords.
    return Scorer(
        known=KnownWords(by_surface={surface: {""} for surface in known}, readings=set()),
        min_sentence_words=1,
    )


def _cold_reader(ds, *, prefetch: bool = False, panel_cache_max: int | None = None):
    """A fresh SessionController on a fake IPC, head-path forced (as a live run with workers would). With
    ``prefetch=True`` the real background workers run (``start_prefetch``), so scroll-ahead warms the
    next blocks off the main thread exactly as a live session does — the realistic scroll path."""
    if panel_cache_max is None:
        reader = SessionController(_fake_ipc(), dict_set=ds, prefetch=prefetch)
    else:
        reader = SessionController(
            _fake_ipc(), dict_set=ds, prefetch=prefetch, panel_cache_max=panel_cache_max
        )
    reader.osd = OSD
    if prefetch:
        reader.start_prefetch()
    return reader


def _bench_word(reader, term: str, reading: str, reps: int) -> dict:
    """Cold first-paint for one word through the real reader path (head render + BGRA + upload)."""
    from saitenka.app.subtitles import WordBox

    tok = Token(term, term, reading, "名詞", 0, len(term))
    reader.tokens = [tok]
    reader.boxes = [WordBox(0, 400, 800, 60, 60)]
    reader.sub_origin = (0, 0)

    def cold():
        reader.tooltip_controller.surface_state().panel_cache.clear()
        reader.tooltip_controller.surface_state().view.state = None
        reader.tooltip_controller.select(0)
        reader._show_tooltip(0)

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
    from saitenka.app.dictdb import DictionaryDb
    from saitenka.app.dictionary import DictionarySet

    fresh_db = DictionaryDb.open(ds.dicts[0].db.path)
    fresh = DictionarySet.from_db(fresh_db, [d.title for d in ds.dicts])
    fresh_reader = _cold_reader(fresh)
    t0 = time.perf_counter()
    first_term, first_reading = corpus[0][1], corpus[0][2]
    ftok = Token(first_term, first_term, first_reading, "名詞", 0, len(first_term))
    from saitenka.app.subtitles import WordBox

    fresh_reader.tokens = [ftok]
    fresh_reader.boxes = [WordBox(0, 400, 800, 60, 60)]
    fresh_reader.sub_origin = (0, 0)
    fresh_reader.tooltip_controller.select(0)
    fresh_reader._show_tooltip(0)
    first_hover_ms = (time.perf_counter() - t0) * 1000.0

    gil_rc = finalize_runtime(rt, require_ft)
    print(f"\nSaitenka overlay — PATHOLOGICAL cold-first-paint benchmark   ({tag})")
    print(format_runtime(rt))
    print(
        f"osd: {OSD[0]}x{OSD[1]}   tip_width: {reader.tip_scale.width}   cap: {reader.tip_scale.cap}px   "
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


def run_render_cache(
    reps: int, rt: dict, require_ft: bool = False, json_path: str | None = None
) -> int:
    """A/B the persistent render cache (#149) on the pathological cold-first-paint corpus: for each word,
    the COLD first paint with no disk entry (full head raster) vs. after a prior session's precompose was
    persisted to disk (the seed → copy+upload fast path, skipping the raster). Isolates exactly what the
    cache buys on the > 16 ms cold tail."""
    import os
    import tempfile

    from saitenka.app.config import ReaderOptions, TooltipOptions

    ds, tag = _load_dict_set()
    if ds is None:
        print("render-cache A/B needs the real dict set (overlay.toml) — nothing to measure")
        return 1

    cache_dir = tempfile.mkdtemp(prefix="saitenka-bench-render-cache-")
    os.environ["SAITENKA_CACHE_DIR"] = cache_dir  # paths.cache_dir() reads this each call
    opts = ReaderOptions(tooltip=TooltipOptions(render_cache=True), prefetch=False)
    reader = SessionController(_fake_ipc(), dict_set=ds, options=opts)
    reader.osd = OSD
    cap = reader.tip_scale.cap
    # The render cache is USE-WHEN-AVAILABLE (opens only if the file exists; prewarm is the builder), so
    # create the file up front — otherwise prime's store is a no-op and every peek misses. Mirrors prewarm.
    from pathlib import Path

    from saitenka.app.render_cache import RenderCache

    _rc = RenderCache.open(
        Path(cache_dir) / "render-cache.sqlite",
        max_bytes=reader.tooltip_preparation.cache.max_bytes,
    )
    if _rc is not None:
        _rc.close()
    corpus = _pathological_corpus(ds)

    from saitenka.app.subtitles import WordBox

    def prime(term: str, reading: str) -> None:
        """Simulate a prior session / `saitenka prewarm`: build + precompose + persist the head, using
        the SAME (inflected, mined, phrase) the show computes so the content_key matches at hover time."""
        tok = Token(term, term, reading, "名詞", 0, len(term))
        reader.tokens = [tok]
        reader.boxes = [WordBox(0, 400, 800, 60, 60)]
        reader.sub_origin = (0, 0)
        inflected = reader._inflected_surface(0)
        st = reader._panel_for(tok, inflected, min_h=cap, mined=False)
        reader.tooltip_preparation.cache.precompose_head(
            reader._preparation_inputs,
            st,
            tok,
            inflected,
            mined=False,
            cap=cap,
        )

    gil_rc = finalize_runtime(rt, require_ft)
    print(f"\nSaitenka overlay — RENDER CACHE A/B (#149)   ({tag})")
    print(format_runtime(rt))
    print(
        f"osd: {OSD[0]}x{OSD[1]}   tip_width: {reader.tip_scale.width}   cap: {cap}px   "
        f"gate: full_h ≥ {reader.tooltip_preparation.cache.min_height}px   reps/word: {reps}"
    )
    import saitenka.app.features.tooltip.tooltip as _tt

    def perceived_paint(term: str, reading: str) -> float | None:
        """Time-to-pixels the user actually feels on a cold cache HIT: place + decorate + upload the
        cached first viewport (the direct-paint path), skipping the build that now runs AFTER the paint.
        ``None`` when the entry is below the cost gate (not stored) — a cheap-cold word the cache skips."""
        tok = Token(term, term, reading, "名詞", 0, len(term))
        reader.tokens = [tok]
        reader.boxes = [WordBox(0, 400, 800, 60, 60)]
        reader.sub_origin = (0, 0)
        reader.retire_hover()
        key = reader._panel_key(tok, reader._inflected_surface(0), mined=False)
        if reader.tooltip_preparation.cache.peek(reader._preparation_inputs, key) is None:
            return None  # below the cost gate — not persisted

        def paint() -> None:
            _tt._paint_from_cache(reader._tip_ports, key, cap, (0, 400, 60))

        return measure(paint, reps, warmup=1)["p50"]

    hdr = f"{'word':10} {'source':26} {'cold-show':>9} {'paint':>8} {'Δ':>8} {'hit?':>5}   (ms)"
    print(hdr)
    print("-" * len(hdr))
    all_un, all_ca, collected = [], [], {}
    for source, term, reading in corpus:
        un = _bench_word(reader, term, reading, reps)[
            "p50"
        ]  # cold: full build+measure+raster+upload
        prime(term, reading)  # persist this head to disk (same key the show computes)
        ca = perceived_paint(term, reading)  # cold cache-hit: what the user actually waits for
        all_un.append(un)
        if ca is not None:
            all_ca.append(ca)
        collected[f"{term} ({source})"] = {"cold_show_p50": un, "paint_p50": ca}
        ca_s = f"{ca:8.1f}" if ca is not None else f"{'—':>8}"
        d_s = f"{un - ca:8.1f}" if ca is not None else f"{'—':>8}"
        print(
            f"{term:10} {source[:26]:26} {un:9.1f} {ca_s} {d_s} {'yes' if ca is not None else 'no':>5}"
        )
    print("-" * len(hdr))
    print(
        f"{'MEDIAN':10} {'over all words':26} {statistics.median(all_un):9.1f} "
        f"{statistics.median(all_ca):8.1f} {statistics.median(all_un) - statistics.median(all_ca):8.1f}"
    )
    print(
        "\ncold-show = full cold pipeline (build+measure+raster+upload); paint = perceived first-paint on"
        "\na cache hit (place+decorate+upload cached pixels — the real panel builds AFTER, off this path)."
    )
    if json_path:
        Path(json_path).write_text(
            json.dumps({"runtime": rt, "osd": OSD, "metrics": collected}, indent=2),
            encoding="utf-8",
        )
        print(f"\nwrote metrics → {json_path}")
    return gil_rc


def _atlas_render_pass(reader, corpus, cap, *, count_rasters: bool):
    """Render every corpus word's first viewport once (fresh glyph memo per word so the atlas, not the
    in-proc memo, is what's exercised). Returns ``(total_getmask2_calls, per_word_render_ms)``."""
    import time as _time

    from saitenka import fonts
    from saitenka.app.subtitles import WordBox

    n = {"r": 0}
    orig = fonts.ImageFont.FreeTypeFont.getmask2

    def counting(self, *a, **k):
        n["r"] += 1
        return orig(self, *a, **k)

    if count_rasters:
        fonts.ImageFont.FreeTypeFont.getmask2 = counting  # type: ignore[method-assign]  # PIL instrumentation
    times: list[float] = []
    try:
        for _source, term, reading in corpus:
            fonts._tls.__dict__.pop("masks", None)  # cold per-thread memo → measure atlas vs raster
            tok = Token(term, term, reading, "名詞", 0, len(term))
            reader.tokens = [tok]
            reader.boxes = [WordBox(0, 400, 800, 60, 60)]
            reader.sub_origin = (0, 0)
            t0 = _time.perf_counter()
            reader._panel_for(tok, term, min_h=cap, mined=False).precompose_head(cap)
            times.append((_time.perf_counter() - t0) * 1000)
    finally:
        fonts.ImageFont.FreeTypeFont.getmask2 = orig  # type: ignore[method-assign]  # PIL instrumentation
    return n["r"], times


def run_mask_atlas(rt: dict, require_ft: bool = False, json_path: str | None = None) -> int:
    """A/B the persistent glyph mask atlas (#149 Tier-1) on the pathological corpus: a COLD render
    rasterises every glyph via getmask2 (~half the render CPU); with a disk-loaded atlas those masks come
    from RAM, so getmask2 is skipped. Isolates the raster-skip rate + render wall-time the atlas buys. A
    FRESH reader per phase keeps the panel cache from hiding the effect (a cached panel wouldn't re-raster)."""
    import os
    import tempfile

    from saitenka import fonts, mask_atlas
    from saitenka.app.config import ReaderOptions, TooltipOptions
    from saitenka.app.paths import cache_dir as _cd

    ds, tag = _load_dict_set()
    if ds is None:
        print("mask-atlas A/B needs the real dict set (overlay.toml) — nothing to measure")
        return 1

    os.environ["SAITENKA_CACHE_DIR"] = tempfile.mkdtemp(prefix="saitenka-bench-mask-atlas-")
    opts = ReaderOptions(
        tooltip=TooltipOptions(render_cache=False, mask_atlas=True), prefetch=False
    )
    corpus = _pathological_corpus(ds)
    atlas_path = _cd() / "mask-atlas.sqlite"

    # Phase A — COLD: render with atlas WRITE on (builds the atlas), counting getmask2 rasterisations.
    reader_a = SessionController(_fake_ipc(), dict_set=ds, options=opts)
    reader_a.osd = OSD
    cap = reader_a.tip_scale.cap
    atlas = mask_atlas.MaskAtlas.open(atlas_path)
    if atlas is None:
        raise RuntimeError(f"failed to open mask atlas at {atlas_path}")
    fonts.set_mask_atlas(None, atlas)
    cold_rasters, cold_ms = _atlas_render_pass(reader_a, corpus, cap, count_rasters=True)
    atlas.checkpoint()

    # Phase B — WARM: fresh reader (empty panel cache) + the atlas bulk-loaded into RAM, atlas READ on.
    mem: dict = {}
    n_masks = atlas.load_into(mem)
    fonts.set_mask_atlas(mem, None)
    reader_b = SessionController(_fake_ipc(), dict_set=ds, options=opts)
    reader_b.osd = OSD
    warm_rasters, warm_ms = _atlas_render_pass(reader_b, corpus, cap, count_rasters=True)
    fonts.set_mask_atlas(None, None)

    gil_rc = finalize_runtime(rt, require_ft)
    print(f"\nSaitenka overlay — MASK ATLAS A/B (#149 Tier-1)   ({tag})")
    print(format_runtime(rt))
    atlas_mb = atlas_path.stat().st_size / 1e6
    print(
        f"osd: {OSD[0]}x{OSD[1]}   words: {len(corpus)}   atlas: {n_masks:,} masks / {atlas_mb:.1f} MB"
    )
    skip = 100 * (1 - warm_rasters / cold_rasters) if cold_rasters else 0.0
    print(
        f"getmask2 rasterisations:  cold {cold_rasters:,}  →  warm {warm_rasters:,}   "
        f"(raster-skip {skip:.1f}%)"
    )
    print(
        f"render wall-time median:  cold {statistics.median(cold_ms):.1f} ms  →  "
        f"warm {statistics.median(warm_ms):.1f} ms"
    )
    if json_path:
        Path(json_path).write_text(
            json.dumps(
                {
                    "runtime": rt,
                    "atlas_masks": n_masks,
                    "atlas_mb": atlas_mb,
                    "cold_rasters": cold_rasters,
                    "warm_rasters": warm_rasters,
                    "raster_skip_pct": skip,
                    "cold_ms_median": statistics.median(cold_ms),
                    "warm_ms_median": statistics.median(warm_ms),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote metrics → {json_path}")
    return gil_rc


def _rss_mb() -> float:
    """Resident set size in MB (cross-platform via psutil, a runtime dep) — captures the numpy/Pillow
    C buffers that tracemalloc, being Python-only, misses. The primary memory signal for the stress."""
    import psutil

    return psutil.Process().memory_info().rss / 1e6


def _synthetic_stress_terms() -> list[tuple[str, str]]:
    """A generated fallback larger than the forced cache cap, so CI exercises eviction."""
    return [(f"語{index:02d}", f"ご{index}") for index in range(_STRESS_CACHE_CAP + 8)]


def stress_to_bench_json(metrics: dict) -> list[dict]:
    """Project the lifecycle signals onto customSmallerIsBetter."""
    frames = metrics["frame_latency_ms"]
    return [
        {"name": "lifecycle: frame p99", "unit": "ms", "value": frames["p99"]},
        {"name": "lifecycle: worst frame", "unit": "ms", "value": frames["max"]},
        {
            "name": "lifecycle: RSS growth",
            "unit": "MB",
            "value": max(0.0, metrics["rss_growth_mb"]),
        },
    ]


def run_stress(
    reps: int,
    rt: dict,
    require_ft: bool = False,
    json_path: str | None = None,
    max_frame_ms: float | None = None,
    max_rss_mb: float | None = None,
    bench_json: str | None = None,
) -> int:
    """A sustained, DETERMINISTIC chained session — cold hover → scroll → nested popup → scroll →
    dismiss — over a corpus of distinct heavy entries, repeated ``reps`` rounds. Unlike the isolated
    micro-benchmarks it exercises what only shows under load: panel-cache eviction thrash, nested-state
    churn, and memory growth across a long session. Reports the per-op frame-latency tail (MAX is the
    jank signal) + peak RSS + growth, and can gate on ``--max-frame-ms`` / ``--max-rss-mb``."""
    import tracemalloc

    from saitenka.app.dictionary import DictionarySet
    from saitenka.app.subtitles import WordBox

    ds, tag = _load_dict_set()
    if ds is None:
        ds = _SyntheticDS()
    reader = _cold_reader(ds, panel_cache_max=_STRESS_CACHE_CAP)
    # The cache cap is a TEST CONTROL, not the user's live [tooltip].panel_cache_max — fix it small
    # and deterministic so eviction is exercised the same way regardless of how many dicts are
    # configured or what the user's own cap is. Scaling the corpus to chase a large live cap instead
    # blows up wall time / memory for reasons unrelated to what's being measured.
    if isinstance(ds, DictionarySet) and ds.dicts:
        # A fixed corpus comfortably larger than the fixed cap — forces real eviction thrash without
        # depending on the live config.
        per_dict = max(3, _STRESS_CACHE_CAP // len(ds.dicts) + 2)
        corpus = [(t, r) for _s, t, r in _pathological_corpus(ds, per_dict=per_dict)]
    else:
        corpus = _synthetic_stress_terms()
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
        reader.tooltip_controller.select(0)
        timed(lambda: reader._show_tooltip(0))
        for _ in range(4):  # scroll toward the bottom of a tall entry
            timed(lambda: reader.scroll_tip(step))
        st = reader.tooltip_controller.surface_state().view.state
        boxes = st.windowed.scan_boxes() if st else []
        if boxes:
            sb = boxes[len(boxes) // 3]  # a deterministic cell well inside the body
            timed(
                lambda: nested_popup.show_nested(
                    reader._tip_ports, reader._panel_ports, reader.word_lookup, sb
                )
            )
            timed(lambda: reader.scroll_tip(step))  # scroll while the nested popup is up
            timed(reader._hide_nested)
        timed(lambda: reader.retire_hover())  # dismiss the whole stack

    for term, reading in corpus:  # one warmup round before the memory baseline
        one_word(term, reading)
    frames.clear()
    tracemalloc.start()
    rss_base = _rss_mb()
    rss_peak = rss_base
    rss_by_round: list[float] = []
    for _ in range(max(1, reps)):
        for term, reading in corpus:
            one_word(term, reading)
        rss_by_round.append(_rss_mb())
        rss_peak = max(rss_peak, rss_by_round[-1])
    _cur, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    m = _stats(frames)
    cache_len = len(reader.tooltip_controller.surface_state().panel_cache)
    growth = rss_peak - rss_base
    gil_rc = finalize_runtime(rt, require_ft)

    print(
        f"\nSaitenka overlay — STRESS: chained scan/scroll/nested over {len(corpus)} distinct heavy "
        f"entries × {reps} rounds   ({tag})"
    )
    print(format_runtime(rt))
    print(f"osd: {OSD[0]}x{OSD[1]}   tip_width: {reader.tip_scale.width}   ops timed: {m['n']}")
    print(
        f"\nper-op frame latency:  p50 {m['p50']:.1f}  p95 {m['p95']:.1f}  p99 {m['p99']:.1f}  "
        f"MAX {m['max']:.1f} ms  (cv {m['cv']:.2f})"
    )
    print(
        f"panel cache: {cache_len}/{reader.tooltip_controller.cache_limit} entries "
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
    metrics = {
        "runtime": rt,
        "osd": OSD,
        "rounds": reps,
        "corpus_size": len(corpus),
        "frame_latency_ms": m,
        "panel_cache_len": cache_len,
        "panel_cache_max": reader.tooltip_controller.cache_limit,
        "rss_peak_mb": rss_peak,
        "rss_growth_mb": growth,
        "rss_by_round_mb": rss_by_round,
        "py_obj_peak_mb": py_peak / 1e6,
    }
    if json_path:
        Path(json_path).write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        print(f"\nwrote stress baseline → {json_path}")
    if bench_json:
        Path(bench_json).write_text(
            json.dumps(stress_to_bench_json(metrics), indent=2), encoding="utf-8"
        )
        print(f"wrote github-action-benchmark JSON → {bench_json}")
    return rc


def _scroll_span_live(reader, subset, step: int, span_px: int, speed: float) -> list[float]:
    """Scroll each entry the first ``span_px`` at ``speed`` px/s (dwell = step/speed between notches) on
    a reader whose real prefetch workers run, timing each frame. The dwell is the wall-clock the worker
    gets to render the next blocks ahead — so this captures both the off-thread warming AND the
    worker/compositor contention the analytical model omits."""
    from saitenka.app.subtitles import WordBox

    dwell = step / speed
    frames: list[float] = []
    for term, reading, _tops, _times in subset:
        reader.tooltip_controller.surface_state().panel_cache.clear()
        reader.tokens = [Token(term, term, reading, "名詞", 0, len(term))]
        reader.boxes = [WordBox(0, 400, 800, 60, 60)]
        reader.sub_origin = (0, 0)
        reader.tooltip_controller.select(0)
        reader._show_tooltip(0)
        if reader.tooltip_controller.surface_state().view.state is None:
            continue
        reader.tooltip_controller.surface_state().view.scroll = 0
        for _s in range(max(1, span_px // step)):
            t0 = time.perf_counter()
            reader.scroll_tip(step)  # scroll_tip requests render-ahead in this direction
            frames.append((time.perf_counter() - t0) * 1000.0)
            time.sleep(dwell)
    return frames


# Realistic scroll paces as px/s (a wheel notch is ``step`` px, so dwell = step/speed): a fast FLICK to
# scan, a NORMAL spin, a slow READING crawl. The jank question is per-pace — the worker hides a block
# only if it renders in the LEAD time the pace allows before the viewport reaches it.
_SCROLL_SPEEDS = ((2600.0, "flick"), (1000.0, "normal"), (300.0, "reading"))
_SCROLL_SPAN_PX = 6000  # realistic distance scrolled into an entry: head + the first big blocks


def _block_profile(reader, term: str, reading: str, span_px: int) -> tuple[list[int], list[float]]:
    """Render each BAND covering the first ``span_px`` of an entry ONCE, returning parallel lists of
    (band-top px, band render ms). Reaches into WindowedPanel internals deliberately — this IS the
    per-band raster cost the scroll pays. The ~200ms SC-walk is EXCLUDED (measure runs it once, ahead,
    and the memoised layout handle serves every band's ``render_window``) — so the times are the pure
    ``getmask2`` a scroll frame pays, which the lead model then hides band-by-band."""
    from saitenka.app.subtitles import WordBox
    from saitenka.render.banded import _row_bands

    reader.tooltip_controller.surface_state().panel_cache.clear()
    reader.tokens = [Token(term, term, reading, "名詞", 0, len(term))]
    reader.boxes = [WordBox(0, 400, 800, 60, 60)]
    reader.sub_origin = (0, 0)
    reader.tooltip_controller.select(0)
    reader._show_tooltip(0)
    st = reader.tooltip_controller.surface_state().view.state
    if st is None:
        return [], []
    wp = st.windowed
    tops: list[int] = []
    times: list[float] = []
    with wp._lock:
        wp._grow_prefix(
            span_px
        )  # measure (walk + wrap) every row in the span — the memoised layouts
        for i in range(wp._offsets.prefix_len):
            row_top = wp._offsets.start(i)  # exact: the whole prefix is measured
            if row_top > span_px and tops:
                break
            row = wp._rows[i]
            if row.render_window is None:  # non-body — one small band, whole-row render
                t0 = time.perf_counter()
                row.render()
                times.append((time.perf_counter() - t0) * 1000.0)
                tops.append(row_top)
                continue
            for _b, y0, y1 in _row_bands(wp._offsets.height(i)):
                if row_top + y0 > span_px and tops:
                    break
                t0 = time.perf_counter()
                row.render_window(y0, y1)  # pure band getmask2 — layout already memoised by measure
                times.append((time.perf_counter() - t0) * 1000.0)
                tops.append(row_top + y0)
    return tops, times


def _simulate_jank(
    tops: list[int], times: list[float], view_h: int, step: int, speed: float
) -> tuple[int, float, int]:
    """Discrete-event lead model: scroll at ``speed`` px/s while a single worker renders blocks in order,
    starting each when render-ahead requests it (viewport within one screen of it) and it is free. A
    block the viewport reaches before the worker finished is rendered synchronously on the main thread —
    a jank frame. Returns (jank frames, worst synchronous ms, notches over the span). No contention
    modelled — that is what the real-threading pass adds."""
    lead_px = 2 * view_h  # render-ahead requests a block when viewport-bottom is one screen from it
    worker_free = 0.0
    jank: list[float] = []
    for top, rms in zip(tops, times, strict=True):
        t_enter = max(0.0, (top - view_h) / speed)  # viewport bottom reaches the block's top
        t_request = max(0.0, (top - lead_px) / speed)  # render-ahead asks for it
        finish = max(worker_free, t_request) + rms / 1000.0
        if finish <= t_enter:
            worker_free = finish  # worker rendered it ahead — hidden
        else:
            jank.append(rms)  # reached first → synchronous render on the scroll thread
            worker_free = t_enter
    span = (tops[-1] - tops[0]) if len(tops) > 1 else step
    return len(jank), (max(jank) if jank else 0.0), max(1, span // step)


def run_scroll_jank(reps: int, rt: dict, require_ft: bool, json_path: str | None = None) -> int:
    """Scroll-ISOLATED jank: show each heavy/tall entry's BASE tooltip and scroll it top→bottom, timing
    each scroll re-composite on its own. Unlike --stress (scroll mixed with hover + nested in one p99), a
    jank tail here IS the scroll frame. Cold = scrolling into un-rendered tail blocks (they rasterise as
    the viewport reaches them — the real jank); a warm re-traverse of the now-cached blocks is the floor.
    A cold ≫ warm gap ⇒ the jank is cold-block render (getmask2), which idle prefetch hides in real use
    only when it rendered ahead (see --timeline)."""
    from saitenka.app.dictionary import DictionarySet
    from saitenka.app.subtitles import WordBox
    from saitenka.render.banded import _BAND_PX

    ds, tag = _load_dict_set()
    if ds is None:
        ds = _SyntheticDS()
    reader = _cold_reader(ds)
    if isinstance(ds, DictionarySet) and ds.dicts:
        corpus = [(t, r) for _s, t, r in _pathological_corpus(ds)]
    else:
        corpus = [(w, w) for w in ("かける", "する", "手", "気", "出る")]
    step = round(OSD[1] * 0.08)  # one wheel step
    cap = reader.tip_scale.cap
    cold: list[float] = []
    warm: list[float] = []
    worst: list[tuple[float, str, int]] = []

    def show(term: str, reading: str) -> None:
        reader.tokens = [Token(term, term, reading, "名詞", 0, len(term))]
        reader.boxes = [WordBox(0, 400, 800, 60, 60)]
        reader.sub_origin = (0, 0)
        reader.tooltip_controller.select(0)
        reader._show_tooltip(0)

    def traverse(bucket: list[float], word: str | None) -> None:
        st = reader.tooltip_controller.surface_state().view.state
        if st is None:
            return
        reader.tooltip_controller.surface_state().view.scroll = 0
        # Scroll DOWN into the cold tail, capped: a 90 kpx entry is ~1000 wheel steps — far past what a
        # user scrolls, and every cold step rasterises a block. _SCROLL_JANK_STEPS samples enough cold
        # blocks to surface the jank without rendering the whole monster.
        steps = min(_SCROLL_JANK_STEPS, max(1, (st.full_height - cap) // step + 1))
        for _ in range(steps):
            t0 = time.perf_counter()
            reader.scroll_tip(step)
            bucket.append((time.perf_counter() - t0) * 1000.0)
            if word is not None:
                worst.append((bucket[-1], word, st.full_height))

    for term, reading in corpus:  # warmup: build each panel once
        show(term, reading)
    for _ in range(max(1, reps)):
        for term, reading in corpus:
            reader.tooltip_controller.surface_state().panel_cache.clear()  # cold panel → tail blocks rasterise DURING the scroll (jank)
            show(term, reading)  # untimed first paint (head only)
            traverse(cold, term)  # top→bottom over cold blocks — the scroll jank
            traverse(warm, None)  # immediate re-traverse: blocks now cached — the warm floor

    # Analytical envelope: measure each block's one-time render cost, then simulate scrolling the span
    # at each pace and count the frames where the viewport reaches a block before the worker warmed it
    # (the lead model — a monster block is rendered ONCE and cruised through as cache hits, so what
    # matters is lead time vs render time, not per-notch throughput). Covers every entry × every pace
    # cheaply because it uses no real-time sleeps.
    profiles = [
        (term, reading, *_block_profile(reader, term, reading, _SCROLL_SPAN_PX))
        for term, reading in corpus
    ]
    envelope: list[tuple[str, int, float, int]] = []  # (label, jank_frames, worst_ms, notches)
    for speed, label in _SCROLL_SPEEDS:
        jf = notches = 0
        wmax = 0.0
        for _term, _reading, tops, times in profiles:
            j, wm, n = _simulate_jank(tops, times, cap, step, speed)
            jf += j
            wmax = max(wmax, wm)
            notches += n
        envelope.append((label, jf, wmax, notches))

    # Real-threading confirmation: scroll the worst entries a realistic span at the fastest paces with
    # the real workers running, so the measured distribution includes the contention the model omits.
    subset = sorted(profiles, key=lambda p: -(max(p[3]) if p[3] else 0.0))[:6]
    live_reader = _cold_reader(ds, prefetch=True)
    real: list[tuple[str, dict]] = []
    try:
        for speed, label in _SCROLL_SPEEDS[:2]:  # flick + normal (reading pace is slow wall-clock)
            frames = _scroll_span_live(live_reader, subset, step, _SCROLL_SPAN_PX, speed)
            real.append((label, _stats(frames)))
    finally:
        live_reader._stop.set()

    c, w = _stats(cold), _stats(warm)
    gil_rc = finalize_runtime(rt, require_ft)
    print(
        f"\nSaitenka overlay — SCROLL JANK: base-tooltip scroll over {len(corpus)} pathological "
        f"entries × {reps} rounds   ({tag})"
    )
    print(format_runtime(rt))
    print(
        f"osd: {OSD[0]}x{OSD[1]}   tip_width: {reader.tip_scale.width}   cap: {cap}px   step: {step}px   "
        f"scroll frames: {c['n']}"
    )
    print(
        f"\ncold scroll frame (renders tail block):  p50 {c['p50']:.1f}  p95 {c['p95']:.1f}  "
        f"p99 {c['p99']:.1f}  MAX {c['max']:.1f} ms  (cv {c['cv']:.2f})"
    )
    print(
        f"warm scroll frame (blocks cached):       p50 {w['p50']:.1f}  p95 {w['p95']:.1f}  "
        f"p99 {w['p99']:.1f}  MAX {w['max']:.1f} ms"
    )
    print(
        f"\nlead model — scroll the first {_SCROLL_SPAN_PX}px warming BANDS ahead (no contention "
        f"modelled). Expectation post-PR3: worst first-reach ≤ ~1 band ({_BAND_PX}px ≈ 9-12ms), so "
        f"even an un-warmed frame lands under the 16ms budget — not a ~500ms whole-block stall:"
    )
    for label, jf, wmax, notches in envelope:
        smooth = 100.0 * (1 - jf / max(1, notches))  # % of bands the worker warmed before reach
        print(
            f"  {label:<8} {smooth:5.1f}% warmed-ahead   {jf:>3} first-reach band(s) / {notches} "
            f"notches   worst first-reach {wmax:.0f} ms"
        )
    print(
        f"\nreal threads — worst {len(subset)} entries, workers running, actual frame time "
        f"(includes contention):"
    )
    for label, r in real:
        print(
            f"  {label:<8} p50 {r['p50']:5.1f}  p95 {r['p95']:5.1f}  p99 {r['p99']:5.1f}  "
            f"MAX {r['max']:6.1f} ms"
        )
    print("\nworst cold scroll frames (word, ms, panel_px):")
    by_word: dict[str, tuple[float, int]] = {}
    for dt, word, px in worst:
        if dt > by_word.get(word, (0.0, 0))[0]:
            by_word[word] = (dt, px)
    for word, (dt, px) in sorted(by_word.items(), key=lambda kv: -kv[1][0])[:8]:
        print(f"    {word:<10}{dt:>9.1f}{px:>10}")
    print(
        "\ncold/warm isolate the synchronous per-BAND render (NO workers). The lead model then asks the "
        "real question: scrolling the span at each pace, how many frames reach a band before the worker "
        "warmed it? Pre-PR3 the unit was a whole def block (up to ~500ms getmask2), so a fast flick hit a "
        "monster stall; PR3 rasterises in ~256px bands (~9-12ms each, the SC-walk paid once by "
        "measure-ahead), so worst-first-reach collapses to ~1 band — under the 16ms budget even un-warmed. "
        "The real-threads rows confirm the model and expose the worker/compositor contention it omits "
        "(p50 under budget at every pace; p99 under the pace's inter-notch ceiling — 33ms at flick)."
    )
    if json_path:
        Path(json_path).write_text(
            json.dumps(
                {
                    "runtime": rt,
                    "cold": c,
                    "warm": w,
                    "lead_model": [
                        {"pace": label, "jank_frames": jf, "worst_ms": wmax, "notches": n}
                        for label, jf, wmax, n in envelope
                    ],
                    "real_threads": [{"pace": label, **r} for label, r in real],
                    "corpus_size": len(corpus),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote scroll-jank baseline → {json_path}")
    return gil_rc


def run_clicks(reps: int, rt: dict, require_ft: bool, json_path: str | None = None) -> int:
    """Click-surface latency — the per-click main-thread work #293 left uncovered: a sidebar action +
    full redraw, a bookmark toggle (SQLite), and the #253 mined-card link write (SQLite). These are
    per-click, not per-frame, so they never appeared in the hover/scroll benches. Driving them here gives
    ``sidebar_click`` / ``backlog_write`` / ``mined_store_write`` real percentiles (and, under
    ``bench_pyspy_all``'s telemetry+py-spy wrapper, their spans + CPU) next to the hover/scroll numbers.

    No Anki / mpv: the isolated cost is the durable STORE writes. A full mine's AnkiConnect + screenshot
    cost is its own ``anki_mine`` span, dominated by I/O — not what a click-stutter check measures."""
    import tempfile
    from types import SimpleNamespace

    import saitenka.app.features.mining.miner as miner_flow
    from saitenka.app import backlog
    from saitenka.app.anki import MineConfig
    from saitenka.app.features.mining import mined_store
    from saitenka.app.features.sidebar import sidebar
    from saitenka.subtitles import Cue, CueIndex

    tmp = Path(tempfile.mkdtemp(prefix="saitenka-clicks-"))
    ipc = FakeIPC()
    ipc.props.update(  # capture_current / _persist_mined read these via _get
        {
            "path": "/x/Nippon Sangoku - 01.mkv",
            "sub-start": 0.0,
            "sub-end": 1.8,
            "track-list": [],
            "time-pos": 1.0,
        }
    )
    mined_store._DB_PATH_OVERRIDE = tmp / "mined.sqlite"
    reader = SessionController(
        cast("MpvIPC", ipc), anki=SimpleNamespace(), mine_cfg=MineConfig(deck="Mining")
    )
    reader.osd = OSD
    cues = [Cue(i * 2.0, i * 2.0 + 1.8, f"これは{i}番目の字幕です") for i in range(60)]
    reader.episode.sub_index = CueIndex(cues)
    reader.sub_text = cues[0].text
    reader.session.backlog_store = backlog.BacklogStore(tmp / "backlog.sqlite")
    ports = reader.mining_controller._operation()
    assert ports is not None

    # Open + render the sidebar so on_click has real hitboxes; click a view-tab so the measured cost is
    # the click dispatch + full redraw ALONE (a bookmark/mine hit would fold a store write into it — we
    # measure those separately below).
    sidebar.show(reader.sidebar_view)
    sidebar.draw(reader.sidebar_view)
    panel = reader.sidebar_controller.panel
    hits = panel.hits
    tab = next((h for h in hits if h.kind.startswith("view:")), hits[0] if hits else None)
    note_id = {"n": 0}

    def click_sidebar() -> None:
        if tab is None or panel.rect is None:
            return
        reader.sidebar_controller.on_click(
            reader._click_target,
            panel.rect[0] + tab.x + 1,
            panel.rect[1] + tab.y + 1,
        )

    def bookmark() -> None:
        backlog.capture_current(
            reader.capture_ports
        )  # toggles create/delete each call — both are writes

    def persist_mine() -> None:
        note_id["n"] += 1
        miner_flow._persist_mined(
            ports,
            note_id["n"],
            SimpleNamespace(expression="猫", reading="ねこ"),
            ports.encounter.media_path,
        )

    sc, bk, mn = (
        measure(click_sidebar, reps),
        measure(bookmark, reps),
        measure(persist_mine, reps),
    )
    gil_rc = finalize_runtime(rt, require_ft)
    print(f"\nSaitenka overlay — CLICKS: per-click main-thread cost × {reps} reps")
    print(format_runtime(rt))
    print(f"\n{'op':<22}{'p50':>8}{'p95':>8}{'p99':>8}{'max':>8}  (ms)")
    print("-" * 62)
    for label, s in (
        ("sidebar_click", sc),
        ("backlog_write (bookmark)", bk),
        ("mined_store_write", mn),
    ):
        print(f"{label:<22}{s['p50']:>8.2f}{s['p95']:>8.2f}{s['p99']:>8.2f}{s['max']:>8.2f}")
    print(
        "\nper-click surfaces (not per-frame): main-thread SQLite / a sidebar redraw. A p50 well under the "
        "16ms frame budget = a click can't stutter; a p99 near/over it is the trigger to move that work "
        "off-thread (plan Gap 4) — note WHICH op: a store write vs the sidebar REDRAW (a tab switch also "
        "queries the store to fill the view), which are different fixes. Under bench_pyspy_all these emit "
        "sidebar_click / backlog_write / mined_store_write spans (span_percentiles) + py-spy CPU."
    )
    if json_path:
        Path(json_path).write_text(
            json.dumps(
                {"runtime": rt, "sidebar_click": sc, "backlog_write": bk, "mined_store_write": mn},
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote clicks baseline → {json_path}")
    return gil_rc


# Module-level worker state for the --vocab --parallel path (ProcessPool workers each rebuild the DB in
# the initializer; threads share the copy run_vocab sets). Top-level so ProcessPool can pickle by name.
_VOCAB_DS = None


def _vocab_init() -> None:
    global _VOCAB_DS
    _VOCAB_DS = _load_dict_set()[0]


def _vocab_render(job: tuple[str, str, str, str, int]) -> int:
    """Render one word's windowed viewport — the shipping compositor path. Self-contained over a
    picklable tuple so it works as a ProcessPool task or a thread task."""
    from saitenka.app.tokenize import Token
    from saitenka.render.banded import WindowedPanel

    surface, lemma, reading, pos, width = job
    assert _VOCAB_DS is not None
    entry = _VOCAB_DS.entry_for(Token(surface, lemma, reading, pos, 0, len(surface)))
    return WindowedPanel(panel_rows(entry, width), width).viewport(0, 432, overscan=80).height


def to_bench_json(metrics: dict) -> list[dict]:
    """Map the synth metrics dict to github-action-benchmark's ``customSmallerIsBetter`` array —
    ``[{name, unit, value, range}]``, smaller = better → fits ms latency with no benchmark rewrite. The
    measured CV becomes the ``range`` band so run-to-run variance shows on the gh-pages chart. A metric
    absent from ``metrics`` is omitted, never emitted as ``null``."""
    out: list[dict] = []
    for name, val_key, cv_key in (
        ("synth median render", "synth_median_ms", "synth_median_cv"),
        ("synth p99 render", "synth_p99_ms", "synth_p99_cv"),
    ):
        value = metrics.get(val_key)
        if value is None:
            continue
        entry: dict = {"name": name, "unit": "ms", "value": round(value, 3)}
        cv = metrics.get(cv_key)
        if cv:
            entry["range"] = f"±{cv * 100:.1f}%"
        out.append(entry)
    return out


def _percentile(samples: list[float], q: float) -> float:
    s = sorted(samples)
    return s[min(len(s) - 1, int(q * len(s)))]


def _cv(xs: list[float]) -> float:
    """Coefficient of variation (stdev/mean) — the run-to-run noise #33 asks to characterize. 0 for a
    single sample or a zero mean."""
    return (
        statistics.pstdev(xs) / statistics.mean(xs) if len(xs) > 1 and statistics.mean(xs) else 0.0
    )


def run_synth(
    reps: int,
    rt: dict,
    require_ft: bool = False,
    json_path: str | None = None,
    *,
    loops: int = 1,
    bench_json: str | None = None,
    n: int = 60,
) -> int:
    """Dict-free deterministic render benchmark — the CI/asv-safe gate target. For each entry in
    :func:`synth_corpus` it times the shipping windowed viewport render (``WindowedPanel.viewport`` — the
    real hover compositor cost, minus the dict lookup), so the same numbers come out on any machine and
    any commit. ``--loops`` repeats the whole corpus to characterize run-to-run variance (CV), surfaced
    in the JSON and, via :func:`to_bench_json`, as the chart's variance band."""
    from saitenka.render.banded import WindowedPanel

    corpus = synth_corpus(n)
    width = 640

    gil_rc = finalize_runtime(rt, require_ft)
    print("\nSaitenka overlay — SYNTH render benchmark (dict-free, deterministic)")
    print(format_runtime(rt))
    print(f"entries: {len(corpus)}   reps: {reps}   loops: {loops}   tip_width: {width}\n")

    for (
        _hw,
        entry,
    ) in corpus:  # warm caches/imports so the first-render outlier doesn't inflate p99 CV
        WindowedPanel(panel_rows(entry, width), width).viewport(0, 432, overscan=80)

    loop_median: list[float] = []
    loop_p99: list[float] = []
    all_ms: list[float] = []
    for _loop in range(loops):
        loop_ms: list[float] = []
        for _ in range(reps):
            for _hw, entry in corpus:
                t0 = time.perf_counter()
                WindowedPanel(panel_rows(entry, width), width).viewport(0, 432, overscan=80)
                loop_ms.append((time.perf_counter() - t0) * 1000.0)
        loop_median.append(statistics.median(loop_ms))
        loop_p99.append(_percentile(loop_ms, 0.99))
        all_ms.extend(loop_ms)

    metrics: dict[str, Any] = {
        "runtime": rt,
        "entries": len(corpus),
        "reps": reps,
        "loops": loops,
        "synth_median_ms": statistics.median(all_ms),
        "synth_p99_ms": _percentile(all_ms, 0.99),
        "synth_max_ms": max(all_ms),
        "synth_median_cv": _cv(loop_median),
        "synth_p99_cv": _cv(loop_p99),
    }
    print(
        f"  median {metrics['synth_median_ms']:7.2f}   p99 {metrics['synth_p99_ms']:7.2f}   "
        f"MAX {metrics['synth_max_ms']:7.2f}  ms"
    )
    if loops > 1:
        print(
            f"  CV over {loops} loops: median {metrics['synth_median_cv'] * 100:.1f}%   "
            f"p99 {metrics['synth_p99_cv'] * 100:.1f}%"
        )
    if json_path:
        Path(json_path).write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        print(f"\nwrote synth baseline → {json_path}")
    if bench_json:
        Path(bench_json).write_text(json.dumps(to_bench_json(metrics), indent=2), encoding="utf-8")
        print(f"wrote github-action-benchmark JSON → {bench_json}")
    return gil_rc


def run_vocab(
    vocab_path: str,
    reps: int,
    rt: dict,
    require_ft: bool,
    json_path: str | None = None,
    *,
    parallel: bool = False,
    workers: int = 8,
) -> int:
    """Render-pipeline benchmark over a FROZEN word list (e.g. one anime episode's unique content words,
    extracted once so the subtitle parser/tokenizer is out of the hot loop). For each word: build the
    Token, ``entry_for`` + the windowed viewport render (the shipping hover cost) and the cold head
    paint. Reports the latency distribution + the slowest words, and is the intended py-spy target — the
    CPU here is entirely dict lookup + glossary decode + SC-walk + document layout, so a sampling profile
    pinpoints which of those dominates the tail."""
    global _VOCAB_DS
    ds, tag = _load_dict_set()
    if ds is None:
        print("vocab benchmark needs the real dict set (overlay.toml) — nothing to measure")
        return 1
    _VOCAB_DS = ds  # threads + serial share this; ProcessPool workers rebuild it in _vocab_init
    words = json.loads(Path(vocab_path).read_text(encoding="utf-8"))
    width = 640
    render = "windowed viewport"

    gil_rc = finalize_runtime(rt, require_ft)
    print(f"\nSaitenka overlay — VOCAB render benchmark   ({tag})")
    print(format_runtime(rt))
    print(f"words: {len(words)}   reps: {reps}   tip_width: {width}   render: {render}")
    print(f"source: {vocab_path}\n")

    # --- parallel path: render the whole batch across pick_executor (threads on FT, processes on GIL) ---
    if parallel:
        from saitenka.parallel import is_free_threaded, pick_executor

        jobs = [(*w, width) for w in words] * reps
        for j in jobs[:workers]:  # warm main thread / each process worker
            _vocab_render(j)
        mode = "threads (free-threaded)" if is_free_threaded() else "processes (GIL fallback)"
        best = float("inf")
        for _ in range(reps):
            with pick_executor(workers, initializer=_vocab_init) as ex:
                list(ex.map(_vocab_render, jobs[: len(words)]))  # warm the process pool
                t0 = time.perf_counter()
                list(ex.map(_vocab_render, jobs))
                best = min(best, time.perf_counter() - t0)
        n = len(jobs)
        print(f"=== PARALLEL batch render ({mode}, {workers} workers) ===")
        print(
            f"  {n} renders in {best * 1000:.0f} ms   →  {best / n * 1000:.2f} ms/render   "
            f"({n / best:.0f} renders/s)"
        )
        return gil_rc

    # --- serial path: per-word latency distribution (the default py-spy target) ---
    full_ms: list[float] = []
    cold_ms: list[float] = []
    slowest: list[tuple[float, str, int]] = []
    for _ in range(reps):
        for surface, lemma, reading, pos in words:
            job = (surface, lemma, reading, pos, width)
            t0 = time.perf_counter()
            h = _vocab_render(job)
            full_ms.append((time.perf_counter() - t0) * 1000.0)
            t1 = time.perf_counter()
            from saitenka.app.tokenize import Token

            LazyPanel(
                panel_rows(
                    ds.entry_for(Token(surface, lemma, reading, pos, 0, len(surface))), width
                ),
                width,
            ).render_to(432)
            cold_ms.append((time.perf_counter() - t1) * 1000.0)
            slowest.append((full_ms[-1], surface, h))

    def _p(samples: list[float], q: float) -> float:
        s = sorted(samples)
        return s[min(len(s) - 1, int(q * len(s)))]

    print("=== windowed viewport render (entry_for + compositor) ===")
    print(
        f"  median {statistics.median(full_ms):7.1f}   p90 {_p(full_ms, 0.9):7.1f}   "
        f"p99 {_p(full_ms, 0.99):7.1f}   MAX {max(full_ms):7.1f}  ms"
    )
    print("=== COLD head paint (viewport only) ===")
    print(
        f"  median {statistics.median(cold_ms):7.1f}   p90 {_p(cold_ms, 0.9):7.1f}   "
        f"MAX {max(cold_ms):7.1f}  ms"
    )
    print("\n  slowest 10 full renders (word, full_ms, panel_px):")
    for full, word, px in sorted({s[1]: s for s in slowest}.values(), reverse=True)[:10]:
        print(f"    {word:<8}{full:>9.1f}{px:>10}")
    if json_path:
        Path(json_path).write_text(
            json.dumps(
                {
                    "runtime": rt,
                    "words": len(words),
                    "full_median_ms": statistics.median(full_ms),
                    "full_p99_ms": _p(full_ms, 0.99),
                    "full_max_ms": max(full_ms),
                    "cold_median_ms": statistics.median(cold_ms),
                    "cold_max_ms": max(cold_ms),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote vocab baseline → {json_path}")
    return gil_rc


def _timeline_interact(reader) -> None:
    """Exercise the nested scan popup + a clicked kanji open + a clicked cross-reference off the CURRENT
    base tooltip, so a --timeline run has realistic ``prefetch_decode``/``tip_compose``/``render``
    kind=nested|clicked|engaged_open latency — not only base hovers. Each deferred interaction is pumped
    through the runtime mailbox so the warm swap is measured rather than omitted. Best-effort: a word
    with no scan cells or kanji simply skips."""
    st = reader.tooltip_controller.surface_state().view.state
    if st is None:
        return
    boxes = st.windowed.scan_boxes()
    if boxes:
        sb = boxes[len(boxes) // 2]  # a cell well inside the body
        nested_popup.show_nested(
            reader._tip_ports, reader._panel_ports, reader.word_lookup, sb
        )  # cold inner word → off-thread compose (kind=engaged_nested / nested)
        time.sleep(0.02)  # let the worker compose the nested head
        nested_popup.show_nested(
            reader._tip_ports, reader._panel_ports, reader.word_lookup, sb
        )  # warm → synchronous nested show (tip_compose kind="nested")
        # scroll the nested popup so its render-ahead + crisp-poll are exercised (the base already is)
        tooltip_panel.scroll_view(
            reader._tip_ports,
            reader.tooltip_controller.surface_state().nest,
            round(reader.osd[1] * 0.1),
        )
        reader._hide_nested()
    # a clicked/keyed kanji open (deferred, tier-3): warms off-thread → prefetch_decode[engaged_open]
    reader.kanji_current()
    time.sleep(0.02)
    reader._drain_events()  # pump the typed completion and warm placement
    reader._hide_nested()
    if 0 <= reader.tooltip_controller.observation().selected < len(reader.tokens):
        tooltip.navigate_tip(
            reader._tip_ports,
            reader._panel_ports,
            reader.tokens[reader.tooltip_controller.observation().selected].surface,
        )  # in-place nav → kind="clicked"
        time.sleep(0.02)
        reader._drain_events()  # pump the typed completion and warm swap


def run_timeline(
    rt: dict,
    require_ft: bool = False,
    json_path: str | None = None,
    cue_words: int = 4,
    max_cues: int = 80,
    dwell_s: float = 0.3,
    hover_every: int = 4,
    lookahead: int = 3,
    head_prefetch: int = 0,
    interact: bool = True,
) -> int:
    """The idle-dominated ground truth (``vibe/hot-path-idle-spreading-plan.md`` Stage 1). Real usage
    is neither continuous churn (``--stress``) nor raw render throughput (``--vocab``) — it's mostly
    idle (video plays, mouse doesn't move) punctuated by occasional hovers, with a background worker
    warming the current + upcoming lines during the idle gaps. Advances real cues built from
    the actual episode vocabulary on a real clock (``time.sleep`` between cues, so the real prefetch
    threads get real wall-clock idle time), and reports hover latency split by whether the background
    worker had already decoded the word (idle-warm) or not (cold, decoded synchronously on hover),
    plus the worker's keep-ahead margin: how long warming actually took vs. the idle budget it had.

    ``head_prefetch > 0`` also enables the EXPERIMENTAL selective head-prefetch prototype
    (``PerfOptions.head_prefetch_lookahead``) and additionally splits hover latency by whether the
    PANEL (not just the dictionary decode) was already warm, plus RSS growth — the transient-memory
    concern that prototype's own review flagged."""
    ds, tag = _load_dict_set()
    if ds is None:
        print("timeline benchmark needs the real dict set (overlay.toml) — nothing to measure")
        return 1
    vocab_words = _load_vocab_words()
    cues = _timeline_cues(vocab_words, cue_words, max_cues, dwell_s)
    if not cues:
        print("no cues built — check examples/vocab.json")
        return 1

    # Ground truth for "was this word already decoded when we hovered it?": wrap the SAME instance
    # method the real hover path (main thread) and the real prefetch workers (background threads)
    # both call — no production code touched, and it sees every decode from either side.
    warm_lock = threading.Lock()
    warmed_at: dict[str, float] = {}
    orig_entry_for = ds.entry_for

    def traced_entry_for(token, inflected=None, **kwargs):
        result = orig_entry_for(token, inflected, **kwargs)
        with warm_lock:
            warmed_at.setdefault(token.lemma, time.monotonic())
        return result

    ds.entry_for = traced_entry_for

    scorer = _timeline_scorer(vocab_words) if head_prefetch > 0 else None
    # A REAL gateway: this whole benchmark is about what the background worker gets done during the
    # idle gaps, and prefetch only starts once its job lane registers.
    timeline_ipc, gateway = _runtime_ipc()
    reader = SessionController(
        timeline_ipc,
        dict_set=ds,
        scorer=scorer,
        prefetch=True,
        prefetch_lookahead=lookahead,
        head_prefetch_lookahead=head_prefetch,
    )
    reader.osd = OSD
    reader.episode.sub_index = CueIndex(cues)
    reader.start_prefetch()

    from saitenka.app.tokenize import SKIP_POS

    def _content_lemmas(text: str) -> list[str]:
        return [
            t.lemma
            for t in tokenize(text)
            if t.is_content and t.pos not in SKIP_POS and t.surface.strip()
        ]

    first_enqueued_at: dict[str, float] = {}  # lemma -> when it FIRST became "upcoming" (lookahead)
    warm_ms: list[float] = []  # hover latency, word already decoded before the hover
    cold_ms: list[float] = []  # hover latency, decode happened synchronously during the hover
    lead_ms: list[float] = []  # enqueued-as-upcoming -> actually decoded, for words later hovered
    render_warm_ms: list[float] = []  # hover latency, panel_cache ALREADY had this word's panel
    render_cold_ms: list[float] = []  # hover latency, panel had to be built (head render) on hover
    misses = 0  # hovered while still cold, despite lookahead having reached the word already
    hovers = 0
    rss_base = _rss_mb() if head_prefetch > 0 else 0.0
    rss_peak = rss_base

    try:
        for i, cue in enumerate(cues):
            reader.set_subtitle(cue.text)
            now = time.monotonic()
            for j in range(i + 1, min(len(cues), i + 1 + lookahead)):
                for lemma in _content_lemmas(cues[j].text):
                    first_enqueued_at.setdefault(lemma, now)
            # Engagement realism: on the cues we'll hover, model the cursor resting over the video
            # (what a real hover carries — pause_on_tooltip pauses + mouse-over-video), so
            # update_prefetch renders the CURRENT line's words as engaged HEADS (prefetch_decode
            # kind="head") — the path a real session (see the report) spends most prefetch time on.
            # Passive (mouse-away) cues stay decode-only WARM, matching idle watching.
            reader._mouse_in = i % hover_every == 0
            reader._update_prefetch()
            time.sleep(dwell_s)  # idle: the real background prefetch threads run during this window
            if head_prefetch > 0:
                rss_peak = max(rss_peak, _rss_mb())

            idxs = _content_indices(reader)
            if not idxs or i % hover_every != 0:
                continue
            idx = idxs[0]
            tok = reader.tokens[idx]
            lemma = tok.lemma
            was_warm = lemma in warmed_at
            # Mirror how panel_for() itself resolves the key (mined via the same main-thread-only path)
            # so this check reflects the REAL cache panel_for() reads.
            mined = reader._is_mined(tok)
            key = reader._panel_key(tok, reader._inflected_surface(idx), mined=mined)
            panel_already_warm = key in reader.tooltip_controller.surface_state().panel_cache
            hovers += 1
            t0 = time.perf_counter()
            reader.set_hover(idx)
            dt = (time.perf_counter() - t0) * 1000.0
            (warm_ms if was_warm else cold_ms).append(dt)
            if head_prefetch > 0:
                (render_warm_ms if panel_already_warm else render_cold_ms).append(dt)
            enq = first_enqueued_at.get(lemma)
            if was_warm and enq is not None:
                lead_ms.append((warmed_at[lemma] - enq) * 1000.0)
            elif not was_warm and enq is not None:
                misses += 1  # the worker had idle time to warm it but hadn't finished yet
            if interact:
                _timeline_interact(
                    reader
                )  # nested + clicked, off this base tooltip (realistic kinds)
            reader.retire_hover()
    finally:
        reader._stop.set()
        gateway.close()  # its worker threads outlive the SessionController otherwise

    # `runtime_info()` ran before any tokenizer existed, so its GIL reading (and the worker count
    # derived from it) predates fugashi silently re-enabling the GIL — the exact collapse its own
    # docstring warns about. The SessionController has since started prefetch, so take the count it ACTUALLY
    # got: a header claiming 8 workers while 2 are running is what makes a miss count unreadable.
    rt = {
        **rt,
        "prefetch_workers": reader.tooltip_preparation.worker_count,
        "gil_enabled": _gil_enabled(),
    }
    gil_rc = finalize_runtime(rt, require_ft)
    idle_budget_ms = lookahead * dwell_s * 1000.0
    print(f"\nSaitenka overlay — TIMELINE: idle-paced session   ({tag})")
    print(format_runtime(rt))
    print(
        f"{len(cues)} cues × {dwell_s * 1000:.0f}ms dwell   lookahead: {lookahead} cues "
        f"({idle_budget_ms:.0f}ms idle budget)   hovers: {hovers} (every {hover_every}th cue)\n"
    )

    def prow(label: str, samples: list[float]) -> dict:
        if not samples:
            print(f"{label:44} n/a (no samples)")
            return {}
        m = _stats(samples)
        print(f"{label:44} {m['p50']:7.1f} {m['p95']:7.1f} {m['max']:7.1f} {m['n']:5d}n   (ms)")
        return m

    hdr = f"{'metric':44} {'p50':>7} {'p95':>7} {'max':>7} {'n':>6}"
    print(hdr)
    print("-" * len(hdr))
    m_warm = prow("hover latency — idle-warm (word pre-decoded)", warm_ms)
    m_cold = prow("hover latency — cold (decoded on hover)", cold_ms)
    m_lead = prow("worker lead time (enqueued -> decoded)", lead_ms)
    print("-" * len(hdr))
    print(
        f"misses: {misses}/{hovers} hovers landed cold despite lookahead coverage "
        f"(worker fell behind the {idle_budget_ms:.0f}ms idle budget)"
    )
    print(
        "\nwarm = the background worker already decoded this word during idle before the hover; "
        "cold = first decode happened synchronously on hover (no lookahead coverage, or the worker "
        "hadn't gotten to it yet — see misses). lead time is the real wall-clock cost of Stage 2's "
        "idle-warming against a REAL idle window, not a synthetic one — a lead time close to or over "
        "the idle budget means the worker is not keeping ahead at this dwell/lookahead setting."
    )
    head_json: dict = {}
    if head_prefetch > 0:
        print("-" * len(hdr))
        m_render_warm = prow(
            "hover latency — PANEL already warm (head prerendered)", render_warm_ms
        )
        m_render_cold = prow("hover latency — PANEL cold (head rendered on hover)", render_cold_ms)
        print("-" * len(hdr))
        rss_growth = rss_peak - rss_base
        print(
            f"head-prefetch lookahead: {head_prefetch} cues   speculative heads built: "
            f"{reader.tooltip_preparation.snapshot.head_built}   "
            f"RSS: base {rss_base:.0f}MB -> peak {rss_peak:.0f}MB "
            f"(+{rss_growth:.0f}MB)"
        )
        print(
            "\nPANEL-warm = panel_cache already held this word's rendered head before the hover (the "
            "hover is upload-only); PANEL-cold = the same head render a normal cold hover would pay, "
            "just not idle-warmed. Compare PANEL-warm's p50 against the idle-warm (decode-only) row "
            "above — the gap between them is what this prototype is trying to close. RSS growth is "
            "the transient-memory cost flagged when this was proposed; a growth wildly out of line "
            "with the retained panel_cache size (see the report's panel_cache.bytes gauge) would mean "
            "the queue's maxsize isn't bounding concurrent work as intended."
        )
        head_json = {
            "head_prefetch_lookahead": head_prefetch,
            "heads_built": reader.tooltip_preparation.snapshot.head_built,
            "render_warm": m_render_warm,
            "render_cold": m_render_cold,
            "rss_base_mb": rss_base,
            "rss_peak_mb": rss_peak,
        }
    if json_path:
        Path(json_path).write_text(
            json.dumps(
                {
                    "runtime": rt,
                    "tag": tag,
                    "cues": len(cues),
                    "dwell_ms": dwell_s * 1000.0,
                    "lookahead": lookahead,
                    "hovers": hovers,
                    "misses": misses,
                    "warm": m_warm,
                    "cold": m_cold,
                    "lead": m_lead,
                    **head_json,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote timeline baseline → {json_path}")
    return gil_rc


def _parse_trace_events(zip_path: str) -> tuple[list[dict], dict]:
    """Extract the ordered interactive event stream from a diagnostics report's ``trace.json``: each
    hover / scroll / cue-change with the idle gap before it. This is the REAL cadence a session
    produced (event mix + timing), so replaying it — with the idle *compressed* — stresses the pipeline
    with a truthful workload instead of a hand-authored guess that rots. Words aren't in the trace
    (low-cardinality telemetry), so content comes from the real episode vocab; only the timing does."""
    import zipfile

    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.endswith("trace.json"))
        data = json.loads(z.read(name))
    kind = {
        "tooltip_show": "hover",
        "scroll_frame": "scroll",
        "cue_redraw": "cue",
        "sub_text_reconcile": "cue",
    }
    evs = sorted(
        (e for e in data["traceEvents"] if e.get("ph") == "X" and e["name"] in kind),
        key=lambda e: e["ts"],
    )
    out: list[dict] = []
    prev_end: float | None = None
    for e in evs:
        gap = 0.0 if prev_end is None else max(0.0, (e["ts"] - prev_end) / 1000.0)
        prev_end = e["ts"] + e["dur"]
        out.append({"kind": kind[e["name"]], "gap_ms": gap})
    mix = {k: sum(1 for x in out if x["kind"] == k) for k in ("cue", "hover", "scroll")}
    meta = {"events": len(out), "mix": mix, "idle_total_s": sum(x["gap_ms"] for x in out) / 1000.0}
    return out, meta


@dataclass(frozen=True)
class TraceParams:
    """The ``--trace`` replay knobs (all from argparse): idle compression, the prefetch levers being
    swept, the free-threading gate, and the JSON baseline sink. Bundled so :func:`run_trace` takes one
    params value instead of a nine-arg clump."""

    idle_scale: float
    idle_cap_ms: float
    loops: int
    lookahead: int
    head_prefetch: int
    workers: int
    raw_ceiling_mb: int
    require_ft: bool
    json_path: str | None


def run_trace(zip_path: str, rt: dict, params: TraceParams) -> int:
    """Replay a report's real event cadence with the idle gaps compressed by ``idle_scale`` (and
    optionally capped) — a stressful-but-real session: the real hover/scroll/cue mix and ordering, the
    real background prefetch workers warming during the (now shorter) gaps, real dict content. Compress
    idle *enough* to starve the workers' keep-ahead margin, not to zero (a machine-gun no user matches).
    Attach py-spy to this process to see where the main-thread critical path goes under load."""
    idle_scale, idle_cap_ms, loops = params.idle_scale, params.idle_cap_ms, params.loops
    lookahead, head_prefetch, workers = params.lookahead, params.head_prefetch, params.workers
    raw_ceiling_mb, require_ft, json_path = (
        params.raw_ceiling_mb,
        params.require_ft,
        params.json_path,
    )
    ds, tag = _load_dict_set()
    if ds is None:
        print("--trace needs the real dict set (overlay.toml) — nothing to measure")
        return 1
    events, meta = _parse_trace_events(zip_path)
    if not events:
        print(f"no interactive events in {zip_path}")
        return 1
    vocab_words = _load_vocab_words()
    cues = _timeline_cues(vocab_words, cue_words=4, max_cues=0, dwell_s=0.3)
    # head_prefetch>0 needs a scorer (its n+1 selective head-render marks the lone unknown word);
    # the engaged current-line head render also only fires when head_prefetch_lookahead>0.
    scorer = _timeline_scorer(vocab_words) if head_prefetch > 0 else None

    # Same warm-tracking seam as --timeline: wrap the instance entry_for both the hover (main) and the
    # prefetch workers call, so a hover can be classed warm (worker pre-decoded it) vs cold.
    warmed: dict[str, bool] = {}
    wl = threading.Lock()
    orig_entry_for = ds.entry_for

    def traced_entry_for(token, inflected=None, **kwargs):
        r = orig_entry_for(token, inflected, **kwargs)
        with wl:
            warmed[token.lemma] = True
        return r

    ds.entry_for = traced_entry_for

    reader = SessionController(
        _fake_ipc(),
        dict_set=ds,
        scorer=scorer,
        prefetch=True,
        prefetch_lookahead=lookahead,
        head_prefetch_lookahead=head_prefetch,
        prefetch_workers=workers,
    )
    reader.osd = OSD
    reader.episode.sub_index = CueIndex(cues)
    if raw_ceiling_mb >= 0:  # >=0 overrides the config default (A/B raw bands vs always-compress)
        reader.raw_band_ceiling = raw_ceiling_mb * 1024 * 1024
    reader.start_prefetch()
    step = round(OSD[1] * 0.12)
    rss_base = _rss_mb()
    rss_peak = rss_base

    def scaled(gap_ms: float) -> float:
        g = gap_ms * idle_scale
        return min(g, idle_cap_ms) if idle_cap_ms > 0 else g

    hov_warm: list[float] = []
    hov_cold: list[float] = []
    panel_warm: list[float] = []  # PANEL (composited head) prebuilt → the upload-only target
    panel_cold: list[float] = []  # panel built on the hover (head_prefetch didn't reach it)
    panel_precomposed: list[float] = []  # warm AND first viewport precomposed in idle (0 rasters)
    panel_recomposed: list[float] = []  # warm head but the show re-composited (precompose missed)
    scroll_ms: list[float] = []
    jank: list[float] = []
    ci = hov_i = 0
    compressed_s = 0.0
    try:
        for _loop in range(loops):
            for ev in events:
                pause = scaled(ev["gap_ms"])
                compressed_s += pause / 1000.0
                if pause > 0:
                    time.sleep(pause / 1000.0)  # the (compressed) idle the prefetch workers run in
                if ev["kind"] == "cue":
                    ci = (ci + 1) % len(cues)
                    reader.set_subtitle(cues[ci].text)
                    reader._mouse_in = True
                    reader._update_prefetch()  # engaged: workers warm the current line's heads
                    rss_peak = max(rss_peak, _rss_mb())
                elif ev["kind"] == "hover":
                    idxs = _content_indices(reader)
                    if not idxs:
                        continue
                    idx = idxs[hov_i % len(idxs)]  # cycle targets → real word-weight variety
                    hov_i += 1
                    tok = reader.tokens[idx]
                    warm = tok.lemma in warmed
                    # PANEL-warm: was the composited head already in panel_cache (upload-only hover)?
                    # Resolve the key the same main-thread way panel_for() does (mined path included).
                    key = reader._panel_key(
                        tok, reader._inflected_surface(idx), mined=reader._is_mined(tok)
                    )
                    panel_already_warm = (
                        key in reader.tooltip_controller.surface_state().panel_cache
                    )
                    t0 = time.perf_counter()
                    reader.set_hover(idx)  # tip stays up so a following scroll event can scroll it
                    dt = (time.perf_counter() - t0) * 1000.0
                    (hov_warm if warm else hov_cold).append(dt)
                    (panel_warm if panel_already_warm else panel_cold).append(dt)
                    panel = reader.tooltip_controller.surface_state().view.state
                    if panel_already_warm and panel is not None:
                        # Step 2: a warm hover whose first viewport was precomposed in idle rasters 0
                        # bands on the show (served from the cached BGRA copy); one that only had its
                        # head built still re-composites overscan bands synchronously (>0).
                        precomposed = panel.last_frame_rasters == 0
                        (panel_precomposed if precomposed else panel_recomposed).append(dt)
                elif ev["kind"] == "scroll":
                    if reader.tooltip_controller.surface_state().view.state is None:
                        continue  # nothing shown to scroll (a scroll before the first hover)
                    t0 = time.perf_counter()
                    reader.scroll_tip(step)
                    dt = (time.perf_counter() - t0) * 1000.0
                    scroll_ms.append(dt)
                    if dt > 16.0:
                        jank.append(dt)
    finally:
        reader._stop.set()

    gil_rc = finalize_runtime(rt, require_ft)
    print(f"\nSaitenka overlay — TRACE REPLAY (stress: idle compressed)   ({tag})")
    print(format_runtime(rt))
    print(
        f"source: {Path(zip_path).name}   {meta['events']} events "
        f"(cue {meta['mix']['cue']} / hover {meta['mix']['hover']} / scroll {meta['mix']['scroll']}) "
        f"× {loops} loops\n"
        f"idle: real {meta['idle_total_s']:.0f}s × {idle_scale}"
        f"{f' cap {idle_cap_ms:.0f}ms' if idle_cap_ms > 0 else ''} → {compressed_s:.0f}s "
        f"(the smaller this is, the more the prefetch workers are starved)\n"
    )

    def prow(label: str, samples: list[float]) -> dict:
        if not samples:
            print(f"{label:44} n/a")
            return {}
        m = _stats(samples)
        print(f"{label:44} {m['p50']:7.1f} {m['p95']:7.1f} {m['max']:7.1f} {m['n']:5d}n   (ms)")
        return m

    hdr = f"{'metric':44} {'p50':>7} {'p95':>7} {'max':>7} {'n':>6}"
    print(hdr)
    print("-" * len(hdr))
    workers_label = f"{workers} (pinned)" if workers > 0 else "auto"
    ceil_label = f"{reader.raw_band_ceiling // (1024 * 1024)}MB"
    print(
        f"prefetch: lookahead {lookahead}   head_prefetch {head_prefetch}   workers {workers_label}   "
        f"raw_band_ceiling {ceil_label}\n"
    )
    m_hw = prow("hover — DECODE idle-warm (word pre-decoded)", hov_warm)
    m_hc = prow("hover — DECODE cold (decoded on hover)", hov_cold)
    print("-" * len(hdr))
    m_pw = prow("hover — PANEL warm (head prebuilt → upload-only)", panel_warm)
    m_pc = prow("hover — PANEL cold (head built on hover)", panel_cold)
    print("-" * len(hdr))
    m_pp = prow("  ├ PRECOMPOSED (first viewport idle-composed, 0 raster)", panel_precomposed)
    m_pr = prow("  └ recomposed (warm head, show re-composited)", panel_recomposed)
    print("-" * len(hdr))
    m_sc = prow("scroll frame", scroll_ms)
    print("-" * len(hdr))
    total_scroll = len(scroll_ms)
    if total_scroll:
        print(
            f"scroll jank (>16ms): {len(jank)}/{total_scroll} ({100 * len(jank) / total_scroll:.0f}%)"
        )
    else:
        print("no scroll frames")
    rss_growth = rss_peak - rss_base
    print(
        f"speculative heads built: {reader.tooltip_preparation.snapshot.head_built}   "
        f"RSS: base {rss_base:.0f}MB → peak {rss_peak:.0f}MB (+{rss_growth:.0f}MB)"
    )
    print(
        "\nPRECOMPOSED is the 8ms target: the first viewport was composited in idle (step 2), so the "
        "warm hover is a BGRA copy + decorate + upload — 0 synchronous rasters. The `recomposed` split "
        "is a warm head whose first viewport precompose did NOT reach (worker starved / evicted): the "
        "show still rasters overscan bands + converts BGRA, the old PANEL-warm ceiling. Compress "
        "--idle-scale until PRECOMPOSED coverage drops — that is where the workers fall behind (raise "
        "prefetch_workers to push it back). RSS is the memory this trades for it."
    )
    if json_path:
        Path(json_path).write_text(
            json.dumps(
                {
                    "runtime": rt,
                    "tag": tag,
                    "source": Path(zip_path).name,
                    "events": meta["events"],
                    "mix": meta["mix"],
                    "loops": loops,
                    "idle_scale": idle_scale,
                    "idle_cap_ms": idle_cap_ms,
                    "idle_real_s": meta["idle_total_s"],
                    "idle_compressed_s": compressed_s,
                    "lookahead": lookahead,
                    "head_prefetch": head_prefetch,
                    "hover_decode_warm": m_hw,
                    "hover_decode_cold": m_hc,
                    "hover_panel_warm": m_pw,
                    "hover_panel_cold": m_pc,
                    "hover_panel_precomposed": m_pp,
                    "hover_panel_recomposed": m_pr,
                    "scroll": m_sc,
                    "scroll_jank": len(jank),
                    "scroll_total": total_scroll,
                    "heads_built": reader.tooltip_preparation.snapshot.head_built,
                    "rss_base_mb": rss_base,
                    "rss_peak_mb": rss_peak,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote trace-replay baseline → {json_path}")
    return gil_rc


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
        "--render-cache",
        action="store_true",
        help="A/B the persistent render cache (#149) on the pathological corpus: cold head raster vs. "
        "a disk-seeded first viewport (copy+upload)",
    )
    ap.add_argument(
        "--mask-atlas",
        action="store_true",
        help="A/B the persistent glyph mask atlas (#149 Tier-1) on the pathological corpus: cold "
        "getmask2 rasterisation vs. a disk-loaded mask (raster-skip rate + render wall-time)",
    )
    ap.add_argument(
        "--json",
        metavar="PATH",
        help="also write the metrics (with runtime info) as JSON, for baseline diffing over time",
    )
    ap.add_argument(
        "--synth",
        action="store_true",
        help="dict-free deterministic render benchmark over synth_corpus() — the CI/asv-safe gate "
        "target (no overlay.toml, no randomness → identical numbers on any machine/commit)",
    )
    ap.add_argument(
        "--loops",
        type=int,
        default=1,
        help="--synth: repeat the whole corpus N times to characterize run-to-run variance (CV)",
    )
    ap.add_argument(
        "--bench-json",
        metavar="PATH",
        help="--synth: also write github-action-benchmark customSmallerIsBetter JSON (for the gh-pages "
        "continuous-history dashboard)",
    )
    ap.add_argument(
        "--require-ft",
        action="store_true",
        help="fail if the GIL is enabled (a C-extension re-enabled it) — for free-threaded runs",
    )
    ap.add_argument(
        "--vocab",
        metavar="PATH",
        nargs="?",
        const=str(Path(__file__).with_name("vocab.json")),
        help="render-pipeline benchmark over a frozen word list (JSON: [[surface,lemma,reading,pos],…]); "
        "bare flag uses the bundled examples/vocab.json (608 unique content words from one real anime "
        "episode). The subtitle parser is out of the loop, so a py-spy profile isolates "
        "lookup/decode/walk/layout",
    )
    ap.add_argument(
        "--parallel",
        action="store_true",
        help="--vocab: render the batch across a pool (saitenka.parallel: threads on free-threading, "
        "processes on a GIL build — py-spy needs --subprocesses for the process fallback)",
    )
    ap.add_argument("--workers", type=int, default=8, help="--vocab --parallel: pool size")
    ap.add_argument(
        "--stress",
        action="store_true",
        help="sustained chained session (scan→scroll→nested→dismiss over many heavy entries) — "
        "surfaces cache-eviction thrash, memory growth, and the frame-latency tail under load",
    )
    ap.add_argument(
        "--scroll-jank",
        action="store_true",
        help="scroll the BASE tooltip top→bottom over pathological entries, timing each scroll frame in "
        "isolation — the scroll-only jank tail (cold tail-block render vs warm cached re-composite)",
    )
    ap.add_argument(
        "--clicks",
        action="store_true",
        help="per-click main-thread cost (not per-frame): a sidebar action + redraw, a bookmark toggle, "
        "and the #253 mined-card link write — the click surfaces #293 left uncovered. Emits "
        "sidebar_click / backlog_write / mined_store_write spans under the telemetry+py-spy wrapper",
    )
    ap.add_argument(
        "--max-frame-ms",
        type=float,
        help="stress: fail if any single op exceeds this frame budget (ms)",
    )
    ap.add_argument(
        "--max-rss-mb", type=float, help="stress: fail if peak resident memory exceeds this (MB)"
    )
    ap.add_argument(
        "--timeline",
        action="store_true",
        help="idle-paced session over the real episode vocabulary (examples/vocab.json): real-time "
        "dwell between cues, the real background prefetch worker warming during the idle gaps, "
        "occasional hovers — reports hover latency split idle-warm vs cold, and the worker's "
        "keep-ahead margin. The felt-experience ground truth; --stress is the eviction/leak ceiling",
    )
    ap.add_argument(
        "--timeline-cues",
        type=int,
        default=80,
        help="--timeline: number of synthetic cues (0 = all)",
    )
    ap.add_argument(
        "--timeline-cue-words",
        type=int,
        default=4,
        help="--timeline: vocab words per synthetic cue",
    )
    ap.add_argument(
        "--timeline-dwell-s",
        type=float,
        default=0.3,
        help="--timeline: real seconds each cue stays up (real subtitle pacing is ~2-4s; lower here "
        "keeps the bench fast while still giving the worker a real idle window)",
    )
    ap.add_argument(
        "--timeline-hover-every",
        type=int,
        default=4,
        help="--timeline: inject a hover on every Nth cue (occasional, not every cue)",
    )
    ap.add_argument(
        "--timeline-lookahead",
        type=int,
        default=3,
        help="--timeline: cues the background worker warms ahead of the current one",
    )
    ap.add_argument(
        "--timeline-head-prefetch",
        type=int,
        default=0,
        help="--timeline: EXPERIMENTAL — also enable selective head-prefetch (PerfOptions."
        "head_prefetch_lookahead) for this many upcoming cues (0 = off, the default/shipped "
        "behavior); splits hover latency by PANEL warmth (not just decode) and reports RSS growth",
    )
    ap.add_argument(
        "--timeline-no-interact",
        action="store_false",
        dest="timeline_interact",
        help="--timeline: skip the per-hover nested-popup + cross-reference exercise (on by default so "
        "the trace carries realistic kind=nested|clicked latency, not just base hovers)",
    )
    ap.add_argument(
        "--trace",
        metavar="REPORT.zip",
        help="replay a diagnostics report's REAL event cadence (hover/scroll/cue mix + ordering from "
        "its trace.json) over the real vocab + prefetch workers, with idle compressed by --idle-scale "
        "— the truthful stress bench. Attach py-spy to see the main-thread critical path under load "
        "(add --subprocesses for the GIL-build render pool)",
    )
    ap.add_argument(
        "--idle-scale",
        type=float,
        default=0.15,
        help="--trace: multiply real inter-event idle by this (1.0 = real cadence, 0.15 = a fast "
        "hover/scrub session). Squeeze it DOWN until cold hovers + scroll jank climb — that crossover "
        "is where prefetch stops keeping ahead. Not 0: back-to-back is a machine no user matches",
    )
    ap.add_argument(
        "--idle-cap-ms",
        type=float,
        default=0.0,
        help="--trace: also cap each idle gap at this many ms after scaling (0 = no cap) — bounds the "
        "few multi-second real gaps so the run stays dense",
    )
    ap.add_argument(
        "--trace-loops",
        type=int,
        default=3,
        help="--trace: replay the event stream this many times (a longer, steadier py-spy sample)",
    )
    ap.add_argument(
        "--trace-lookahead",
        type=int,
        default=2,
        help="--trace: decode-warm this many cues ahead (PerfOptions.prefetch_lookahead)",
    )
    ap.add_argument(
        "--trace-head-prefetch",
        type=int,
        default=1,
        help="--trace: selective head-prefetch lookahead (PerfOptions.head_prefetch_lookahead) — "
        "prebuild the composited head in idle. Splits the hover report by PANEL warmth and reports "
        "RSS. 0 = decode-warm only (no head prebuild)",
    )
    ap.add_argument(
        "--trace-workers",
        type=int,
        default=0,
        help="--trace: pin the prefetch worker count (idle-warm capacity lever; 0 = per-build auto, "
        "~8 on free-threaded). Sweep 8→12 to see if more idle workers push PRECOMPOSED coverage up.",
    )
    ap.add_argument(
        "--trace-raw-ceiling-mb",
        type=int,
        default=-1,
        help="--trace: override tooltip.raw_band_ceiling_mb (A/B step 3). -1 = config default (100); "
        "0 = always compress (pre-1.3); a big value = keep all bands raw. Compare scroll jank + RSS.",
    )
    args = ap.parse_args()

    # Snapshot the runtime; the GIL state is re-read AFTER the workload (finalize_runtime), because
    # fugashi re-enables the GIL only when first USED, not at startup — a start-of-run check would
    # miss exactly the regression --require-ft is meant to catch.
    rt = runtime_info()

    if args.trace:
        return run_trace(
            args.trace,
            rt,
            TraceParams(
                idle_scale=args.idle_scale,
                idle_cap_ms=args.idle_cap_ms,
                loops=args.trace_loops,
                lookahead=args.trace_lookahead,
                head_prefetch=args.trace_head_prefetch,
                workers=args.trace_workers,
                raw_ceiling_mb=args.trace_raw_ceiling_mb,
                require_ft=args.require_ft,
                json_path=args.json,
            ),
        )

    if args.timeline:
        return run_timeline(
            rt,
            args.require_ft,
            args.json,
            cue_words=args.timeline_cue_words,
            max_cues=args.timeline_cues,
            dwell_s=args.timeline_dwell_s,
            hover_every=args.timeline_hover_every,
            lookahead=args.timeline_lookahead,
            head_prefetch=args.timeline_head_prefetch,
            interact=args.timeline_interact,
        )
    if args.stress:
        return run_stress(
            args.reps,
            rt,
            args.require_ft,
            args.json,
            args.max_frame_ms,
            args.max_rss_mb,
            args.bench_json,
        )
    if args.scroll_jank:
        return run_scroll_jank(args.reps, rt, args.require_ft, args.json)
    if args.clicks:
        return run_clicks(args.reps, rt, args.require_ft, args.json)
    if args.synth:
        return run_synth(
            args.reps,
            rt,
            args.require_ft,
            args.json,
            loops=args.loops,
            bench_json=args.bench_json,
        )
    if args.vocab:
        return run_vocab(
            args.vocab,
            args.reps,
            rt,
            args.require_ft,
            args.json,
            parallel=args.parallel,
            workers=args.workers,
        )
    if args.pathological:
        return run_pathological(args.reps, rt, args.require_ft, args.json)
    if args.render_cache:
        return run_render_cache(args.reps, rt, args.require_ft, args.json)
    if args.mask_atlas:
        return run_mask_atlas(rt, args.require_ft, args.json)

    ds, tag = _load_dict_set()
    if ds is None:
        ds = _SyntheticDS()

    fake_ipc = FakeIPC()
    reader = SessionController(cast("MpvIPC", fake_ipc), dict_set=ds, prefetch=False)
    reader.osd = OSD
    reader.set_subtitle(LINE)
    idxs = _content_indices(reader)
    words = [reader.tokens[i].surface for i in idxs]
    cap = reader.tip_scale.cap

    rows = []
    cyc = {"cold": 0, "warm": 0}  # cycle through the words so each sample times ONE tooltip

    def show_cold(i):
        reader.tooltip_controller.surface_state().panel_cache.clear()
        reader.retire_hover()
        reader._show_tooltip(i)

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

    # 2) warm hover: panel prefetched/cached → just re-slice + upload
    for i in idxs:  # warm the cache fully first
        reader._panel_for(reader.tokens[i], reader._inflected_surface(i))
    rows.append(
        ("warm hover  (prefetched → upload only)", measure(warm_one, args.reps * len(idxs)))
    )

    # 3) scroll frame: one wheel step re-slice + scrollbar + upload on the tallest tooltip
    tall = _tallest(reader, idxs)
    reader._panel_for(reader.tokens[tall], reader._inflected_surface(tall))
    show_warm(tall)
    step = round(OSD[1] * 0.12)

    def scroll_frame():
        reader.tooltip_controller.surface_state().view.scroll = 0
        reader.scroll_tip(step)  # down one step (re-render)

    rows.append((f"scroll frame  (one {step}px step)", measure(scroll_frame, args.reps * 3)))

    # 4) nested popup first paint: hover a word inside the tooltip
    show_warm(tall)
    st = reader.tooltip_controller.surface_state().view.state
    boxes = st.windowed.scan_boxes() if st else []
    if boxes:
        sb = boxes[len(boxes) // 3]  # a cell well inside the body

        def nested_cold():
            reader._hide_nested()
            # drop only the inner word's cached panel so we measure a cold nested paint
            reader.tooltip_controller.surface_state().panel_cache.discard(
                reader._panel_key(tokenize(sb.text)[0], tokenize(sb.text)[0].surface)
            )
            nested_popup.show_nested(reader._tip_ports, reader._panel_ports, reader.word_lookup, sb)

        rows.append(("nested popup first paint  (inner word)", measure(nested_cold, args.reps)))

    # 5) per-tick hover hit-test: the poll-loop cost while the cursor sits on the tooltip body
    show_warm(tall)
    rect = reader.tooltip_controller.surface_state().view.rect
    assert rect is not None
    tx, ty, tw, th = rect
    fake_ipc.props["mouse-pos"] = {"hover": True, "x": tx + tw / 2, "y": ty + th - 8}
    reader.tooltip_controller.configure_delays(
        scan=1e9
    )  # isolate the hit-test; don't actually open a nested popup
    rows.append(
        ("poll tick hover hit-test  (_update_hover)", measure(reader._update_hover, args.reps * 5))
    )
    reader.tooltip_controller.configure_delays(scan=0.25)

    # 7) horizontal sweep across the line — cold vs warm (shows what prefetch buys you)
    sweep_cold = measure(lambda: [show_cold(i) for i in idxs], max(4, args.reps // 2))
    sweep_warm = measure(lambda: [show_warm(i) for i in idxs], max(4, args.reps // 2))

    # 8) components, for diagnosis
    def comp_lookup():
        for i in idxs:
            ds.entry_for(reader.tokens[i], reader._inflected_surface(i))

    def comp_headrender():
        for i in idxs:
            e = ds.entry_for(reader.tokens[i], reader._inflected_surface(i))
            LazyPanel(panel_rows(e, reader.tip_scale.width), reader.tip_scale.width).render_to(cap)

    _tall_head = LazyPanel(
        panel_rows(ds.entry_for(reader.tokens[tall]), reader.tip_scale.width),
        reader.tip_scale.width,
    ).render_to(cap)  # pre-rendered once, outside the timer

    def comp_bgra():
        to_bgra_array(_tall_head)  # isolate the RGBA→premultiplied-BGRA conversion

    # Isolate the temp-file UPLOAD write (the last hop before mpv reads the bitmap) from the render.
    # The real publish, not a model of it: `Overlay._write_frame` creates a fresh inode per frame,
    # records it, and sweeps the retired ones — so the sweep's unlink is inside the measurement, which
    # a hand-rolled `write_bytes` would leave out. The fsync row is the pessimistic bound (a forced
    # device write); production never fsyncs, so a gap between them is page cache doing its job, not
    # a cost we pay.
    import shutil
    import tempfile

    from saitenka.mpvio.osd import Overlay

    _up_data, _up_w, _up_h, _up_stride = to_bgra(_tall_head)
    _up_dir = Path(tempfile.mkdtemp(prefix="saitenka-bench-upload-"))
    _up_cold = {"i": 0}

    _up_overlay = Overlay(cast("MpvIPC", FakeIPC()))

    def comp_upload_publish():
        _up_overlay._write_frame(1, _up_data)

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
    print(
        f"line: {LINE}   osd: {OSD[0]}x{OSD[1]}   tip_width: {reader.tip_scale.width}   cap: {cap}px"
    )
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
        ("component: upload write, publish (fresh)", comp_upload_publish),
        ("component: upload write, cold (fresh+fsync)", comp_upload_cold),
    ]:
        prow(label, measure(fn, args.reps))
    _up_overlay.close()
    shutil.rmtree(_up_dir, ignore_errors=True)
    print(
        f"\nupload payload: {_up_w}x{_up_h} BGRA ≈ {len(_up_data) / 1e6:.1f} MB. publish is the "
        "production path (fresh inode per frame, no fsync); the fsync row is the pessimistic bound."
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


def _ensure_free_threaded() -> None:
    """Force the GIL OFF before fugashi's C extension loads, same rationale and mechanism as
    ``saitenka.app.cli._ensure_free_threaded`` (not reused directly — that one re-execs into
    ``-m saitenka.app.cli``, this one re-execs THIS script). Without this, every free-threaded-build
    bench run silently measures "GIL auto-reenabled by fugashi" instead of genuine free-threading —
    found the hard way: worker lead time and RSS both come out very different once this is forced
    (PYTHON_GIL=0 only takes effect if set before the interpreter finishes starting, hence re-exec,
    not a post-hoc ``os.environ`` write)."""
    if sysconfig.get_config_var("Py_GIL_DISABLED") and os.environ.get("PYTHON_GIL") != "0":
        os.environ["PYTHON_GIL"] = "0"
        argv = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
        if sys.platform == "win32":
            import subprocess

            try:
                sys.exit(subprocess.run(argv, check=False).returncode)
            except KeyboardInterrupt:
                sys.exit(130)
        os.execv(sys.executable, argv)


if __name__ == "__main__":
    _ensure_free_threaded()
    raise SystemExit(main())

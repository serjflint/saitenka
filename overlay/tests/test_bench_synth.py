"""The dict-free synth benchmark seam (#33/#32): the deterministic corpus and the
github-action-benchmark JSON mapper. These are what make the perf gate + continuous-history dashboard
runnable in CI (unlike `--vocab`, which needs real dicts). No timing is asserted — that's the bench's
job; here we pin the pure, CI-load-independent parts."""

import importlib.util
import sys
from pathlib import Path

from overlay.panel import Entry

BENCH_PATH = Path(__file__).resolve().parent.parent / "examples" / "bench_responsiveness.py"


def _bench_module():
    spec = importlib.util.spec_from_file_location("bench_responsiveness", BENCH_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: a @dataclass under `from __future__ import annotations` resolves field
    # types via sys.modules[cls.__module__], which is None for an unregistered manual import.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_synth_corpus_is_deterministic():
    # The whole point: identical bytes every call → identical numbers on any machine/commit (a CI-safe
    # gate target). Compare the rendered shape, not object identity.
    mod = _bench_module()
    a, b = mod.synth_corpus(40), mod.synth_corpus(40)
    assert [hw for hw, _ in a] == [hw for hw, _ in b]
    assert [len(e.defs) for _, e in a] == [len(e.defs) for _, e in b]


def test_synth_corpus_spans_the_cost_space():
    # Short/medium/tall tiers must all appear, or the p99 metric never sees a tall scrolling entry.
    mod = _bench_module()
    corpus = mod.synth_corpus(30)
    assert len(corpus) == 30
    def_counts = {len(e.defs) for _, e in corpus}
    assert def_counts == {1, 3, 6}  # the three tiers are all represented
    assert all(isinstance(e, Entry) for _, e in corpus)


def test_to_bench_json_maps_metrics_to_customsmallerisbetter():
    # github-action-benchmark customSmallerIsBetter: each entry needs name/unit/value; the CV rides in
    # the optional `range` band.
    mod = _bench_module()
    out = mod.to_bench_json(
        {
            "synth_median_ms": 2.3597,
            "synth_p99_ms": 4.41,
            "synth_median_cv": 0.011,
            "synth_p99_cv": 0.25,
        }
    )
    assert [e["name"] for e in out] == ["synth median render", "synth p99 render"]
    assert all(e["unit"] == "ms" for e in out)
    assert out[0]["value"] == 2.36  # rounded to 3 decimals
    assert out[0]["range"] == "±1.1%" and out[1]["range"] == "±25.0%"


def test_to_bench_json_omits_a_missing_metric_never_emits_null():
    # An older-schema metrics dict without one key must drop that entry, not emit a null value the
    # action would choke on.
    mod = _bench_module()
    out = mod.to_bench_json({"synth_median_ms": 2.0})  # no p99
    assert [e["name"] for e in out] == ["synth median render"]
    assert "range" not in out[0]  # no CV provided → no band, not an empty/None one


def test_to_bench_json_drops_a_zero_cv_band():
    # A single-loop run has CV 0 → no meaningful variance band; don't render "±0.0%".
    mod = _bench_module()
    out = mod.to_bench_json({"synth_median_ms": 2.0, "synth_median_cv": 0.0})
    assert "range" not in out[0]

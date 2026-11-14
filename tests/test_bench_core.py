import importlib.util
import json
import math
import sys
from pathlib import Path

CORE_PATH = Path(__file__).resolve().parent.parent / "examples" / "bench_core.py"


def _core_module():
    examples = str(CORE_PATH.parent)
    sys.path.insert(0, examples)
    try:
        spec = importlib.util.spec_from_file_location("bench_core", CORE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(examples)


def test_subtitle_benchmark_exercises_parse_index_and_tokenize(monkeypatch):
    core = _core_module()
    counts = {"locate": 0, "tokenize": 0}
    locate, tokenize = core.CueIndex.locate, core.tokenize

    def counted_locate(self, **kwargs):
        counts["locate"] += 1
        return locate(self, **kwargs)

    def counted_tokenize(*args, **kwargs):
        counts["tokenize"] += 1
        return tokenize(*args, **kwargs)

    monkeypatch.setattr(core.CueIndex, "locate", counted_locate)
    monkeypatch.setattr(core, "tokenize", counted_tokenize)

    result = core.benchmark_subtitles(cues=6, reps=1)

    assert set(result) == {"median_ms", "p95_ms"}
    assert all(math.isfinite(value) and value >= 0 for value in result.values())
    assert counts == {"locate": 12, "tokenize": 12}


def test_dictionary_benchmark_imports_and_queries_generated_archive(tmp_path):
    result = _core_module().benchmark_dictionary(tmp_path, terms=8, reps=1)

    assert set(result) == {"import_median_ms", "lookup_p95_ms"}
    assert all(math.isfinite(value) and value >= 0 for value in result.values())


def test_scroll_benchmark_checks_ready_frames_at_every_scale():
    result = _core_module().benchmark_scroll(reps=1, samples=2)

    assert len(result) == 9
    assert all(math.isfinite(item["value"]) and item["value"] >= 0 for item in result)
    assert {item["name"].rsplit("(", 1)[1] for item in result} == {"1x)", "1.5x)", "2x)"}
    manifest = json.loads(
        (CORE_PATH.parent.parent / "benchmarks/continuous-core-metrics.json").read_text()
    )
    assert [(item["name"], item["unit"]) for item in result] == [
        (item["name"], item["unit"]) for item in manifest if item["name"].startswith("scroll:")
    ]

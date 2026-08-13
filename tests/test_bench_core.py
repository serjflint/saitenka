import importlib.util
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


def test_subtitle_benchmark_exercises_parse_index_and_tokenize():
    result = _core_module().benchmark_subtitles(cues=6, reps=1)

    assert set(result) == {"median_ms", "p95_ms"}
    assert all(math.isfinite(value) and value >= 0 for value in result.values())


def test_dictionary_benchmark_imports_and_queries_generated_archive(tmp_path):
    result = _core_module().benchmark_dictionary(tmp_path, terms=8, reps=1)

    assert set(result) == {"import_median_ms", "lookup_p95_ms"}
    assert all(math.isfinite(value) and value >= 0 for value in result.values())

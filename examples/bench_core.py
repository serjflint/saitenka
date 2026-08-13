"""Deterministic, self-contained continuous benchmark suite for GitHub-hosted runners."""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bench_responsiveness as responsiveness
from saitenka_dict import DictionaryDatabase, SqliteDictionaryStore, TermQuery, Translator

from saitenka.app.tokenize import tokenize
from saitenka.subtitles import CueIndex
from saitenka.subtitles.parsers import parse_cues


@dataclass(frozen=True, slots=True)
class CoreBenchmarkConfig:
    reps: int = 5
    latency_samples: int = 25
    render_entries: int = 60
    subtitle_cues: int = 180
    dictionary_terms: int = 300


def _percentile(samples: list[float], quantile: float) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(quantile * len(ordered)))]


def _timed(call, reps: int) -> list[float]:
    samples: list[float] = []
    for _ in range(reps):
        start = time.perf_counter()
        call()
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples


def _srt_timestamp(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, whole_seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},000"


def _subtitle_corpus(count: int) -> str:
    blocks = []
    for index in range(count):
        start = index * 2
        blocks.append(
            f"{index + 1}\n{_srt_timestamp(start)} --> {_srt_timestamp(start + 1)}\n"
            f"門前の小僧は{index}番目の経を読んでいる。\n"
        )
    return "\n".join(blocks)


def benchmark_subtitles(*, cues: int, reps: int) -> dict[str, float]:
    content = _subtitle_corpus(cues)

    def one_pass() -> None:
        parsed = parse_cues(content, "episode.srt")
        if len(parsed) != cues:
            raise RuntimeError(f"generated subtitle corpus parsed {len(parsed)}/{cues} cues")
        index = CueIndex(parsed)
        for position, cue in enumerate(parsed):
            tokenize(cue.text)
            index.locate(sub_start=position * 2.0)

    one_pass()  # initialize MeCab outside the measurement
    samples = _timed(one_pass, reps)
    return {"median_ms": statistics.median(samples), "p95_ms": _percentile(samples, 0.95)}


def _write_dictionary(path: Path, terms: int) -> list[str]:
    surfaces = [f"語{index:04d}" for index in range(terms)]
    records = [
        [surface, f"ご{index}", "n", "", index % 10, [f"generated gloss {index}"], index, ""]
        for index, surface in enumerate(surfaces)
    ]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("index.json", json.dumps({"title": "CI generated", "format": 3}))
        archive.writestr("term_bank_1.json", json.dumps(records, ensure_ascii=False))
    return surfaces


def benchmark_dictionary(root: Path, *, terms: int, reps: int) -> dict[str, float]:
    archive = root / "generated.zip"
    surfaces = _write_dictionary(archive, terms)
    import_samples: list[float] = []
    lookup_samples: list[float] = []
    for replica in range(reps):
        database = DictionaryDatabase(root / f"dictionary-{replica}.sqlite")
        start = time.perf_counter()
        database.import_dictionary(archive)
        import_samples.append((time.perf_counter() - start) * 1000.0)

        translator = Translator(SqliteDictionaryStore(database.path))
        for surface in surfaces:
            start = time.perf_counter()
            result = translator.lookup_terms(TermQuery(surface))
            lookup_samples.append((time.perf_counter() - start) * 1000.0)
            if not result.entries:
                raise RuntimeError(f"generated dictionary lookup missed {surface}")
    return {
        "import_median_ms": statistics.median(import_samples),
        "lookup_p95_ms": _percentile(lookup_samples, 0.95),
    }


def _entry(name: str, unit: str, value: float) -> dict[str, Any]:
    return {"name": name, "unit": unit, "value": round(value, 6)}


def run(config: CoreBenchmarkConfig, output: Path) -> list[dict[str, Any]]:
    if (
        min(
            config.reps,
            config.latency_samples,
            config.render_entries,
            config.subtitle_cues,
            config.dictionary_terms,
        )
        < 1
    ):
        raise ValueError("benchmark sizes and repetitions must be positive")
    runtime = responsiveness.runtime_info()
    with tempfile.TemporaryDirectory(prefix="saitenka-core-bench-") as directory:
        root = Path(directory)
        synth_path, clicks_path = root / "synth.json", root / "clicks.json"
        responsiveness.run_synth(
            config.reps,
            runtime,
            json_path=str(synth_path),
            loops=1,
            n=config.render_entries,
        )
        responsiveness.run_clicks(config.latency_samples, runtime, False, str(clicks_path))
        synth = json.loads(synth_path.read_text(encoding="utf-8"))
        clicks = json.loads(clicks_path.read_text(encoding="utf-8"))
        subtitles = benchmark_subtitles(cues=config.subtitle_cues, reps=config.latency_samples)
        dictionary = benchmark_dictionary(root, terms=config.dictionary_terms, reps=config.reps)

    result = [
        _entry("synth median render", "ms", synth["synth_median_ms"]),
        _entry("synth p99 render", "ms", synth["synth_p99_ms"]),
        _entry("subtitles: parse/index/tokenize median", "ms", subtitles["median_ms"]),
        _entry("subtitles: parse/index/tokenize p95", "ms", subtitles["p95_ms"]),
        _entry("dictionary: generated archive import", "ms", dictionary["import_median_ms"]),
        _entry("dictionary: exact lookup p95", "ms", dictionary["lookup_p95_ms"]),
        _entry("click: sidebar redraw p95", "ms", clicks["sidebar_click"]["p95"]),
        _entry("click: backlog write p95", "ms", clicks["backlog_write"]["p95"]),
        _entry("click: mined-card store p95", "ms", clicks["mined_store_write"]["p95"]),
    ]
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("bench.json"))
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--latency-samples", type=int, default=25)
    parser.add_argument("--render-entries", type=int, default=60)
    parser.add_argument("--subtitle-cues", type=int, default=180)
    parser.add_argument("--dictionary-terms", type=int, default=300)
    args = parser.parse_args(argv)
    run(
        CoreBenchmarkConfig(
            reps=args.reps,
            latency_samples=args.latency_samples,
            render_entries=args.render_entries,
            subtitle_cues=args.subtitle_cues,
            dictionary_terms=args.dictionary_terms,
        ),
        args.output,
    )
    print(f"wrote continuous benchmark replica → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from saitenka_dict.importer import DictionaryDatabase
from saitenka_dict.models import KanjiQuery, TermQuery, TermResultMode
from saitenka_dict.sqlite_store import SqliteDictionaryStore
from saitenka_dict.translator import Translator

from oracle.headless import HeadlessYomitanOracle, OracleQuery

_MODES = tuple(TermResultMode)
_ORACLE_DIRECTORY = Path(__file__).parent


@dataclass(frozen=True, slots=True)
class Difference:
    path: str
    local: Any
    oracle: Any


@dataclass(frozen=True, slots=True)
class ParityCheck:
    surface: str
    passed: bool
    local: Any
    oracle: Any
    difference: Difference | None


@dataclass(frozen=True, slots=True)
class ParityReport:
    checkout: str
    revision: str
    checks: tuple[ParityCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def as_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    def as_markdown(self) -> str:
        lines = [
            "# Yomitan differential report",
            "",
            f"- Checkout: `{self.checkout}`",
            f"- Revision: `{self.revision}`",
            f"- Result: **{'PASS' if self.passed else 'FAIL'}**",
            "",
            "| Surface | Result |",
            "| --- | --- |",
        ]
        lines.extend(
            f"| `{check.surface}` | {'pass' if check.passed else 'FAIL'} |" for check in self.checks
        )
        for check in self.checks:
            if check.passed:
                continue
            lines.extend(
                (
                    "",
                    f"## {check.surface}",
                    "",
                    f"First difference: `{check.difference.path if check.difference else '$'}`",
                    "",
                    "Local value:",
                    "```json",
                    json.dumps(
                        check.difference.local if check.difference else check.local,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "```",
                    "Oracle value:",
                    "```json",
                    json.dumps(
                        check.difference.oracle if check.difference else check.oracle,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "```",
                )
            )
        return "\n".join(lines) + "\n"


def compare_with_yomitan(
    checkout: str | Path, *, runner: str | Path, upstream_lock: str | Path
) -> ParityReport:
    root = Path(checkout).expanduser().resolve()
    oracle = HeadlessYomitanOracle.for_upstream_fixture(root, runner=runner)
    revision = oracle.revision()
    _assert_pinned_revision(revision, upstream_lock)
    fixture = oracle.dictionary_directory
    queries = (
        *(
            OracleQuery(
                "term",
                "打ち込む",
                mode.value,
                ["default", {"type": "terms", "deinflect": False}],
            )
            for mode in _MODES
        ),
        OracleQuery(
            "term",
            "好き",
            TermResultMode.GROUP.value,
            ["default", {"type": "terms", "deinflect": False}],
        ),
        OracleQuery(
            "term",
            "内容",
            TermResultMode.GROUP.value,
            ["default", {"type": "terms", "deinflect": False}],
        ),
        OracleQuery("kanji", "打込", options="kanji"),
    )
    oracle_results = oracle.batch(queries)

    with tempfile.TemporaryDirectory(prefix="saitenka-dict-parity-") as directory:
        temporary = Path(directory)
        archive = temporary / "dictionary.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for path in fixture.iterdir():
                if path.is_file():
                    output.write(path, path.name)
        database = DictionaryDatabase(temporary / "dictionary.sqlite")
        database.import_dictionary(archive)
        translator = Translator(SqliteDictionaryStore(database.path))
        local_terms = [translator.lookup_terms(TermQuery("打ち込む", mode=mode)) for mode in _MODES]
        local_ipa = translator.lookup_terms(TermQuery("好き", mode=TermResultMode.GROUP))
        local_structured = translator.lookup_terms(TermQuery("内容", mode=TermResultMode.GROUP))
        local_kanji = translator.lookup_kanji(KanjiQuery("打込"))

    checks = [
        _check(f"term/{mode.value}", _local_terms(result), _oracle_terms(oracle))
        for mode, result, oracle in zip(
            _MODES, local_terms, oracle_results[: len(_MODES)], strict=True
        )
    ]
    grouped = local_terms[_MODES.index(TermResultMode.GROUP)]
    grouped_oracle = oracle_results[_MODES.index(TermResultMode.GROUP)]
    checks.extend(
        (
            _check("term/tags", _local_tags(grouped), _oracle_tags(grouped_oracle)),
            _check(
                "term/frequencies",
                _local_frequencies(grouped),
                _oracle_frequencies(grouped_oracle),
            ),
            _check(
                "term/pronunciations",
                _local_pronunciations(grouped),
                _oracle_pronunciations(grouped_oracle),
            ),
            _check(
                "term/ipa",
                _local_pronunciations(local_ipa),
                _oracle_pronunciations(oracle_results[5]),
            ),
            _check(
                "term/structured-content",
                _local_terms(local_structured, canonical_media=True),
                _oracle_terms(oracle_results[6], canonical_media=True),
            ),
        )
    )
    checks.append(
        _check(
            "kanji/core",
            _local_kanji_core(local_kanji),
            _oracle_kanji_core(oracle_results[7]),
        )
    )
    checks.extend(
        (
            _check(
                "kanji/tags", _local_kanji_tags(local_kanji), _oracle_kanji_tags(oracle_results[7])
            ),
            _check(
                "kanji/frequencies",
                _local_kanji_frequencies(local_kanji),
                _oracle_kanji_frequencies(oracle_results[7]),
            ),
        )
    )
    return ParityReport(str(root), revision, tuple(checks))


def _assert_pinned_revision(actual: str, upstream_lock: str | Path) -> None:
    expected = json.loads(Path(upstream_lock).read_text(encoding="utf-8"))["yomitan"]
    if actual != expected:
        raise RuntimeError(
            f"Yomitan checkout is {actual}, expected pinned revision {expected}; "
            "update upstream-lock.json deliberately before comparing"
        )


def _check(surface: str, local: Any, oracle: Any) -> ParityCheck:
    difference = _first_difference(local, oracle)
    return ParityCheck(surface, difference is None, local, oracle, difference)


def _first_difference(local: Any, oracle: Any, path: str = "$") -> Difference | None:
    if isinstance(local, dict) and isinstance(oracle, dict):
        if local.keys() != oracle.keys():
            return Difference(path, sorted(local), sorted(oracle))
        for key in local:
            difference = _first_difference(local[key], oracle[key], f"{path}.{key}")
            if difference is not None:
                return difference
        return None
    if isinstance(local, (list, tuple)) and isinstance(oracle, (list, tuple)):
        if len(local) != len(oracle):
            return Difference(f"{path}.length", len(local), len(oracle))
        for index, (local_item, oracle_item) in enumerate(zip(local, oracle, strict=True)):
            difference = _first_difference(local_item, oracle_item, f"{path}[{index}]")
            if difference is not None:
                return difference
        return None
    return None if local == oracle else Difference(path, local, oracle)


def _local_terms(result: Any, *, canonical_media: bool = False) -> dict[str, Any]:
    return {
        "originalTextLength": result.original_text_length,
        "entries": [
            {
                "headwords": [(item.term, item.reading) for item in entry.headwords],
                "definitions": [
                    _canonical_media(list(item.content)) if canonical_media else list(item.content)
                    for item in entry.definitions
                ],
                "scores": [item.score for item in entry.definitions],
                "sequence": entry.sequence,
            }
            for entry in result.entries
        ],
    }


def _oracle_terms(result: dict[str, Any], *, canonical_media: bool = False) -> dict[str, Any]:
    return {
        "originalTextLength": result["originalTextLength"],
        "entries": [
            {
                "headwords": [(item["term"], item["reading"]) for item in entry["headwords"]],
                "definitions": [
                    _canonical_media(item["entries"]) if canonical_media else item["entries"]
                    for item in entry["definitions"]
                ],
                "scores": [item["score"] for item in entry["definitions"]],
                "sequence": next(
                    (
                        sequence
                        for definition in entry["definitions"]
                        for sequence in definition["sequences"]
                    ),
                    -1,
                ),
            }
            for entry in result["dictionaryEntries"]
        ],
    }


def _canonical_media(value: Any) -> Any:
    if isinstance(value, list):
        return [_canonical_media(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _canonical_media(item) for key, item in value.items()}
    if result.get("type") == "image" or result.get("tag") == "img":
        if isinstance(result.get("width"), (int, float)):
            result["width"] = "<resolved>"
        if isinstance(result.get("height"), (int, float)):
            result["height"] = "<resolved>"
    return result


def _local_tags(result: Any) -> list[Any]:
    return [
        [
            ((headword.term, headword.reading), [tag.name for tag in headword.tags])
            for headword in entry.headwords
        ]
        for entry in result.entries
    ]


def _oracle_tags(result: dict[str, Any]) -> list[Any]:
    return [
        [
            ((headword["term"], headword["reading"]), [tag["name"] for tag in headword["tags"]])
            for headword in entry["headwords"]
        ]
        for entry in result["dictionaryEntries"]
    ]


def _local_frequencies(result: Any) -> list[Any]:
    return [
        [
            (item.dictionary, item.value, item.display_value, item.reading is not None)
            for item in entry.frequencies
        ]
        for entry in result.entries
    ]


def _oracle_frequencies(result: dict[str, Any]) -> list[Any]:
    return [
        [
            (
                item["dictionary"],
                item["frequency"],
                item["displayValue"],
                item["hasReading"],
            )
            for item in entry["frequencies"]
        ]
        for entry in result["dictionaryEntries"]
    ]


def _local_pronunciations(result: Any) -> list[Any]:
    return [
        [
            (
                item.dictionary,
                item.reading,
                item.pitch_positions[0] if item.pitch_positions else None,
                item.ipa,
                list(item.nasal_morae),
                list(item.devoiced_morae),
            )
            for item in entry.pronunciations
        ]
        for entry in result.entries
    ]


def _oracle_pronunciations(result: dict[str, Any]) -> list[Any]:
    return [
        [
            (
                group["dictionary"],
                entry["headwords"][group["headwordIndex"]]["reading"],
                item.get("positions"),
                item.get("ipa"),
                item.get("nasalPositions", []),
                item.get("devoicePositions", []),
            )
            for group in entry["pronunciations"]
            for item in group["pronunciations"]
        ]
        for entry in result["dictionaryEntries"]
    ]


def _local_kanji_core(result: Any) -> list[dict[str, Any]]:
    return [
        {
            "character": entry.character,
            "onyomi": list(entry.onyomi),
            "kunyomi": list(entry.kunyomi),
            "definitions": list(entry.meanings),
            "stats": dict(entry.stats),
        }
        for entry in result.entries
    ]


def _oracle_kanji_core(result: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "character": entry["character"],
            "onyomi": entry["onyomi"],
            "kunyomi": entry["kunyomi"],
            "definitions": entry["definitions"],
            "stats": {
                stat["name"]: stat["value"] for group in entry["stats"].values() for stat in group
            },
        }
        for entry in result
    ]


def _local_kanji_tags(result: Any) -> list[Any]:
    return [[tag.name for tag in entry.tags] for entry in result.entries]


def _oracle_kanji_tags(result: list[dict[str, Any]]) -> list[Any]:
    return [[tag["name"] for tag in entry["tags"]] for entry in result]


def _local_kanji_frequencies(result: Any) -> list[Any]:
    return [
        [(item.dictionary, item.value, item.display_value) for item in entry.frequencies]
        for entry in result.entries
    ]


def _oracle_kanji_frequencies(result: list[dict[str, Any]]) -> list[Any]:
    return [
        [
            (item["dictionary"], item["frequency"], item["displayValue"])
            for item in entry["frequencies"]
        ]
        for entry in result
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare saitenka-dict with a headless Yomitan checkout"
    )
    parser.add_argument("--checkout", type=Path, default=Path.home() / "workspace/yomitan")
    parser.add_argument("--runner", type=Path, default=_ORACLE_DIRECTORY / "yomitan_oracle.mjs")
    parser.add_argument(
        "--upstream-lock", type=Path, default=_ORACLE_DIRECTORY / "upstream-lock.json"
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = compare_with_yomitan(
        arguments.checkout,
        runner=arguments.runner,
        upstream_lock=arguments.upstream_lock,
    )
    rendered = report.as_json() + "\n" if arguments.json_output else report.as_markdown()
    if arguments.output is None:
        sys.stdout.write(rendered)
    else:
        arguments.output.write_text(rendered)
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

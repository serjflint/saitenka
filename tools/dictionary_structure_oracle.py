from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from saitenka_dict import SqliteDictionaryStore, TermQuery, TermResultMode, Translator
from saitenka_tokenize.japanese import Token

from saitenka.app.dictdb import default_db_path
from saitenka.app.source_adapter import DictionarySourceAdapter, SourceAdapterOptions
from saitenka.model import Style
from saitenka.render.sc_adapter import walk

_REPOSITORY = Path(__file__).parents[1]
_ORACLE_DIRECTORY = _REPOSITORY / "saitenka-dict" / "oracle"


@dataclass(frozen=True, slots=True)
class TraceBlock:
    marker: str | None
    depth: int
    text: str


@dataclass(frozen=True, slots=True)
class Difference:
    index: int
    yomitan: TraceBlock | None
    saitenka: TraceBlock | None


@dataclass(frozen=True, slots=True)
class StructureReport:
    dictionary: str
    term: str
    reading: str
    yomitan_revision: str
    yomitan_html: str
    yomitan: tuple[TraceBlock, ...]
    saitenka: tuple[TraceBlock, ...]
    difference: Difference | None

    @property
    def passed(self) -> bool:
        return self.difference is None

    def as_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    def as_markdown(self) -> str:
        lines = [
            "# Dictionary structure differential",
            "",
            f"- Dictionary: `{self.dictionary}`",
            f"- Query: `{self.term}` / `{self.reading}`",
            f"- Yomitan revision: `{self.yomitan_revision}`",
            f"- Result: **{'PASS' if self.passed else 'FAIL'}**",
            "",
            "| # | Yomitan marker / text | Saitenka marker / indent / text |",
            "| ---: | --- | --- |",
        ]
        width = max(len(self.yomitan), len(self.saitenka))
        for index in range(width):
            yomitan = self.yomitan[index] if index < len(self.yomitan) else None
            saitenka = self.saitenka[index] if index < len(self.saitenka) else None
            lines.append(f"| {index} | {_format_block(yomitan)} | {_format_block(saitenka)} |")
        if self.difference is not None:
            lines.extend(("", f"First difference: block `{self.difference.index}`."))
        return "\n".join(lines) + "\n"


def compare_blocks(
    yomitan: tuple[TraceBlock, ...], saitenka: tuple[TraceBlock, ...]
) -> Difference | None:
    for index in range(max(len(yomitan), len(saitenka))):
        left = yomitan[index] if index < len(yomitan) else None
        right = saitenka[index] if index < len(saitenka) else None
        if left is None or right is None or left != right:
            return Difference(index, left, right)
    return None


def compare_dictionary_structure(
    database: Path,
    dictionary: str,
    term: str,
    reading: str,
    yomitan_checkout: Path,
) -> StructureReport:
    source = Translator(SqliteDictionaryStore(database))
    result = source.lookup_terms(
        TermQuery(
            term,
            mode=TermResultMode.GROUP,
            dictionaries=(dictionary,),
            primary_reading=reading or None,
        )
    )
    semantics = _matching_definitions(result, dictionary, term, reading)
    yomitan_revision = _revision(yomitan_checkout)
    _assert_pinned_revision(yomitan_revision)
    yomitan_html, yomitan_blocks = _yomitan_blocks(
        yomitan_checkout,
        dictionary,
        [list(definition.content) for definition in semantics],
    )
    adapter = DictionarySourceAdapter(
        source,
        SourceAdapterOptions(dictionaries=(dictionary,), result_mode=TermResultMode.GROUP),
    )
    token = Token(term, term, reading, "", 0, len(term))
    entry = adapter.entry_for(token)
    definition = next(item for item in entry.defs if item.dict_name == dictionary)
    saitenka_blocks = tuple(
        trace
        for block in walk(definition.content, Style(size=26), definition.media)
        for trace in _trace_block(block)
    )
    return StructureReport(
        dictionary,
        term,
        reading,
        yomitan_revision,
        yomitan_html,
        yomitan_blocks,
        saitenka_blocks,
        compare_blocks(yomitan_blocks, saitenka_blocks),
    )


def _matching_definitions(result: Any, dictionary: str, term: str, reading: str) -> tuple[Any, ...]:
    entries = tuple(
        entry
        for entry in result.entries
        if any(headword.term == term for headword in entry.headwords)
    )
    if not entries:
        entries = result.entries
    definitions: list[Any] = []
    for entry in entries:
        definitions.extend(
            definition
            for definition in entry.definitions
            if definition.source is not None and definition.source.dictionary == dictionary
        )
    if definitions:
        return tuple(definitions)
    raise RuntimeError(f"no definition for {term!r} / {reading!r} in {dictionary!r}")


def _yomitan_blocks(
    checkout: Path, dictionary: str, definitions: list[list[Any]]
) -> tuple[str, tuple[TraceBlock, ...]]:
    request = {"checkout": str(checkout), "dictionary": dictionary, "definitions": definitions}
    completed = subprocess.run(
        ["node", str(_ORACLE_DIRECTORY / "structured_content_oracle.mjs")],
        input=json.dumps(request, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    response = json.loads(completed.stdout)
    blocks = tuple(
        TraceBlock(item["marker"], item["depth"], item["text"]) for item in response["blocks"]
    )
    return response["html"], blocks


def _revision(checkout: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    return completed.stdout.strip()


def _assert_pinned_revision(actual: str) -> None:
    lock = json.loads((_ORACLE_DIRECTORY / "upstream-lock.json").read_text(encoding="utf-8"))
    if actual != lock["yomitan"]:
        raise RuntimeError(
            f"Yomitan checkout is {actual}, expected pinned revision {lock['yomitan']}; "
            "update upstream-lock.json deliberately before comparing"
        )


def _flow_text(block: Any) -> str:
    parts: list[str] = []
    for item in block.flow:
        if hasattr(item, "base"):
            parts.extend(span.text for span in item.base)
        elif hasattr(item, "label") and not hasattr(item, "text"):
            parts.append("[image]")
        elif hasattr(item, "text"):
            parts.append(item.text)
    return "".join(parts)


def _trace_block(block: Any) -> tuple[TraceBlock, ...]:
    text = _flow_text(block)
    traces = tuple(
        TraceBlock(
            _effective_marker(block) if index == 0 else None, block.indent, " ".join(row.split())
        )
        for index, row in enumerate(text.split("\n"))
        if row.strip()
    )
    if not traces and text and (marker := _effective_marker(block)) is not None:
        return (TraceBlock(marker, block.indent, ""),)
    return traces


def _effective_marker(block: Any) -> str | None:
    if block.marker is not None:
        return block.marker
    if block.kind != "list-item":
        return None
    if block.list_type == "ol":
        return f"{block.ordinal}."
    return "・"


def _format_block(block: TraceBlock | None) -> str:
    if block is None:
        return "—"
    marker = "∅" if block.marker is None else block.marker or "none"
    return f"`{marker}` / `{block.depth}` / {block.text.replace('|', '\\|')}"


def _write_artifacts(directory: Path, report: StructureReport) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "yomitan.html").write_text(report.yomitan_html, encoding="utf-8")
    (directory / "structure-report.json").write_text(report.as_json() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Yomitan DOM blocks with Saitenka's pre-render structured-content blocks."
    )
    parser.add_argument("term")
    parser.add_argument("--reading", default="")
    parser.add_argument("--dictionary", required=True)
    parser.add_argument("--db", type=Path, default=default_db_path())
    parser.add_argument("--yomitan-checkout", type=Path, default=Path("~/workspace/yomitan"))
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = compare_dictionary_structure(
        args.db.expanduser().resolve(),
        args.dictionary,
        args.term,
        args.reading,
        args.yomitan_checkout.expanduser().resolve(),
    )
    if args.artifacts is not None:
        _write_artifacts(args.artifacts.expanduser().resolve(), report)
    sys.stdout.write(report.as_json() + "\n" if args.json else report.as_markdown())
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

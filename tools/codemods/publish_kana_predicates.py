"""Rename the kana/kanji predicates to their public names and route every importer at the tokenizer.

    uv run --group codemod python tools/codemods/publish_kana_predicates.py [--check]

`wordlists` and `fsrs` imported `tokenize._has_kanji`; `card_markers` imported `lookup._is_kana`,
which was a fourth copy of the same idea in the module that happened to need it first. At a package
boundary a leading underscore is a decision, not a hint — so these become the tokenizer's public
surface and `lookup._is_kana` folds into it.

Name nodes rather than text: `_is_kana` as a substring would also match nothing here, but the same
transform run against `is_content` / `is_content_word` later would, and a codemod that is only safe
for today's names is a codemod that gets copied.
"""

from __future__ import annotations

import ast
import sys
from typing import TYPE_CHECKING

import libcst as cst
from harness import ROOT, apply

if TYPE_CHECKING:
    from pathlib import Path

#: old name -> new name. Both predicates end up in `saitenka.app.tokenize`.
RENAMES = {"_has_kanji": "has_kanji", "_is_kana": "is_kana"}

#: Importers that must now point at the tokenizer. `lookup` is here because its own definition is the
#: one being retired, not merely re-exported.
_TOKENIZE_MODULE = "saitenka.app.tokenize"
_OLD_IS_KANA_HOME = "saitenka.app.lookup"

_SWEPT = ("src", "tests", "tools", "examples")


class PublishKanaPredicates(cst.CSTTransformer):
    def __init__(self) -> None:
        self.count = 0

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:  # noqa: ARG002
        new = RENAMES.get(updated_node.value)
        if new is None:
            return updated_node
        self.count += 1
        return updated_node.with_changes(value=new)

    def leave_ImportFrom(
        self,
        original_node: cst.ImportFrom,  # noqa: ARG002
        updated_node: cst.ImportFrom,
    ) -> cst.ImportFrom:
        """`from saitenka.app.lookup import is_kana` -> the tokenizer. Runs after leave_Name, so the
        alias has already been renamed and only the module needs repointing."""
        module = updated_node.module
        if module is None or _dotted(module) != _OLD_IS_KANA_HOME:
            return updated_node
        names = updated_node.names
        if isinstance(names, cst.ImportStar) or not all(
            alias.name.value == "is_kana" for alias in names
        ):
            return updated_node
        self.count += 1
        return updated_node.with_changes(module=cst.parse_expression(_TOKENIZE_MODULE))


def _dotted(node: cst.BaseExpression) -> str:
    return cst.Module(body=[]).code_for_node(node).strip()


def worklist() -> list[Path]:
    """Files mentioning either old name at all — the rename is by Name node, so this only bounds the
    parse cost."""
    wanted = set(RENAMES)
    found: list[Path] = []
    for path in sorted(p for base in _SWEPT for p in (ROOT / base).rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        if any(
            (isinstance(node, ast.Name) and node.id in wanted)
            or (isinstance(node, ast.alias) and node.name in wanted)
            or (isinstance(node, ast.FunctionDef) and node.name in wanted)
            for node in ast.walk(tree)
        ):
            found.append(path)
    return found


if __name__ == "__main__":
    apply(
        "publish-kana-predicates",
        worklist(),
        PublishKanaPredicates,
        check="--check" in sys.argv,
    )

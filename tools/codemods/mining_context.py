"""Move exact ``p.<field>`` reads onto MiningEncounter or MiningApply.

Usage: ``uv run --group codemod tools/codemods/mining_context.py [--check]``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import libcst as cst

SOURCE = Path("src/saitenka/app/miner.py")
ENCOUNTER = {"cue", "dict_set", "ipc", "media_path", "playhead", "sentence_html", "hovered_terms"}
APPLY = {"toast", "mark_mined", "mined_here", "preview_existing", "preview_mined"}


class MiningContextTransform(cst.CSTTransformer):
    def leave_Attribute(
        self, original_node: cst.Attribute, updated_node: cst.Attribute
    ) -> cst.BaseExpression:
        if not isinstance(original_node.value, cst.Name) or original_node.value.value != "p":
            return updated_node
        field = original_node.attr.value
        owner = "encounter" if field in ENCOUNTER else "apply" if field in APPLY else None
        if owner is None:
            return updated_node
        return updated_node.with_changes(
            value=cst.Attribute(value=cst.Name("p"), attr=cst.Name(owner))
        )


def transformed(source: str) -> str:
    return cst.parse_module(source).visit(MiningContextTransform()).code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    before = SOURCE.read_text(encoding="utf-8")
    after = transformed(before)
    changed = before != after
    if args.check:
        return int(changed)
    if changed:
        SOURCE.write_text(after, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

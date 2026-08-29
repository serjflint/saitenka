"""Rewrite a flat host member onto the object that actually holds it.

    uv run --group codemod python tools/codemods/move_member.py _tip_state=tip.view.state [--check]

The worked example the recipe points at, and the shape most retirements here take: a `Delegated`
descriptor exposes one field of one owner under a flat name on the host. The rewrite retires that
compatibility projection without changing formatting or comments.

The receiver is rewritten wherever the attribute appears, which is sound only when every receiver
in the worklist is the host. Inspect the reported attribute sites before applying the transform.
"""

from __future__ import annotations

import sys
from pathlib import Path

import libcst as cst

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # a sibling script, not an installed package


def _attribute(value: cst.BaseExpression, path: str) -> cst.BaseExpression:
    for part in path.split("."):
        value = cst.Attribute(value=value, attr=cst.Name(part))
    return value


def transformer(mapping: dict[str, str]) -> type[cst.CSTTransformer]:
    class Move(cst.CSTTransformer):
        def __init__(self) -> None:
            self.count = 0

        def leave_Attribute(
            self,
            original_node: cst.Attribute,  # noqa: ARG002  # LibCST fixes both parameter names
            updated_node: cst.Attribute,
        ) -> cst.BaseExpression:
            path = mapping.get(updated_node.attr.value)
            if path is None:
                return updated_node
            self.count += 1
            return _attribute(updated_node.value, path)

    return Move


def main(argv: list[str]) -> int:
    check = "--check" in argv
    pairs = [a for a in argv if "=" in a]
    if not pairs:
        print("name at least one rewrite: old_name=owner.field", file=sys.stderr)
        return 2
    mapping = dict(pair.split("=", 1) for pair in pairs)
    paths = [p for p in harness.worklist(mapping) if p != harness.CONTROLLER]
    harness.apply("move-member", paths, transformer(mapping), check=check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

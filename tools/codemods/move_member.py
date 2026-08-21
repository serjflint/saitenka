"""Rewrite a flat host member onto the object that actually holds it.

    uv run --group codemod python tools/codemods/move_member.py _tip_state=tip.view.state [--check]

The worked example the recipe points at, and the shape most retirements here take: a `Delegated`
descriptor exposes one field of one owner under a flat name on the host, and every call site then
reads as a separate host member. Eight reads of one `tip` count as eight members to every ratchet,
so the cluster looks far more coupled than it is — the rewrite is what makes the arithmetic honest,
not a cosmetic rename.

The receiver is rewritten wherever the attribute appears, which is sound only when every receiver
in the tree is the host. Verify that with `cluster_map --member NAME` first: it lists every site,
and a site whose receiver is something else is the signal to type the receiver instead.
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
            self, _original: cst.Attribute, updated: cst.Attribute
        ) -> cst.BaseExpression:
            path = mapping.get(updated.attr.value)
            if path is None:
                return updated
            self.count += 1
            return _attribute(updated.value, path)

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

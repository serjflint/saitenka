"""Give every dictionary stand-in the `rareness_rank` view the panel now asks for.

    uv run --group codemod python tools/codemods/add_panel_dictionary_view.py [--check]

`rareness_pill` used to walk `dict_set.freqs` and re-derive the blend itself, so a fake that
answered nothing still worked by accident (`getattr(ds, "freqs", None)` → None → no pill). The view
is explicit now, and `PanelDictionary` declares it — so each stand-in has to answer.

The worklist is "test classes that define `entry_for`": that method is what makes a class a
dictionary stand-in, and no amount of text matching says so as precisely.
"""

from __future__ import annotations

import ast
import sys
from typing import TYPE_CHECKING

import libcst as cst

if TYPE_CHECKING:
    from pathlib import Path
from harness import ROOT, apply  # ROOT is the sweep anchor

_METHOD = '''
def rareness_rank(self, _token):  # noqa: ARG002  # protocol shape
    """No frequency dictionaries, so no blended rank and no pill."""
    return None
'''


class AddRarenessRank(cst.CSTTransformer):
    def __init__(self) -> None:
        self.count = 0

    def leave_ClassDef(
        self,
        original_node: cst.ClassDef,  # noqa: ARG002  # LibCST visitor signature
        updated_node: cst.ClassDef,
    ) -> cst.ClassDef:
        names = {
            item.name.value for item in updated_node.body.body if isinstance(item, cst.FunctionDef)
        }
        if "entry_for" not in names or "rareness_rank" in names:
            return updated_node
        self.count += 1
        method = cst.parse_statement(_METHOD.strip(), config=cst.PartialParserConfig())
        return updated_node.with_changes(
            body=updated_node.body.with_changes(body=[*updated_node.body.body, method])
        )


#: Harnesses live beside the suite as well as inside it — `examples/jank_live.py` carries a stand-in
#: that only the live-order test drives, and a tests-only sweep misses it.
_SWEPT = ("tests", "examples")


def worklist() -> list[Path]:
    """Files holding a class that defines `entry_for` — the dictionary stand-ins."""
    found: list[Path] = []
    for path in sorted(p for base in _SWEPT for p in (ROOT / base).rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        if any(
            isinstance(node, ast.ClassDef)
            and any(
                isinstance(item, ast.FunctionDef) and item.name == "entry_for" for item in node.body
            )
            for node in ast.walk(tree)
        ):
            found.append(path)
    return found


if __name__ == "__main__":
    apply(
        "panel-dictionary-view",
        worklist(),
        AddRarenessRank,
        check="--check" in sys.argv,
    )

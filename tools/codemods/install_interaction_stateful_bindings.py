"""Replace production interaction-store constructors with registered factories.

    uv run --group codemod python tools/codemods/install_interaction_stateful_bindings.py [--check]

Only the two production construction sites and exact constructor names from the census are eligible.
Standalone unit-test stores deliberately keep their direct constructors.
"""

from __future__ import annotations

import sys
from pathlib import Path

import libcst as cst

ROOT = Path(__file__).resolve().parents[2]
PATHS = (
    ROOT / "src/saitenka/app/session/controller.py",
    ROOT / "src/saitenka/app/features/tooltip/tooltip_controller.py",
)
STORE_BINDINGS = {
    "HoverStore": "HOVER_STATEFUL_BINDING",
    "HoveredWordStore": "HOVERED_WORD_STATEFUL_BINDING",
    "HoverPauseStore": "HOVER_PAUSE_STATEFUL_BINDING",
    "PickerStore": "PICKER_STATEFUL_BINDING",
    "PreviewStore": "PREVIEW_STATEFUL_BINDING",
    "PulseStore": "PULSE_STATEFUL_BINDING",
    "SidebarStore": "SIDEBAR_STATEFUL_BINDING",
    "TipNavStore": "TIP_NAV_STATEFUL_BINDING",
}


class InstallInteractionStatefulBindings(cst.CSTTransformer):
    def __init__(self) -> None:
        self.count = 0

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.Call:
        if not isinstance(original_node.func, cst.Name):
            return updated_node
        binding = STORE_BINDINGS.get(original_node.func.value)
        if binding is None:
            return updated_node
        self.count += 1
        return updated_node.with_changes(
            func=cst.Attribute(value=cst.Name(binding), attr=cst.Name("store"))
        )


def transformed(source: str) -> tuple[str, int]:
    transform = InstallInteractionStatefulBindings()
    tree = cst.parse_module(source).visit(transform)
    return tree.code, transform.count


def _self_test() -> None:
    source = "HoverStore(ipc)\nmodule.HoverStore(ipc)\nHoverStoreFake(ipc)\n"
    expected = "HOVER_STATEFUL_BINDING.store(ipc)\nmodule.HoverStore(ipc)\nHoverStoreFake(ipc)\n"
    rewritten, count = transformed(source)
    if rewritten != expected or count != 1:
        raise AssertionError((rewritten, count))


def main(argv: list[str]) -> int:
    _self_test()
    check = "--check" in argv
    total = touched = 0
    for path in PATHS:
        source = path.read_text(encoding="utf-8")
        rewritten, count = transformed(source)
        if not count:
            continue
        total += count
        touched += 1
        if not check:
            path.write_text(rewritten, encoding="utf-8")
    verb = "would rewrite" if check else "rewrote"
    print(f"install-interaction-stateful-bindings: {verb} {total} site(s) in {touched} file(s)")
    return int(check and total != 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

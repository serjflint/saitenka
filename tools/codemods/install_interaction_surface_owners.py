"""Move SessionController/test surface references to their feature owners.

    uv run --group codemod python tools/codemods/install_interaction_surface_owners.py [--check]

Only the established SessionController receiver names and exact legacy member chains are eligible.
"""

from __future__ import annotations

import sys
from pathlib import Path

import libcst as cst

ROOT = Path(__file__).resolve().parents[2]
PATHS = (
    ROOT / "src/saitenka/app/session/controller.py",
    *(ROOT / "examples").glob("*.py"),
    *(ROOT / "tests").glob("test_*.py"),
)
RECEIVERS = {"self", "reader", "r"}
DIRECT = {
    "_picker_store": ("picker_controller", "store"),
    "_sidebar_store": ("sidebar_controller", "store"),
    "redraw_sub_picker": ("picker_controller", "redraw"),
}
INTERACTION = {
    "picker_panel": ("picker_controller", "panel"),
    "sidebar_panel": ("sidebar_controller", "panel"),
    "preview": ("preview_controller", "state"),
    "preview_panel": ("preview_controller", "panel"),
    "preview_store": ("preview_controller", "store"),
}
SURFACE_CALLS = {
    ("sidebar", "on_click", "click_target"): ("sidebar_controller", "on_click"),
    ("sidebar", "scroll", "wheel_step"): ("sidebar_controller", "scroll"),
    ("sub_picker", "on_click", "click_target"): ("picker_controller", "on_click"),
    ("sub_picker", "scroll", "wheel_step"): ("picker_controller", "scroll"),
    ("sub_picker", "suppress_hover", "hover_suppression"): (
        "picker_controller",
        "suppress_hover",
    ),
}


def _owned(base: cst.BaseExpression, owner: str, member: str) -> cst.Attribute:
    return cst.Attribute(cst.Attribute(base, cst.Name(owner)), cst.Name(member))


class InstallInteractionSurfaceOwners(cst.CSTTransformer):
    def __init__(self) -> None:
        self.count = 0

    def leave_Attribute(
        self,
        original_node: cst.Attribute,
        updated_node: cst.Attribute,
    ) -> cst.BaseExpression:
        if isinstance(original_node.value, cst.Name) and original_node.value.value in RECEIVERS:
            replacement = DIRECT.get(original_node.attr.value)
            if replacement is not None:
                self.count += 1
                return _owned(updated_node.value, *replacement)
        value = original_node.value
        if (
            isinstance(value, cst.Attribute)
            and value.attr.value == "interaction"
            and isinstance(value.value, cst.Name)
            and value.value.value in RECEIVERS
        ):
            replacement = INTERACTION.get(original_node.attr.value)
            if replacement is not None:
                self.count += 1
                updated_value = updated_node.value
                assert isinstance(updated_value, cst.Attribute)
                return _owned(updated_value.value, *replacement)
        return updated_node

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.Call:
        func = original_node.func
        if not (
            isinstance(func, cst.Attribute)
            and isinstance(func.value, cst.Name)
            and original_node.args
        ):
            return updated_node
        first = original_node.args[0].value
        if not (
            isinstance(first, cst.Attribute)
            and isinstance(first.value, cst.Name)
            and first.value.value in RECEIVERS
        ):
            return updated_node
        replacement = SURFACE_CALLS.get((func.value.value, func.attr.value, first.attr.value))
        if replacement is None:
            return updated_node
        self.count += 1
        base = cst.Name(first.value.value)
        return updated_node.with_changes(func=_owned(base, *replacement))


def transformed(source: str) -> tuple[str, int]:
    transform = InstallInteractionSurfaceOwners()
    tree = cst.parse_module(source).visit(transform)
    return tree.code, transform.count


def _self_test() -> None:
    source = (
        "reader._sidebar_store\n"
        "reader.interaction.preview_panel\n"
        "sidebar.on_click(reader.click_target, 1, 2)\n"
        "other._sidebar_store\n"
        "sidebar.on_click(other.click_target, 1, 2)\n"
    )
    expected = (
        "reader.sidebar_controller.store\n"
        "reader.preview_controller.panel\n"
        "reader.sidebar_controller.on_click(reader.click_target, 1, 2)\n"
        "other._sidebar_store\n"
        "sidebar.on_click(other.click_target, 1, 2)\n"
    )
    rewritten, count = transformed(source)
    if rewritten != expected or count != 3:
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
    print(f"install-interaction-surface-owners: {verb} {total} site(s) in {touched} file(s)")
    return int(check and total != 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

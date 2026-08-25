"""Route the proved surface call family through the installed ``SurfaceRouter``.

    uv run --group codemod python tools/codemods/install_surface_router.py [--check]

The transform covers only the two production modules and exact receiver shapes in the source census.
It does not rewrite registry tests or surface policy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import libcst as cst

ROOT = Path(__file__).resolve().parents[2]
PATHS = (
    ROOT / "src/saitenka/app/session_controller.py",
    ROOT / "src/saitenka/app/interaction_adapter.py",
)


def _dotted(node: cst.BaseExpression) -> str | None:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        left = _dotted(node.value)
        return f"{left}.{node.attr.value}" if left else None
    return None


class InstallSurfaceRouter(cst.CSTTransformer):
    def __init__(self, relative: str) -> None:
        self._relative = relative
        self.count = 0

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.Call:
        name = _dotted(original_node.func)
        if self._relative == "src/saitenka/app/session_controller.py":
            methods = {
                "surfaces.suppress_hover": "suppress_hover",
                "surfaces.route_click": "route_click",
                "surfaces.wants_mouse_capture": "wants_mouse_capture",
            }
            receiver = cst.Name("self")
        elif self._relative == "src/saitenka/app/interaction_adapter.py":
            methods = {"surfaces.route_scroll": "route_scroll"}
            receiver = cst.Name("host")
        else:
            return updated_node
        if name is None:
            return updated_node
        method = methods.get(name)
        if method is None:
            return updated_node
        args = updated_node.args
        if method == "wants_mouse_capture":
            if (
                len(original_node.args) != 1
                or _dotted(original_node.args[0].value) != "self.interaction"
            ):
                return updated_node
            args = ()
        self.count += 1
        return updated_node.with_changes(
            func=cst.Attribute(
                value=cst.Attribute(value=receiver, attr=cst.Name("surface_router")),
                attr=cst.Name(method),
            ),
            args=args,
        )


def transformed(source: str, relative: str) -> tuple[str, int]:
    transform = InstallSurfaceRouter(relative)
    tree = cst.parse_module(source).visit(transform)
    return tree.code, transform.count


def _self_test() -> None:
    source = (
        "surfaces.suppress_hover(self.hover_suppression)\n"
        "surfaces.wants_mouse_capture(self.interaction)\n"
        "surfaces.wants_mouse_capture(other.interaction)\n"
    )
    expected = (
        "self.surface_router.suppress_hover(self.hover_suppression)\n"
        "self.surface_router.wants_mouse_capture()\n"
        "surfaces.wants_mouse_capture(other.interaction)\n"
    )
    rewritten, count = transformed(source, "src/saitenka/app/session_controller.py")
    if rewritten != expected or count != 2:
        raise AssertionError((rewritten, count))


def main(argv: list[str]) -> int:
    _self_test()
    check = "--check" in argv
    total = touched = 0
    for path in PATHS:
        source = path.read_text(encoding="utf-8")
        rewritten, count = transformed(source, path.relative_to(ROOT).as_posix())
        if not count:
            continue
        total += count
        touched += 1
        if not check:
            path.write_text(rewritten, encoding="utf-8")
    verb = "would rewrite" if check else "rewrote"
    print(f"install-surface-router: {verb} {total} site(s) in {touched} file(s)")
    return int(check and total != 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

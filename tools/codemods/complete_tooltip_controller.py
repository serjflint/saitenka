"""Route exact SessionController tooltip facts through ``TooltipController``.

    uv run --group codemod python tools/codemods/complete_tooltip_controller.py [--check]

The names moved here are deliberately ambiguous across the tree (``hover`` is also a field on
requests, observations, and ports), so this transform matches only the receiver inventory verified
before the migration. Semantic residue such as host adapters and teardown order is not guessed here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import libcst as cst

ROOT = Path(__file__).resolve().parents[2]
SESSION_CONTROLLER = "src/saitenka/app/session_controller.py"

_READS = {
    "hover": "selected",
    "pause_on_tooltip": "pause_enabled",
    "_hover_store": "hover_store",
    "_nav_store": "nav_store",
    "_pulse_store": "pulse_store",
    "_pause_store": "pause_store",
    "word_store": "word_store",
}
_DELAYS = {
    "scan_delay": "scan",
    "hide_delay": "hide",
    "hover_switch_delay": "switch",
}
_SETTERS = {
    "hover": ("select", None),
    "pause_on_tooltip": ("set_pause_enabled", "enabled"),
    "scan_delay": ("configure_delays", "scan"),
    "hide_delay": ("configure_delays", "hide"),
    "hover_switch_delay": ("configure_delays", "switch"),
}
_GLOBAL_SESSION_NAMES = frozenset({"r", "reader", "fresh_reader"})
_PATH_SESSION_NAMES = {
    "tests/test_native_subtitles.py": frozenset({"result"}),
}


def _controller(value: cst.BaseExpression) -> cst.Attribute:
    return cst.Attribute(value=value, attr=cst.Name("tooltip_controller"))


def _receiver_key(value: cst.BaseExpression) -> str | None:
    if isinstance(value, cst.Name):
        return value.value
    if (
        isinstance(value, cst.Attribute)
        and isinstance(value.value, cst.Name)
        and value.value.value == "self"
        and value.attr.value == "r"
    ):
        return "self.r"
    return None


class CompleteTooltipController(cst.CSTTransformer):
    def __init__(self, relative: str) -> None:
        self._relative = relative
        self.count = 0

    def _is_session(self, value: cst.BaseExpression) -> bool:
        receiver = _receiver_key(value)
        if self._relative == SESSION_CONTROLLER:
            return receiver == "self"
        if self._relative == "tests/driver.py":
            return receiver == "self.r"
        return receiver in (
            _GLOBAL_SESSION_NAMES | _PATH_SESSION_NAMES.get(self._relative, frozenset())
        )

    def leave_Attribute(
        self, original_node: cst.Attribute, updated_node: cst.Attribute
    ) -> cst.BaseExpression:
        if not self._is_session(original_node.value):
            return updated_node
        attribute = original_node.attr.value
        if replacement := _READS.get(attribute):
            self.count += 1
            return cst.Attribute(value=_controller(updated_node.value), attr=cst.Name(replacement))
        if delay := _DELAYS.get(attribute):
            self.count += 1
            return cst.Attribute(
                value=cst.Attribute(value=_controller(updated_node.value), attr=cst.Name("delays")),
                attr=cst.Name(delay),
            )
        if attribute == "flash_secs":
            self.count += 1
            return cst.Attribute(
                value=_controller(updated_node.value), attr=cst.Name("flash_seconds")
            )
        return updated_node

    def leave_Assign(
        self, original_node: cst.Assign, updated_node: cst.Assign
    ) -> cst.BaseSmallStatement:
        if len(original_node.targets) != 1:
            return updated_node
        target = original_node.targets[0].target
        if not isinstance(target, cst.Attribute) or not self._is_session(target.value):
            return updated_node
        setter = _SETTERS.get(target.attr.value)
        if setter is None:
            return updated_node
        method, keyword = setter
        self.count += 1
        argument = cst.Arg(
            updated_node.value,
            keyword=cst.Name(keyword) if keyword else None,
            equal=(
                cst.AssignEqual(
                    whitespace_before=cst.SimpleWhitespace(""),
                    whitespace_after=cst.SimpleWhitespace(""),
                )
                if keyword
                else cst.MaybeSentinel.DEFAULT
            ),
        )
        return cst.Expr(
            cst.Call(
                func=cst.Attribute(value=_controller(target.value), attr=cst.Name(method)),
                args=(argument,),
            )
        )


def transformed(source: str, relative: str) -> tuple[str, int]:
    transform = CompleteTooltipController(relative)
    tree = cst.parse_module(source).visit(transform)
    return tree.code, transform.count


def _self_test() -> None:
    source = (
        "r.hover = 2\n"
        "reader.pause_on_tooltip\n"
        "reader.scan_delay = 0.1\n"
        "result._pause_store.dispatch(event)\n"
        "inputs.hover\n"
        "ports.word_store\n"
        "r.tip.hover\n"
    )
    expected = (
        "r.tooltip_controller.select(2)\n"
        "reader.tooltip_controller.pause_enabled\n"
        "reader.tooltip_controller.configure_delays(scan=0.1)\n"
        "result.tooltip_controller.pause_store.dispatch(event)\n"
        "inputs.hover\n"
        "ports.word_store\n"
        "r.tip.hover\n"
    )
    rewritten, count = transformed(source, "tests/test_native_subtitles.py")
    if rewritten != expected or count != 6:
        raise AssertionError((rewritten, count))


def _paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line]


def main(argv: list[str]) -> int:
    _self_test()
    check = "--check" in argv
    total = touched = 0
    for path in _paths():
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        rewritten, count = transformed(source, relative)
        if not count:
            continue
        total += count
        touched += 1
        if not check:
            path.write_text(rewritten, encoding="utf-8")
    verb = "would rewrite" if check else "rewrote"
    print(f"complete-tooltip-controller: {verb} {total} site(s) in {touched} file(s)")
    return int(check and total != 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

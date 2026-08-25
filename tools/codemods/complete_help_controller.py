"""Route exact SessionController help access through ``HelpController``.

    uv run --group codemod python tools/codemods/complete_help_controller.py [--check]

Only the receiver names observed at the retired compatibility sites are rewritten. Surface routing
and command-registration tests are semantic residue and stay outside this transform.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import libcst as cst

ROOT = Path(__file__).resolve().parents[2]
PATH_RECEIVERS = {
    "tests/test_chrome_scale.py": frozenset({"r"}),
    "tests/test_fullscreen_chrome_smoke.py": frozenset({"r"}),
    "tests/test_help_overlay.py": frozenset({"reader", "normal", "enlarged"}),
    "tests/test_session_controller_runtime.py": frozenset({"reader"}),
    "tests/test_surfaces.py": frozenset({"reader"}),
}
ATTRIBUTE_RENAMES = {
    "_help_store": "store",
}
CALL_RENAMES = {
    "_help_document": "document",
    "_redraw_help": "redraw",
    "_run_help_command": "run",
}


def _help_owner(receiver: cst.BaseExpression) -> cst.Attribute:
    return cst.Attribute(value=receiver, attr=cst.Name("help_controller"))


class CompleteHelpController(cst.CSTTransformer):
    def __init__(self, relative: str) -> None:
        self._receivers = PATH_RECEIVERS.get(relative, frozenset())
        self.count = 0

    def _matches(self, expression: cst.BaseExpression) -> bool:
        return isinstance(expression, cst.Name) and expression.value in self._receivers

    def leave_Attribute(
        self, original_node: cst.Attribute, updated_node: cst.Attribute
    ) -> cst.BaseExpression:
        replacement = ATTRIBUTE_RENAMES.get(original_node.attr.value)
        if replacement is None or not self._matches(original_node.value):
            return updated_node
        self.count += 1
        return cst.Attribute(value=_help_owner(updated_node.value), attr=cst.Name(replacement))

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.Call:
        func = original_node.func
        if not isinstance(func, cst.Attribute) or not self._matches(func.value):
            return updated_node
        replacement = CALL_RENAMES.get(func.attr.value)
        if replacement is None:
            return updated_node
        self.count += 1
        return updated_node.with_changes(
            func=cst.Attribute(value=_help_owner(func.value), attr=cst.Name(replacement))
        )


def transformed(source: str, relative: str) -> tuple[str, int]:
    transform = CompleteHelpController(relative)
    tree = cst.parse_module(source).visit(transform)
    return tree.code, transform.count


def _self_test() -> None:
    source = (
        "reader._help_store.dispatch(command)\n"
        "reader._help_document()\n"
        "reader._redraw_help()\n"
        "reader._run_help_command(command)\n"
        "other._help_document()\n"
        'setattr(reader, "_run_help_command", callback)\n'
    )
    expected = (
        "reader.help_controller.store.dispatch(command)\n"
        "reader.help_controller.document()\n"
        "reader.help_controller.redraw()\n"
        "reader.help_controller.run(command)\n"
        "other._help_document()\n"
        'setattr(reader, "_run_help_command", callback)\n'
    )
    rewritten, count = transformed(source, "tests/test_surfaces.py")
    if rewritten != expected or count != 4:
        raise AssertionError((rewritten, count))


def _paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", *PATH_RECEIVERS],
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
    print(f"complete-help-controller: {verb} {total} site(s) in {touched} file(s)")
    return int(check and total != 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

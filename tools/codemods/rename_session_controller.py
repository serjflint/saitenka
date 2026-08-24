"""Rename the application session host and its construction seam.

    uv run --group codemod python tools/codemods/rename_session_controller.py [--check]

Python identifiers, exact module paths, and standalone prose references to the renamed host are
rewritten. Product terms such as ``ReaderOptions`` remain outside the transform.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import libcst as cst

ROOT = Path(__file__).resolve().parents[2]
NAME_RENAMES = {
    "Reader": "SessionController",
    "ReaderServices": "SessionServices",
    "_make_reader": "_make_session_controller",
    "_mentions_reader": "_mentions_session_controller",
    "_reader_aliases": "_session_controller_aliases",
    "create_reader": "create_session_controller",
    "enforce_reader_host_contract": "enforce_session_controller_host_contract",
    "reader_parameter_counts": "session_controller_parameter_counts",
    "reader_factory": "session_factory",
    "unexpected_reader_parameters": "unexpected_session_controller_parameters",
}
MODULE_RENAMES = {
    "saitenka.app.controller": "saitenka.app.session_controller",
    "saitenka.app.reader_factory": "saitenka.app.session_factory",
}
TEXT_EXCLUSIONS = {
    ".ledger.grow.jsonl",
    ".ledger.sharpen.jsonl",
    "CHANGELOG.md",
    "docs/why/comparisons.md",
    "tools/codemods/rename_session_controller.py",
}
TEXT_RENAMES = (
    (re.compile(r"saitenka\.app\.controller"), "saitenka.app.session_controller"),
    (re.compile(r"app/controller\.py"), "app/session_controller.py"),
    (re.compile(r"controller\.Reader\b"), "session_controller.SessionController"),
    (re.compile(r"\breader_factory\.py\b"), "session_factory.py"),
    (re.compile(r"\breader_factory\b"), "session_factory"),
    (re.compile(r"\bcreate_reader\b"), "create_session_controller"),
    (re.compile(r"\bReaderServices\b"), "SessionServices"),
    (
        re.compile(r"tests/test_reader_host_contract\.py"),
        "tests/test_session_controller_host_contract.py",
    ),
    (re.compile(r"tests/reader_host_contract\.py"), "tests/session_controller_host_contract.py"),
    (
        re.compile(r"tests/fixtures/reader_host_allowlist\.json"),
        "tests/fixtures/session_controller_host_allowlist.json",
    ),
    (re.compile(r"\breader_host_allowlist\b"), "session_controller_host_allowlist"),
    (re.compile(r"\breader_host_contract\b"), "session_controller_host_contract"),
    (re.compile(r"\blegacy_reader_behavior\b"), "legacy_session_controller_behavior"),
    (re.compile(r"\btest_reader_runtime\.py\b"), "test_session_controller_runtime.py"),
    (re.compile(r"\btest_controller\.py\b"), "test_session_controller.py"),
    (re.compile(r"\bReader\b"), "SessionController"),
)


def _dotted_name(node: cst.BaseExpression | None) -> str | None:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        left = _dotted_name(node.value)
        return f"{left}.{node.attr.value}" if left is not None else None
    return None


def _module(name: str) -> cst.BaseExpression:
    return cst.parse_expression(name)


class RenameSessionController(cst.CSTTransformer):
    def __init__(self) -> None:
        self.count = 0

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:
        replacement = NAME_RENAMES.get(original_node.value)
        if replacement is None:
            return updated_node
        self.count += 1
        return updated_node.with_changes(value=replacement)

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        module_name = _dotted_name(original_node.module)
        replacement = MODULE_RENAMES.get(module_name) if module_name is not None else None
        if replacement is None:
            return updated_node
        self.count += 1
        return updated_node.with_changes(module=_module(replacement))

    def leave_Import(self, original_node: cst.Import, updated_node: cst.Import) -> cst.Import:
        changed = 0
        aliases: list[cst.ImportAlias] = []
        for original_alias, updated_alias in zip(
            original_node.names, updated_node.names, strict=True
        ):
            module_name = _dotted_name(original_alias.name)
            replacement = MODULE_RENAMES.get(module_name) if module_name is not None else None
            if replacement is None:
                aliases.append(updated_alias)
                continue
            changed += 1
            aliases.append(updated_alias.with_changes(name=_module(replacement)))
        self.count += changed
        return updated_node.with_changes(names=tuple(aliases))


def _paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line]


def _tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line not in TEXT_EXCLUSIONS]


def _rewrite(source: str) -> tuple[str, int]:
    transformer = RenameSessionController()
    tree = cst.parse_module(source).visit(transformer)
    return tree.code, transformer.count


def _rewrite_text(source: str) -> tuple[str, int]:
    count = 0
    for pattern, replacement in TEXT_RENAMES:
        source, changed = pattern.subn(replacement, source)
        count += changed
    return source, count


def _self_test() -> None:
    source = (
        "from saitenka.app.controller import Reader as RealReader\n"
        "from saitenka.app.reader_factory import ReaderServices, create_reader\n"
        "value: Reader = create_reader(services=ReaderServices())\n"
        "options: ReaderOptions\n"
    )
    expected = (
        "from saitenka.app.session_controller import SessionController as RealReader\n"
        "from saitenka.app.session_factory import SessionServices, create_session_controller\n"
        "value: SessionController = create_session_controller(services=SessionServices())\n"
        "options: ReaderOptions\n"
    )
    rewritten, count = _rewrite(source)
    if rewritten != expected or count != 9:
        raise AssertionError((rewritten, count))
    text, text_count = _rewrite_text(
        "app/controller.py Reader ReaderOptions reader_factory.create_reader ReaderServices"
    )
    if (
        text
        != (
            "app/session_controller.py SessionController ReaderOptions "
            "session_factory.create_session_controller SessionServices"
        )
        or text_count != 5
    ):
        raise AssertionError((text, text_count))


def main(argv: list[str]) -> int:
    _self_test()
    check = "--check" in argv
    total = 0
    touched = 0
    for path in _paths():
        source = path.read_text(encoding="utf-8")
        rewritten, count = _rewrite(source)
        if not count:
            continue
        total += count
        touched += 1
        if not check:
            path.write_text(rewritten, encoding="utf-8")
    text_total = 0
    text_touched = 0
    for path in _tracked_paths():
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError):
            continue
        rewritten, count = _rewrite_text(source)
        if not count:
            continue
        text_total += count
        text_touched += 1
        if not check:
            path.write_text(rewritten, encoding="utf-8")
    verb = "would rewrite" if check else "rewrote"
    print(f"rename-session-controller: {verb} {total} site(s) in {touched} file(s)")
    print(f"rename-session-controller-text: {verb} {text_total} site(s) in {text_touched} file(s)")
    return int(check and (total != 0 or text_total != 0))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

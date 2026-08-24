"""Route SessionController profile reads and commands through the bounded owner.

uv run --group codemod tools/codemods/extract_profile_controller.py [--check]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import libcst as cst

ROOT = Path(__file__).resolve().parents[2]
PROFILE_ATTRIBUTES = frozenset(
    {"profile", "profiles", "profile_index", "langs", "tokenizer", "dict_set"}
)
PROFILE_METHODS = {
    "set_profile_cycle": "configure_cycle",
    "use_tokenizer": "use_tokenizer",
    "switch_to_profile": "switch_to",
}
SESSION_NAMES = frozenset({"reader", "r", "_reader", "template", "baseline", "native"})
PATH_SESSION_NAMES = {
    "tests/test_native_subtitles.py": frozenset({"result"}),
}


def _profile_controller(value: cst.BaseExpression) -> cst.Attribute:
    return cst.Attribute(value=value, attr=cst.Name("profile_controller"))


class ExtractProfileController(cst.CSTTransformer):
    def __init__(self, *, session_module: bool, session_names: frozenset[str]) -> None:
        self._session_module = session_module
        self._session_names = session_names
        self.count = 0

    def leave_Attribute(
        self, original_node: cst.Attribute, updated_node: cst.Attribute
    ) -> cst.Attribute:
        if not isinstance(original_node.value, cst.Name):
            return updated_node
        owner = original_node.value.value
        if owner == "self" and not self._session_module:
            return updated_node
        if owner != "self" and owner not in self._session_names:
            return updated_node
        attr = original_node.attr.value
        if attr in PROFILE_ATTRIBUTES:
            self.count += 1
            return updated_node.with_changes(value=_profile_controller(updated_node.value))
        replacement = PROFILE_METHODS.get(attr)
        if replacement is None:
            return updated_node
        self.count += 1
        return updated_node.with_changes(
            value=_profile_controller(updated_node.value),
            attr=cst.Name(replacement),
        )

    def leave_Assign(
        self, original_node: cst.Assign, updated_node: cst.Assign
    ) -> cst.BaseSmallStatement:
        if len(original_node.targets) != 1:
            return updated_node
        target = original_node.targets[0].target
        if not isinstance(target, cst.Attribute) or not isinstance(target.value, cst.Name):
            return updated_node
        owner = target.value.value
        if owner == "self" and not self._session_module:
            return updated_node
        if owner != "self" and owner not in self._session_names:
            return updated_node
        method = {"dict_set": "replace_dictionary_set", "tokenizer": "use_tokenizer"}.get(
            target.attr.value
        )
        if method is None:
            return updated_node
        return cst.Expr(
            cst.Call(
                func=cst.Attribute(
                    value=_profile_controller(cst.Name(owner)), attr=cst.Name(method)
                ),
                args=(cst.Arg(updated_node.value),),
            )
        )


def _paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line]


def _rewrite(path: Path, source: str) -> tuple[str, int]:
    relative = path.relative_to(ROOT).as_posix()
    transformer = ExtractProfileController(
        session_module=path == ROOT / "src/saitenka/app/session_controller.py",
        session_names=SESSION_NAMES | PATH_SESSION_NAMES.get(relative, frozenset()),
    )
    tree = cst.parse_module(source).visit(transformer)
    return tree.code, transformer.count


def _self_test() -> None:
    session_path = ROOT / "src/saitenka/app/session_controller.py"
    source = (
        "self.tokenizer\n"
        "reader.dict_set\n"
        "r.set_profile_cycle(items)\n"
        "template.dict_set = dictionaries\n"
        "native.tokenizer = tokenizer\n"
        "options.profile\n"
    )
    expected = (
        "self.profile_controller.tokenizer\n"
        "reader.profile_controller.dict_set\n"
        "r.profile_controller.configure_cycle(items)\n"
        "template.profile_controller.replace_dictionary_set(dictionaries)\n"
        "native.profile_controller.use_tokenizer(tokenizer)\n"
        "options.profile\n"
    )
    rewritten, count = _rewrite(session_path, source)
    if rewritten != expected or count != 5:
        raise AssertionError((rewritten, count))


def main(argv: list[str]) -> int:
    _self_test()
    check = "--check" in argv
    total = 0
    touched = 0
    for path in _paths():
        source = path.read_text(encoding="utf-8")
        rewritten, count = _rewrite(path, source)
        if not count:
            continue
        total += count
        touched += 1
        if not check:
            path.write_text(rewritten, encoding="utf-8")
    verb = "would rewrite" if check else "rewrote"
    print(f"extract-profile-controller: {verb} {total} site(s) in {touched} file(s)")
    return int(check and total != 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Move flat application imports onto the bounded feature packages.

    uv run --group codemod python tools/codemods/package_app_features.py [--check]

The file moves are explicit ``git mv`` operations. This transform owns the uniform import rewrite
across production, tests, tools, and examples; a second ``--check`` must report zero residue.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import libcst as cst

ROOT = Path(__file__).resolve().parents[2]

MODULE_MOVES = {
    # Feature owners and their private collaborators.
    "saitenka.app.help_controller": "saitenka.app.features.help.help_controller",
    "saitenka.app.help_overlay": "saitenka.app.features.help.help_overlay",
    "saitenka.app.picker_controller": "saitenka.app.features.picker.picker_controller",
    "saitenka.app.sub_picker": "saitenka.app.features.picker.sub_picker",
    "saitenka.app.preview_controller": "saitenka.app.features.preview.preview_controller",
    "saitenka.app.card_preview": "saitenka.app.features.preview.card_preview",
    "saitenka.app.miner_ui": "saitenka.app.features.preview.miner_ui",
    "saitenka.app.sidebar_controller": "saitenka.app.features.sidebar.sidebar_controller",
    "saitenka.app.sidebar": "saitenka.app.features.sidebar.sidebar",
    "saitenka.app.profile_controller": "saitenka.app.features.profiles.profile_controller",
    "saitenka.app.profile_adapter": "saitenka.app.features.profiles.profile_adapter",
    "saitenka.app.profile_intents": "saitenka.app.features.profiles.profile_intents",
    "saitenka.app.profile_cli": "saitenka.app.features.profiles.profile_cli",
    "saitenka.app.mining_controller": "saitenka.app.features.mining.mining_controller",
    "saitenka.app.mine_adapter": "saitenka.app.features.mining.mine_adapter",
    "saitenka.app.mine_intents": "saitenka.app.features.mining.mine_intents",
    "saitenka.app.miner": "saitenka.app.features.mining.miner",
    "saitenka.app.mined_seed": "saitenka.app.features.mining.mined_seed",
    "saitenka.app.mined_set": "saitenka.app.features.mining.mined_set",
    "saitenka.app.mined_store": "saitenka.app.features.mining.mined_store",
    "saitenka.app.word_audio": "saitenka.app.features.mining.word_audio",
    # Shared domain leaves stay below feature packages; undo either transient move idempotently.
    "saitenka.app.features.profiles.profiles": "saitenka.app.profiles",
    "saitenka.app.features.mining.card_markers": "saitenka.app.card_markers",
    "saitenka.app.tooltip_controller": "saitenka.app.features.tooltip.tooltip_controller",
    "saitenka.app.tooltip": "saitenka.app.features.tooltip.tooltip",
    "saitenka.app.tooltip_engaged": "saitenka.app.features.tooltip.tooltip_engaged",
    "saitenka.app.tooltip_panel": "saitenka.app.features.tooltip.tooltip_panel",
    "saitenka.app.tooltip_raster": "saitenka.app.features.tooltip.tooltip_raster",
    "saitenka.app.hover_adapter": "saitenka.app.features.tooltip.hover_adapter",
    "saitenka.app.hover_intents": "saitenka.app.features.tooltip.hover_intents",
    "saitenka.app.hover_metadata": "saitenka.app.features.tooltip.hover_metadata",
    "saitenka.app.hover_snapshot": "saitenka.app.features.tooltip.hover_snapshot",
    "saitenka.app.nested_popup": "saitenka.app.features.tooltip.nested_popup",
    "saitenka.app.popups": "saitenka.app.features.tooltip.popups",
    "saitenka.app.prefetch": "saitenka.app.features.tooltip.prefetch",
    # Shared interaction primitives. The global router moves with session composition below.
    "saitenka.app.interaction_jobs": "saitenka.app.interaction.jobs",
    "saitenka.app.interaction_surfaces": "saitenka.app.interaction.presentation",
    "saitenka.app.mouse_capture": "saitenka.app.interaction.mouse_capture",
    # Session composition and explicit cross-feature coordinators.
    "saitenka.app.session_controller": "saitenka.app.session.controller",
    "saitenka.app.session_assembly": "saitenka.app.session.assembly",
    "saitenka.app.session_factory": "saitenka.app.session.factory",
    "saitenka.app.session_adapter": "saitenka.app.session.adapter",
    "saitenka.app.session_intents": "saitenka.app.session.intents",
    "saitenka.app.session_resources": "saitenka.app.session.resources",
    "saitenka.app.session_routes": "saitenka.app.session.routes",
    "saitenka.app.session_runtime": "saitenka.app.session.runtime",
    "saitenka.app.reader_context": "saitenka.app.session.context",
    "saitenka.app.reader_deps": "saitenka.app.session.deps",
    "saitenka.app.close_ledger": "saitenka.app.session.close_ledger",
    "saitenka.app.stateless": "saitenka.app.session.stateless",
    "saitenka.app.interaction_adapter": "saitenka.app.session.interaction_adapter",
    "saitenka.app.interaction_intents": "saitenka.app.session.interaction_intents",
    "saitenka.app.panel_adapter": "saitenka.app.session.panel_adapter",
    "saitenka.app.panel_intents": "saitenka.app.session.panel_intents",
    "saitenka.app.mined_feedback": "saitenka.app.session.mined_feedback",
    "saitenka.app.surfaces": "saitenka.app.session.surfaces",
}

PATH_MOVES = {
    f"src/{source.replace('.', '/')}.py": f"src/{target.replace('.', '/')}.py"
    for source, target in MODULE_MOVES.items()
}


def _dotted_name(node: cst.BaseExpression | None) -> str | None:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        left = _dotted_name(node.value)
        return f"{left}.{node.attr.value}" if left is not None else None
    return None


def _module(name: str) -> cst.Name | cst.Attribute:
    expression = cst.parse_expression(name)
    assert isinstance(expression, cst.Name | cst.Attribute)
    return expression


class PackageAppFeatures(cst.CSTTransformer):
    def __init__(self) -> None:
        self.count = 0

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        module_name = _dotted_name(original_node.module)
        replacement = MODULE_MOVES.get(module_name) if module_name is not None else None
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
            replacement = MODULE_MOVES.get(module_name) if module_name is not None else None
            if replacement is None:
                aliases.append(updated_alias)
                continue
            changed += 1
            aliases.append(updated_alias.with_changes(name=_module(replacement)))
        self.count += changed
        return updated_node.with_changes(names=tuple(aliases))

    def leave_SimpleString(
        self, original_node: cst.SimpleString, updated_node: cst.SimpleString
    ) -> cst.SimpleString:
        value = ast.literal_eval(original_node.value)
        if not isinstance(value, str):
            return updated_node
        for old, new in MODULE_MOVES.items():
            if value == old or value.startswith(f"{old}."):
                self.count += 1
                return updated_node.with_changes(value=repr(f"{new}{value[len(old) :]}"))
        return updated_node

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.SimpleStatementLine | cst.FlattenSentinel[cst.BaseStatement]:
        if (
            len(original_node.body) != 1
            or not isinstance(original_node.body[0], cst.ImportFrom)
            or not isinstance(updated_node.body[0], cst.ImportFrom)
        ):
            return updated_node
        original_import = original_node.body[0]
        updated_import = updated_node.body[0]
        if _dotted_name(original_import.module) != "saitenka.app":
            return updated_node
        if not isinstance(original_import.names, tuple) or not isinstance(
            updated_import.names, tuple
        ):
            return updated_node

        unchanged: list[cst.ImportAlias] = []
        moved: list[cst.ImportAlias] = []
        for original_alias, updated_alias in zip(
            original_import.names, updated_import.names, strict=True
        ):
            name = _dotted_name(original_alias.name)
            replacement = MODULE_MOVES.get(f"saitenka.app.{name}") if name is not None else None
            if replacement is None:
                unchanged.append(updated_alias)
                continue
            local_name = (
                original_alias.asname.name.value
                if original_alias.asname is not None
                and isinstance(original_alias.asname.name, cst.Name)
                else name
            )
            if local_name is None:
                unchanged.append(updated_alias)
                continue
            moved.append(
                cst.ImportAlias(
                    name=_module(replacement),
                    asname=cst.AsName(cst.Name(local_name)),
                )
            )

        if not moved:
            return updated_node
        self.count += len(moved)
        lines: list[cst.BaseStatement] = []
        if unchanged:
            unchanged[-1] = unchanged[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
            lines.append(
                updated_node.with_changes(
                    body=(updated_import.with_changes(names=tuple(unchanged)),),
                    trailing_whitespace=cst.TrailingWhitespace(),
                )
            )
        lines.append(
            cst.SimpleStatementLine(
                body=(cst.Import(names=tuple(moved)),),
                leading_lines=updated_node.leading_lines if not unchanged else (),
                trailing_whitespace=updated_node.trailing_whitespace,
            )
        )
        return cst.FlattenSentinel(lines)


def _tracked_python_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line]


def _import_paths() -> list[Path]:
    skipped = {
        ROOT / "tools/codemods/package_app_features.py",
        ROOT / "tools/test_app_package_layout.py",
    }
    return [
        path
        for path in _tracked_python_paths()
        if path not in skipped and path.parent != ROOT / "tools/codemods"
    ]


def _rewrite(source: str) -> tuple[str, int]:
    transformer = PackageAppFeatures()
    tree = cst.parse_module(source).visit(transformer)
    return tree.code, transformer.count


def _rewrite_path_references(source: str) -> tuple[str, int]:
    count = 0
    for old, new in PATH_MOVES.items():
        occurrences = source.count(old)
        if occurrences:
            source = source.replace(old, new)
            count += occurrences
    return source, count


def _self_test() -> None:
    source = (
        "from saitenka.app.miner import bulk_mine\n"
        "from saitenka.app import miner, tooltip, telemetry\n"
        "import saitenka.app.session_controller as controller\n"
        "TARGET = 'saitenka.app.tooltip.update_hover'\n"
    )
    expected = (
        "from saitenka.app.features.mining.miner import bulk_mine\n"
        "from saitenka.app import telemetry\n"
        "import saitenka.app.features.mining.miner as miner, "
        "saitenka.app.features.tooltip.tooltip as tooltip\n"
        "import saitenka.app.session.controller as controller\n"
        "TARGET = 'saitenka.app.features.tooltip.tooltip.update_hover'\n"
    )
    rewritten, count = _rewrite(source)
    if rewritten != expected or count != 5:
        raise AssertionError((rewritten, count))


def main(argv: list[str]) -> int:
    _self_test()
    check = "--check" in argv
    total = 0
    touched = 0
    for path in _import_paths():
        source = path.read_text(encoding="utf-8")
        rewritten, count = _rewrite(source)
        if not count:
            continue
        total += count
        touched += 1
        if not check:
            path.write_text(rewritten, encoding="utf-8")
    path_reference_files = [
        path
        for path in _tracked_python_paths()
        if path != ROOT / "tools/codemods/package_app_features.py"
    ]
    path_reference_files.extend(
        (
            ROOT / "complexipy-snapshot.json",
            ROOT / ".agents/architecture-review/census.json",
        )
    )
    for path in path_reference_files:
        source = path.read_text(encoding="utf-8")
        rewritten, count = _rewrite_path_references(source)
        if not count:
            continue
        total += count
        touched += 1
        if not check:
            path.write_text(rewritten, encoding="utf-8")
    verb = "would rewrite" if check else "rewrote"
    print(f"package-app-features: {verb} {total} site(s) in {touched} file(s)")
    return int(check and total != 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

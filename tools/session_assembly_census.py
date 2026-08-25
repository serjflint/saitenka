"""Census the declarations and callbacks that assemble one study session.

The report is migration evidence, not a ratchet.  It names the live sites that must either move
into a mechanism-specific registration or remain as an explicit shell/coordinator contract.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_SESSION_STORES = {
    "ConnectionStore",
    "HelpStore",
    "HoverPauseStore",
    "HoveredWordStore",
    "HoverStore",
    "PickerStore",
    "PlaybackStore",
    "PreviewStore",
    "PulseStore",
    "SidebarStore",
    "SubtitleTrackStore",
    "TipNavStore",
    "TranslationStore",
}


@dataclass(frozen=True, slots=True)
class Site:
    path: str
    line: int
    shape: str
    symbol: str


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    return ast.unparse(node)


def _site(root: Path, path: Path, node: ast.AST, shape: str, symbol: str) -> Site:
    return Site(str(path.relative_to(root)), node.lineno, shape, symbol)


def _python_files(root: Path) -> tuple[Path, ...]:
    roots = (root / "src", root / "tests", root / "tools", root / "examples")
    return tuple(path for base in roots if base.exists() for path in sorted(base.rglob("*.py")))


def build(root: Path = ROOT) -> dict[str, object]:
    families: dict[str, list[Site]] = {
        "direct_construction": [],
        "input_and_commands": [],
        "stateless_policy": [],
        "stateful_policy": [],
        "surfaces": [],
        "lifecycle": [],
        "operation_performers": [],
        "physical_observations": [],
        "owner_absorption": [],
    }
    for path in _python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constructor_names: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "saitenka.app.session_controller"
            ):
                constructor_names.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "SessionController"
                )
            elif isinstance(node, ast.Import):
                constructor_names.update(
                    f"{alias.asname or alias.name}.SessionController"
                    for alias in node.names
                    if alias.name == "saitenka.app.session_controller"
                )
        if path == root / "src" / "saitenka" / "app" / "session_controller.py":
            constructor_names.add("SessionController")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called = _name(node.func)
                leaf = called.rsplit(".", 1)[-1]
                if called in constructor_names:
                    families["direct_construction"].append(
                        _site(root, path, node, "constructor", called)
                    )
                if leaf in {"CommandExecutor", "CommandPolicy", "CommandSpec", "BindingSpec"}:
                    families["input_and_commands"].append(
                        _site(root, path, node, "catalog-row", leaf)
                    )
                if leaf.endswith("Adapter") or leaf == "StatelessRouter":
                    families["stateless_policy"].append(_site(root, path, node, "adapter", leaf))
                if leaf in {"SliceReducer", "RouteKey", *_SESSION_STORES}:
                    families["stateful_policy"].append(
                        _site(root, path, node, "state-install", leaf)
                    )
                if leaf == "SurfaceSpec":
                    families["surfaces"].append(_site(root, path, node, "surface-row", leaf))
                if leaf == "register_session_resource":
                    families["lifecycle"].append(
                        _site(root, path, node, "resource-registration", called)
                    )
                if leaf in {"Performing", "_perform"}:
                    families["operation_performers"].append(
                        _site(root, path, node, "performer", leaf)
                    )
                if leaf in {"observe", "observe_property"}:
                    families["physical_observations"].append(
                        _site(root, path, node, "observation", called)
                    )
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {_name(target) for target in targets}
                for name in names:
                    leaf = name.rsplit(".", 1)[-1]
                    if leaf in {"BINDINGS", "COMMAND_SPECS", "_OWNER_COMMANDS"}:
                        families["input_and_commands"].append(
                            _site(root, path, node, "catalog", leaf)
                        )
                    if leaf == "SURFACES":
                        families["surfaces"].append(_site(root, path, node, "z-order", leaf))
                    if leaf in {"_RESOURCE_OF", "_PARTICIPANT_OF"}:
                        families["lifecycle"].append(
                            _site(root, path, node, "dispatch-table", leaf)
                        )
                    if leaf == "_PERFORMER_OF":
                        families["operation_performers"].append(
                            _site(root, path, node, "dispatch-table", leaf)
                        )
        controller = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == "SessionController"
            ),
            None,
        )
        if controller is not None:
            init = next(
                (
                    node
                    for node in controller.body
                    if isinstance(node, ast.FunctionDef) and node.name == "__init__"
                ),
                None,
            )
            if init is not None:
                for node in ast.walk(init):
                    if isinstance(node, ast.Assign | ast.AnnAssign):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        for target in targets:
                            if isinstance(target, ast.Attribute) and _name(target.value) == "self":
                                families["owner_absorption"].append(
                                    _site(root, path, node, "controller-field", target.attr)
                                )

    normalized = {
        name: [asdict(site) for site in sorted(sites, key=lambda s: (s.path, s.line, s.symbol))]
        for name, sites in families.items()
    }
    payload = {"families": normalized}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return {
        "source": _source_revision(root),
        "content_hash": hashlib.sha256(canonical).hexdigest(),
        "families": normalized,
        "counts": {name: len(sites) for name, sites in normalized.items()},
    }


def _source_revision(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    head = root / ".git"
    if head.is_file():
        text = head.read_text(encoding="utf-8").strip()
        git_dir = Path(text.removeprefix("gitdir: "))
        head = git_dir / "HEAD"
    else:
        head /= "HEAD"
    if not head.exists():
        return "unknown"
    value = head.read_text(encoding="utf-8").strip()
    if not value.startswith("ref: "):
        return value
    ref = value.removeprefix("ref: ")
    candidate = head.parent / ref
    return candidate.read_text(encoding="utf-8").strip() if candidate.exists() else ref


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.root), indent=None if args.compact else 2, sort_keys=True))


if __name__ == "__main__":
    main()

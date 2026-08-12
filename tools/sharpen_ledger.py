"""Read/write library for the sharpen ledger (`.ledger.sharpen.jsonl`, repo top level).

The loop's durable memory: which module was audited, at what content-hash, under which toolset. Triage
reads it to skip sharpened-and-unchanged modules and grow-filed gaps; an audit appends one record. The key
is a **content-hash** (`source_sha` over the module's bytes + its mapped test files' bytes), not mtime,
so a sharpened verdict survives clones/CI. See `.agents/sharpen/SPEC.md` → *Ledger*.

Module keys are relative to `src/saitenka/` — e.g. `app/sub_index.py` — matching the existing records.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

SRC = "src/saitenka"  # module keys are relative to here
TESTS = "tests"

# Status of a module against the ledger (what triage acts on).
UNSEEN = "unseen"  # never audited → prime candidate
STALE_SHA = "stale-sha"  # audited, but module/tests changed since → re-audit
STALE_TOOLSET = "stale-toolset"  # toolset_version bumped → whole ledger re-audits
IN_PROGRESS = "in-progress"  # audited, unchanged, work explicitly left undone
SHARPENED_CURRENT = "sharpened-current"  # sharpened, unchanged, current toolset → SKIP
DRY_RUN = "dry-run"  # recorded as a dry-run (no valid review) → re-selectable


def source_sha(root: Path, module_key: str, test_files: list[str]) -> str:
    """SHA-256 over the module's bytes concatenated with its mapped test files' bytes (sorted for
    determinism). Content, not mtime — portable across clones."""
    h = hashlib.sha256()
    h.update((root / SRC / module_key).read_bytes())
    for t in sorted(test_files):
        h.update((root / t).read_bytes())
    return h.hexdigest()


def _module_key_of(dotted: str, root: Path) -> str | None:
    """`saitenka.app.sub_index` → `app/sub_index.py` if that file exists under src/saitenka, else None."""
    if not dotted.startswith("saitenka."):
        return None
    rel = dotted[len("saitenka.") :].replace(".", "/") + ".py"
    return rel if (root / SRC / rel).exists() else None


@dataclass(frozen=True)
class Attribution:
    test_file: str
    module: str
    function: str | None
    start_line: int
    end_line: int
    evidence: str
    high_confidence: bool


def _import_bindings(tree: ast.AST, root: Path) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                child = _module_key_of(f"{node.module}.{alias.name}", root)
                base = _module_key_of(node.module, root)
                module = child or base
                if module:
                    bindings[alias.asname or alias.name] = module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module = _module_key_of(alias.name, root)
                if module:
                    bindings[alias.asname or alias.name.split(".")[0]] = module
    return bindings


def test_attributions(root: Path) -> list[Attribution]:
    """Evidence-bearing test-function to production-module edges.

    A binding referenced inside a test is actionable evidence. Direct file imports that no test function
    references remain low-confidence telemetry, so a shared test file cannot silently become another
    module's actionable Conformance debt.
    """
    out: list[Attribution] = []
    for tf in sorted((root / TESTS).glob("test_*.py")):
        try:
            tree = ast.parse(tf.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        bindings = _import_bindings(tree, root)
        if not bindings:
            continue
        rel = str(tf.relative_to(root))
        used_modules: set[str] = set()
        funcs = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        by_name = {fn.name: fn for fn in funcs}
        names_by_func = {
            fn.name: {
                *(node.id for node in ast.walk(fn) if isinstance(node, ast.Name)),
                *(arg.arg for arg in fn.args.args),
            }
            for fn in funcs
        }
        modules_by_func = {
            name: {bindings[used] for used in names & bindings.keys()}
            for name, names in names_by_func.items()
        }
        changed = True
        while changed:
            changed = False
            for name, names in names_by_func.items():
                inherited: set[str] = set()
                for dependency in names & by_name.keys():
                    inherited |= modules_by_func[dependency]
                before = len(modules_by_func[name])
                modules_by_func[name] |= inherited
                changed |= len(modules_by_func[name]) != before
        for fn in funcs:
            if not fn.name.startswith("test"):
                continue
            modules = modules_by_func[fn.name]
            for module in sorted(modules):
                used_modules.add(module)
                out.append(
                    Attribution(
                        rel,
                        module,
                        fn.name,
                        fn.lineno,
                        fn.end_lineno or fn.lineno,
                        "referenced-import",
                        True,
                    )
                )
        for module in sorted(set(bindings.values()) - used_modules):
            out.extend([Attribution(rel, module, None, 1, 0, "file-import", False)])
    return out


def map_tests_to_modules(root: Path) -> dict[str, list[str]]:
    """Many-to-many module map derived from all evidence edges, for hashing and test selection."""
    out: dict[str, set[str]] = {}
    for edge in test_attributions(root):
        out.setdefault(edge.module, set()).add(edge.test_file)
    return {module: sorted(tests) for module, tests in sorted(out.items())}


def private_symbol_owners(root: Path) -> dict[str, set[str]]:
    """Private member name to modules that define it on ``self``/``cls`` or as a def."""
    owners: dict[str, set[str]] = {}
    base = root / SRC
    for source in base.rglob("*.py"):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        module = str(source.relative_to(base))
        names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("_")
        }
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for item in ast.walk(target):
                    if (
                        isinstance(item, ast.Attribute)
                        and item.attr.startswith("_")
                        and isinstance(item.value, ast.Name)
                        and item.value.id in {"self", "cls"}
                    ):
                        names.add(item.attr)
        for name in names:
            owners.setdefault(name, set()).add(module)
    return owners


@dataclass
class Ledger:
    path: Path
    lines: list[dict]

    @classmethod
    def load(cls, path: Path) -> Ledger:
        recs = [
            json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        return cls(path, recs)

    @property
    def manifest(self) -> dict:
        return next((r for r in self.lines if r.get("type") == "manifest"), {})

    @property
    def toolset_version(self) -> int:
        return int(self.manifest.get("toolset_version", 1))

    def _module_records(self) -> list[dict]:
        return [r for r in self.lines if "module" in r and "source_sha" in r]

    def latest(self, module_key: str) -> dict | None:
        """The most recent audit record for a module (records are chronological)."""
        for r in reversed(self._module_records()):
            if r["module"] == module_key:
                return r
        return None

    def status(self, module_key: str, root: Path, test_files: list[str]) -> str:
        rec = self.latest(module_key)
        if rec is None:
            return UNSEEN
        if int(rec.get("toolset_version", 1)) != self.toolset_version:
            return STALE_TOOLSET
        try:
            current = source_sha(root, module_key, test_files)
        except FileNotFoundError:
            return STALE_SHA  # module or a mapped test moved/deleted → re-audit, never crash
        if rec["source_sha"] != current:
            return STALE_SHA
        state = rec.get("state")
        if state == "sharpened":
            return SHARPENED_CURRENT
        if state == "dry-run":
            return DRY_RUN
        return IN_PROGRESS

    def grow_filed(self) -> dict[str, list[str]]:
        """`module_key -> [issue refs]` from each module's latest record (open-ness checked by triage)."""
        out: dict[str, list[str]] = {}
        for r in self._module_records():
            ids = r.get("grow-filed") or r.get("grow_filed") or []
            if ids:
                out[r["module"]] = list(ids)
        return out

    def append(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.lines.append(record)

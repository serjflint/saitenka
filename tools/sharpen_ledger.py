"""Read/write library for the sharpen ledger (`.ledger.sharpen.jsonl`, repo top level).

The loop's durable memory: which module was audited, at what content-hash, under which toolset. Triage
reads it to skip sharpened-and-unchanged modules and grow-filed gaps; an audit appends one record. The key
is a **content-hash** (`source_sha` over the module's bytes + its mapped test files' bytes), not mtime,
so a sharpened verdict survives clones/CI. See `.agents/sharpen/SPEC.md` → *Ledger*.

Module keys are relative to `src/saitenka/` — e.g. `app/sub_index.py` — matching the existing records.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SRC = "src/saitenka"  # module keys are relative to here
TESTS = "tests"
CONTRACT_VERSION = 6
OUTER_REFLECTION_CADENCE = 3
REQUIRED_AXES = {"efficacy", "conformance", "preservation", "brittleness", "redundancy"}

# Status of a module against the ledger (what triage acts on).
UNSEEN = "unseen"  # never audited → prime candidate
STALE_SHA = "stale-sha"  # audited, but module/tests changed since → re-audit
STALE_TOOLSET = "stale-toolset"  # toolset_version bumped → whole ledger re-audits
IN_PROGRESS = "in-progress"  # audited, unchanged, work explicitly left undone
SHARPENED_CURRENT = "sharpened-current"  # sharpened, unchanged, current toolset → SKIP
DRY_RUN = "dry-run"  # recorded as a dry-run (no valid review) → re-selectable
STALE_CONTRACT = "stale-contract"


def _has_axis_evidence(record: dict) -> bool:
    skipped = record.get("axes_not_applied")
    if not (
        isinstance(record.get("audited"), str)
        and bool(record["audited"].strip())
        and isinstance(record.get("axes"), dict)
        and isinstance(skipped, list)
        and all(isinstance(item, str) and bool(item.strip()) for item in skipped)
    ):
        return False
    axes = record["axes"]
    normalized = {"efficacy": axes.get("efficacy", axes.get("survival")), **axes}
    applied = set()
    for axis in REQUIRED_AXES:
        evidence = normalized.get(axis)
        if not isinstance(evidence, dict):
            continue
        if evidence.get("status") not in {"pass", "fail", "advisory"}:
            continue
        if not isinstance(evidence.get("evidence"), str) or not evidence["evidence"].strip():
            continue
        applied.add(axis)
    skipped_axes = {
        axis
        for axis in REQUIRED_AXES
        if any(
            item.lower().startswith(f"{axis}:") and item.split(":", 1)[1].strip()
            for item in skipped
        )
    }
    return applied | skipped_axes == REQUIRED_AXES


def _has_valid_review(record: dict) -> bool:
    review = record.get("review")
    if not isinstance(review, dict):
        return False
    identities = [review.get(key) for key in ("author", "skeptic", "judge")]
    if not all(isinstance(item, str) and item.strip() for item in identities):
        return False
    normalized = {item.strip().casefold() for item in identities if isinstance(item, str)}
    return len(normalized) == 3 and all(
        review.get(key) == "UPHELD" for key in ("skeptic_verdict", "judge_verdict", "verdict")
    )


def _record_contract_valid(record: dict, toolset_version: int) -> bool:
    if (
        record.get("toolset_version") != toolset_version
        or record.get("contract_version") != CONTRACT_VERSION
        or not _has_axis_evidence(record)
    ):
        return False
    return record.get("state") not in {"sharpened", "in-progress"} or _has_valid_review(record)


def _valid_outer_reflection(record: dict, toolset_version: int) -> bool:
    decision = record.get("human_decision")
    valid = bool(
        record.get("type") == "outer-reflection"
        and record.get("toolset_version") == toolset_version
        and record.get("contract_version") == CONTRACT_VERSION
        and isinstance(record.get("findings"), list)
        and record["findings"]
        and isinstance(record.get("next"), list)
        and record["next"]
        and isinstance(decision, dict)
        and decision.get("decision") == "accepted"
        and decision.get("source") == "human-provided"
        and all(
            isinstance(decision.get(key), str) and decision[key].strip()
            for key in ("identity", "decision_id", "accepted_at")
        )
    )
    if not valid:
        return False
    assert isinstance(decision, dict)
    try:
        datetime.fromisoformat(decision["accepted_at"])
    except (TypeError, ValueError):
        return False
    return True


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
    for tf in sorted((root / TESTS).rglob("test_*.py")):
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
        if not _record_contract_valid(rec, self.toolset_version):
            return STALE_CONTRACT
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
        latest = {record["module"]: record for record in self._module_records()}
        for r in latest.values():
            if not _record_contract_valid(r, self.toolset_version):
                continue
            ids = r.get("grow-filed") or r.get("grow_filed") or []
            if ids:
                out[r["module"]] = list(ids)
        return out

    def append(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.lines.append(record)

    def audits_since_outer_reflection(self) -> int:
        last = -1
        for index, record in enumerate(self.lines):
            if _valid_outer_reflection(record, self.toolset_version):
                last = index
        return sum(
            1
            for record in self.lines[last + 1 :]
            if "module" in record
            and "source_sha" in record
            and _record_contract_valid(record, self.toolset_version)
        )

    def outer_reflection_due(self) -> bool:
        return self.audits_since_outer_reflection() >= OUTER_REFLECTION_CADENCE


def _validate_test_files(root: Path, test_files: object) -> list[str]:
    if not isinstance(test_files, list) or not test_files:
        raise ValueError("record requires a non-empty tests list")
    tests_root = (root / TESTS).resolve()
    for item in test_files:
        if not isinstance(item, str):
            raise TypeError("record tests must be strings")
        path = (root / item).resolve()
        try:
            path.relative_to(tests_root)
        except ValueError as exc:
            raise ValueError("record tests must stay under tests") from exc
        if not path.is_file() or not path.name.startswith("test_") or path.suffix != ".py":
            raise ValueError("record tests must be existing test_*.py files")
    return sorted(test_files)


def prepare_record(record: dict, root: Path, ledger: Ledger) -> dict:
    if ledger.outer_reflection_due():
        raise ValueError("outer reflection is due; module records are blocked")
    module = record.get("module")
    if not isinstance(module, str) or not module:
        raise ValueError("record requires a non-empty module")
    source_root = (root / SRC).resolve()
    module_path = (source_root / module).resolve()
    try:
        module_path.relative_to(source_root)
    except ValueError as exc:
        raise ValueError("record module must stay under src/saitenka") from exc
    if not module_path.is_file():
        raise ValueError("record module must be an existing file")
    tests = _validate_test_files(root, record.get("tests"))
    if record.get("state") not in {
        "sharpened",
        "in-progress",
        "blocked-on-bug",
        "dry-run",
        "left-undone",
    }:
        raise ValueError("record has invalid state")
    if not isinstance(record.get("audited"), str) or not record["audited"].strip():
        raise ValueError("record requires audited timestamp")
    if not _has_axis_evidence(record):
        raise ValueError("record must account for every required axis")
    if record["state"] in {"sharpened", "in-progress"} and not _has_valid_review(record):
        raise ValueError(
            "shippable record requires three distinct review identities and UPHELD votes"
        )
    if record["state"] == "sharpened":
        normalized = {
            "efficacy": record["axes"].get("efficacy", record["axes"].get("survival")),
            **record["axes"],
        }
        for axis in ("efficacy", "conformance", "preservation", "brittleness"):
            if (
                not isinstance(normalized.get(axis), dict)
                or normalized[axis].get("status") != "pass"
            ):
                raise ValueError(f"sharpened record requires passing {axis} evidence")
    prepared = dict(record)
    prepared["tests"] = tests
    prepared["source_sha"] = source_sha(root, module, tests)
    prepared["toolset_version"] = ledger.toolset_version
    prepared["contract_version"] = CONTRACT_VERSION
    return prepared


def prepare_outer_reflection(record: dict, ledger: Ledger) -> dict:
    if not ledger.outer_reflection_due():
        raise ValueError("outer reflection is not due")
    for key in ("findings", "next"):
        value = record.get(key)
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item for item in value)
        ):
            raise ValueError(f"outer reflection requires a non-empty {key} string list")
    for key in ("date", "toolset_decision"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError(f"outer reflection requires {key}")
    decision = record.get("human_decision")
    if not isinstance(decision, dict):
        raise TypeError("outer reflection requires human_decision provenance")
    if decision.get("decision") != "accepted":
        raise ValueError("outer reflection human decision must be accepted")
    if decision.get("source") != "human-provided":
        raise ValueError("outer reflection must identify a human-provided decision")
    for key in ("identity", "decision_id", "accepted_at"):
        if not isinstance(decision.get(key), str) or not decision[key].strip():
            raise ValueError(f"outer reflection human decision requires {key}")
    try:
        datetime.fromisoformat(decision["accepted_at"])
    except ValueError as exc:
        raise ValueError("outer reflection accepted_at must be ISO-8601") from exc
    prepared = dict(record)
    prepared["type"] = "outer-reflection"
    prepared["toolset_version"] = ledger.toolset_version
    prepared["contract_version"] = CONTRACT_VERSION
    return prepared


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--ledger", type=Path, default=Path(".ledger.sharpen.jsonl"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reflection-status")
    for name in ("append", "append-reflection"):
        command = sub.add_parser(name)
        records = command.add_mutually_exclusive_group(required=True)
        records.add_argument("--record-json")
        records.add_argument("--record-file", type=Path)
    args = parser.parse_args()
    root = args.repo.resolve()
    ledger_path = args.ledger if args.ledger.is_absolute() else root / args.ledger
    ledger = Ledger.load(ledger_path.resolve())
    if args.command == "reflection-status":
        print(
            json.dumps(
                {
                    "due": ledger.outer_reflection_due(),
                    "audits": ledger.audits_since_outer_reflection(),
                    "cadence": OUTER_REFLECTION_CADENCE,
                }
            )
        )
        return 0
    raw = args.record_file.read_text(encoding="utf-8") if args.record_file else args.record_json
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise TypeError("record must be a JSON object")
    prepared = (
        prepare_outer_reflection(record, ledger)
        if args.command == "append-reflection"
        else prepare_record(record, root, ledger)
    )
    ledger.append(prepared)
    print(json.dumps(prepared, ensure_ascii=False))
    return 0


def main() -> int:
    try:
        return _main()
    except (OSError, ValueError, TypeError, SyntaxError, KeyError) as exc:
        print(f"sharpen-ledger: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

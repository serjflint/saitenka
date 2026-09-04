"""Read/write library for the grow ledger (`.ledger.grow.jsonl`, repo top level) — the loop's durable
memory of examined behaviour gaps and completed no-gap module audits.

Sharpen keys on a whole-module content-hash; a Grow gap is fuzzier and must be keyed SEMANTICALLY, or
line-number drift from unrelated edits spuriously reopens a closed gap and the loop never terminates
(proven in `vibe/proto_grow_ledger.py`). So:

    gap_id      = hash(source, target_symbol, dimension)          # position-free identity
    target_sha  = content-hash of the TARGET SYMBOL's AST source  # NOT the whole module

``source`` ∈ {survivor, dead_config, invariant, filed}; ``target_symbol`` = ``module_key::dotted.symbol``
(e.g. ``app/dictionary.py::Dictionary._entry_from_row``); ``dimension`` = the under-specified axis (a
coverage-context label like ``scale=2.0``, an invariant like ``warm==cold``, a survivor's operator, a
filed issue id). A closed gap stays closed under unrelated churn and reopens ONLY when its own target
symbol changes. See `.agents/grow/SPEC.md` → *Ledger*.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import grow_reflect as gr

SRC = "src/saitenka"  # module keys are relative to here (matching the sharpen ledger)
CONTRACT_VERSION = 11

# Gap status against the ledger (what triage acts on).
UNSEEN = "unseen"  # never examined → a candidate
OPEN = "open"  # examined, work left undone (e.g. a product issue filed, no test yet)
CLOSED_CURRENT = "closed-current"  # a grown test landed, target unchanged → SKIP
STALE_TARGET = "stale-target"  # the target symbol changed since → reopen
STALE_TOOLSET = "stale-toolset"  # toolset_version bumped → whole ledger re-examines
UNCLOSABLE = "unclosable"  # recorded infeasible (equivalent mutant / infeasible config) → SKIP
AUDIT_UNSEEN = "audit-unseen"
AUDITED_CURRENT = "audited-current"  # no orphan found, module/test tree unchanged → SKIP
STALE_AUDIT = "stale-audit"  # module or test tree changed → re-audit
STALE_CONTRACT = "stale-contract"  # audit predates the current lifecycle contract → re-audit


def _validate_reflection(record: dict, root: Path) -> None:
    reflection = record.get("reflection")
    if not isinstance(reflection, dict):
        raise TypeError("record requires a reflection receipt")
    if (
        not isinstance(reflection.get("introspection"), str)
        or not reflection["introspection"].strip()
    ):
        raise ValueError("reflection requires non-empty introspection")
    if reflection.get("appended") is not True:
        raise ValueError("reflection must be durably appended before the record")
    if not isinstance(reflection.get("findings"), list) or not isinstance(
        reflection.get("escalations"), list
    ):
        raise TypeError("reflection findings and escalations must be lists")
    reflection_id = reflection.get("reflection_id")
    trace_sha = reflection.get("trace_sha")
    if not isinstance(reflection_id, str) or not re.fullmatch(r"[0-9a-f]{16}", reflection_id):
        raise ValueError("reflection requires a valid reflection_id")
    if not isinstance(trace_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", trace_sha):
        raise ValueError("reflection requires a valid trace_sha")
    ledger = gr.ReflectionLedger.load(root / ".reflection.grow.jsonl")
    persisted = ledger.run_receipt(reflection_id)
    if persisted is None or persisted.get("trace_sha") != trace_sha:
        raise ValueError("reflection receipt is not present in .reflection.grow.jsonl")
    if not ledger.run_receipt_sequence_valid():
        raise ValueError("reflection receipt sequence is not unique and monotonic")
    if gr.reflection_id(persisted) != reflection_id:
        raise ValueError("reflection receipt identity differs from its durable content")
    if not isinstance(persisted.get("sequence"), int) or persisted["sequence"] < 1:
        raise ValueError("reflection receipt predates unique invocation sequencing")
    if gr._canonical_sha(persisted.get("trace")) != trace_sha:
        raise ValueError("reflection trace differs from its durable digest")
    expected_findings = [
        gr.finding_id(finding.get("category", ""), finding.get("subject", ""))
        for finding in reflection["findings"]
    ]
    if (
        persisted.get("introspection") != reflection["introspection"]
        or persisted.get("finding_ids") != expected_findings
        or persisted.get("findings_sha") != gr._canonical_sha(reflection["findings"])
        or persisted.get("escalations") != reflection["escalations"]
    ):
        raise ValueError("reflection payload differs from its durable receipt")
    trace_gap = persisted.get("trace", {}).get("gap", {})
    expected_target = record.get("target_symbol")
    if expected_target:
        expected_gap = {
            "source": record.get("source"),
            "target_symbol": expected_target,
            "dimension": record.get("dimension"),
        }
        if any(trace_gap.get(key) != value for key, value in expected_gap.items()):
            raise ValueError("reflection receipt belongs to a different Grow gap")
        if persisted.get("trace", {}).get("outcome") != record.get("state"):
            raise ValueError("reflection receipt belongs to a different Grow outcome")
        if trace_gap.get("selection_outcome") != "gap" or trace_gap.get("found") is not True:
            raise ValueError("reflection receipt did not select a Grow gap")
    expected_module = record.get("audit_module")
    if expected_module:
        expected_tests = sorted(record.get("tests", []))
        if (
            trace_gap.get("module") != expected_module
            or trace_gap.get("selection_outcome") != "no-orphan"
            or trace_gap.get("found") is not False
            or trace_gap.get("target_symbol") is not None
            or trace_gap.get("dimension") is not None
            or sorted(trace_gap.get("tests", [])) != expected_tests
            or persisted.get("trace", {}).get("outcome") != "no-gap"
        ):
            raise ValueError("reflection receipt belongs to a different module audit")


def _has_valid_review(record: dict) -> bool:
    review = record.get("review")
    if not isinstance(review, dict):
        return False
    identities = [review.get(key) for key in ("author", "skeptic", "judge")]
    normalized = {
        item.strip().casefold() for item in identities if isinstance(item, str) and item.strip()
    }
    return len(normalized) == 3 and all(
        review.get(key) == "UPHELD" for key in ("skeptic_verdict", "judge_verdict", "verdict")
    )


def _has_valid_reflection(record: dict, root: Path) -> bool:
    try:
        _validate_reflection(record, root)
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return False
    return True


def _reflection_id(record: dict) -> object:
    reflection = record.get("reflection")
    return reflection.get("reflection_id") if isinstance(reflection, dict) else None


def _has_outward_evidence(record: dict) -> bool:
    return bool(record.get("pr_url") or record.get("filed") or record.get("grow-filed"))


def _reflection_use_allowed(record: dict, prior: list[dict], *, audit: bool) -> bool:
    filed = bool(record.get("filed") or record.get("grow-filed"))
    if filed != (record.get("state") == "filed"):
        return False
    if not prior:
        return True
    if audit or len(prior) != 1:
        return False
    previous = prior[0]
    same_gap = all(
        previous.get(key) == record.get(key) for key in ("source", "target_symbol", "dimension")
    )
    return bool(
        same_gap
        and previous.get("state") == "open"
        and not _has_outward_evidence(previous)
        and record.get("state") in {"open", "filed"}
        and _has_outward_evidence(record)
    )


def gap_id(source: str, target_symbol: str, dimension: str) -> str:
    """Semantic, position-free identity — same gap, same id, wherever the symbol sits in the file."""
    return hashlib.sha256(f"{source}\x00{target_symbol}\x00{dimension}".encode()).hexdigest()[:16]


_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _symbol_nodes(module_src: str, symbol: str) -> list[ast.AST]:
    """Every def/class node matching a possibly-dotted ``symbol`` (``Foo`` or ``Foo.method``), walking
    into class bodies for the prefix. The final segment may resolve to MORE THAN ONE node — ``@overload``
    stubs plus the implementation, or a redefinition — and ALL are returned so a change to any reopens the
    gap (C7). Raises ``KeyError`` if any path segment is absent."""
    body: list[ast.stmt] = ast.parse(module_src).body
    *prefix, last = symbol.split(".")
    for part in prefix:
        node = next((n for n in body if isinstance(n, _DEFS) and n.name == part), None)
        if node is None:
            raise KeyError(symbol)
        body = node.body
    nodes: list[ast.AST] = [n for n in body if isinstance(n, _DEFS) and n.name == last]
    if not nodes:
        raise KeyError(symbol)
    return nodes


def symbol_source(module_src: str, symbol: str) -> str:
    """The normalised source (via ``ast.unparse``, which INCLUDES decorators) of every node matching
    ``symbol``, concatenated. Hashing this reopens the gap on a decorator swap (``@property`` →
    ``@cached_property``) or an overload/redefinition change (C7); formatting and comments normalise away
    (not behaviour), which also strengthens the P1 line-drift idempotency."""
    return "\n".join(ast.unparse(n) for n in _symbol_nodes(module_src, symbol))


def target_sha(module_src: str, symbol: str) -> str:
    return hashlib.sha256(symbol_source(module_src, symbol).encode()).hexdigest()[:16]


def audit_sha(root: Path, module_key: str) -> str:
    """Hash a no-gap audit's module and test tree; unrelated test drift safely reopens it."""
    h = hashlib.sha256()
    test_files = sorted(
        path
        for path in (root / "tests").rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )
    paths = [root / SRC / module_key, *test_files]
    for path in paths:
        rel = str(path.relative_to(root))
        h.update(rel.encode())
        h.update(b"\0")
        h.update(path.read_bytes())
    return h.hexdigest()


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

    def _gap_records(self) -> list[dict]:
        return [r for r in self.lines if "gap_id" in r]

    def _audit_records(self) -> list[dict]:
        return [r for r in self.lines if "audit_module" in r and "audit_sha" in r]

    def _prior_gap_record_valid(self, record: dict, root: Path) -> bool:
        if (
            record.get("contract_version") != CONTRACT_VERSION
            or record.get("toolset_version") != self.toolset_version
            or not _has_valid_reflection(record, root)
        ):
            return False
        source = record.get("source")
        target = record.get("target_symbol")
        dimension = record.get("dimension")
        if not all(isinstance(value, str) and value for value in (source, target, dimension)):
            return False
        module_key, separator, symbol = target.partition("::")
        if not separator or record.get("gap_id") != gap_id(source, target, dimension):
            return False
        try:
            module_src = (root / SRC / module_key).read_text(encoding="utf-8")
            return record.get("target_sha") == target_sha(module_src, symbol)
        except (FileNotFoundError, KeyError, SyntaxError):
            return False

    def reflection_use_valid(self, record: dict, root: Path, *, audit: bool) -> bool:
        index = next(i for i, candidate in enumerate(self.lines) if candidate is record)
        reflection_id = _reflection_id(record)
        prior = [
            candidate
            for candidate in self.lines[:index]
            if _reflection_id(candidate) == reflection_id
        ]
        return _reflection_use_allowed(record, prior, audit=audit) and (
            not prior or self._prior_gap_record_valid(prior[0], root)
        )

    def validate_new_reflection_use(self, record: dict, root: Path, *, audit: bool) -> None:
        reflection_id = _reflection_id(record)
        prior = [
            candidate for candidate in self.lines if _reflection_id(candidate) == reflection_id
        ]
        if not _reflection_use_allowed(record, prior, audit=audit) or (
            prior and not self._prior_gap_record_valid(prior[0], root)
        ):
            raise ValueError("reflection receipt was already consumed by another Grow outcome")

    def latest(self, gap: str) -> dict | None:
        """The most recent record for a gap (records are chronological)."""
        for r in reversed(self._gap_records()):
            if r["gap_id"] == gap:
                return r
        return None

    def latest_audit(self, module_key: str) -> dict | None:
        """The most recent no-gap scenario-map audit for a module."""
        for record in reversed(self._audit_records()):
            if record["audit_module"] == module_key:
                return record
        return None

    def audit_status(self, module_key: str, root: Path) -> str:
        record = self.latest_audit(module_key)
        if record is None:
            return AUDIT_UNSEEN
        if int(record.get("toolset_version", 1)) != self.toolset_version:
            return STALE_TOOLSET
        if record.get("contract_version") != CONTRACT_VERSION:
            return STALE_CONTRACT
        if not _has_valid_reflection(record, root):
            return STALE_CONTRACT
        if not self.reflection_use_valid(record, root, audit=True):
            return STALE_CONTRACT
        try:
            current = audit_sha(root, module_key)
        except FileNotFoundError:
            return STALE_AUDIT
        if record["audit_sha"] != current:
            return STALE_AUDIT
        return AUDITED_CURRENT if record.get("state") == "no-gap" else AUDIT_UNSEEN

    def status(self, gap: str, root: Path) -> str:
        """Resolve a gap's status. The module + symbol come from the stored ``target_symbol``, so the
        caller needs only the gap id and the repo root."""
        rec = self.latest(gap)
        if rec is None:
            return UNSEEN
        if int(rec.get("toolset_version", 1)) != self.toolset_version:
            return STALE_TOOLSET
        if rec.get("contract_version") != CONTRACT_VERSION:
            return STALE_CONTRACT
        if not _has_valid_reflection(rec, root):
            return STALE_CONTRACT
        if not self.reflection_use_valid(rec, root, audit=False):
            return STALE_CONTRACT
        if rec.get("state") == "closed" and not _has_valid_review(rec):
            return STALE_CONTRACT
        module_key, _, symbol = rec.get("target_symbol", "").partition("::")
        try:
            src = (root / SRC / module_key).read_text(encoding="utf-8")
            current = target_sha(src, symbol)
        except (FileNotFoundError, KeyError, SyntaxError):
            return STALE_TARGET  # module/symbol moved or unparsable → reopen, never crash
        if rec.get("target_sha") != current:
            return STALE_TARGET
        state = rec.get("state")
        if state == "closed":
            return CLOSED_CURRENT
        if state == "unclosable":
            return UNCLOSABLE
        return OPEN

    def filed(self) -> dict[str, list[str]]:
        """`gap_id -> [product issue refs]` from each gap's latest record (open-ness checked by triage).
        The reverse of Sharpen's grow-filed handshake — gaps Grow found that need a product fix."""
        out: dict[str, list[str]] = {}
        for r in self._gap_records():
            ids = r.get("filed") or r.get("grow-filed") or []
            if ids and r.get("state") == "filed":
                out[r["gap_id"]] = list(ids)
        return out

    def append(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.lines.append(record)


def prepare_record(
    record: dict, root: Path, ledger: Ledger, *, require_reflection: bool = False
) -> dict:
    """Fill the semantic identity fields a loop record must not hand-calculate."""
    source = record.get("source")
    target_symbol = record.get("target_symbol")
    dimension = record.get("dimension")
    if not isinstance(source, str) or not source:
        raise ValueError("record requires non-empty source, target_symbol, and dimension")
    if not isinstance(target_symbol, str) or not target_symbol:
        raise ValueError("record requires non-empty source, target_symbol, and dimension")
    if not isinstance(dimension, str) or not dimension:
        raise ValueError("record requires non-empty source, target_symbol, and dimension")
    if require_reflection:
        _validate_reflection(record, root)
        ledger.validate_new_reflection_use(record, root, audit=False)
    if record.get("state") == "closed" and not _has_valid_review(record):
        raise ValueError("closed record requires three distinct review identities and UPHELD votes")
    module_key, separator, symbol = target_symbol.partition("::")
    if not separator or not module_key or not symbol:
        raise ValueError("target_symbol must be module_key::dotted.symbol")
    source_root = (root / SRC).resolve()
    module_path = (source_root / module_key).resolve()
    try:
        module_path.relative_to(source_root)
    except ValueError as exc:
        raise ValueError("target module must stay under src/saitenka") from exc
    module_src = module_path.read_text(encoding="utf-8")
    prepared = dict(record)
    prepared["gap_id"] = gap_id(source, target_symbol, dimension)
    prepared["target_sha"] = target_sha(module_src, symbol)
    prepared["toolset_version"] = ledger.toolset_version
    if require_reflection:
        prepared["contract_version"] = CONTRACT_VERSION
    return prepared


def prepare_audit_record(record: dict, root: Path, ledger: Ledger) -> dict:
    """Fill the identity fields for a completed scenario-map audit with no orphan gap."""
    module_key = record.get("audit_module")
    test_files = record.get("tests")
    if not isinstance(module_key, str) or not module_key:
        raise ValueError("audit record requires a non-empty audit_module")
    if (
        not isinstance(test_files, list)
        or not test_files
        or not all(isinstance(path, str) for path in test_files)
    ):
        raise ValueError("audit record requires a non-empty tests list")
    if record.get("state") != "no-gap":
        raise ValueError("audit record state must be no-gap")
    source_root = (root / SRC).resolve()
    module_path = (source_root / module_key).resolve()
    try:
        module_path.relative_to(source_root)
    except ValueError as exc:
        raise ValueError("audit module must stay under src/saitenka") from exc
    tests_root = (root / "tests").resolve()
    for test_file in test_files:
        test_path = (root / test_file).resolve()
        try:
            test_path.relative_to(tests_root)
        except ValueError as exc:
            raise ValueError("audit tests must stay under tests") from exc
        if (
            not test_path.is_file()
            or not test_path.name.startswith("test_")
            or test_path.suffix != ".py"
        ):
            raise ValueError("audit tests must be existing test_*.py files")
    _validate_reflection(record, root)
    ledger.validate_new_reflection_use(record, root, audit=True)
    prepared = dict(record)
    prepared["tests"] = sorted(test_files)
    prepared["audit_sha"] = audit_sha(root, module_key)
    prepared["toolset_version"] = ledger.toolset_version
    prepared["contract_version"] = CONTRACT_VERSION
    return prepared


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--ledger", type=Path, default=Path("../.ledger.grow.jsonl"))
    sub = parser.add_subparsers(dest="command", required=True)

    identity = sub.add_parser("identity", help="print semantic gap_id and target_sha")
    identity.add_argument("--source", required=True)
    identity.add_argument("--target-symbol", required=True)
    identity.add_argument("--dimension", required=True)

    append = sub.add_parser("append", help="fill identity fields and append one JSON record")
    records = append.add_mutually_exclusive_group(required=True)
    records.add_argument("--record-json", help="record as one JSON object")
    records.add_argument("--record-file", type=Path, help="path containing one JSON object")

    args = parser.parse_args()
    root = args.repo.resolve()
    ledger_path = args.ledger if args.ledger.is_absolute() else root / args.ledger
    ledger = Ledger.load(ledger_path.resolve())
    if args.command == "identity":
        record = {
            "source": args.source,
            "target_symbol": args.target_symbol,
            "dimension": args.dimension,
        }
        prepared = prepare_record(record, root, ledger)
        print(
            json.dumps({key: prepared[key] for key in ("gap_id", "target_sha", "toolset_version")})
        )
        return 0

    raw = (
        args.record_file.read_text(encoding="utf-8")
        if args.record_file is not None
        else args.record_json
    )
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise TypeError("record must be a JSON object")
    prepared = (
        prepare_audit_record(record, root, ledger)
        if "audit_module" in record
        else prepare_record(record, root, ledger, require_reflection=True)
    )
    ledger.append(prepared)
    print(json.dumps(prepared, ensure_ascii=False))
    return 0


def main() -> int:
    try:
        return _main()
    except (OSError, ValueError, TypeError, SyntaxError, KeyError) as exc:
        print(f"grow-ledger: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

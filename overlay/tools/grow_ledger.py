"""Read/write library for the grow ledger (`.ledger.grow.jsonl`, repo top level) — the loop's durable
memory of which behaviour-gaps have been examined.

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

import ast
import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path  # annotation-only here — grow_ledger never constructs a Path

SRC = "src/overlay"  # module keys are relative to here (matching the sharpen ledger)

# Gap status against the ledger (what triage acts on).
UNSEEN = "unseen"  # never examined → a candidate
OPEN = "open"  # examined, work left undone (e.g. a product issue filed, no test yet)
CLOSED_CURRENT = "closed-current"  # a grown test landed, target unchanged → SKIP
STALE_TARGET = "stale-target"  # the target symbol changed since → reopen
STALE_TOOLSET = "stale-toolset"  # toolset_version bumped → whole ledger re-examines
UNCLOSABLE = "unclosable"  # recorded infeasible (equivalent mutant / infeasible config) → SKIP


def gap_id(source: str, target_symbol: str, dimension: str) -> str:
    """Semantic, position-free identity — same gap, same id, wherever the symbol sits in the file."""
    return hashlib.sha256(f"{source}\x00{target_symbol}\x00{dimension}".encode()).hexdigest()[:16]


def _symbol_node(module_src: str, symbol: str) -> ast.AST:
    """The def/class node for a possibly-dotted ``symbol`` (``Foo`` or ``Foo.method``), walking into
    class bodies. Raises ``KeyError`` if any path segment is absent."""
    body: list[ast.stmt] = ast.parse(module_src).body
    node: ast.AST | None = None
    for part in symbol.split("."):
        node = next(
            (
                n
                for n in body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.name == part
            ),
            None,
        )
        if node is None:
            raise KeyError(symbol)
        body = node.body
    assert node is not None  # split() is never empty
    return node


def symbol_source(module_src: str, symbol: str) -> str:
    """The exact source text of ``symbol`` — the unit whose change reopens the gap."""
    return ast.get_source_segment(module_src, _symbol_node(module_src, symbol)) or ""


def target_sha(module_src: str, symbol: str) -> str:
    return hashlib.sha256(symbol_source(module_src, symbol).encode()).hexdigest()[:16]


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

    def latest(self, gap: str) -> dict | None:
        """The most recent record for a gap (records are chronological)."""
        for r in reversed(self._gap_records()):
            if r["gap_id"] == gap:
                return r
        return None

    def status(self, gap: str, root: Path) -> str:
        """Resolve a gap's status. The module + symbol come from the stored ``target_symbol``, so the
        caller needs only the gap id and the repo root."""
        rec = self.latest(gap)
        if rec is None:
            return UNSEEN
        if int(rec.get("toolset_version", 1)) != self.toolset_version:
            return STALE_TOOLSET
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
            if ids:
                out[r["gap_id"]] = list(ids)
        return out

    def append(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.lines.append(record)

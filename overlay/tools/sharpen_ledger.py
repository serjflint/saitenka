"""Read/write library for the sharpen ledger (`.ledger.sharpen.jsonl`, repo top level).

The loop's durable memory: which module was audited, at what content-hash, under which toolset. Triage
reads it to skip sharpened-and-unchanged modules and grow-filed gaps; an audit appends one record. The key
is a **content-hash** (`source_sha` over the module's bytes + its mapped test files' bytes), not mtime,
so a sharpened verdict survives clones/CI. See `.agents/sharpen/SPEC.md` → *Ledger*.

Module keys are relative to `src/overlay/` — e.g. `app/sub_index.py` — matching the existing records.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

SRC = "src/overlay"  # module keys are relative to here
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
    """`overlay.app.sub_index` → `app/sub_index.py` if that file exists under src/overlay, else None."""
    if not dotted.startswith("overlay."):
        return None
    rel = dotted[len("overlay.") :].replace(".", "/") + ".py"
    return rel if (root / SRC / rel).exists() else None


def map_tests_to_modules(root: Path) -> dict[str, list[str]]:
    """`module_key -> [test file paths]`. A test's primary module is the one matching its filename stem
    (``test_controller.py`` → ``controller.py``) when that module is imported at all; otherwise the
    overlay module it imports from most (data-driven, for stage-/feature-named files with no stem match)."""
    out: dict[str, list[str]] = {}
    for tf in sorted((root / TESTS).glob("test_*.py")):
        counts: Counter[str] = Counter()
        try:
            tree = ast.parse(tf.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mk = _module_key_of(node.module, root)
                if mk:
                    counts[mk] += 1
        if not counts:
            continue
        stem = tf.stem.removeprefix("test_")
        stem_match = next((m for m in counts if Path(m).stem == stem), None)  # strongest signal
        best = max(counts.values())
        top = stem_match or min(m for m, c in counts.items() if c == best)
        out.setdefault(top, []).append(str(tf.relative_to(root)))
    return out


@dataclass
class Ledger:
    path: Path
    lines: list[dict]

    @classmethod
    def load(cls, path: Path) -> Ledger:
        recs = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
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
        if rec["source_sha"] != source_sha(root, module_key, test_files):
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

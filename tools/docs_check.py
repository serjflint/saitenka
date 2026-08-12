"""Deterministic doc-to-code checker (`poe docs-refs` / `poe docs-consts`).

Enforces "text explains, checks enforce" for saitenka's agent-facing docs. AGENTS.md,
`overlay/ARCHITECTURE.md`, and every `.agents/skills/*/SKILL.md` make concrete, checkable claims about
the code — poe task names, `.agents/` paths, module files, and constant defaults. Prose can't be
mechanically verified, so a rename or a retune silently rots the reference. These two zero-LLM passes
bind each claim to the code, so drift fails the gate instead of misleading the next agent.

  refs   — every code-font ``poe <task>`` a doc names is a real poe task; every ``.agents/{skills,rules,
           hooks,sharpen,grow,mcp}/<name>`` path and every package-qualified module/tool path it
           RECOGNISES exists on disk (best-effort recall — bare filenames / prose refs are out of scope).
  consts — each numeric default in ARCHITECTURE.md's "Constants, limits" table is (a) attributed to
           its real defining symbol and (b) equal to the value the code actually holds. Two-sided:
           a doc-only claim with no registry binding, or a registry entry the doc dropped, both fail.

Design templates (the SOTA sweep that motivated this — vibe/harness-engineering-enforcement-plan.md):
ContextCov's Markdown-slice + header-path scope; HANDBOOK.md's two-sided ``verify()`` (assert the
required claim resolves AND the stale one is caught). Planted +/- controls: tests/test_docs_check.py.

stdlib only (re / pathlib / tomllib / importlib / inspect). Run from `overlay/` via poe, but the repo
root is derived from the script location, so cwd doesn't matter.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

_TOOLS = Path(__file__).resolve().parent
_REPO = _TOOLS.parent
_PKG = _REPO / "src" / "saitenka"

# --- doc corpus ----------------------------------------------------------------------------------
# The agent-facing docs whose claims we bind. AGENTS.md is the always-loaded map; ARCHITECTURE.md the
# module/data-flow reference; each SKILL.md a loaded-on-demand procedure. All name real paths/tasks.


def _doc_files() -> list[Path]:
    docs = [
        _REPO / "AGENTS.md",
        _REPO / "overlay" / "ARCHITECTURE.md",
        _REPO / "overlay" / "README.md",
    ]
    docs += sorted((_REPO / ".agents" / "skills").glob("*/SKILL.md"))
    return [d for d in docs if d.is_file()]


# --- refs pass -----------------------------------------------------------------------------------

# `poe <task>` — whitespace after `poe` (so "poethepoet" / `` `poe` `` don't match); task is the next
# lowercase-dash token. Args after it (``--base main``, ``sub_index``) fall outside the capture.
_POE = re.compile(r"\bpoe\s+([a-z][a-z0-9-]*)")
# `.agents/<area>/<rest>` path. Trailing slash allowed (dir refs); trailing punctuation stripped later.
_AGENTS_PATH = re.compile(r"\.agents/(?:skills|rules|hooks|sharpen|grow|mcp)/[A-Za-z0-9_./-]+")
# pkg-qualified source file, e.g. `render/banded.py` — resolved under src/saitenka. The
# lookbehind stops it matching the tail of a longer explicit path (that path matches _OVERLAY_PATH).
_MODULE_FILE = re.compile(r"(?<![\w/])(?:app|render|sc|draw|raster|mpvio)/[a-z0-9_]+\.py")
# tool/script file at the repository root, e.g. `tools/mutate/run.py`, `tools/semgrep/rules.yml`.
_TOOL_FILE = re.compile(r"(?<![\w/])tools/[A-Za-z0-9_./-]+\.(?:py|yml|toml)")
# explicit repo-relative historical overlay path with a known extension.
_OVERLAY_PATH = re.compile(r"(?<![\w/])overlay/[A-Za-z0-9_./-]+\.(?:py|md|json|yml|toml)")

_STRIP = ".,;:)’'\"`"  # trailing prose punctuation to peel off a captured path
# a poe *invocation* is always in code font here; prose ("a poe gate", "the poe shim") is not a ref.
_FENCED = re.compile(r"```.*?```", re.DOTALL)
_INLINE = re.compile(r"`[^`\n]+`")


def _code_spans(text: str) -> str:
    return "\n".join(_FENCED.findall(text) + _INLINE.findall(text))


def _poe_tasks() -> set[str]:
    data = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data.get("tool", {}).get("poe", {}).get("tasks", {}))


def _ref_failures(text: str, rel: str, tasks: set[str]) -> list[str]:
    """Unresolved refs in ONE doc's text (pure seam — paths resolve against the real repo)."""
    poe = sorted(set(_POE.findall(_code_spans(text))))
    agents = sorted({m.rstrip(_STRIP) for m in _AGENTS_PATH.findall(text)})
    modules = sorted(set(_MODULE_FILE.findall(text)))
    toolz = sorted(set(_TOOL_FILE.findall(text)))
    overlays = sorted(set(_OVERLAY_PATH.findall(text)))
    return [
        *(
            f"{rel}: `poe {t}` is not a poe task (see [tool.poe.tasks])"
            for t in poe
            if t not in tasks
        ),
        *(f"{rel}: `{m}` does not exist" for m in agents if not (_REPO / m).exists()),
        *(
            f"{rel}: module `{m}` not found under src/saitenka/"
            for m in modules
            if not (_PKG / m).is_file()
        ),
        *(
            f"{rel}: `{m}` not found under repository root"
            for m in toolz
            if not (_REPO / m).is_file()
        ),
        *(f"{rel}: `{m}` does not exist" for m in overlays if not (_REPO / m).is_file()),
    ]


def check_refs() -> list[str]:
    """Every doc-named poe task / `.agents` path / module file resolves. Returns failure lines."""
    tasks = _poe_tasks()
    fails: list[str] = []
    for doc in _doc_files():
        fails += _ref_failures(doc.read_text(encoding="utf-8"), str(doc.relative_to(_REPO)), tasks)
    return fails


# --- consts pass ---------------------------------------------------------------------------------
# Each entry binds one numeric default the ARCHITECTURE.md "Constants, limits" table claims to the
# code symbol that actually owns it: `where` is a substring the row's "Where" cell must contain
# (attribution bind), `resolve` returns the live code value (value bind). The doc supplies the claimed
# number; code supplies the truth; a mismatch on either side fails. Every `ident = N` row in the table
# must be registered here and vice-versa — no silent gap in either direction.


def _cfg(cls: str, attr: str) -> Callable[[], object]:
    def go() -> object:
        mod = importlib.import_module("saitenka.app.config")
        return getattr(getattr(mod, cls)(), attr)  # frozen dataclass instance -> field default

    return go


def _attr(dotted: str, name: str) -> Callable[[], object]:
    def go() -> object:
        return getattr(importlib.import_module(dotted), name)

    return go


def _param_default(dotted: str, cls: str, param: str) -> Callable[[], object]:
    def go() -> object:
        target = getattr(importlib.import_module(dotted), cls)
        return inspect.signature(target).parameters[param].default

    return go


def _field_default(dotted: str, cls: str, attr: str) -> Callable[[], object]:
    def go() -> object:
        target = getattr(importlib.import_module(dotted), cls)
        return getattr(target(), attr)  # frozen dataclass instance -> field default

    return go


@dataclass(frozen=True)
class ConstSpec:
    ident: str  # token as written in the doc table
    where: str  # substring the row's "Where" cell must contain (real defining symbol)
    resolve: Callable[[], object]  # live code value


CONSTS: list[ConstSpec] = [
    ConstSpec("_BAND_PX", "banded.py", _attr("saitenka.render.banded", "_BAND_PX")),
    ConstSpec(
        "seed_height",
        "BandedTuning",
        _field_default("saitenka.render.banded", "BandedTuning", "seed_height"),
    ),
    ConstSpec("tip_max_frac", "TooltipOptions", _cfg("TooltipOptions", "tip_max_frac")),
    ConstSpec("panel_cache_max", "TooltipOptions", _cfg("TooltipOptions", "panel_cache_max")),
    ConstSpec("entry_cache_max", "DictDbOptions", _cfg("DictDbOptions", "entry_cache_max")),
    ConstSpec("prefetch_lookahead", "PerfOptions", _cfg("PerfOptions", "prefetch_lookahead")),
    ConstSpec(
        "head_prefetch_lookahead", "PerfOptions", _cfg("PerfOptions", "head_prefetch_lookahead")
    ),
    ConstSpec(
        "head_prefetch_queue_max", "PerfOptions", _cfg("PerfOptions", "head_prefetch_queue_max")
    ),
]

_CONST_TABLE_HEADER = "### Constants, limits, and measured timings"
# a `word = number` claim inside a table cell (number may be float); trailing unit (px) ignored.
_CLAIM = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\d+(?:\.\d+)?)")


def _const_table_rows() -> list[list[str]]:
    """The Constants table as [Knob, Value, Where] rows (header + separator dropped)."""
    text = (_REPO / "overlay" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == _CONST_TABLE_HEADER)
    except StopIteration:
        return []
    rows: list[list[str]] = []
    for ln in lines[start + 1 :]:
        s = ln.strip()
        if s.startswith(("## ", "### ")):
            break
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 3 and set(cells[0]) <= set("-: "):  # separator row
            continue
        if cells and cells[0].lower().startswith("knob"):  # header row
            continue
        rows.append(cells)
    return rows


def _doc_claims(rows: list[list[str]]) -> dict[str, tuple[str, str]]:
    """ident -> (claimed literal, its row's Where-cell text) for every `ident = N` in a Value cell."""
    doc: dict[str, tuple[str, str]] = {}
    for cells in rows:
        value_cell = cells[1]
        where_cell = cells[2] if len(cells) > 2 else ""
        for ident, literal in _CLAIM.findall(value_cell):
            doc[ident] = (literal, where_cell)
    return doc


def _consts_failures(doc: dict[str, tuple[str, str]], registry: dict[str, ConstSpec]) -> list[str]:
    """Two-sided bind of doc claims to the registry (pure seam; `resolve` still reads real code)."""
    fails: list[str] = [
        *(
            f"ARCHITECTURE.md: constant `{i}` claimed but not registered in docs_check.CONSTS"
            for i in sorted(set(doc) - set(registry))
        ),
        *(
            f"docs_check.CONSTS: `{i}` registered but not found in the ARCHITECTURE.md table"
            for i in sorted(set(registry) - set(doc))
        ),
    ]
    for ident, spec in registry.items():
        if ident not in doc:
            continue
        literal, where_cell = doc[ident]
        if spec.where not in where_cell:
            fails.append(
                f"ARCHITECTURE.md: `{ident}` is attributed to `{where_cell}` but is defined in `{spec.where}`"
            )
        try:
            code_value = spec.resolve()
        except Exception as exc:
            fails.append(f"docs_check: cannot resolve `{ident}` in code ({exc!r})")
            continue
        if float(code_value) != float(literal):
            fails.append(
                f"ARCHITECTURE.md: `{ident}` = {literal} in the doc but {code_value} in code"
            )
    return fails


def check_consts() -> list[str]:
    """Bind every numeric default in the ARCHITECTURE.md constants table to its code symbol."""
    rows = _const_table_rows()
    if not rows:
        return [f"ARCHITECTURE.md: '{_CONST_TABLE_HEADER}' table not found"]
    return _consts_failures(_doc_claims(rows), {c.ident: c for c in CONSTS})


# --- cli -----------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("which", choices=("refs", "consts"), help="which check to run")
    ns = ap.parse_args(argv)

    fails = check_refs() if ns.which == "refs" else check_consts()
    label = "docs-refs" if ns.which == "refs" else "docs-consts"
    if fails:
        print(f"{label}: {len(fails)} doc-to-code drift(s):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"{label}: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Census-lock for the vendored external-oracle corpora (`poe corpus-lock`).

We STEAL upstream conformance suites as oracles ([[external-oracle-corpus-series]] — UAX #14 linebreak,
Yomitan deinflect transforms, SubMiner subtitle-cue vectors). But a re-vendor / re-gen can silently DROP
cases and the suite still greens: the denominator is unlocked. This binds it. Per corpus we derive the
case **census** from its source-of-truth (count + a SHA-256 over the sorted key-set) and assert it EQUALS a
committed manifest. For UAX #14 the upstream file is vendored, so the census is re-derived independently;
for the transcribed JSON corpora the fixture IS the census and the lock is against silent drift between
re-gen and commit. Either way a dropped case fails the gate until the manifest is **deliberately
re-blessed** (a reviewed diff) — exactly like a golden or a `docs-consts` constant.

pg83's census-lock (trustme/shitty), grounded: Saitenka-Vault `_source/conformance-oracle-census-lock-
research.md`. Sibling to `tools/docs_check.py`; same "text explains, checks enforce" idiom. Planted +/-
controls: `tests/test_corpus_check.py`. Re-bless: `python tools/corpus_check.py show` prints the current
(count, sha256) for each corpus — paste into `MANIFEST` below when a bump legitimately moves the census.

stdlib only (json / gzip / hashlib / pathlib / dataclasses).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

_TOOLS = Path(__file__).resolve().parent  # overlay/tools
_OVERLAY = _TOOLS.parent  # overlay
_REPO = _OVERLAY.parent  # repo root

# --- census derivation (one key-list per corpus, from its source-of-truth) -----------------------


def _uax14_keys() -> list[str]:
    """Every conformance line in the vendored UAX #14 ``LineBreakTest.txt`` — the same parse
    ``tests/test_linebreak.py`` consumes (drop ``#`` comments + blanks). Key = the cleaned line, so the
    census re-derives from the upstream file itself, independent of any local manifest."""
    src = _OVERLAY / "tests" / "fixtures" / "uax14" / "LineBreakTest.txt.gz"
    text = gzip.decompress(src.read_bytes()).decode("utf-8")
    keys = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            keys.append(line)
    return keys


def _json_cases(rel: str) -> list[dict]:
    return json.loads((_OVERLAY / rel).read_text(encoding="utf-8"))["cases"]


def _deinflect_keys() -> list[str]:
    """One key per Yomitan transform vector — the full identity (category/source/term/rule/reasons/valid),
    so a dropped OR mutated vector moves the census."""
    return [
        "\x1f".join(
            (
                c["category"],
                c["source"],
                c["term"],
                str(c["rule"]),
                ",".join(c["reasons"]),
                str(c["valid"]),
            )
        )
        for c in _json_cases("../deinflect/tests/fixtures/japanese_transforms_cases.json")
    ]


def _subtitle_keys() -> list[str]:
    """One key per SubMiner parser vector — the WHOLE case (name/fn/content/expect/…), so a mutated
    input or expected-parse moves the census even when the unique ``name`` is kept."""
    return [
        json.dumps(c, sort_keys=True, ensure_ascii=False)
        for c in _json_cases("tests/fixtures/subtitle/subminer_parser_cases.json")
    ]


# --- registry --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusSpec:
    name: str
    keys: Callable[[], list[str]]  # derive the census key-list from the source-of-truth
    count: int  # committed denominator (exact) — the un-shrinkable case count
    sha256: str  # committed hash over the sorted key-list (multiset — dups preserved)
    unique: bool = True  # keys must be distinct (a dup silently inflates the denominator)


def _census(keys: list[str]) -> tuple[int, str]:
    digest = hashlib.sha256("\n".join(sorted(keys)).encode("utf-8")).hexdigest()
    return len(keys), digest


CORPORA: list[CorpusSpec] = [
    # UAX #14 line-breaking — vendored LineBreakTest.txt (census re-derived from the upstream file). Lines
    # can legitimately coincide across contexts, so keys are not required unique here.
    CorpusSpec(
        "uax14",
        _uax14_keys,
        count=19338,
        sha256="414d25e4f23e2c8e6bd40a225fe491ba37acd77102357aeea979d3a758816574",
        unique=False,
    ),
    # Yomitan deinflect transforms — transcribed from japanese-transforms.test.js @ 3af775bda1df.
    # Upstream ships 10 genuinely-identical vectors (same category/source/term/rule/reasons/valid), so the
    # census is a multiset — the sorted-key-set hash preserves multiplicity; uniqueness isn't required.
    CorpusSpec(
        "deinflect",
        _deinflect_keys,
        count=1280,
        sha256="aa52c291a0cd2d53a9b7d6e10a618f3a4f595a52729ba59d0d7f056bf3c00fa1",
        unique=False,
    ),
    # SubMiner subtitle-cue parser — transcribed from subtitle-cue-parser.test.ts @ 35adf8299cb9.
    CorpusSpec(
        "subtitle",
        _subtitle_keys,
        count=23,
        sha256="9315039ebe2150b600b7be758d89cb086762c3a653deacc2ca569168c782dbcd",
    ),
]


# --- check -----------------------------------------------------------------------------------------


def _spec_failures(spec: CorpusSpec) -> list[str]:
    """Drift for ONE corpus: dup keys (if required unique), then count and hash vs the committed manifest."""
    try:
        keys = spec.keys()
    except Exception as exc:  # noqa: BLE001 — a missing/broken source-of-truth is a real drift signal
        return [f"{spec.name}: cannot derive census ({exc!r})"]
    fails: list[str] = []
    if spec.unique and len(set(keys)) != len(keys):
        dups = sorted({k for k in keys if keys.count(k) > 1})
        fails.append(f"{spec.name}: {len(keys) - len(set(keys))} duplicate case key(s), e.g. {dups[:3]}")
    count, digest = _census(keys)
    if count != spec.count:
        verb = "shrank" if count < spec.count else "grew"
        fails.append(
            f"{spec.name}: census {verb} to {count} (manifest pins {spec.count}) — "
            f"re-bless via `python tools/corpus_check.py show` if this is a deliberate upstream bump"
        )
    if digest != spec.sha256:
        fails.append(
            f"{spec.name}: census key-set hash changed ({digest[:12]}… vs pinned {spec.sha256[:12]}…) — "
            f"a case was added/removed/mutated; re-bless deliberately"
        )
    return fails


def check() -> list[str]:
    """Every registered corpus's census equals its committed manifest. Returns failure lines."""
    fails: list[str] = []
    for spec in CORPORA:
        fails += _spec_failures(spec)
    return fails


# --- cli -------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "mode", nargs="?", default="check", choices=("check", "show"), help="check (default) or re-bless print"
    )
    ns = ap.parse_args(argv)

    if ns.mode == "show":
        for spec in CORPORA:
            count, digest = _census(spec.keys())
            print(f'CorpusSpec("{spec.name}", ..., count={count}, sha256="{digest}")')
        return 0

    fails = check()
    if fails:
        print(f"corpus-lock: {len(fails)} corpus census drift(s):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"corpus-lock: OK ({len(CORPORA)} corpora locked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

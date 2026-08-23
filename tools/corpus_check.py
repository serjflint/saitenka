"""Census-lock for the vendored external-oracle corpora (`poe corpus-lock`).

We reuse upstream conformance suites as oracles (UAX #14 linebreak and Yomitan deinflect transforms).
But a re-vendor / re-gen can silently DROP
cases and the suite still greens: the denominator is unlocked. This binds it. Per corpus we derive the
case **census** from its source-of-truth (count + a SHA-256 over the sorted key-set) and assert it EQUALS a
committed manifest. For UAX #14 the upstream file is vendored, so the census is re-derived independently;
for the transcribed JSON corpora the fixture IS the census and the lock is against silent drift between
re-gen and commit. Either way a dropped case fails the gate until the manifest is **deliberately
re-blessed** (a reviewed diff) — exactly like a golden or a `docs-consts` constant.

The idea is pg83's census-lock (trustme/shitty). Sibling to `tools/docs_check.py`; same "text explains,
checks enforce" idiom. Planted +/- controls: `tests/test_corpus_check.py`. Re-bless: `python
tools/corpus_check.py show` prints the current (count, sha256) for each corpus — paste into the registry
below when a bump legitimately moves the census.

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

_TOOLS = Path(__file__).resolve().parent
_REPO = _TOOLS.parent

# --- census derivation (one key-list per corpus, from its source-of-truth) -----------------------


def _uax14_keys() -> list[str]:
    """Every conformance line in the vendored UAX #14 ``LineBreakTest.txt`` — the same parse
    ``tests/test_linebreak.py`` consumes (drop ``#`` comments + blanks). Key = the cleaned line, so the
    census re-derives from the upstream file itself, independent of any local manifest."""
    src = _REPO / "tests" / "fixtures" / "uax14" / "LineBreakTest.txt.gz"
    text = gzip.decompress(src.read_bytes()).decode("utf-8")
    keys = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            keys.append(line)
    return keys


def _json_cases(rel: str) -> list[dict]:
    return json.loads((_REPO / rel).read_text(encoding="utf-8"))["cases"]


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
        for c in _json_cases("deinflect/tests/fixtures/japanese_transforms_cases.json")
    ]


def _subrip_keys() -> list[str]:
    """Every recorded libavcodec SubRip conversion — name, the markup fed in, and the row mpv
    reported. The row is in the key because a re-record on a machine where mpv answered differently
    is exactly the drift this catches; the name alone would call that the same case."""
    return [
        "\x1f".join((c["name"], c["markup"], c["row"]))
        for c in _json_cases("tests/fixtures/subrip_rows.json")
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
    # libavcodec SubRip→ASS conversions recorded from a live mpv by `tools/subrip_oracle.py`. The
    # oracle re-records against mpv; nothing there checks the census, so without this a re-record
    # that lost cases would shrink the agreement corpus with the suite still green.
    CorpusSpec(
        "subrip",
        _subrip_keys,
        count=26,
        sha256="98af5c408b21d77fea4112280455b7f9d1d73e4e8b8c6119b7c96f17385c16aa",
    ),
]


# --- check -----------------------------------------------------------------------------------------


def _spec_failures(spec: CorpusSpec) -> list[str]:
    """Drift for ONE corpus: dup keys (if required unique), then count and hash vs the committed manifest."""
    try:
        keys = spec.keys()
    except Exception as exc:
        return [f"{spec.name}: cannot derive census ({exc!r})"]
    fails: list[str] = []
    if spec.unique and len(set(keys)) != len(keys):
        dups = sorted({k for k in keys if keys.count(k) > 1})
        fails.append(
            f"{spec.name}: {len(keys) - len(set(keys))} duplicate case key(s), e.g. {dups[:3]}"
        )
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
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "mode",
        nargs="?",
        default="check",
        choices=("check", "show"),
        help="check (default) or re-bless print",
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

"""Machine-readable subsystem → oracle-kind map — the ACTIVE form of the coverage matrix.

`.agents/skills/write-test/references/coverage-matrix.md` is the human audit; this is the same map made
computable, so `test-kinds-advisory.py` can turn a diff into "you touched X → these non-unit kinds apply"
instead of the maintainer having to nudge for every integration / invariant / e2e test by hand. Pure +
stdlib (fnmatch), imported by the hook and tested from `tests/test_test_kinds_advisory.py`.

Each subsystem lists the **non-unit** kinds worth reaching for when its source changes (the metamorphic /
external-oracle / other families from `oracle-catalog.md`). A path may match several subsystems — the union
of their kinds applies. Keep this in sync with the matrix; it's the SSOT the advisory reads.
"""

from __future__ import annotations

from fnmatch import fnmatch

# subsystem -> (source-path globs, non-unit kinds that apply when it changes). Globs match the
# repo-relative path; test files never match (globs are source-tree specific), so a test-only commit is quiet.
SUBSYSTEMS: dict[str, tuple[list[str], list[str]]] = {
    "tokenizer": (
        ["*/app/tokenize.py", "*/app/tokenizer*.py"],
        ["metamorphic (input-equivalence)", "stolen-corpus (UAX#14)", "assembly (pipeline oracle)"],
    ),
    "deinflect": (
        ["deinflect/src/*", "deinflect/src/**"],
        ["stolen-corpus / differential (Yomitan)", "assembly (pipeline oracle)"],
    ),
    "fsrs / scoring": (
        ["*/app/fsrs.py", "*/app/scoring.py"],
        ["reference-vector (py-fsrs differential)", "property-based"],
    ),
    "render / layout": (
        ["*/render/*.py"],
        ["metamorphic (sub-pixel / scale-invariance)", "golden-image", "property-based"],
    ),
    "panel / windowed / interaction": (
        ["*/render/window.py", "*/app/controller.py"],
        ["agreement / hit-test round-trip", "stateful (RuleBasedStateMachine)", "golden-image"],
    ),
    "cache layers": (
        ["*cache*.py"],
        ["warm==cold cache-equivalence", "concurrency (race gate)", "eviction / idempotence"],
    ),
    "cli assembly": (
        ["*/app/cli_run.py", "*/app/controller.py"],
        ["assembly / pipeline integration oracle"],
    ),
    "profiles": (
        ["*/app/profiles.py"],
        ["config-commutativity", "assembly (pipeline oracle)"],
    ),
    "dictionary lookup": (
        ["*/app/dictionary.py"],
        ["assembly (pipeline oracle)", "humble-object (Fake DB / Anki)"],
    ),
    "anki note build": (
        ["*/app/anki.py"],
        ["humble-object (FakeAnki)", "assembly (pipeline oracle)"],
    ),
    "subtitle providers": (
        ["*/app/subtitle*.py", "*/app/subtitles.py"],
        ["metamorphic (input-equivalence)", "humble-object (FakeTransport)"],
    ),
    "sub_index": (
        ["*/app/sub_index.py"],
        ["metamorphic", "property-based"],
    ),
    "sc.walk": (
        ["*/sc/*.py"],
        ["golden-image"],
    ),
}


def applicable(paths: list[str]) -> list[tuple[str, list[str]]]:
    """(subsystem, kinds) for every subsystem some changed source path touches — the advisory payload.

    Deterministic `SUBSYSTEMS` order; a path matching several subsystems contributes to each."""
    hits: list[tuple[str, list[str]]] = []
    for name, (globs, kinds) in SUBSYSTEMS.items():
        if any(fnmatch(p, g) for p in paths for g in globs):
            hits.append((name, kinds))
    return hits


def signature(hits: list[tuple[str, list[str]]]) -> str:
    """A stable key over the touched subsystems — the advisory nudges once per subsystem-set per session."""
    return ",".join(sorted(name for name, _ in hits))


def advisory_text(hits: list[tuple[str, list[str]]]) -> str:
    """The nudge shown at commit time — which non-unit kinds the touched subsystems warrant."""
    lines = [
        (
            f"{len(hits)} subsystem(s) in this commit carry oracle KINDS beyond a unit test. Did this "
            "change exercise one, or only add a unit test / no test?"
        ),
        (
            "  (AGENTS.md Testing: test-kind is a decision, not a default — unit-only is a smell here. "
            "Menu: write-test skill's oracle-catalog.md + coverage-matrix.md.)"
        ),
        "",
    ]
    lines += [f"  • {name} → {', '.join(kinds)}" for name, kinds in hits]
    lines += [
        "",
        (
            "Add the applicable kind if it's missing; if it's genuinely N/A for this change, re-run the "
            "SAME commit to proceed (this fires once per subsystem-set per session)."
        ),
    ]
    return "\n".join(lines)

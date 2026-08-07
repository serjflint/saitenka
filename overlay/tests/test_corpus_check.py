"""Planted +/- controls for the corpus census-lock (tools/corpus_check.py, `poe corpus-lock`).

A denominator lock is only worth its green: it must FAIL on a planted shrink / mutated / duplicated census
and PASS on the real vendored tree. Positive control asserts the live corpora are clean (so `poe all`
stays green); negative controls plant one drift each and assert exactly it is caught (so the gate can't rot
into a no-op). Same two-sided shape as tests/test_docs_check.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CC = Path(__file__).resolve().parent.parent / "tools" / "corpus_check.py"


def _mod():
    spec = importlib.util.spec_from_file_location("_corpus_check", _CC)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m  # @dataclass resolves types via sys.modules[__module__] (3.14)
    spec.loader.exec_module(m)
    return m


# --- positive control: the real tree is clean (this IS the gate) ---------------------------------


def test_corpus_lock_clean_on_real_tree() -> None:
    assert _mod().check() == []


def test_every_corpus_manifest_matches_its_derived_census() -> None:
    # each registered spec's pinned (count, sha256) equals what its keys() derive right now
    cc = _mod()
    for spec in cc.CORPORA:
        count, digest = cc._census(spec.keys())
        assert (count, digest) == (spec.count, spec.sha256), spec.name


# --- negative controls: one planted drift each ---------------------------------------------------


def _spec(cc, keys, *, count, sha256, unique=True):
    return cc.CorpusSpec("planted", lambda: keys, count=count, sha256=sha256, unique=unique)


def _clean(cc, keys, *, unique=True):
    count, sha = cc._census(keys)
    return _spec(cc, keys, count=count, sha256=sha, unique=unique)


def test_catches_a_shrunk_census() -> None:
    cc = _mod()
    spec = cc.CorpusSpec("planted", lambda: ["a", "b"], count=3, sha256="whatever")
    fails = cc._spec_failures(spec)
    assert any("shrank to 2" in f and "pins 3" in f for f in fails)


def test_catches_a_grown_census() -> None:
    cc = _mod()
    spec = cc.CorpusSpec("planted", lambda: ["a", "b", "c"], count=2, sha256="whatever")
    fails = cc._spec_failures(spec)
    assert any("grew to 3" in f for f in fails)


def test_catches_a_mutated_case_same_count() -> None:
    # a case swapped for another keeps the count but changes the key-set hash
    cc = _mod()
    original = _clean(cc, ["a", "b", "c"])
    mutated = cc.CorpusSpec(
        "planted", lambda: ["a", "b", "X"], count=original.count, sha256=original.sha256
    )
    fails = cc._spec_failures(mutated)
    assert any("key-set hash changed" in f for f in fails)


def test_catches_a_duplicate_when_uniqueness_required() -> None:
    cc = _mod()
    spec = cc.CorpusSpec("planted", lambda: ["a", "a", "b"], count=3, sha256="x", unique=True)
    fails = cc._spec_failures(spec)
    assert any("duplicate case key" in f for f in fails)


def test_allows_a_duplicate_when_uniqueness_not_required() -> None:
    # the deinflect corpus legitimately carries duplicate upstream vectors
    cc = _mod()
    spec = _clean(cc, ["a", "a", "b"], unique=False)
    assert cc._spec_failures(spec) == []


def test_subtitle_keys_encode_full_case_not_just_the_name() -> None:
    # regression guard: a name-only key would silently miss a mutated content/expect (same name) — the
    # census hash wouldn't move. Assert the derived keys carry more than the bare names, yet still the name.
    cc = _mod()
    rel = "tests/fixtures/subtitle/subminer_parser_cases.json"
    names = [c["name"] for c in cc._json_cases(rel)]
    keys = cc._subtitle_keys()
    assert keys != names
    assert all(n in k for n, k in zip(names, keys, strict=True))


def test_flags_an_unreadable_source() -> None:
    cc = _mod()

    def boom() -> list[str]:
        raise FileNotFoundError("gone")

    spec = cc.CorpusSpec("planted", boom, count=1, sha256="x")
    fails = cc._spec_failures(spec)
    assert any("cannot derive census" in f for f in fails)

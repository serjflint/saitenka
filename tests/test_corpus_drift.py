"""Planted +/- controls for the corpus drift guard (tools/corpus_drift.py, `poe corpus-drift`).

The guard regenerates each committed corpus and byte-diffs it; a regenerate-and-diff check is only worth
its green if it FAILS on real drift and SKIPS honestly when its upstream is absent. These exercise the pure
`_check` seam with a synthetic corpus whose "generator" is a trivial writer subprocess — no Node / uv /
Yomitan — so the classification logic is proven hermetically. Same two-sided shape as test_corpus_check.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CD = Path(__file__).resolve().parent.parent / "tools" / "corpus_drift.py"


def _mod():
    spec = importlib.util.spec_from_file_location("_corpus_drift", _CD)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m  # @dataclass resolves types via sys.modules[__module__] (3.14)
    spec.loader.exec_module(m)
    return m


def _writer_corpus(mod, committed: Path, payload: bytes):
    """A Corpus whose 'generator' just writes `payload` to $CORPUS_OUT — no external toolchain."""
    src = f"import os,pathlib;pathlib.Path(os.environ['CORPUS_OUT']).write_bytes({payload!r})"
    return mod.Corpus(
        name="synthetic", committed=committed, argv=[sys.executable, "-c", src], pin_source=None
    )


def test_check_passes_when_regen_is_byte_identical(tmp_path):
    mod = _mod()
    committed = tmp_path / "corpus.json"
    committed.write_bytes(b"IDENTICAL\n")
    status, _ = mod._check(_writer_corpus(mod, committed, b"IDENTICAL\n"), None)
    assert status == "ok"


def test_check_catches_drift_when_regen_differs(tmp_path):
    mod = _mod()
    committed = tmp_path / "corpus.json"
    committed.write_bytes(b"STALE COMMITTED\n")  # generator now emits something else
    status, _ = mod._check(_writer_corpus(mod, committed, b"FRESH REGENERATED\n"), None)
    assert status == "drift"


def test_check_gives_generator_an_unoccupied_output_path(tmp_path, monkeypatch):
    mod = _mod()
    committed = tmp_path / "corpus.json"
    committed.write_bytes(b"IDENTICAL\n")
    corpus = _writer_corpus(mod, committed, b"unused")

    def write_output(_corpus, _checkout, out):
        assert not out.exists()
        out.write_bytes(b"IDENTICAL\n")

    monkeypatch.setattr(mod, "_regen", write_output)
    status, _ = mod._check(corpus, None)
    assert status == "ok"


def test_check_skips_when_no_yomitan_checkout(tmp_path):
    """A pin_source corpus with no checkout skips (not fails) — the opt-in posture, logged not silent."""
    mod = _mod()
    committed = tmp_path / "corpus.json"
    committed.write_bytes(b"anything\n")
    corpus = mod.Corpus(
        name="needs-checkout",
        committed=committed,
        argv=[sys.executable, "-c", "pass", mod._SLOT],
        pin_source=_CD.parent.parent / "deinflect/tools/gen_transform_differential.mjs",
    )
    status, detail = mod._check(corpus, None)
    assert status == "skip"
    assert "checkout" in detail


def test_check_reports_error_when_generator_fails(tmp_path):
    mod = _mod()
    committed = tmp_path / "corpus.json"
    committed.write_bytes(b"x\n")
    corpus = mod.Corpus(
        name="boom",
        committed=committed,
        argv=[sys.executable, "-c", "import sys;sys.exit(3)"],
        pin_source=None,
    )
    status, _ = mod._check(corpus, None)
    assert status == "error"


def test_check_reports_error_when_generator_writes_no_output(tmp_path):
    mod = _mod()
    committed = tmp_path / "corpus.json"
    committed.write_bytes(b"x\n")
    corpus = mod.Corpus(
        name="no-output",
        committed=committed,
        argv=[sys.executable, "-c", "pass"],
        pin_source=None,
    )
    status, _ = mod._check(corpus, None)
    assert status == "error"

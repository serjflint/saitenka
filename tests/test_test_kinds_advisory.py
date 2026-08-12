"""Teeth for the test-kinds advisory (.agents/hooks/{test_kinds,_hooklib}.py, `test-kinds-advisory.py`).

The advisory maps a commit's staged source files to the non-unit oracle kinds they warrant. It's only
useful if the map DISCRIMINATES — a kind-bearing source path surfaces kinds, and a docs/test-only commit
stays quiet — and if commit detection doesn't fire on `git log`. Loads the agent-agnostic modules by path
(they're outside the Saitenka package, like test_corpus_check.py loads tools/corpus_check.py).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / ".agents" / "hooks"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _HOOKS / filename)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m  # @dataclass / annotations resolve via sys.modules[__module__]
    spec.loader.exec_module(m)
    return m


def test_map_surfaces_kinds_for_a_touched_subsystem():
    tk = _load("test_kinds", "test_kinds.py")
    hits = tk.applicable(["src/saitenka/app/render_cache.py"])
    names = {n for n, _ in hits}
    assert "cache layers" in names  # a cache source surfaces the cache kinds
    kinds = dict(hits)["cache layers"]
    assert any("warm==cold" in k for k in kinds)


def test_map_unions_kinds_when_a_path_matches_several_subsystems():
    tk = _load("test_kinds", "test_kinds.py")
    names = {n for n, _ in tk.applicable(["src/saitenka/app/controller.py"])}
    assert {"cli assembly", "panel / windowed / interaction"} <= names


def test_map_tracks_extracted_cli_and_structured_content_paths():
    tk = _load("test_kinds", "test_kinds.py")

    assert "cli assembly" in dict(tk.applicable(["src/saitenka/app/cli.py"]))
    assert "cli assembly" in dict(tk.applicable(["src/saitenka/app/commands/run.py"]))
    assert "structured-content adapter" in dict(
        tk.applicable(["src/saitenka/render/sc_adapter.py"])
    )


def test_map_stays_quiet_for_docs_and_test_only_changes():
    """Negative control: a commit that touches no kind-bearing SOURCE must surface nothing (else the
    advisory would nag on every docs/test commit and get muted)."""
    tk = _load("test_kinds", "test_kinds.py")
    assert tk.applicable(["README.md", "tests/test_foo.py", "docs/index.md"]) == []


def test_signature_is_stable_and_set_based():
    tk = _load("test_kinds", "test_kinds.py")
    a = tk.applicable(["src/saitenka/app/dictionary.py"])
    assert tk.signature(a) == tk.signature(list(reversed(a)))  # order-independent


def test_is_git_commit_detects_commit_but_not_log():
    hl = _load("_hooklib", "_hooklib.py")
    assert hl.is_git_commit("git commit -m 'x'")
    assert hl.is_git_commit("cd /tmp && git -C repo commit --amend")
    assert not hl.is_git_commit("git log --oneline | head")
    assert not hl.is_git_commit("echo 'git commit' >> notes.txt")  # inside a string, not a command

"""Planted +/- controls for the comment-review hook (.agents/hooks/comment-review.py).

The hook forces a review of long NEW comments at commit time. Pin the two pure seams: which commands
count as `git commit` (not `log --grep commit`, not a quoted mention), and which added comment blocks
are "long" enough to surface (only .py `#` blocks past the thresholds, only additions).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parents[2] / ".agents" / "hooks"
_HOOK = _HOOKS / "comment-review.py"


def _mod():
    if str(_HOOKS) not in sys.path:
        sys.path.insert(
            0, str(_HOOKS)
        )  # so the hook's `_hooklib` sibling import resolves at runtime
    spec = importlib.util.spec_from_file_location("_comment_review", _HOOK)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


# --- is_git_commit -------------------------------------------------------------------------------


def test_detects_git_commit() -> None:
    m = _mod()
    assert m.is_git_commit("git commit -m 'x'")
    assert m.is_git_commit("git commit")
    assert m.is_git_commit("git -C /repo commit -m msg")
    assert m.is_git_commit("git add . && git commit -m x")


def test_ignores_non_commit_git() -> None:
    m = _mod()
    assert not m.is_git_commit("git add .")
    assert not m.is_git_commit("git log --grep commit")
    assert not m.is_git_commit("git status")


def test_ignores_commit_inside_a_string() -> None:
    m = _mod()
    assert not m.is_git_commit("echo 'git commit -m x'")
    assert not m.is_git_commit('printf "run git commit"')


# --- flagged_comments ----------------------------------------------------------------------------


def _diff(path: str, added: list[str]) -> str:
    body = "\n".join(f"+{line}" for line in added)
    return f"--- a/{path}\n+++ b/{path}\n@@ -1,0 +1,{len(added)} @@\n{body}\n"


def test_flags_a_long_comment_block() -> None:
    diff = _diff("overlay/src/overlay/app/x.py", [f"# reason line {i}" for i in range(6)])
    flagged = _mod().flagged_comments(diff)
    assert len(flagged) == 1
    assert flagged[0][0].endswith("app/x.py")


def test_flags_a_single_dense_oneliner() -> None:
    long = "# " + "very ".join("dense clause" for _ in range(30))
    assert len(long) > 200
    flagged = _mod().flagged_comments(_diff("a/b/c.py", [long]))
    assert len(flagged) == 1


def test_short_comment_is_not_flagged() -> None:
    flagged = _mod().flagged_comments(_diff("m.py", ["# skip the exporter header", "x = 1"]))
    assert flagged == []


def test_non_python_file_is_ignored() -> None:
    diff = _diff("pyproject.toml", [f"# long toml comment {i}" for i in range(8)])
    assert _mod().flagged_comments(diff) == []


def test_removed_comments_are_ignored() -> None:
    # only ADDED (+) comment lines count; a deletion diff has '-' bodies
    diff = "--- a/m.py\n+++ b/m.py\n@@ -1,6 +1,0 @@\n" + "\n".join(
        f"-# old comment {i}" for i in range(6)
    )
    assert _mod().flagged_comments(diff) == []


def test_signature_is_stable_and_discriminating() -> None:
    m = _mod()
    a = [("m.py", "# block one\n# two")]
    b = [("m.py", "# block DIFFERENT\n# two")]
    assert m.signature(a) == m.signature(a)
    assert m.signature(a) != m.signature(b)

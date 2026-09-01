"""Planted controls for the docstring-refs gate."""

from __future__ import annotations

import ast
import subprocess

import pytest
from docstring_refs import REFERENCE, _docstrings, violations


def test_the_tree_is_clean_today():
    assert violations() == []


def test_a_file_written_but_not_yet_added_is_still_checked(tmp_path, monkeypatch):
    """The gate's own first run reported clean because `git ls-files` does not list an untracked
    file — so it skipped itself, and only failed once `git add` had made it visible. A gate that
    goes quiet exactly when new code arrives is worse than no gate.

    Driven against a throwaway repository rather than this one: planting a file in the real tree is
    global state, and under `-n auto` it raced the clean-tree assertion above into a false failure.
    """
    import docstring_refs

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.py"
    tracked.write_text('"""Fine."""\n', encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    (tmp_path / "brand_new.py").write_text(
        '"""Delegates to ``a_file_that_does_not_exist.py``."""\n', encoding="utf-8"
    )
    monkeypatch.setattr(docstring_refs, "ROOT", tmp_path)

    assert [(name, ref) for name, _line, ref in docstring_refs.violations()] == [
        ("brand_new.py", "a_file_that_does_not_exist.py")
    ]


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("module docstring", '"""Delegates to ``tokenize.py``."""'),
        ("double backticks", '"""See ``app/analysis_overlay.py``."""'),
        ("single backticks", '"""See `app/episode_analysis.py`."""'),
        ("nested function", 'def f():\n    """Wraps ``gone/missing.py``."""'),
        ("class", 'class C:\n    """Was ``mpvio/gateway.py``."""'),
    ],
)
def test_a_file_reference_is_found_wherever_the_docstring_lives(label, source):
    refs = [ref for _line, doc in _docstrings(ast.parse(source)) for ref in REFERENCE.findall(doc)]
    assert refs, f"{label} reference was not extracted"


def test_a_comment_is_not_a_docstring():
    """The gate reads docstrings only; a `#` comment naming a dead file is out of scope by design,
    so this pins the boundary rather than letting it drift into a surprise."""
    source = "# see ``tokenize.py``\nx = 1\n"
    assert list(_docstrings(ast.parse(source))) == []


@pytest.mark.parametrize(
    "text",
    [
        "no reference at all",
        "``poe all`` is the gate",  # a command, not a path
        "see ``vibe/plan.md``",  # not a .py
    ],
)
def test_prose_without_a_python_file_reference_is_not_flagged(text):
    assert REFERENCE.findall(text) == []

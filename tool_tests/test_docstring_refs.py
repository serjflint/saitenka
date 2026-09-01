"""Planted controls for the docstring-refs gate."""

from __future__ import annotations

import ast

import pytest
from docstring_refs import REFERENCE, _docstrings, violations


def test_the_tree_is_clean_today():
    assert violations() == []


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

"""Rot guard for the codemod harness — it is opt-in, so nothing else would notice it breaking.

Skipped unless the `codemod` dependency group is installed (`uv run --group codemod`); LibCST has
no free-threaded wheel, so it is deliberately outside the default env.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("libcst")

sys.path.insert(0, str(Path(__file__).resolve().parent / "codemods"))

import harness
import move_member


def test_a_rewrite_preserves_the_formatting_around_it(tmp_path):
    """The reason this is LibCST and not `ast.unparse`: a diff of the whole file is not reviewable."""
    path = tmp_path / "a.py"
    path.write_text(
        "def go(r):\n    # keep me\n    return r._tip_state  # and me\n", encoding="utf-8"
    )

    harness.apply("t", [path], move_member.transformer({"_tip_state": "tip.view.state"}))

    assert path.read_text(encoding="utf-8") == (
        "def go(r):\n    # keep me\n    return r.tip.view.state  # and me\n"
    )


def test_check_writes_nothing_and_a_finished_transform_reports_zero(tmp_path):
    """`--check` is what lets a transform prove it finished, so it must not be the run itself."""
    path = tmp_path / "a.py"
    path.write_text("def go(r):\n    return r._tip_state\n", encoding="utf-8")
    make = move_member.transformer({"_tip_state": "tip.view.state"})

    assert harness.apply("t", [path], make, check=True) == 1
    assert "_tip_state" in path.read_text(encoding="utf-8")

    harness.apply("t", [path], make)

    assert harness.apply("t", [path], make, check=True) == 0


def test_an_unrelated_attribute_of_the_same_name_tail_is_left_alone(tmp_path):
    """Only the named attribute moves. A prefix match here would rewrite half the tree."""
    path = tmp_path / "a.py"
    path.write_text("def go(r):\n    return r._tip_state_extra\n", encoding="utf-8")

    assert (
        harness.apply(
            "t", [path], move_member.transformer({"_tip_state": "tip.view.state"}), check=True
        )
        == 0
    )

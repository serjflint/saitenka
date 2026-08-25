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

import complete_help_controller
import complete_tooltip_controller
import harness
import install_interaction_stateful_bindings
import install_interaction_surface_owners
import install_surface_router
import move_member
import rename_session_controller


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


def test_the_session_controller_rename_is_idempotent():
    assert rename_session_controller.main(["--check"]) == 0


def test_tooltip_rewrite_is_receiver_exact_and_preserves_assignment_intent():
    source = (
        "r.hover = index\n"
        "r.scan_delay = delay\n"
        "result._pause_store.dispatch(event)\n"
        "inputs.hover\n"
        "ports.word_store\n"
    )

    rewritten, count = complete_tooltip_controller.transformed(
        source, "tests/test_native_subtitles.py"
    )

    assert rewritten == (
        "r.tooltip_controller.select(index)\n"
        "r.tooltip_controller.configure_delays(scan=delay)\n"
        "result.tooltip_controller.pause_store.dispatch(event)\n"
        "inputs.hover\n"
        "ports.word_store\n"
    )
    assert count == 5


def test_help_rewrite_is_receiver_exact_and_leaves_string_lookup_alone():
    source = (
        "reader._help_store.dispatch(command)\n"
        "reader._help_document()\n"
        "other._help_document()\n"
        'setattr(reader, "_run_help_command", callback)\n'
    )

    rewritten, count = complete_help_controller.transformed(source, "tests/test_surfaces.py")

    assert rewritten == (
        "reader.help_controller.store.dispatch(command)\n"
        "reader.help_controller.document()\n"
        "other._help_document()\n"
        'setattr(reader, "_run_help_command", callback)\n'
    )
    assert count == 2


def test_surface_router_rewrite_refuses_a_different_context_receiver():
    source = (
        "surfaces.wants_mouse_capture(self.interaction)\n"
        "surfaces.wants_mouse_capture(other.interaction)\n"
    )

    rewritten, count = install_surface_router.transformed(
        source,
        "src/saitenka/app/session_controller.py",
    )

    assert rewritten == (
        "self.surface_router.wants_mouse_capture()\n"
        "surfaces.wants_mouse_capture(other.interaction)\n"
    )
    assert count == 1


def test_stateful_binding_rewrite_matches_only_direct_store_constructors():
    source = "HoverStore(ipc)\nmodule.HoverStore(ipc)\nHoverStoreFake(ipc)\n"

    rewritten, count = install_interaction_stateful_bindings.transformed(source)

    assert rewritten == (
        "HOVER_STATEFUL_BINDING.store(ipc)\nmodule.HoverStore(ipc)\nHoverStoreFake(ipc)\n"
    )
    assert count == 1


def test_surface_owner_rewrite_refuses_unknown_receivers():
    source = "reader._sidebar_store\nother._sidebar_store\n"

    rewritten, count = install_interaction_surface_owners.transformed(source)

    assert rewritten == "reader.sidebar_controller.store\nother._sidebar_store\n"
    assert count == 1

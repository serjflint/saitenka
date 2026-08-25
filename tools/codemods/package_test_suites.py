"""Move bounded application tests and repository-tool tests to their owned suites.

    uv run --group codemod python tools/codemods/package_test_suites.py [--check]

The move table is the migration worklist. The transform also rewrites exact repository-relative
path references across tracked text files; semantic test ownership was decided before this script.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _test_moves(directory: str, names: tuple[str, ...]) -> dict[str, str]:
    return {f"tests/{name}": f"tests/{directory}/{name}" for name in names}


TEST_MOVES = {
    **_test_moves(
        "features/help",
        (
            "test_help_machine.py",
            "test_help_overlay.py",
        ),
    ),
    **_test_moves(
        "features/mining",
        (
            "test_mine_addable.py",
            "test_mine_intents.py",
            "test_mined_set.py",
            "test_mined_store.py",
            "test_mining.py",
            "test_mining_controller.py",
            "test_mining_french.py",
            "test_word_audio.py",
        ),
    ),
    **_test_moves("features/picker", ("test_sub_picker.py",)),
    **_test_moves(
        "features/preview",
        (
            "test_card_preview.py",
            "test_card_preview_machine.py",
            "test_preview_audio.py",
        ),
    ),
    **_test_moves(
        "features/profiles",
        (
            "test_profile_cli.py",
            "test_profile_deinflect_routing.py",
            "test_profile_identity_e2e.py",
            "test_profile_intents.py",
            "test_profile_switcher.py",
        ),
    ),
    **_test_moves("features/sidebar", ("test_sidebar.py",)),
    **_test_moves(
        "features/tooltip",
        (
            "test_hover_intents.py",
            "test_hover_metadata.py",
            "test_hover_metadata_value.py",
            "test_nested_longest_match.py",
            "test_prefetch_lookahead.py",
            "test_prefetch_runtime.py",
            "test_prewarm.py",
            "test_render_ahead_wiring.py",
            "test_same_token_oracle.py",
            "test_scale_boundary.py",
            "test_tip_scale.py",
            "test_tooltip_engaged.py",
            "test_tooltip_show_parts.py",
            "test_tooltip_statemachine.py",
            "test_windowed_prefetch.py",
        ),
    ),
    **_test_moves(
        "interaction",
        (
            "test_interaction.py",
            "test_interaction_intents.py",
            "test_interaction_slice.py",
            "test_interaction_surfaces.py",
        ),
    ),
    **_test_moves(
        "session",
        (
            "test_bindings_registry.py",
            "test_capabilities.py",
            "test_close_ledger.py",
            "test_episode_retirement.py",
            "test_lifecycle.py",
            "test_lifecycle_start.py",
            "test_lifecycle_surfaces.py",
            "test_lifecycle_timers.py",
            "test_reader_context.py",
            "test_reader_deps.py",
            "test_runtime_behavior_oracle.py",
            "test_session.py",
            "test_session_assembly.py",
            "test_session_commands.py",
            "test_session_connection.py",
            "test_session_controller.py",
            "test_session_controller_host_contract.py",
            "test_session_controller_runtime.py",
            "test_session_intents.py",
            "test_session_mode.py",
            "test_session_runner.py",
            "test_session_runtime.py",
            "test_stateless_registration.py",
            "test_surfaces.py",
        ),
    ),
}


TOOL_TESTS = (
    "test_app_package_layout.py",
    "test_arch_map.py",
    "test_bundle_release.py",
    "test_cluster_map.py",
    "test_codemods.py",
    "test_dictionary_structure_oracle.py",
    "test_grow_contexts.py",
    "test_grow_gate.py",
    "test_grow_ledger.py",
    "test_grow_reflect.py",
    "test_grow_triage.py",
    "test_host_arity.py",
    "test_host_mass.py",
    "test_libass_prototype_benchmark.py",
    "test_libass_token_matrix.py",
    "test_lint.py",
    "test_mining_ownership_check.py",
    "test_mpv_shadow_geometry.py",
    "test_mpv_source_transition.py",
    "test_native_subtitle_integration_benchmark.py",
    "test_perf_gate.py",
    "test_port_probe_census.py",
    "test_prepare_libass_bundle.py",
    "test_reducer_purity.py",
    "test_runtime_migration_check.py",
    "test_session_assembly_census.py",
    "test_sharpen_gate.py",
    "test_sharpen_ledger.py",
    "test_sharpen_triage.py",
    "test_skill_check.py",
    "test_subtitle_report.py",
    "test_test_lint.py",
    "test_tool_json.py",
    "test_tooltip_ownership_check.py",
    "test_trace_report.py",
)


def _tool_test_moves() -> dict[str, str]:
    return {f"tools/{name}": f"tool_tests/{name}" for name in TOOL_TESTS}


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / path.decode() for path in result.stdout.split(b"\0") if path]


def _rewritten(source: str, moves: dict[str, str]) -> tuple[str, int]:
    count = 0
    for old, new in moves.items():
        occurrences = source.count(old)
        if occurrences:
            source = source.replace(old, new)
            count += occurrences
    return source, count


def main(argv: list[str]) -> int:
    check = "--check" in argv
    moves = {**TEST_MOVES, **_tool_test_moves()}
    moved = 0
    for old, new in moves.items():
        source = ROOT / old
        target = ROOT / new
        if source.exists() and not target.exists():
            moved += 1
            if not check:
                target.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["git", "mv", old, new], cwd=ROOT, check=True)
        elif source.exists() == target.exists():
            raise RuntimeError(f"expected exactly one migration endpoint: {old} -> {new}")

    rewritten = 0
    touched = 0
    for path in _tracked_files():
        if path == Path(__file__).resolve() or path.parent == ROOT / "tools" / "codemods":
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        replacement, count = _rewritten(source, moves)
        if not count:
            continue
        rewritten += count
        touched += 1
        if not check:
            path.write_text(replacement, encoding="utf-8")

    verb = "would move" if check else "moved"
    rewrite_verb = "would rewrite" if check else "rewrote"
    print(
        f"package-test-suites: {verb} {moved} file(s); "
        f"{rewrite_verb} {rewritten} reference(s) in {touched} file(s)"
    )
    return int(check and (moved > 0 or rewritten > 0))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

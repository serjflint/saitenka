from __future__ import annotations

import analysis_ownership_check


def _inspect(source: str, site: str):
    return analysis_ownership_check.inspect_source(source, analysis_ownership_check.APP / site)


def test_controller_cannot_restore_analysis_state_or_facades():
    findings = _inspect(
        """
class SessionController:
    def __init__(self):
        self.analysis = object()

    def invalidate_analysis(self):
        pass
""",
        analysis_ownership_check.COMPOSITION,
    )

    assert {(finding.rule, finding.detail) for finding in findings} == {
        ("retired-session-field", "analysis"),
        ("retired-facade", "invalidate_analysis"),
    }


def test_only_session_assembly_constructs_the_owner():
    findings = _inspect(
        "owner = AnalysisController(ipc)",
        "session/controller.py",
    )

    assert [(finding.rule, finding.detail) for finding in findings] == [
        ("owned-constructor", "AnalysisController")
    ]


def test_an_import_alias_cannot_hide_owner_construction():
    findings = _inspect(
        """
from saitenka.app.features.analysis.analysis_controller import AnalysisController as AC
owner = AC(ipc)
""",
        "features/sidebar/sidebar.py",
    )

    assert [(finding.rule, finding.detail) for finding in findings] == [("owned-constructor", "AC")]


def test_controller_cannot_reach_into_owner_private_state_or_methods():
    findings = _inspect(
        """
class SessionController:
    def escape(self, completion):
        self.analysis_controller._state.current = None
        self.analysis_controller._finish(completion)
""",
        analysis_ownership_check.COMPOSITION,
    )

    assert {(finding.rule, finding.detail) for finding in findings} == {
        ("private-owner-access", "_state"),
        ("private-owner-access", "_finish"),
    }


def test_private_state_cannot_be_imported_or_constructed_elsewhere():
    findings = _inspect(
        """
from saitenka.app.features.analysis.analysis_controller import _AnalysisState
state = _AnalysisState()
""",
        "features/sidebar/sidebar.py",
    )

    assert {(finding.rule, finding.detail) for finding in findings} == {
        ("private-state-import", "_AnalysisState"),
        ("private-state-constructor", "_AnalysisState"),
    }


def test_live_analysis_ownership_census_is_clean():
    assert analysis_ownership_check.inspect_tree() == []

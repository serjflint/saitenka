from __future__ import annotations

import annotation_ownership_check


def _inspect(source: str, site: str):
    return annotation_ownership_check.inspect_source(source, annotation_ownership_check.APP / site)


def test_controller_cannot_restore_annotation_state_or_facades():
    findings = _inspect(
        """
class SessionController:
    def __init__(self):
        self._sub_pending = None
        self.token_cache = object()

    def _finish_annotation(self):
        pass
""",
        annotation_ownership_check.COMPOSITION,
    )

    assert {(finding.rule, finding.detail) for finding in findings} == {
        ("retired-session-field", "_sub_pending"),
        ("retired-session-field", "token_cache"),
        ("retired-facade", "_finish_annotation"),
    }


def test_only_session_assembly_constructs_the_owner():
    findings = _inspect(
        "owner = CueAnnotationController(ipc, mode='full', cache_max=4)",
        annotation_ownership_check.COMPOSITION,
    )

    assert [(finding.rule, finding.detail) for finding in findings] == [
        ("owned-constructor", "CueAnnotationController")
    ]


def test_an_import_alias_cannot_hide_owner_construction():
    findings = _inspect(
        """
from saitenka.app.features.annotation.annotation_controller import CueAnnotationController as Owner
owner = Owner(ipc, mode='full', cache_max=4)
""",
        "features/sidebar/sidebar.py",
    )

    assert [(finding.rule, finding.detail) for finding in findings] == [
        ("owned-constructor", "Owner")
    ]


def test_controller_cannot_reach_into_owner_private_state():
    findings = _inspect(
        "self.annotation_controller._token_cache.clear()",
        annotation_ownership_check.COMPOSITION,
    )

    assert [(finding.rule, finding.detail) for finding in findings] == [
        ("private-owner-access", "_token_cache")
    ]


def test_an_alias_cannot_hide_owner_private_access():
    findings = _inspect(
        """
owner = self.annotation_controller
owner._finish(result)
""",
        annotation_ownership_check.COMPOSITION,
    )

    assert [(finding.rule, finding.detail) for finding in findings] == [
        ("private-owner-access", "_finish")
    ]


def test_a_new_private_policy_method_is_protected_without_a_gate_allowlist():
    findings = _inspect(
        "self.annotation_controller._submit_current(text, inputs)",
        annotation_ownership_check.COMPOSITION,
    )

    assert [(finding.rule, finding.detail) for finding in findings] == [
        ("private-owner-access", "_submit_current")
    ]


def test_another_app_module_cannot_reach_through_a_typed_owner_parameter():
    findings = _inspect(
        """
from saitenka.app.features.annotation.annotation_controller import CueAnnotationController

def escape(owner: CueAnnotationController):
    owner._token_cache.clear()
""",
        "features/sidebar/sidebar.py",
    )

    assert [(finding.rule, finding.detail) for finding in findings] == [
        ("private-owner-access", "_token_cache")
    ]


def test_only_the_owner_constructs_its_mutable_cache():
    findings = _inspect("cache = TokenCache(4)", "features/tooltip/prefetch.py")

    assert [(finding.rule, finding.detail) for finding in findings] == [
        ("private-cache-constructor", "TokenCache")
    ]


def test_live_annotation_ownership_census_is_clean():
    assert annotation_ownership_check.inspect_tree() == []

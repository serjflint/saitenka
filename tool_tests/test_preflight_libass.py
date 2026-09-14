"""Capability checks must not mistake imports or empty output for rendering."""

import pytest
from preflight_libass import preflight


def test_preflight_rejects_an_explicit_library_override(monkeypatch):
    monkeypatch.setenv("LIBASSLITE_LIBRARY", "/not/the/selected/provider")

    with pytest.raises(RuntimeError, match="unset LIBASSLITE_LIBRARY"):
        preflight("system")


def test_system_preflight_rejects_implicit_bundle_selection(monkeypatch):
    monkeypatch.delenv("LIBASSLITE_LIBRARY", raising=False)
    monkeypatch.delenv("LIBASSLITE_BUNDLE", raising=False)

    with pytest.raises(RuntimeError, match="requires LIBASSLITE_BUNDLE=0"):
        preflight("system")


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_native_preflight_requires_nonempty_rendered_coverage(monkeypatch):
    monkeypatch.delenv("LIBASSLITE_LIBRARY", raising=False)
    monkeypatch.setenv("LIBASSLITE_BUNDLE", "0")
    monkeypatch.setattr("preflight_libass.event_lines", lambda _: ())

    with pytest.raises(RuntimeError, match="rendered no text coverage"):
        preflight("system")


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_native_preflight_reports_loaded_runtime_and_ink(monkeypatch):
    monkeypatch.delenv("LIBASSLITE_LIBRARY", raising=False)
    monkeypatch.setenv("LIBASSLITE_BUNDLE", "0")

    result = preflight("system")

    assert result["provider"] == "system"
    assert result["library"]
    assert result["version"] > 0
    assert result["ink_bytes"] > 0

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("workflow_name", "project", "tag_prefix", "artifact"),
    [
        (
            "saitenka-dict-release.yml",
            "saitenka-dict",
            "saitenka-dict-v",
            "saitenka-dict-dist",
        ),
        (
            "ankiconnect-client-release.yml",
            "ankiconnect-client",
            "ankiconnect-client-v",
            "ankiconnect-client-dist",
        ),
    ],
)
def test_addon_manual_release_is_testpypi_only(
    workflow_name: str,
    project: str,
    tag_prefix: str,
    artifact: str,
) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")
    )
    triggers = workflow[True]  # PyYAML 1.1 parses the unquoted Actions key ``on`` as true.
    jobs = workflow["jobs"]
    testpypi = jobs["testpypi"]
    production = jobs["pypi"]
    publish = testpypi["steps"][-1]

    assert triggers["workflow_dispatch"] is None
    assert testpypi["if"] == "github.event_name == 'workflow_dispatch'"
    assert testpypi["environment"]["name"] == "testpypi"
    assert testpypi["environment"]["url"] == f"https://test.pypi.org/p/{project}"
    assert testpypi["steps"][0]["with"]["name"] == artifact
    assert publish["with"]["repository-url"] == "https://test.pypi.org/legacy/"
    assert publish["with"]["skip-existing"] is True
    assert production["if"] == (
        f"github.event_name == 'push' && startsWith(github.ref, 'refs/tags/{tag_prefix}')"
    )
    assert production["environment"]["name"] == "pypi"
    assert "repository-url" not in production["steps"][-1].get("with", {})


def test_libasslite_bundle_manual_release_is_testpypi_only() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "libasslite-bundle-release.yml").read_text(
            encoding="utf-8"
        )
    )
    triggers = workflow[True]
    jobs = workflow["jobs"]
    testpypi = jobs["testpypi"]
    production = jobs["pypi"]
    upload = testpypi["steps"][-1]["with"]

    assert triggers["workflow_dispatch"] is None
    assert testpypi["if"] == "github.event_name == 'workflow_dispatch'"
    assert testpypi["environment"] == {
        "name": "testpypi",
        "url": "https://test.pypi.org/p/libasslite-bundle",
    }
    assert upload["repository-url"] == "https://test.pypi.org/legacy/"
    assert upload["skip-existing"] is True
    assert production["if"] == (
        "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/libasslite-bundle-v')"
    )
    assert production["environment"] == {
        "name": "pypi",
        "url": "https://pypi.org/p/libasslite-bundle",
    }
    assert "repository-url" not in production["steps"][-1]["with"]


def test_libasslite_macos_smoke_uses_runner_homebrew_prefix() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "libasslite-release.yml").read_text(encoding="utf-8")
    )
    install = next(
        step
        for step in workflow["jobs"]["smoke"]["steps"]
        if step.get("name") == "Install libass (macOS)"
    )

    assert install["run"].splitlines() == [
        "brew install libass",
        'echo "LIBASSLITE_LIBRARY=$(brew --prefix libass)/lib/libass.dylib" >> "$GITHUB_ENV"',
    ]

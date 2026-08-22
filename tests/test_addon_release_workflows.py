import json
import re
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CACHE_ACTION = "actions/cache@27d5ce7f107fe9357f9df03efb73ab90386fccae"  # v5.0.5


def _named_step(workflow: dict, job: str, name: str) -> dict:
    return next(step for step in workflow["jobs"][job]["steps"] if step.get("name") == name)


def test_libasslite_release_versions_stay_coherent() -> None:
    wrapper = tomllib.loads((ROOT / "libasslite" / "pyproject.toml").read_text())
    bundle = tomllib.loads((ROOT / "libasslite-bundle" / "pyproject.toml").read_text())
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())
    vcpkg = json.loads((ROOT / "libasslite-bundle" / "vcpkg.json").read_text())
    wrapper_version = wrapper["project"]["version"]
    bundle_version = bundle["project"]["version"]

    assert bundle["project"]["dependencies"] == [f"libasslite=={wrapper_version}"]
    assert vcpkg["version-string"] == bundle_version
    assert root["project"]["optional-dependencies"]["subtitle-geometry"] == [
        f"libasslite=={wrapper_version}"
    ]
    assert root["project"]["optional-dependencies"]["subtitle-geometry-bundle"] == [
        "saitenka[subtitle-geometry]",
        f"libasslite-bundle=={bundle_version}",
    ]


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


def test_libasslite_bundle_source_release_has_explicit_repository_context() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "libasslite-bundle-release.yml").read_text(
            encoding="utf-8"
        )
    )

    assert workflow["jobs"]["sources"]["env"]["GH_REPO"] == "${{ github.repository }}"


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


def test_libasslite_releases_allow_only_supported_macos_arm64() -> None:
    wrapper = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "libasslite-release.yml").read_text(encoding="utf-8")
    )
    bundle = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "libasslite-bundle-release.yml").read_text(
            encoding="utf-8"
        )
    )

    assert wrapper["jobs"]["native"]["strategy"]["matrix"]["os"] == [
        "macos-14",
        "windows-latest",
    ]
    assert [
        row
        for row in wrapper["jobs"]["smoke"]["strategy"]["matrix"]["include"]
        if row["os"].startswith("macos-")
    ] == [
        {"os": "macos-14", "py": "3.13", "artifact": "wheels-macos-14-3.13"},
        {"os": "macos-14", "py": "3.14t", "artifact": "wheels-macos-14-3.14t"},
        {"os": "macos-14", "py": "3.15t", "artifact": "wheels-macos-14-3.15t"},
    ]
    assert [
        row
        for row in bundle["jobs"]["build"]["strategy"]["matrix"]["include"]
        if row["name"].startswith("macos-")
    ] == [
        {
            "name": "macos-arm64",
            "runner": "macos-14",
            "triplet": "arm64-osx-saitenka",
            "tag": "macosx_11_0_arm64",
            "repair": "delocate",
            "target": "11.0",
        }
    ]


def test_bundle_release_caches_only_pinned_source_downloads() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "libasslite-bundle-release.yml").read_text(
            encoding="utf-8"
        )
    )

    cache = _named_step(workflow, "build", "Cache vcpkg source downloads")

    assert cache["uses"] == CACHE_ACTION
    assert cache["with"] == {
        "path": ".vcpkg/downloads",
        "key": (
            "bundle-vcpkg-downloads-${{ runner.os }}-${{ matrix.triplet }}-"
            "${{ env.VCPKG_REF }}-${{ hashFiles('libasslite-bundle/vcpkg.json', "
            "'libasslite-bundle/ports/**', 'libasslite-bundle/triplets/**') }}"
        ),
    }


#: Contexts GitHub allows in `jobs.<id>.env`. `runner` is NOT among them — it only exists once a
#: runner is assigned, i.e. from the steps down.
_JOB_ENV_CONTEXTS = ("github", "needs", "strategy", "matrix", "vars", "inputs")


def _job_env_context_uses() -> list[tuple[str, str, str, str]]:
    """Every `(workflow, job, var, expression)` whose job-level env reads an unavailable context."""
    return [
        (path.name, job_name, var, context)
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        for job_name, job in (
            yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs") or {}
        ).items()
        for var, value in ((job or {}).get("env") or {}).items()
        for context in re.findall(r"\$\{\{\s*([a-zA-Z_]+)\.", str(value))
        if context not in _JOB_ENV_CONTEXTS
    ]


def test_no_job_level_env_uses_the_runner_context() -> None:
    """A context GitHub does not offer in `jobs.<id>.env` is a workflow VALIDATION error: the run
    fails at startup with zero jobs, carries no error of its own, and the workflow is listed under
    its filename instead of its `name:`. `ci.yml` and `libasslite-release.yml` both carried
    `${{ runner.temp }}` there from 2026-08-16 until 2026-08-22 — six days in which the main gate
    silently did not run at all, and a release tag could not build.

    Checked across every workflow, not the two that broke: the failure is invisible in review and
    the cost of finding it again is a dead CI nobody notices.
    """
    assert _job_env_context_uses() == []


def test_the_job_env_context_check_can_fail(tmp_path: Path, monkeypatch) -> None:
    """Negative control: the scan above is worthless if it cannot see the exact defect it exists
    for, and a glob that silently matches nothing looks identical to a clean tree."""
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "broken.yml").write_text(
        "name: broken\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
        "    env:\n      TMP: ${{ runner.temp }}/x\n    steps:\n      - run: 'true'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)

    assert _job_env_context_uses() == [("broken.yml", "build", "TMP", "runner")]


def test_windows_libass_jobs_cache_downloads_by_allowlisted_vcpkg_revision() -> None:
    observed = []
    for workflow_name, job in [("ci.yml", "libasslite"), ("libasslite-release.yml", "smoke")]:
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")
        )
        revision = _named_step(workflow, job, "Resolve hosted vcpkg revision")
        cache = _named_step(workflow, job, "Cache vcpkg source downloads")
        observed.append(
            {
                "workflow": workflow_name,
                # Exported from the step, not declared in the job's `env` — see
                # `test_no_job_level_env_uses_the_runner_context` for why that is not optional.
                "downloads_exported": 'VCPKG_DOWNLOADS=$downloads" | Out-File $env:GITHUB_ENV'
                in revision["run"],
                "downloads_from_runner_temp": "$env:RUNNER_TEMP/vcpkg-downloads" in revision["run"],
                "downloads_initialized": "New-Item -ItemType Directory -Force" in revision["run"],
                "revision_if": revision["if"],
                "revision_allowlisted": "^[0-9a-f]{40}$" in revision["run"],
                "cache_if": cache["if"],
                "cache_action": cache["uses"],
                "cache_inputs": cache["with"],
            }
        )

    expected = {
        "downloads_exported": True,
        "downloads_from_runner_temp": True,
        "downloads_initialized": True,
        "revision_if": "runner.os == 'Windows'",
        "revision_allowlisted": True,
        "cache_if": "runner.os == 'Windows'",
        "cache_action": CACHE_ACTION,
        "cache_inputs": {
            "path": "${{ runner.temp }}/vcpkg-downloads",
            "key": (
                "vcpkg-downloads-${{ runner.os }}-${{ runner.arch }}-"
                "${{ steps.vcpkg-revision.outputs.revision }}"
            ),
        },
    }
    assert observed == [
        {"workflow": "ci.yml", **expected},
        {"workflow": "libasslite-release.yml", **expected},
    ]

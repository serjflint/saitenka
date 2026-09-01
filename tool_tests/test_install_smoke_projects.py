"""The install smoke must build a wheel for every in-repo package the install needs.

This is the one job that resolves saitenka's dependencies against the real registry, so an
unpublished first-party package fails there and nowhere else — as a resolver error naming a missing
distribution, not as a packaging assertion. Extracting `saitenka-tokenize`/`saitenka-wordstate` did
exactly that: they became core dependencies that exist only in this checkout while the build list
still named the previous three by hand.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from install_smoke import _local_projects, _required_names

ROOT = Path(__file__).resolve().parents[1]

#: In-repo projects deliberately consumed from the registry rather than built here — they are
#: released independently, so `[tool.uv.sources]` gives them no path entry and the pinned published
#: wheel is what an install is supposed to resolve. Living in this checkout does not make a package
#: first-party to the install.
_PUBLISHED_INDEPENDENTLY = {"taffylite"}


def _declared_name(pyproject: Path) -> str | None:
    name = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get("name")
    return name.lower().replace("_", "-") if isinstance(name, str) else None


def test_extras_expand_to_the_first_party_packages_the_install_resolves():
    """Pins the parser itself: every check below is vacuous if extra expansion returns nothing."""
    required = _required_names(subtitle_geometry=True)
    assert {
        "ankiconnect-client",
        "saitenka-dict",
        "saitenka-subtitles",
        "saitenka-tokenize",
        "saitenka-wordstate",
        "saitenka-deinflect",  # reached only through `full` -> `deinflect`
        "libasslite",  # reached only through `full` -> `subtitle-geometry`
    } <= required


def test_every_in_repo_package_the_install_needs_is_built_locally():
    """Cross-checks the `[tool.uv.sources]` derivation against the checkout itself.

    Walking the tree is a different question than reading the sources table, so a package that is
    added as a dependency and given a directory is caught even if the two disagree.
    """
    required = _required_names(subtitle_geometry=True)
    assert len(required) > 10, "extras failed to expand; the assertion below would be vacuous"
    built = {path.resolve() for path in _local_projects(subtitle_geometry=True)}
    on_disk = {
        name: pyproject.parent
        for pyproject in ROOT.glob("*/pyproject.toml")
        if (name := _declared_name(pyproject)) is not None
    }
    assert on_disk, "found no sibling projects to check"
    missing = sorted(
        name
        for name, directory in on_disk.items()
        if name in required
        and name not in _PUBLISHED_INDEPENDENTLY
        and directory.resolve() not in built
    )
    assert not missing, (
        f"in-repo packages the install needs but never builds a wheel for: {missing}"
    )


def test_the_registry_exceptions_are_still_dependencies_resolved_from_the_registry():
    """Keeps the allowlist honest from both sides: an entry that stops being a dependency, or that
    gains a path source, is a stale exception rather than a standing decision."""
    sources = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["uv"][
        "sources"
    ]
    required = _required_names(subtitle_geometry=True)
    for name in _PUBLISHED_INDEPENDENTLY:
        assert name in required, f"{name} is no longer an install dependency; drop the exception"
        assert "path" not in sources.get(name, {}), f"{name} is a path source; build it instead"


def test_the_root_wheel_is_built_last_and_is_the_one_selected():
    """`_build_wheel` returns `sorted(dist.glob("saitenka-*.whl"))[-1]`; a sibling whose *wheel*
    filename also started with `saitenka-` would be picked instead. Wheel names normalize `-` to
    `_`, so only the root qualifies — assert that rather than trusting it."""
    siblings = [path for path in _local_projects(subtitle_geometry=True) if path != ROOT]
    for path in siblings:
        name = _declared_name(path / "pyproject.toml")
        assert name is not None
        assert not f"{name.replace('-', '_')}-".startswith("saitenka-")

"""Where each in-repo package belongs: inside this wheel, or on the index as a dependency.

The install smoke is the one job that resolves against the real registry — but on a pull request it
builds every path-sourced sibling locally and installs with `--find-links`, so an unpublished
first-party dependency resolves there and fails only on a user's machine. That is not a gap these
tests can close by resolving harder; it is closed by never depending on a package nobody publishes.
So the rule is checked statically instead: a first-party package ships in the wheel or has a
distribution of its own, and never both.
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


#: Distributions of their own: each has a release workflow, a namespaced tag, and a published wheel
#: an install is meant to resolve. Every other in-repo package ships inside the saitenka wheel.
_OWN_DISTRIBUTION = {
    "ankiconnect-client",
    "libasslite",
    "libasslite-bundle",
    "resvglite",
    "saitenka-deinflect",
    "saitenka-dict",
    "taffylite",
}


def _shipped_packages() -> set[str]:
    """The top-level package directories this wheel carries, as distribution-style names."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packages = config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    return {Path(entry).name.replace("_", "-") for entry in packages}


def test_a_first_party_package_is_shipped_or_published_but_never_depended_on_unpublished():
    """The invariant a release rests on, and the one that was missing.

    An in-repo package can be a dependency, resolved from the registry like any other, or it can
    ship inside this wheel. Being a dependency without being published is neither: the install smoke
    builds it from the checkout and passes, so nothing fails until the wheel meets a machine that
    has only the index — which is the machine of every user. Four packages sat in that state, and
    the first thing to notice was a release build.
    """
    required = _required_names(subtitle_geometry=True)
    shipped = _shipped_packages()
    on_disk = {
        name
        for pyproject in ROOT.glob("*/pyproject.toml")
        if (name := _declared_name(pyproject)) is not None
    }
    stranded = sorted(
        name
        for name in on_disk
        if name in required and name not in _OWN_DISTRIBUTION and name not in shipped
    )
    assert not stranded, (
        f"depended on but neither published nor shipped in the wheel: {stranded} — "
        "either give it a release workflow and a tag, or add it to the wheel's packages"
    )


def test_a_package_this_wheel_ships_is_not_also_a_dependency():
    """The other direction: shipping a package and requiring it names one import path twice, and the
    copy that wins is whichever the resolver installed — not the tree these tests ran against."""
    both = sorted(_shipped_packages() & _required_names(subtitle_geometry=True))
    assert not both, f"shipped inside the wheel and required from the index: {both}"

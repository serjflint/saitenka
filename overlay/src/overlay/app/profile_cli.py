"""``saitenka profile`` — manage reading profiles (#254 W4): the ``[profile]`` default and named
``[profiles.<name>]`` overlays the resolver (:mod:`overlay.app.profiles`) reads.

``saitenka config`` only edits the singular default table; creating and switching *named* profiles (a
French one alongside the Japanese default) lived only in hand-edited TOML. This adds the CRUD surface.

Split like ``config_editor``: the value plumbing here is pure and unit-tested (proposal builders,
validation, the list/show renderers); the thin cyclopts shell that prompts + writes via
:func:`~overlay.app.init_wizard.write_config` (tomlkit round-trip, ``.bak``, atomic) is ``pragma: no
cover``. All dictionary fields are TITLE lists resolved against the consolidated DB, never paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import cyclopts

from overlay.app.config import load_config
from overlay.app.profiles import (
    canonical_language,
    configured_profiles,
    default_tokenizer_for,
    profile_names,  # re-exported: the canonical impl lives on the profiles leaf (cycle-free for doctor)
    resolve_profile,
    scope_config,
    validate_language_code,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_profile_table(
    *,
    language: str,
    second: str | None = None,
    tokenizer: str | None = None,
    dicts: Sequence[str] = (),
    freq: Sequence[str] = (),
    pitch: Sequence[str] = (),
) -> dict:
    """The ``[profiles.<name>]`` table for one profile, validated. ``language`` is shape-checked;
    ``tokenizer`` defaults via :func:`default_tokenizer_for` (so ``--language fr`` alone yields
    ``latin``, and an unknown-script language raises here, not silently at reader construction).

    dicts/freq/pitch are ALWAYS written — an unspecified one as an empty list, NOT omitted. A profile is
    a self-contained identity: a French profile must never inherit the top-level Japanese dictionaries
    (``scope_config`` only replaces a list the profile actually defines), so scoping all three explicitly
    is what keeps a JP dict / NHK pitch out of a French lookup. Add the titles later with a re-``add``."""
    validate_language_code(language)
    canon = canonical_language(language)
    table: dict = {"language": language, "tokenizer": tokenizer or default_tokenizer_for(canon)}
    if second is not None:
        table["second"] = validate_language_code(second)
    for key, values in (("dicts", dicts), ("freq", freq), ("pitch", pitch)):
        table[key] = list(values)  # empty when unspecified → scopes to nothing, never inherits
    return table


def add_proposal(name: str, table: dict) -> dict:
    """A ``write_config`` proposal that creates/updates ``[profiles.<name>]`` (deep-merged, so a
    re-``add`` of the same name overlays only the given keys)."""
    if not name:
        raise ValueError("profile name must be non-empty")
    return {"profiles": {name: table}}


def use_proposal(name: str | None) -> dict:
    """Proposal setting the active profile. ``None`` selects the built-in default — written as an empty
    ``active_profile`` so it round-trips (the resolver treats falsy as "the default ``[profile]``")."""
    return {"active_profile": name or ""}


def remove_paths(cfg: dict, name: str) -> tuple[tuple[str, ...], ...]:
    """The ``write_config(remove=…)`` paths that delete ``[profiles.<name>]`` — plus ``active_profile``
    when it currently points at the removed profile (else the selector dangles at a gone table)."""
    paths: list[tuple[str, ...]] = [("profiles", name)]
    if cfg.get("active_profile") == name:
        paths.append(("active_profile",))
    return tuple(paths)


def render_list(cfg: dict) -> list[str]:
    """One line per profile in cycle order (base default first), the active one marked ``*``."""
    active = cfg.get("active_profile") or ""
    lines = []
    for p in configured_profiles(cfg):
        marker = "*" if (p.name == active or (not active and p.name == "default")) else " "
        lines.append(f"{marker} {p.name:<16} {p.langs.main}→{p.langs.second}  [{p.tokenizer}]")
    return lines


def render_show(cfg: dict, name: str | None = None) -> list[str]:
    """The fully-resolved identity of ``name`` (or the active profile) — language, tokenizer, and the
    SCOPED dictionary titles the reader would actually consult (via :func:`scope_config`)."""
    profile = resolve_profile(cfg, override=name)
    scoped = scope_config(cfg, override=name)
    lines = [
        f"profile:   {profile.name}",
        f"language:  {profile.langs.main}  (second: {profile.langs.second})",
        f"tokenizer: {profile.tokenizer}",
    ]
    for key in ("dicts", "freq", "pitch"):
        got = scoped.get(key)
        lines.append(f"{key + ':':<10} {', '.join(got) if got else '—'}")
    return lines


# --- CLI shell (thin; the plumbing above is what's unit-tested) ----------------------------------

profile_app = cyclopts.App(
    name="profile",
    help="Manage reading profiles (the [profile] default and named [profiles.<name>] overlays).",
    # No config layer: this is a config EDITOR, so its --dicts/--freq/--pitch params must NOT be
    # pre-filled from the runtime overlay.toml (the root app's Toml config does that for the reader
    # commands). Without this, `profile add` inherits the top-level dicts/freq/pitch as param defaults —
    # exactly the "a French profile silently borrows the Japanese dicts / NHK pitch" bug.
    config=(),
)

_CSV = (
    "comma-separated dictionary TITLES (as shown by `saitenka doctor`), replacing the top-level set"
)


def _split(value: str | None) -> list[str]:
    return [s.strip() for s in (value or "").split(",") if s.strip()]


@profile_app.command(name="list")
def _list() -> int:  # pragma: no cover — thin CLI wrapper over render_list
    """List configured profiles (base default first), the active one marked ``*``."""
    for line in render_list(load_config()):
        print(line)
    return 0


@profile_app.command
def show(
    name: Annotated[
        str | None, cyclopts.Parameter(help="profile name (default: the active one)")
    ] = None,
) -> int:  # pragma: no cover — thin CLI wrapper over render_show
    """Show a profile's fully-resolved identity: language, tokenizer, and the scoped dictionary titles."""
    for line in render_show(load_config(), name):
        print(line)
    return 0


@profile_app.command
def add(
    name: str,
    *,
    language: Annotated[
        str, cyclopts.Parameter(help="main (target) language code, e.g. fr, de-CH")
    ],
    second: Annotated[str | None, cyclopts.Parameter(help="second (known) language code")] = None,
    tokenizer: Annotated[
        str | None, cyclopts.Parameter(help="tokenizer strategy (default from the language)")
    ] = None,
    dicts: Annotated[str | None, cyclopts.Parameter(help=_CSV)] = None,
    freq: Annotated[
        str | None, cyclopts.Parameter(help="comma-separated frequency dict titles")
    ] = None,
    pitch: Annotated[
        str | None, cyclopts.Parameter(help="comma-separated pitch dict titles")
    ] = None,
    yes: Annotated[bool, cyclopts.Parameter(negative=(), help="write without prompting")] = False,
) -> int:  # pragma: no cover — thin CLI wrapper; build_profile_table/add_proposal are unit-tested
    """Create or update a named ``[profiles.<name>]``. Flags only (no prompts), so it works headless."""
    from overlay.app import prompt
    from overlay.app.init_wizard import write_config

    table = build_profile_table(
        language=language,
        second=second,
        tokenizer=tokenizer,
        dicts=_split(dicts),
        freq=_split(freq),
        pitch=_split(pitch),
    )
    backup = write_config(
        add_proposal(name, table), confirm=(lambda _p: True) if yes else prompt.confirm
    )
    if backup:
        print(f"backed up existing config → {backup}")
    return 0


@profile_app.command
def use(
    name: Annotated[
        str | None, cyclopts.Parameter(help="profile to activate (omit for the default [profile])")
    ] = None,
    *,
    yes: Annotated[bool, cyclopts.Parameter(negative=(), help="write without prompting")] = False,
) -> int:  # pragma: no cover — thin CLI wrapper over use_proposal
    """Set the active profile (top-level ``active_profile``); omit NAME to select the built-in default."""
    from overlay.app import prompt
    from overlay.app.init_wizard import write_config

    write_config(use_proposal(name), confirm=(lambda _p: True) if yes else prompt.confirm)
    return 0


@profile_app.command
def remove(
    name: str,
    *,
    yes: Annotated[bool, cyclopts.Parameter(negative=(), help="write without prompting")] = False,
) -> int:  # pragma: no cover — thin CLI wrapper over remove_paths
    """Delete a named ``[profiles.<name>]`` (and clear ``active_profile`` if it pointed there)."""
    from overlay.app import prompt
    from overlay.app.init_wizard import write_config

    cfg = load_config()
    if name not in profile_names(cfg):
        print(f"no profile named {name!r}")
        return 1
    write_config(
        {}, confirm=(lambda _p: True) if yes else prompt.confirm, remove=remove_paths(cfg, name)
    )
    return 0

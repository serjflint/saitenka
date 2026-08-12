"""``saitenka init`` — first-run wizard.

Runs auto-discovery (Yomitan import + mpv discovery), proposes a config, and writes the
platform-native ``overlay.toml`` (see :func:`config.config_path`) ONLY on confirm — backing up an existing file first,
timestamped (non-destructive rule). The write path (:func:`write_config`) is the shared confirm+backup
sink used by ``init``, ``import-settings``, and the setup wizard.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from saitenka.app import prompt
from saitenka.app.config import config_path

if TYPE_CHECKING:
    from pathlib import Path

# The default config a fresh install starts from (what `init`/`setup` propose writing). One template,
# shared by both wizards — edit a starter value here, not in two places.
DEFAULT_CONFIG = {"slang": "ja,jpn,jp", "tip_height": 0.4}

Confirm = Callable[[str], bool]


def _toml_value(v) -> str:
    """Serialise a scalar/list to TOML (tomllib is read-only; we only emit strings/lists/bools)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        esc = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{esc}"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"can't serialise {type(v).__name__} to TOML")


def _toml_key(k: str) -> str:
    """A bare TOML key when possible, else a quoted key (e.g. a deck name ``Saitenka::Known``)."""
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else _toml_value(k)


def _toml_assign(k: str, v) -> str:
    """One ``key = value`` line, splitting a >1-element list across lines for readability."""
    if isinstance(v, (list, tuple)) and len(v) > 1:
        body = ",\n  ".join(_toml_value(x) for x in v)
        return f"{_toml_key(k)} = [\n  {body},\n]"
    return f"{_toml_key(k)} = {_toml_value(v)}"


def _emit_table(lines: list[str], prefix: str, table: dict) -> None:
    """Emit ``[prefix]`` then its scalar keys, recursing into nested dicts as ``[prefix.child]`` —
    a dict value is a subtable, not a scalar. Scalars go BEFORE any subtable header, because a
    ``[prefix.child]`` line closes ``prefix`` and any bare key after it would land in the child."""
    lines.append("")
    lines.append(f"[{prefix}]")
    subtables = [(k, v) for k, v in table.items() if isinstance(v, dict)]
    for k, v in table.items():
        if not isinstance(v, dict):
            lines.append(_toml_assign(k, v))
    for k, v in subtables:
        _emit_table(lines, f"{prefix}.{_toml_key(k)}", v)


def dumps_toml(proposal: dict) -> str:
    """A minimal deterministic TOML writer. Top-level scalars/lists first, then nested ``dict`` values
    as ``[table]`` sections (recursively — a dict inside a table becomes ``[table.child]``, e.g.
    ``[profiles.french]``), so merging onto a config with ``[mine]``/``[jimaku]``/``[profiles.*]``
    tables round-trips instead of raising ``TypeError`` (or silently dropping the tables)."""
    lines = ["# Saitenka overlay settings — written by `saitenka init`.", ""]
    tables = [(k, v) for k, v in proposal.items() if isinstance(v, dict)]
    for k, v in proposal.items():
        if not isinstance(v, dict):
            lines.append(_toml_assign(k, v))
    for name, table in tables:
        _emit_table(lines, _toml_key(name), table)
    return "\n".join(lines) + "\n"


def backup_existing(dest: Path) -> Path | None:
    """Timestamped copy of ``dest`` if it exists, else None. Non-destructive rule."""
    if not dest.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = dest.with_name(f"{dest.name}.{ts}.bak")
    from saitenka.app.paths import atomic_write_text

    atomic_write_text(backup, dest.read_text(encoding="utf-8"))
    return backup


def _merge_into(doc, data: dict) -> None:
    """Recursively set ``data`` into a tomlkit document/table: recurse into existing tables and update
    only CHANGED keys in place (so their comments + position survive), add new keys."""
    for k, v in data.items():
        existing = doc.get(k)
        if isinstance(v, dict) and isinstance(existing, dict):
            _merge_into(existing, v)
        elif existing != v:  # unchanged keys are left untouched (keeps any inline comment)
            doc[k] = v


def _remove_path(doc, path: tuple[str, ...]) -> None:
    table = doc
    for key in path[:-1]:
        table = table.get(key)
        if not isinstance(table, dict):
            return
    table.pop(path[-1], None)


def write_config(
    proposal: dict,
    confirm: Confirm,
    dest: Path | None = None,
    *,
    remove: tuple[tuple[str, ...], ...] = (),
) -> Path | None:
    """Write the proposed config on confirm; back up an existing file first.

    Round-trips through **tomlkit** so an existing file's COMMENTS + formatting survive — we only set
    the keys that actually changed. Returns the backup path (None if there was nothing to back up);
    writes nothing if declined.
    """
    import tomlkit

    dest = dest or config_path()
    if not confirm(f"Write config to {dest}?"):
        return None
    backup = backup_existing(dest)
    doc = tomlkit.parse(dest.read_text(encoding="utf-8")) if dest.exists() else tomlkit.document()
    for path in remove:
        _remove_path(doc, path)
    _merge_into(doc, proposal)
    from saitenka.app.paths import atomic_write_text

    atomic_write_text(
        dest, tomlkit.dumps(doc)
    )  # temp + fsync + os.replace (no half-written config)
    return backup


def store_jimaku_key(
    k: str, confirm: Confirm = lambda _p: True, *, prefer_file: bool = False
) -> tuple[str, Path | None]:
    """Persist the jimaku key where a plugin-mode (GUI-launched) mpv can read it: the OS secret store
    via ``keyring`` (macOS Keychain / Windows Credential Locker / opt-in Linux Secret Service), else
    a private file next to the config. Returns ``(method, backup)`` where method is ``"keyring"`` or
    ``"file"``.

    ``prefer_file`` (or a disabled keyring — see :func:`jimaku.keyring_enabled`) skips the OS store and
    persists ``[jimaku].keyring = false`` so a later read also bypasses it — the escape hatch for the
    Windows AV that flags the first Credential Locker read. A no-backend fallback (headless Linux)
    still uses the file but leaves keyring enabled — the opt-out is only recorded when it's deliberate.

    Either way it writes ``[jimaku].fetch = true``: setting a key MEANS "fetch JP subs from jimaku when
    a file has no JP track", so ``run``/``attach`` act on it without a flag. It also gives the installer
    a plain-text config marker that jimaku is set up (the keyring isn't cheaply readable from a shell)."""
    from saitenka.app.config import load_config
    from saitenka.app.jimaku import key_file_set, keychain_set, keyring_enabled

    opted_out = prefer_file or not keyring_enabled()
    if not opted_out and keychain_set(k):
        method = "keyring"
    else:
        key_file_set(k)
        method = "file"
    cfg = load_config()
    jm = dict(cfg.get("jimaku") or {})
    jm["fetch"] = True
    jm.pop("key", None)
    if opted_out:  # record the deliberate opt-out so resolve() skips the keyring read too
        jm["keyring"] = False
    backup = write_config({**cfg, "jimaku": jm}, confirm=confirm, remove=(("jimaku", "key"),))
    return method, backup


def _maybe_store_jimaku_key() -> None:  # pragma: no cover — interactive/secret I/O
    """Offer to store a jimaku.cc key if none resolves yet (skips if already set)."""
    import getpass

    from saitenka.app.jimaku import prompt_for_key, resolve_jimaku_key

    key, src = resolve_jimaku_key()
    if key:
        print(f"jimaku API key: found (from {src})")
        return
    if not prompt.confirm("\nStore a jimaku.cc API key now (for sub fetch in plugin mode)?"):
        return
    k = prompt_for_key(
        getpass.getpass
    )  # hidden prompt + truncated-paste guard (Windows Ctrl+V trap)
    if not k:
        return
    method, _ = store_jimaku_key(k)
    if method == "keyring":
        print("stored in the OS secret store (Keychain / Credential Locker / Secret Service)")
    else:
        from saitenka.app.jimaku import key_file_path

        print(f"stored in {key_file_path()} (plaintext, owner-only)")


def run_init() -> int:  # pragma: no cover — interactive wizard, exercised live
    from saitenka.app.doctor import run_checks
    from saitenka.mpvio.discover import find_mpv

    print("saitenka init — first-run setup")
    mpv = find_mpv()
    print(f"  mpv: {mpv or 'not found — install it (see doctor)'}")
    proposal = dict(DEFAULT_CONFIG)
    print("\nProposed config:")
    print(dumps_toml(proposal))
    backup = write_config(proposal, confirm=prompt.confirm)
    if backup:
        print(f"backed up existing config → {backup}")

    _maybe_store_jimaku_key()

    print("\nRunning doctor…")
    from saitenka.app.doctor import print_report

    print_report(run_checks())
    return 0

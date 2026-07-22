"""``saitenka-overlay init`` — first-run wizard.

Runs auto-discovery (Yomitan import + mpv discovery), proposes a config, and writes the
platform-native ``overlay.toml`` (see :func:`config.config_path`) ONLY on confirm — backing up an existing file first,
timestamped (non-destructive rule). The write path (:func:`write_config`) is the shared confirm+backup
sink used by ``init``, ``import-settings``, and the setup wizard.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path

from overlay.app.config import config_path

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


def dumps_toml(proposal: dict) -> str:
    """A minimal deterministic TOML writer. Scalars/lists first, then nested ``dict`` values as
    ``[table]`` sections — so merging onto a config with ``[mine]``/``[jimaku]``/``[known]`` tables
    round-trips instead of raising ``TypeError`` (or silently dropping the tables)."""
    lines = ["# Saitenka overlay settings — written by `saitenka-overlay init`.", ""]
    tables: list[tuple[str, dict]] = []
    for k, v in proposal.items():
        if isinstance(v, dict):
            tables.append((k, v))
        elif isinstance(v, (list, tuple)) and len(v) > 1:
            body = ",\n  ".join(_toml_value(x) for x in v)
            lines.append(f"{k} = [\n  {body},\n]")
        else:
            lines.append(f"{k} = {_toml_value(v)}")
    for name, table in tables:
        lines.append("")
        lines.append(f"[{_toml_key(name)}]")
        for k, v in table.items():
            lines.append(f"{_toml_key(k)} = {_toml_value(v)}")
    return "\n".join(lines) + "\n"


def backup_existing(dest: Path) -> Path | None:
    """Timestamped copy of ``dest`` if it exists, else None. Non-destructive rule."""
    if not dest.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = dest.with_name(f"{dest.name}.{ts}.bak")
    backup.write_bytes(dest.read_bytes())
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


def write_config(proposal: dict, confirm: Confirm, dest: Path | None = None) -> Path | None:
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
    _merge_into(doc, proposal)
    from overlay.app.paths import atomic_write_text

    atomic_write_text(
        dest, tomlkit.dumps(doc)
    )  # temp + fsync + os.replace (no half-written config)
    return backup


def _ask(prompt: str) -> bool:  # pragma: no cover — interactive I/O
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def store_jimaku_key(k: str, confirm: Confirm = lambda _p: True) -> tuple[str, Path | None]:
    """Persist the jimaku key where a plugin-mode (GUI-launched) mpv can read it: the OS secret store
    via ``keyring`` (macOS Keychain / Windows Credential Locker / Linux Secret Service), else
    ``[jimaku].key`` in the config when no keyring backend exists (headless Linux). Returns
    ``(method, backup)`` where method is ``"keyring"`` or ``"config"``.

    Either way it writes ``[jimaku].fetch = true``: setting a key MEANS "fetch JP subs from jimaku when
    a file has no JP track", so ``run``/``attach`` act on it without a flag. It also gives the installer
    a plain-text config marker that jimaku is set up (the keyring isn't cheaply readable from a shell)."""
    from overlay.app.config import load_config
    from overlay.app.jimaku import keychain_set

    method = "keyring" if keychain_set(k) else "config"
    cfg = load_config()
    jm = dict(cfg.get("jimaku") or {})
    jm["fetch"] = True
    if method == "config":
        jm["key"] = k  # no OS secret store available — persist the key in the config (plaintext)
    backup = write_config({**cfg, "jimaku": jm}, confirm=confirm)
    return method, backup


def _maybe_store_jimaku_key() -> None:  # pragma: no cover — interactive/secret I/O
    """Offer to store a jimaku.cc key if none resolves yet (skips if already set)."""
    import getpass

    from overlay.app.jimaku import prompt_for_key, resolve_jimaku_key

    key, src = resolve_jimaku_key()
    if key:
        print(f"jimaku API key: found (from {src})")
        return
    if not _ask("\nStore a jimaku.cc API key now (for sub fetch in plugin mode)?"):
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
        print(f"stored in {config_path()} as [jimaku].key (plaintext — keep it private)")


def run_init() -> int:  # pragma: no cover — interactive wizard, exercised live
    from overlay.app.doctor import run_checks
    from overlay.mpvio.discover import find_mpv

    print("saitenka-overlay init — first-run setup")
    mpv = find_mpv()
    print(f"  mpv: {mpv or 'not found — install it (see doctor)'}")
    proposal: dict = {"slang": "ja,jpn,jp", "tip_height": 0.6}
    print("\nProposed config:")
    print(dumps_toml(proposal))
    backup = write_config(proposal, confirm=_ask)
    if backup:
        print(f"backed up existing config → {backup}")

    _maybe_store_jimaku_key()

    print("\nRunning doctor…")
    from overlay.app.doctor import print_report

    print_report(run_checks())
    return 0

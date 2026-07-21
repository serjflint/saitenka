"""``saitenka-overlay init`` — first-run wizard.

Runs auto-discovery (Yomitan import + mpv discovery), proposes a config, and writes
``~/.config/saitenka/overlay.toml`` ONLY on confirm — backing up an existing file first,
timestamped (non-destructive rule). The write path (:func:`write_config`) is the shared confirm+backup
sink used by ``init``, ``import-yomitan``, and the setup wizard.
"""

from __future__ import annotations

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


def dumps_toml(proposal: dict) -> str:
    """A minimal deterministic TOML writer for the flat config we produce (no nested tables here)."""
    lines = ["# Saitenka overlay settings — written by `saitenka-overlay init`.", ""]
    for k, v in proposal.items():
        if isinstance(v, (list, tuple)) and len(v) > 1:
            body = ",\n  ".join(_toml_value(x) for x in v)
            lines.append(f"{k} = [\n  {body},\n]")
        else:
            lines.append(f"{k} = {_toml_value(v)}")
    return "\n".join(lines) + "\n"


def backup_existing(dest: Path) -> Path | None:
    """Timestamped copy of ``dest`` if it exists, else None. Non-destructive rule."""
    if not dest.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = dest.with_name(f"{dest.name}.{ts}.bak")
    backup.write_bytes(dest.read_bytes())
    return backup


def write_config(proposal: dict, confirm: Confirm, dest: Path | None = None) -> Path | None:
    """Write the proposed config on confirm; back up an existing file first.

    Returns the backup path (or None if there was nothing to back up). Writes nothing if declined.
    """
    dest = dest or config_path()
    if not confirm(f"Write config to {dest}?"):
        return None
    backup = backup_existing(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(dumps_toml(proposal), encoding="utf-8")
    return backup


def _ask(prompt: str) -> bool:  # pragma: no cover — interactive I/O
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def _maybe_store_jimaku_key() -> None:  # pragma: no cover — interactive/secret I/O
    """Offer to store a jimaku.cc key in the Keychain if none resolves yet (skips if already set)."""
    import getpass

    from overlay.app.jimaku import keychain_set, resolve_jimaku_key

    key, src = resolve_jimaku_key()
    if key:
        print(f"jimaku API key: found (from {src})")
        return
    if not _ask("\nStore a jimaku.cc API key in the Keychain now (for sub fetch in plugin mode)?"):
        return
    k = getpass.getpass("jimaku.cc API key (hidden): ").strip()
    if k and keychain_set(k):
        print("stored in the macOS Keychain")
    elif k:
        print("could not store in the Keychain — set $JIMAKU_API_KEY or [jimaku].key instead")


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

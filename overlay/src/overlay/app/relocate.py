"""Copy dictionaries out of TCC-protected folders and repoint the config.

Dictionaries under ~/Documents (or Desktop/Downloads) make a GUI-launched, plugin-mode mpv trip a
macOS "wants to access your Documents" consent prompt every run, because the overlay opens the zips
as a child of mpv. Copying them into ~/.local/share/saitenka/dicts (not protected) removes the
prompt. Repointing edits the config as TEXT — a targeted path substitution — so the [known]/[mine]/
[jimaku] tables and all comments survive (we have no round-trip TOML writer).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from overlay.app.config import config_path, dicts_data_dir, is_protected, load_config

log = logging.getLogger(__name__)

_KINDS = ("dicts", "freq", "pitch")


def plan_relocation(cfg: dict, dest_dir: Path) -> list[tuple[str, str, Path]]:
    """For each dict/freq/pitch entry under a protected folder that exists on disk, return
    ``(raw_old, expanded_src, dest_path)``. ``raw_old`` is the string exactly as written in the
    config (``~`` intact) so we can text-substitute it; entries already outside a protected folder
    (or missing) are skipped."""
    plan: list[tuple[str, str, Path]] = []
    for kind in _KINDS:
        for raw in cfg.get(kind) or []:
            raw = str(raw)
            src = Path(raw).expanduser()
            if not is_protected(raw) or not src.exists():
                continue
            plan.append((raw, str(src), dest_dir / src.name))
    return plan


def _new_raw(raw_old: str, dest_dir: Path) -> str:
    """The repointed config value, keeping a ``~`` prefix when the dest is under $HOME."""
    dest = dest_dir / Path(raw_old).name
    try:
        return "~/" + str(dest.relative_to(Path.home()))
    except ValueError:  # pragma: no cover — dest outside HOME
        return str(dest)


def repoint_text(text: str, mappings: list[tuple[str, str]]) -> str:
    """Replace each ``raw_old`` path with ``raw_new`` in the config text (leaves everything else —
    tables, comments — untouched)."""
    for raw_old, raw_new in mappings:
        text = text.replace(raw_old, raw_new)
    return text


def relocate_dicts(
    dest_dir: Path | None = None,
    *,
    config: str | None = None,
    copy=shutil.copy2,
) -> list[tuple[str, str]]:
    """Copy protected dict/freq/pitch zips into ``dest_dir`` (default ~/.local/share/saitenka/dicts)
    and repoint the config text at them. Returns the ``(old, new)`` path mappings applied (empty when
    nothing needed moving). A copy that already matches (same size) is skipped."""
    dest_dir = dest_dir or dicts_data_dir()
    cfg = load_config(config)
    plan = plan_relocation(cfg, dest_dir)
    if not plan:
        return []

    dest_dir.mkdir(parents=True, exist_ok=True)
    mappings: list[tuple[str, str]] = []
    for raw_old, src, dest in plan:
        if not (dest.exists() and dest.stat().st_size == Path(src).stat().st_size):
            copy(src, dest)
        mappings.append((raw_old, _new_raw(raw_old, dest_dir)))

    cfg_file = config_path(config)
    if cfg_file.exists():
        text = cfg_file.read_text(encoding="utf-8")
        cfg_file.write_text(repoint_text(text, mappings), encoding="utf-8")
    return mappings


def import_from_dir(
    source_dir: str | Path, *, config: str | None = None, copy=shutil.copy2
) -> list[tuple[str, str]]:
    """Copy every Yomitan dictionary ``.zip`` under ``source_dir`` into the data dir, classify each by
    CONTENT (dict/freq/pitch), and ADD it to the config. Non-Yomitan zips are skipped. Returns
    ``(dest_path, kind)`` for each imported zip (empty when the dir has no Yomitan dictionaries)."""
    from overlay.app.init_wizard import write_config
    from overlay.app.yomitan_import import _index_title, classify_zip

    src = Path(source_dir).expanduser()
    if not src.is_dir():
        raise NotADirectoryError(f"not a directory: {src}")
    dest_dir = dicts_data_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(config)
    lists = {k: list(cfg.get(k) or []) for k in _KINDS}
    key_for = {"dict": "dicts", "freq": "freq", "pitch": "pitch"}
    added: list[tuple[str, str]] = []
    for zp in sorted(src.rglob("*.zip")):
        if _index_title(zp) is None:  # not a Yomitan dictionary — skip it
            continue
        dest = dest_dir / zp.name
        if not (dest.exists() and dest.stat().st_size == zp.stat().st_size):
            copy(str(zp), str(dest))
        raw = _new_raw(zp.name, dest_dir)  # config value, ~-relative when under $HOME
        key = key_for[classify_zip(str(dest))]
        # Reclassify across buckets, don't just append: a zip already listed under the WRONG kind
        # (e.g. an NHK pitch dict mis-filed as `dicts` by an older classifier) is moved to its correct
        # bucket, so re-running `copy-dicts` repairs the config instead of double-listing it.
        for other in _KINDS:
            if other != key and raw in lists[other]:
                lists[other].remove(raw)
        if raw not in lists[key]:
            lists[key].append(raw)
        added.append((str(dest), key))
    if added:
        # Set each bucket that has content OR was already present (so a bucket emptied by a
        # reclassification is overwritten to [] instead of keeping its stale, now-wrong entry). Don't
        # introduce empty freq/pitch keys the config never had.
        merged = {**cfg}
        for k in _KINDS:
            if lists[k] or k in cfg:
                merged[k] = lists[k]
        write_config(merged, confirm=lambda _p: True, dest=config_path(config))
    return added

"""``saitenka-overlay import-settings`` — map a Yomitan settings export onto our config.

Reads a Yomitan **settings export** — the small file from Yomitan → Settings → Backup, NOT the
multi-GB collection/dictionary export. We refuse anything over ``MAX_SETTINGS_BYTES`` (a settings
export is well under a megabyte; a collection export is gigabytes) so the tool can never be pointed
at the wrong file and eat memory.

Mapping: the enabled dictionaries in Yomitan priority order → ``dicts``; when a dictionary's zip is
found (via ``--scan-dir``) it is bucketed into ``dicts`` / ``freq`` / ``pitch`` by INSPECTING ITS
CONTENT the way Yomitan does — definition dicts carry ``term_bank`` glossaries; frequency and pitch
dicts carry ``term_meta`` banks whose entries declare a ``"freq"`` / ``"pitch"`` mode. Titles with no
matching zip default to ``dicts`` (a name can't reliably tell you the type) and are reported for the
user to supply / re-bucket. Zip locations are the user's to supply via ``--scan-dir DIR`` (opt-in,
repeatable) — we never auto-scan personal folders; scan-dir zips are validated as Yomitan-format
(``index.json`` with a ``format``/``title``).
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

MAX_SETTINGS_BYTES = 50 * 1024 * 1024  # a settings export is < 1 MB; refuse a collection export

# Yomitan dictionary banks: definition dicts ship term_bank glossaries; frequency and pitch dicts
# ship term_meta banks (``[term, "freq"|"pitch", data]``). We classify by the term_meta MODE — never
# by the title, and never by mere term_bank presence (pitch dicts carry headword term_banks too).
_META_BANK = re.compile(r"term_meta_bank_\d+\.json$")


class YomitanImportError(RuntimeError):
    pass


@dataclass(frozen=True)
class DictRef:
    name: str
    enabled: bool
    priority: int


@dataclass(frozen=True)
class YomitanSettings:
    dictionaries: list[DictRef]  # enabled-first, priority-desc order
    scan_modifier: str
    popup_scale: float


def classify_zip(zip_path: str | Path) -> str:
    """Classify a Yomitan dictionary zip by its CONTENT (the way Yomitan does): ``"freq"`` /
    ``"pitch"`` / ``"dict"``.

    Definition dictionaries carry ``term_bank_*.json`` glossaries; frequency and pitch dictionaries
    carry ``term_meta_bank_*.json`` whose entries are ``[term, "freq"|"pitch", data]``. The term-meta
    **mode wins**: a pitch (or freq) dict often ALSO ships headword ``term_bank`` files — the popular
    NHK 2016 pitch dict does — so keying off "has a term_bank" would misfile it as a definition dict
    (the exact bug: pitch accents never rendered because the dict landed in ``dicts``, not ``pitch``).
    Only when there's no freq/pitch term-meta does a term_bank make it a definition dict. Falls back to
    ``"dict"`` when the zip can't be read or has no recognisable banks — the title is never consulted.
    """
    modes: set[str] = set()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            for n in sorted(n for n in names if _META_BANK.match(n))[:1]:
                for entry in json.loads(zf.read(n)):
                    if len(entry) >= 2 and isinstance(entry[1], str):
                        modes.add(entry[1])
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError, ValueError, TypeError):
        return "dict"
    if "pitch" in modes:
        return "pitch"
    if "freq" in modes:
        return "freq"
    return "dict"


def parse_settings(path: str | Path) -> YomitanSettings:
    """Parse a Yomitan settings export. Raises :class:`YomitanImportError` on the wrong file."""
    p = Path(path)
    size = p.stat().st_size
    if size > MAX_SETTINGS_BYTES:
        raise YomitanImportError(
            f"{p} is {size} bytes — too large for a settings export (did you export the "
            f"whole collection? point at the small Settings → Backup file instead)"
        )
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise YomitanImportError(f"couldn't parse {p}: {e}") from e

    profiles = obj.get("options", {}).get("profiles", [])
    if not profiles:
        raise YomitanImportError(f"{p} has no options.profiles — not a Yomitan settings export")
    popts = profiles[0].get("options", {})

    refs = [
        DictRef(d.get("name", ""), bool(d.get("enabled", False)), int(d.get("priority", 0) or 0))
        for d in popts.get("dictionaries", [])
        if d.get("name")
    ]
    # Yomitan renders higher-priority dicts first; keep a stable order for equal priorities.
    ordered = sorted(refs, key=lambda r: (not r.enabled, -r.priority))

    scan_mod = ""
    for inp in popts.get("scanning", {}).get("inputs", []):
        if inp.get("types", {}).get("mouse"):
            scan_mod = inp.get("include", "") or ""
            break
    scale = popts.get("general", {}).get("popupScale")
    popup_scale = float(scale) if isinstance(scale, (int, float)) else 1.0

    return YomitanSettings(ordered, scan_mod, popup_scale)


def _index_title(zip_path: Path) -> str | None:
    """The Yomitan ``index.json`` title if the zip is a valid Yomitan dictionary, else None."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            idx = json.loads(zf.read("index.json"))
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
        return None
    if "format" not in idx and "version" not in idx:
        return None  # not a Yomitan dictionary index
    title = idx.get("title")
    return str(title) if title else None


def match_scan_dirs(
    titles: list[str], scan_dirs: list[str | Path]
) -> tuple[dict[str, str], list[str]]:
    """Match dictionary ``titles`` against Yomitan zips found in ``scan_dirs`` (opt-in).

    Returns ``(matches, missing)`` where ``matches`` maps title → zip path and ``missing`` lists the
    titles with no matching zip. Non-Yomitan zips are ignored.
    """
    by_title: dict[str, str] = {}
    for d in scan_dirs:
        dp = Path(d)
        if not dp.is_dir():
            continue
        for zp in sorted(dp.rglob("*.zip")):
            t = _index_title(zp)
            if t and t not in by_title:
                by_title[t] = str(zp)
    matches = {t: by_title[t] for t in titles if t in by_title}
    missing = [t for t in titles if t not in matches]
    return matches, missing


def to_config(settings: YomitanSettings, matches: dict[str, str]) -> dict:
    """Build the config fragment (``dicts``/``freq``/``pitch``) from parsed settings.

    Each enabled dictionary maps to its matched zip path if one is known, else its bare title (so the
    user can fill in the path afterwards). Order within each list preserves Yomitan's ordering.
    """
    buckets: dict[str, list[str]] = {"dict": [], "freq": [], "pitch": []}
    for ref in settings.dictionaries:
        if not ref.enabled:
            continue
        zip_path = matches.get(ref.name)
        # classify by the matched zip's content; a title with no zip can't be typed → default dicts
        kind = classify_zip(zip_path) if zip_path else "dict"
        buckets[kind].append(zip_path or ref.name)
    cfg: dict = {}
    if buckets["dict"]:
        cfg["dicts"] = buckets["dict"]
    if buckets["freq"]:
        cfg["freq"] = buckets["freq"]
    if buckets["pitch"]:
        cfg["pitch"] = buckets["pitch"]
    return cfg


SETTINGS_GLOB = "yomitan-settings*.json"


def find_settings_export() -> str | None:
    """Newest Yomitan *settings* export in the usual spots — Downloads (where the browser drops it),
    the repo's ``yomitan/`` dir, then home. Returns its path, or None if none is found."""
    dirs = [Path.home() / "Downloads", Path.home() / "Documents/Japanese/yomitan", Path.home()]
    found = [p for d in dirs for p in d.glob(SETTINGS_GLOB)]
    return str(max(found, key=lambda p: p.stat().st_mtime)) if found else None


def run_import(
    settings_path: str | None, scan_dirs: list[str] | None, confirm
) -> int:  # pragma: no cover — interactive glue; the pieces above are unit-tested
    """CLI entry: parse → match → propose → write via the shared confirm+backup sink."""
    from overlay.app.init_wizard import write_config

    if not settings_path:
        settings_path = find_settings_export()
        if settings_path:
            print(f"using {settings_path}")
        elif sys.stdin.isatty():
            # Interactive: don't skip past silently — ask for the path and WAIT.
            entered = (
                input(
                    "Yomitan settings export not found. Enter its path (Yomitan → Settings → Backup → "
                    "Export Settings), or press Enter to skip: "
                )
                .strip()
                .strip("\"'")
            )
            if not entered:
                print(
                    "import skipped — run `saitenka-overlay import-settings <settings.json>` later"
                )
                return 1
            settings_path = str(Path(entered).expanduser())
        else:
            print("no settings export given and none found — pass the path explicitly")
            return 1

    settings = parse_settings(settings_path)
    enabled = [d.name for d in settings.dictionaries if d.enabled]
    print(f"{len(enabled)} enabled dictionaries in Yomitan order")

    matches: dict[str, str] = {}
    if scan_dirs:
        matches, missing = match_scan_dirs(enabled, list(scan_dirs))
        print(f"matched {len(matches)} zip(s) in {len(scan_dirs)} scan dir(s)")
        if missing:
            print("no zip found for (supply paths manually in the config):")
            for t in missing:
                print(f"  - {t}")

    cfg = to_config(settings, matches)
    from overlay.app.config import load_config

    existing = load_config()
    merged = {**existing, **cfg}  # overlay the imported dict/freq/pitch onto any existing config
    from overlay.app.init_wizard import dumps_toml

    print("\nProposed config:")
    print(dumps_toml(merged))
    backup = write_config(merged, confirm=confirm)
    if backup:
        print(f"backed up existing config → {backup}")
    return 0

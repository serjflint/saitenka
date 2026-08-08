"""Pure Yomitan-zip parsing: read a bank JSON, classify a dict by content, read its ``index.json``
title. A leaf — it depends on nothing else in ``overlay.app``, so the consolidated DB (:mod:`dictdb`),
the settings importer (:mod:`yomitan_import`) and the bundled-list builder (:mod:`wordlists`) all sit
*above* it and none form a cycle. (These helpers used to live in those three modules and cross-call
each other, which is exactly the import cycle #30 tracks.)
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import zipfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

# Yomitan dictionary banks: definition dicts ship term_bank glossaries; frequency and pitch dicts
# ship term_meta banks (``[term, "freq"|"pitch", data]``). We classify by the term_meta MODE — never
# by the title, and never by mere term_bank presence (pitch dicts carry headword term_banks too).
_META_BANK = re.compile(r"term_meta_bank_\d+\.json$")


@contextlib.contextmanager
def _crc_lenient():
    """Temporarily disable zipfile CRC-32 validation. Some Yomitan dict exporters (notably certain
    pitch-accent dicts) write wrong/zero CRCs even though the deflate data is perfectly intact;
    Python's strict check would otherwise reject them. Scoped + restored, single-threaded use."""
    orig = zipfile.ZipExtFile._update_crc  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]  # deliberate
    zipfile.ZipExtFile._update_crc = lambda _self, *_: None  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]  # patched sig takes the data chunk; ignored
    try:
        yield
    finally:
        zipfile.ZipExtFile._update_crc = orig  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]  # restore


def read_json_bank(zf: zipfile.ZipFile, name: str):
    """Read + parse one bank, tolerating a wrong stored CRC (the data is still valid). Returns the
    decoded list, or None only if the JSON itself is unparseable."""
    try:
        return json.loads(zf.read(name))
    except zipfile.BadZipFile:
        try:
            with _crc_lenient():
                return json.loads(zf.read(name))
        except (zipfile.BadZipFile, ValueError):
            return None
    except ValueError:
        return None


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
    # Read the term_meta bank CRC-tolerantly: some Yomitan pitch/freq exports (notably NHK 2016
    # pitch) ship a WRONG stored CRC-32 on intact deflate data, and a strict read would raise
    # BadZipFile → the dict would silently fall back to "dict" and its pitch/freq never render.
    modes: set[str] = set()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            metas = sorted(n for n in zf.namelist() if _META_BANK.match(n))
            for n in metas[:2]:  # first bank suffices; try a 2nd only if the 1st yields nothing
                for entry in read_json_bank(zf, n) or []:
                    if len(entry) >= 2 and isinstance(entry[1], str):
                        modes.add(entry[1])
                if modes:
                    break
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError, ValueError, TypeError):
        return "dict"
    if "pitch" in modes:
        return "pitch"
    if "freq" in modes:
        return "freq"
    return "dict"


def _title_of(zf: zipfile.ZipFile, fallback: str) -> str:
    try:
        return json.loads(zf.read("index.json")).get("title", fallback) or fallback
    except Exception:
        log.debug("index.json title read failed", exc_info=True)
        return fallback

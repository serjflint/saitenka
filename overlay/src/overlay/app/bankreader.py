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
# ship term_meta banks (``[term, "freq"|"pitch", data]``). Classified by CONTENT, never by the title.
_META_BANK = re.compile(r"term_meta_bank_\d+\.json$")
_TERM_BANK = re.compile(r"term_bank_\d+\.json$")

# Precedence for the single ``kind`` column / display when a zip fills several roles: definitions win
# (a combined dict shows as a definition dict), then pitch, then freq. Full membership is `zip_roles`.
_PRIMARY_ORDER = ("dict", "pitch", "freq")


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


def _meta_modes(zf: zipfile.ZipFile) -> set[str]:
    """The term_meta modes present ({``"freq"``, ``"pitch"``} ∩). The first non-empty bank suffices.
    CRC-tolerant via :func:`read_json_bank` — some pitch/freq exports (NHK 2016) ship a wrong CRC on
    intact data, and a strict read would drop the mode and misfile the dict."""
    modes: set[str] = set()
    for n in sorted(n for n in zf.namelist() if _META_BANK.match(n))[:2]:
        for entry in read_json_bank(zf, n) or []:
            if len(entry) >= 2 and isinstance(entry[1], str):
                modes.add(entry[1])
        if modes:
            break
    return modes & {"freq", "pitch"}


def _has_glossary_terms(zf: zipfile.ZipFile) -> bool:
    """True if a ``term_bank`` carries real definitions (a non-empty glossary at index 5), not
    headword-only stubs. A pitch/freq dict (NHK 2016) ships a term_bank purely to register readings —
    its glossaries are empty — so this distinguishes a genuine definition dictionary from meta-only
    banks, letting a COMBINED definition+frequency dict (e.g. the seth-js French dict: 448k glossaries +
    37k freq) keep BOTH roles instead of the frequency mode silently winning and dropping every
    definition. Yomitan v3 term entry: ``[term, reading, tags, rules, score, glossary, seq, termtags]``."""
    for n in sorted(n for n in zf.namelist() if _TERM_BANK.match(n))[:2]:
        for entry in read_json_bank(zf, n) or []:
            if len(entry) >= 6 and entry[5]:  # a non-empty glossary list
                return True
    return False


def zip_roles(zip_path: str | Path) -> frozenset[str]:
    """The set of roles a Yomitan zip fills — any of ``{"dict", "freq", "pitch"}`` — classified by
    CONTENT, never the title. A dictionary can be several at once (definitions + frequency); each role
    routes the title into the matching config bucket and loads the matching banks. Falls back to
    ``{"dict"}`` when the zip can't be read or has no recognisable banks."""
    roles: set[str] = set()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            roles |= _meta_modes(zf)
            if _has_glossary_terms(zf):
                roles.add("dict")
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError, ValueError, TypeError):
        return frozenset({"dict"})
    return frozenset(roles) or frozenset({"dict"})


def classify_zip(zip_path: str | Path) -> str:
    """The PRIMARY kind for the ``kind`` column / display: definitions win (a combined dict shows as a
    definition dict), then pitch, then freq. The role-complete membership is :func:`zip_roles` — a dict
    with both glossaries and freq meta is BOTH ``dict`` and ``freq`` there, but ``dict`` here."""
    roles = zip_roles(zip_path)
    return next(k for k in _PRIMARY_ORDER if k in roles)


def _title_of(zf: zipfile.ZipFile, fallback: str) -> str:
    try:
        return json.loads(zf.read("index.json")).get("title", fallback) or fallback
    except Exception:
        log.debug("index.json title read failed", exc_info=True)
        return fallback

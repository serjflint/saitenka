"""Data providers for subtitle coloring: JLPT level, frequency rank, and known-words.

JLPT + frequency are Yomitan ``term_meta_bank`` dictionaries (the same format SubMiner reads). The
freq-value shapes seen in the wild:
  - value form:     ``[term, "freq", {"value": rank, "displayValue": "rank㋕"}]``       (term = kana)
  - frequency form: ``[term, "freq", {"reading": r, "frequency": rank}]``               (term = word)
  - JLPT:           ``[term, "freq", {"reading": r, "frequency": {"value": -1, "displayValue": "N5"}}]``
We index by both term and reading so a lemma or its kana reading can match.
"""

from __future__ import annotations

import contextlib
import logging
import json
import math
import re
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from overlay.resources import asset

log = logging.getLogger(__name__)


@contextlib.contextmanager
def _crc_lenient():
    """Temporarily disable zipfile CRC-32 validation. Some Yomitan dict exporters (notably certain
    pitch-accent dicts) write wrong/zero CRCs even though the deflate data is perfectly intact;
    Python's strict check would otherwise reject them. Scoped + restored, single-threaded use."""
    orig = zipfile.ZipExtFile._update_crc  # type: ignore[attr-defined]  # deliberate
    zipfile.ZipExtFile._update_crc = lambda self, newdata: None  # type: ignore[attr-defined]
    try:
        yield
    finally:
        zipfile.ZipExtFile._update_crc = orig  # type: ignore[attr-defined]  # restore


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


ASSETS = asset("wordlists")  # importlib.resources so the wheel path works too
JLPT_ZIP = ASSETS / "jlpt.zip"

_LEVEL_RANK = {"N1": 1, "N2": 2, "N3": 3, "N4": 4, "N5": 5}


def _iter_meta_banks(zip_path: str | Path):
    """Yield decoded term_meta_bank_*.json lists, skipping banks that fail to read.

    Some Yomitan freq/pitch zips in the wild have Bad-CRC banks (notably some pitch dicts); we
    skip those rather than let one corrupt bank break the whole overlay."""
    with zipfile.ZipFile(zip_path) as zf:
        for name in sorted(zf.namelist()):
            if not (name.startswith("term_meta_bank") and name.endswith(".json")):
                continue
            bank = read_json_bank(zf, name)
            if bank is not None:
                yield bank


def _iter_term_meta(zip_path: str | Path):
    """Yield (term, reading, rank, display) over every ``freq`` entry in a Yomitan zip."""
    for bank in _iter_meta_banks(zip_path):
        for entry in bank:
            if len(entry) < 3 or entry[1] != "freq":
                continue
            term, data = entry[0], entry[2]
            reading, rank, disp = None, None, None
            if isinstance(data, (int, float)):
                rank = int(data)
            elif isinstance(data, dict):
                reading = data.get("reading")
                fval = data.get("frequency", data)
                if isinstance(fval, dict):
                    rank = fval.get("value")
                    disp = fval.get("displayValue")
                elif isinstance(fval, (int, float)):
                    rank = int(fval)
            yield term, reading, rank, disp


def _zip_title(zip_path: str | Path, fallback: str = "") -> str:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return json.loads(zf.read("index.json")).get("title", fallback)
    except Exception:
        return fallback


@dataclass
class JlptDict:
    by_key: dict[str, str]  # term|reading -> level ("N1".."N5"), highest (N1) wins

    @classmethod
    @lru_cache(maxsize=4)
    def load(cls, zip_path: str | Path = JLPT_ZIP) -> JlptDict:
        by_key: dict[str, str] = {}

        def put(key: str | None, level: str) -> None:
            if not key:
                return
            cur = by_key.get(key)
            if cur is None or _LEVEL_RANK[level] < _LEVEL_RANK[cur]:
                by_key[key] = level

        for term, reading, _rank, disp in _iter_term_meta(zip_path):
            if disp in _LEVEL_RANK:
                put(term, disp)
                put(reading, disp)
        return cls(by_key)

    def level(self, *forms: str | None) -> str | None:
        for f in forms:
            if f and f in self.by_key:
                return self.by_key[f]
        return None


@dataclass
class FreqDict:
    by_key: dict[str, int]  # term|reading -> rank (lowest/most-frequent wins)
    title: str = ""

    @classmethod
    @lru_cache(maxsize=4)
    def load(cls, zip_path: str | Path) -> FreqDict:
        by_key: dict[str, int] = {}

        def put(key: str | None, rank: int) -> None:
            if not key or rank <= 0:
                return
            cur = by_key.get(key)
            if cur is None or rank < cur:
                by_key[key] = rank

        for term, reading, rank, _disp in _iter_term_meta(zip_path):
            if isinstance(rank, int):
                put(term, rank)
                put(reading, rank)
        title = ""
        try:
            with zipfile.ZipFile(zip_path) as zf:
                title = json.loads(zf.read("index.json")).get("title", "")
        except Exception:
            log.debug("wordlist source failed to load", exc_info=True)
        return cls(by_key, title)

    def rank(self, *forms: str | None) -> int | None:
        ranks = [self.by_key[f] for f in forms if f and f in self.by_key]
        return min(ranks) if ranks else None

    @staticmethod
    def band(rank: int, top_x: int = 10000, bands: int = 5) -> int | None:
        if rank <= 0 or rank > top_x:
            return None
        return min(bands, max(1, math.ceil(rank / top_x * bands)))


@dataclass
class FreqSource:
    """One frequency dictionary as the tooltip shows it: a title + per-term display string(s).

    Unlike :class:`FreqDict` (which keeps only a min rank for coloring), this keeps the human display
    value SubMiner shows in the pill row — the ``displayValue`` if present (``"8912, 143969㋕"``), else
    the raw rank. A term can have several entries (some freq lists give SUW+LUW → ``12813, 14117``); we join them."""

    title: str
    by_term: dict[str, list[tuple[str | None, str]]]  # term -> [(reading, display), ...]

    @classmethod
    @lru_cache(maxsize=16)
    def load(cls, zip_path: str | Path) -> FreqSource:
        by_term: dict[str, list[tuple[str | None, str]]] = {}
        for term, reading, rank, disp in _iter_term_meta(zip_path):
            display = disp if disp else (str(rank) if rank is not None else None)
            if display is None:
                continue
            by_term.setdefault(term, []).append((reading, display))
        return cls(_zip_title(zip_path, Path(zip_path).stem), by_term)

    def display(self, forms, reading: str | None = None) -> str | None:
        """Display string for the first matching form. Prefer entries whose reading matches the
        token's (disambiguates 本命/ほんめい), else fall back to all entries for that term."""
        for f in forms:
            if not f or f not in self.by_term:
                continue
            ents = self.by_term[f]
            matched = [d for (r, d) in ents if reading is None or r is None or r == reading]
            use = matched or [d for _, d in ents]
            seen: set[str] = set()
            out = [d for d in use if not (d in seen or seen.add(d))]  # type: ignore[func-returns-value]
            return ", ".join(out)
        return None


@dataclass
class PitchSource:
    """A pitch-accent dictionary → the ``reading [positions]`` label the tooltip shows."""

    title: str
    by_key: dict[str, tuple[str, list[int]]]  # term|reading -> (reading, [positions])

    @classmethod
    @lru_cache(maxsize=16)
    def load(cls, zip_path: str | Path) -> PitchSource:
        by_key: dict[str, tuple[str, list[int]]] = {}
        for bank in _iter_meta_banks(zip_path):
            for entry in bank:
                if len(entry) < 3 or entry[1] != "pitch":
                    continue
                term, data = entry[0], entry[2]
                if not isinstance(data, dict):
                    continue
                reading = data.get("reading") or term
                positions: list[int] = [
                    pos
                    for p in data.get("pitches", [])
                    if isinstance(p, dict) and isinstance(pos := p.get("position"), int)
                ]
                if not positions:
                    continue
                val = (reading, positions)
                by_key.setdefault(term, val)
                by_key.setdefault(reading, val)
        return cls(_zip_title(zip_path, Path(zip_path).stem), by_key)

    def accents(self, forms, reading: str | None = None) -> tuple[str, list[int]] | None:
        """Raw (reading, positions) for the first matching form — the pitch-graph input."""
        for f in forms:
            if f and f in self.by_key:
                return self.by_key[f]
        return None

    def display(self, forms, reading: str | None = None) -> str | None:
        got = self.accents(forms, reading)
        if got is None:
            return None
        r, positions = got
        return f"{r} [{','.join(str(p) for p in positions)}]"


_HTML_TAG = re.compile(r"<[^>]+>")
_FURI = re.compile(r"([^\s\[\]]+)\[([^\]]*)\]")  # Anki furigana segment: kanji[reading]

# Reading / furigana fields scanned in addition to the caller's fields, so readings are captured even
# when the note keeps them in a furigana field (Kanji Study `EntryFurigana`, Migaku/Lapis `*Furigana`).
_READING_FIELDS = (
    "Reading",
    "Word Reading",
    "ExpressionReading",
    "EntryFurigana",
    "Furigana",
    "ExpressionFurigana",
    "WordFurigana",
    "Kana",
)


def _field_forms(raw: str) -> list[str]:
    """Word forms a note field contributes to the known set: HTML stripped, and if it's Anki furigana
    (``お 孫[まご]さん``) both the plain surface (お孫さん) and the reading (おまごさん)."""
    val = _HTML_TAG.sub("", raw).strip()
    if not val:
        return []
    if "[" in val and "]" in val:
        surface = re.sub(r"\[[^\]]*\]", "", val).replace(" ", "")
        reading = _FURI.sub(lambda m: m.group(2), val).replace(" ", "")
        return [w for w in {surface, reading} if w]
    return [val]


@dataclass
class KnownWords:
    words: set[str]

    def is_known(self, *forms: str | None) -> bool:
        return any(f in self.words for f in forms if f)

    @classmethod
    def from_set(cls, it) -> KnownWords:
        return cls({w.strip() for w in it if w and w.strip()})

    @classmethod
    def from_ankiconnect(
        cls,
        decks: dict[str, list[str]],
        host: str = "http://127.0.0.1:8765",
        reading_fields=_READING_FIELDS,
    ) -> KnownWords:
        """Build the known set from Anki notes, mirroring SubMiner's decks→fields config. Requested
        fields that don't exist on a note are skipped; furigana fields yield both surface and reading."""
        import urllib.request

        def call(action, **params):
            body = json.dumps({"action": action, "version": 6, "params": params}).encode()
            req = urllib.request.Request(host, body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read()).get("result")

        words: set[str] = set()
        for deck, fields in decks.items():
            ids = call("findNotes", query=f'deck:"{deck}"') or []
            for chunk in (ids[i : i + 500] for i in range(0, len(ids), 500)):
                for note in call("notesInfo", notes=chunk) or []:
                    nf = note.get("fields", {})
                    for fname in list(fields) + list(reading_fields):
                        words.update(_field_forms(nf.get(fname, {}).get("value", "")))
        return cls(words)

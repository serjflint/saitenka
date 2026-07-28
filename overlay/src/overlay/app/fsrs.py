"""In-overlay FSRS knownness snapshot + difficulty estimate.

Two public surfaces:

1. :func:`load_knownness` — reads a **copy** of ``collection.anki2`` (never the live DB) and
   returns a :class:`KnownSnap` that maps ``word → state`` (known / forgotten / learning).
   The FSRS retrievability math is copied verbatim from ``tools/anki_rank_dicts.py`` and
   cross-checked against py-fsrs.

2. :func:`harmonic_rank` / :func:`diff_pill` — blend frequency ranks from multiple Yomitan
   freq dicts using the harmonic-mean formula (identical to ``tools/anki_rank_dicts.py``),
   returning a compact ``Freq("diff", "1333", …)`` pill for the tooltip header row.

Threading note: KnownSnap is read-only once built; ``load_knownness`` is called at launch
(or on snapshot refresh) from the main thread only.
"""

from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from overlay.app.tokenize import _has_kanji, kata_to_hira

if TYPE_CHECKING:
    from overlay.panel import Freq

log = logging.getLogger(__name__)

# FSRS-6 default decay (py-fsrs scheduler.py w20, negated per Anki's convention)
FSRS_DEFAULT_DECAY = 0.1542

# A word is "forgotten" when its retrievability drops below this threshold.
FORGOTTEN_R = 0.85

# Mature interval (days): cards with ivl ≥ this are "known", below = "young".
MATURE_IVL = 21

# Colour for the "diff" pill (medium grey-blue, distinct from the per-source freq pills)
DIFF_COLOR: tuple[int, int, int, int] = (90, 140, 160, 255)


# ---------------------------------------------------------------------------
# FSRS retrievability — verbatim from tools/anki_rank_dicts.py
# ---------------------------------------------------------------------------


def retrievability(
    s: float | None,
    elapsed: float | None,
    decay: float,
) -> float | None:
    """FSRS-6 retrievability: ``(1 + (0.9^(1/|decay|) - 1) * elapsed/s)^decay``.

    Matches ``py-fsrs card.py:232`` exactly.  Returns ``None`` for degenerate inputs
    (new/learning cards have no meaningful retrievability).
    """
    if s is None or s <= 0 or elapsed is None or elapsed < 0:
        return None
    factor = 0.9 ** (1.0 / decay) - 1.0
    return (1.0 + factor * elapsed / s) ** decay


# ---------------------------------------------------------------------------
# Text-cleaning helpers (minimal subset of anki_rank_dicts.py)
# ---------------------------------------------------------------------------

_JP = re.compile(r"[぀-ヿ㐀-鿿豈-﫿]")
_KANA_RUN = re.compile(r"[ぁ-ゟァ-ヿーｦ-ﾟ・〜]+")
_SENTENCE_MARKS = "。、！？…「」『』（）\n\t"
_TERM_FIELDS = {
    "expression",
    "word",
    "vocab",
    "vocabkanji",
    "kanji",
    "単語",
    "japanese",
    "target",
    "term",
    "vocabulary",
    "characters",
    "front",
}
_READING_FIELDS = {
    "expressionreading",
    "reading",
    "kana",
    "hiragana",
    "yomikata",
    "yomi",
    "読み",
    "expressionfurigana",
    "vocabfurigana",
    "furigana",
}


def _strip_markup(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", "", s)
    for z in ("\u200b", "﻿", "‎", "‏"):  # noqa: PLE2502  # this line IS the strip-list of invisible/bidi chars
        s = s.replace(z, "")
    return s.strip()


def _term_base(s: str) -> str:
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = re.sub(r"[［（【〈《][^］）】〉》]*[］）】〉》]", "", s)
    return s.replace(" ", "").replace("　", "").strip()


def _to_reading(s: str) -> str:
    s = re.sub(r"[^\[\]\s]*\[([^\]]*)\]", r"\1", s).replace(" ", "").replace("　", "")
    m = _KANA_RUN.match(s)
    return m.group(0) if m else ""


def _wordlike(t: str) -> bool:
    return (
        bool(t)
        and bool(_JP.search(t))
        and len(t) <= 12
        and not any(c in t for c in _SENTENCE_MARKS)
        and "  " not in t
    )


# ---------------------------------------------------------------------------
# KnownSnap — the knownness snapshot returned to the Scorer
# ---------------------------------------------------------------------------


@dataclass
class KnownSnap:
    """Read-only reading-aware knownness snapshot. ``by_surface`` maps each written surface to the
    ``{reading|"" : state}`` its cards teach; ``readings`` maps each reading to its best state, for
    kana-only tokens. Matching keys on BOTH surface and reading, so a kanji token whose card teaches a
    *different* reading (床 とこ vs a known 床/ゆか card) doesn't inherit its state."""

    by_surface: dict[str, dict[str, str]]  # surface -> {reading|"" : state}
    readings: dict[str, str]  # reading -> best state

    def state(
        self, surface: str | None, lemma: str | None = None, reading: str | None = None
    ) -> str | None:
        """State for the best-matching (surface, reading), or None if not in the snapshot."""
        read = kata_to_hira(reading) if reading else ""
        return self._surface_state(surface, lemma, read) or self._kana_state(surface, read)

    def _surface_state(self, surface: str | None, lemma: str | None, read: str) -> str | None:
        """State of the matching surface/lemma spelling; a spelling taught only with a *different*
        reading is a same-spelling homograph — skipped, so it doesn't inherit the state."""
        for spelling in (surface, lemma):
            taught = self.by_surface.get(spelling) if spelling else None
            if not taught:
                continue
            if read and read in taught:
                return taught[read]
            if not read:
                return _best_state(taught.values())
            if "" in taught:
                return taught[""]
        return None

    def _kana_state(self, surface: str | None, read: str) -> str | None:
        """A kana-only token matches any taught reading (kana↔kanji)."""
        if not surface or _has_kanji(surface):
            return None
        if read and read in self.readings:
            return self.readings[read]
        return self.readings.get(surface)

    def is_known(
        self, surface: str | None, lemma: str | None = None, reading: str | None = None
    ) -> bool:
        return self.state(surface, lemma, reading) == "known"

    def is_forgotten(
        self, surface: str | None, lemma: str | None = None, reading: str | None = None
    ) -> bool:
        return self.state(surface, lemma, reading) == "forgotten"

    @classmethod
    def of(cls, word_states: dict[str, str]) -> KnownSnap:
        """Build from bare word→state (no reading pairing) — the empty snap and tests."""
        return cls({w: {"": st} for w, st in word_states.items()}, {})


_EMPTY_SNAP = KnownSnap({}, {})


# ---------------------------------------------------------------------------
# load_knownness — reads a COPY of collection.anki2
# ---------------------------------------------------------------------------


def load_knownness(
    path: str | Path,
    *,
    forgotten_r: float = FORGOTTEN_R,
    mature_ivl: int = MATURE_IVL,
    decay_override: float | None = None,
) -> KnownSnap:
    """Build a :class:`KnownSnap` from a collection.anki2 copy.

    Never opens the live Anki database — must be called on a snapshot copy.
    Returns an empty :class:`KnownSnap` if the file is missing or unreadable.
    """
    path = Path(path)
    if not path.exists():
        log.debug("fsrs: collection not found at %s — returning empty snapshot", path)
        return _EMPTY_SNAP

    try:
        return _load(
            path, forgotten_r=forgotten_r, mature_ivl=mature_ivl, decay_override=decay_override
        )
    except Exception:
        log.debug("fsrs: failed to load collection %s", path, exc_info=True)
        return _EMPTY_SNAP


def _load(
    path: Path,
    *,
    forgotten_r: float,
    mature_ivl: int,
    decay_override: float | None,
) -> KnownSnap:
    """The actual loader — raises on DB errors so the caller can catch gracefully."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.text_factory = str
    try:
        return _read(
            con, forgotten_r=forgotten_r, mature_ivl=mature_ivl, decay_override=decay_override
        )
    finally:
        con.close()


def _parse_card_json(data: str | None) -> tuple[float | None, float | None]:
    if not data:
        return None, None
    try:
        j = json.loads(data)
        return j.get("s"), j.get("decay")
    except (json.JSONDecodeError, AttributeError):  # best-effort parse - fall back to a default
        return None, None


def _classify_state(
    ctype: int, r: float | None, ivl: int, forgotten_r: float, mature_ivl: int
) -> str:
    """``ctype``: 0=new, 1=lrn, 2=rev, 3=relearn."""
    match ctype:
        case 0:
            return "new"
        case 1 | 3:
            return "learning"
        case _ if r is not None and r < forgotten_r:
            return "forgotten"
        case _ if ivl and ivl >= mature_ivl:
            return "known"
        case _:
            return "young"


def _build_card_info(
    con: sqlite3.Connection,
    last_rev: dict[int, int],
    now_ms: float,
    *,
    forgotten_r: float,
    mature_ivl: int,
    decay_override: float | None,
) -> dict[int, dict]:
    """nid → best card info (``{"st": state, "k": knowledge_score}``) — when a note has more than
    one card, the highest-knowledge card wins."""
    card_info: dict[int, dict] = {}
    for cid, nid, ctype, ivl, _queue, data in con.execute(
        "SELECT id,nid,type,ivl,queue,data FROM cards"
    ):
        s, d_card = _parse_card_json(data)
        decay = decay_override or (-d_card if d_card else FSRS_DEFAULT_DECAY)
        elapsed = (now_ms - last_rev[cid]) / 86_400_000.0 if cid in last_rev else None
        r = retrievability(s, elapsed, decay) if elapsed is not None else None
        st = _classify_state(ctype, r, ivl, forgotten_r, mature_ivl)
        # knowledge score for deduplication (higher = better)
        k = (s or 0.0) * (r if r is not None else 1.0) if ctype == 2 else 0.0
        # keep best card per note
        prev = card_info.get(nid)
        if prev is None or k > prev["k"]:
            card_info[nid] = {"st": st, "k": k}
    return card_info


def _read_field_names(con: sqlite3.Connection) -> dict[int, list[str]]:
    """ntid → [fname_ord0, fname_ord1, …] from the ``fields`` table (Anki 2.1 schema). Empty on an
    older schema / missing table — callers fall back to scanning every field by content instead."""
    field_names: dict[int, list[str]] = {}
    try:
        for ntid, _ord, name in con.execute(
            "SELECT ntid, ord, name FROM fields ORDER BY ntid, ord"
        ):
            field_names.setdefault(ntid, []).append(name.lower())
    except sqlite3.Error:  # older schema or missing table — fall back
        pass
    return field_names


def _fallback_term_scan(parts: list[str], reading: str) -> tuple[str, str]:
    """Scan every field for the first word-like value, taking the next field as its reading."""
    for i, p in enumerate(parts):
        cleaned = _strip_markup(p)
        base = _term_base(cleaned)
        if _wordlike(base):
            if i + 1 < len(parts):  # next field might be reading
                reading = _to_reading(_strip_markup(parts[i + 1]))
            return base, reading
    return "", reading


def _extract_term_reading(names: list[str], parts: list[str]) -> tuple[str, str]:
    """(term, reading) for one note: match known field names (``_TERM_FIELDS``/``_READING_FIELDS``)
    at their mapped position; when no field map is available (or none matched), scan every field
    for the first word-like value and take the next field as its reading."""
    term = reading = ""
    for i, fname in enumerate(names):
        val = parts[i] if i < len(parts) else ""
        cleaned = _strip_markup(val)
        base = _term_base(cleaned)
        if fname in _TERM_FIELDS and not term and _wordlike(base):
            term = base
        if fname in _READING_FIELDS and not reading:
            reading = _to_reading(cleaned)

    if not term and parts:
        term, reading = _fallback_term_scan(parts, reading)
    return term, reading


# Prefer states with higher priority: known > forgotten > young > learning.
_STATE_PRIORITY = {"known": 4, "forgotten": 3, "young": 2, "learning": 1}


def _record_state(states: dict[str, str], key: str, st: str) -> None:
    cur = states.get(key)
    if cur is None or _STATE_PRIORITY.get(st, 0) > _STATE_PRIORITY.get(cur, 0):
        states[key] = st


def _best_state(states) -> str | None:
    """Highest-priority state among the readings taught for one surface (used when a token gives no
    reading to disambiguate). known > forgotten > young > learning."""
    best: str | None = None
    for st in states:
        if best is None or _STATE_PRIORITY.get(st, 0) > _STATE_PRIORITY.get(best, 0):
            best = st
    return best


def _build_states(
    con: sqlite3.Connection, card_info: dict[int, dict], field_names: dict[int, list[str]]
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """(by_surface, readings), scanning every note whose best card isn't "new". The term is keyed to
    its taught reading (``""`` when none) so a same-spelling homograph with a different reading doesn't
    inherit the state; the reading is also indexed on its own for kana-only tokens."""
    by_surface: dict[str, dict[str, str]] = {}
    readings: dict[str, str] = {}
    for nid, mid, flds in con.execute("SELECT id, mid, flds FROM notes"):
        info = card_info.get(nid)
        if info is None or info["st"] == "new":
            continue  # new cards don't appear in the snapshot

        term, reading = _extract_term_reading(field_names.get(mid, []), flds.split("\x1f"))
        if not term:
            continue

        st = info["st"]
        reading = kata_to_hira(reading) if reading else ""
        _record_state(by_surface.setdefault(term, {}), reading, st)
        if reading:
            _record_state(readings, reading, st)
    return by_surface, readings


def _read(
    con: sqlite3.Connection,
    *,
    forgotten_r: float,
    mature_ivl: int,
    decay_override: float | None,
) -> KnownSnap:
    now_ms = time.time() * 1000.0
    last_rev: dict[int, int] = dict(con.execute("SELECT cid, MAX(id) FROM revlog GROUP BY cid"))
    card_info = _build_card_info(
        con,
        last_rev,
        now_ms,
        forgotten_r=forgotten_r,
        mature_ivl=mature_ivl,
        decay_override=decay_override,
    )
    field_names = _read_field_names(con)
    by_surface, readings = _build_states(con, card_info, field_names)
    return KnownSnap(by_surface, readings)


# ---------------------------------------------------------------------------
# Difficulty pill — harmonic-mean frequency rank
# ---------------------------------------------------------------------------


def harmonic_rank(
    word: str,
    freq_dicts: list[dict[str, int]],
) -> float | None:
    """Harmonic mean of ``word``'s rank across all dicts that contain it.

    Matches the blend used by ``tools/anki_rank_dicts.py:harmonic()``.
    Returns ``None`` if the word appears in no dict.
    """
    ranks = [d[word] for d in freq_dicts if word in d]
    if not ranks:
        return None
    return len(ranks) / sum(1.0 / r for r in ranks)


def diff_pill(rank: float | None) -> Freq | None:
    """A ``Freq("diff", …, DIFF_COLOR)`` pill for the harmonic-blended difficulty rank.

    Returns ``None`` when ``rank`` is ``None`` so the caller can skip it cleanly.
    """
    if rank is None:
        return None
    from overlay.panel import Freq as _Freq

    r = round(rank)
    value = (f"{r // 1000}k" if r % 1000 == 0 else f"{r / 1000:.1f}k") if r >= 1000 else str(r)
    return _Freq(name="diff", value=value, color=DIFF_COLOR)

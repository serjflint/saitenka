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
    """Read-only knownness snapshot: word → state (known / forgotten / learning / None)."""

    _states: dict[str, str]  # word → "known" | "forgotten" | "young" | "learning"

    def state(self, *forms: str | None) -> str | None:
        """State for the best-matching form, or None if not in the snapshot."""
        for f in forms:
            if f and f in self._states:
                return self._states[f]
        return None

    def is_known(self, *forms: str | None) -> bool:
        return self.state(*forms) == "known"

    def is_forgotten(self, *forms: str | None) -> bool:
        return self.state(*forms) == "forgotten"


_EMPTY_SNAP = KnownSnap(_states={})


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
    except Exception:  # best-effort parse - fall back to a default
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
    except Exception:  # noqa: S110  # best-effort parse - fall back to a default
        pass  # older schema or missing table — fall back
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


def _build_states(
    con: sqlite3.Connection, card_info: dict[int, dict], field_names: dict[int, list[str]]
) -> dict[str, str]:
    """word → best state, scanning every note whose best card isn't "new". Both the term and (if
    distinct) its reading are recorded, so either form resolves via :meth:`KnownSnap.state`."""
    states: dict[str, str] = {}
    for nid, mid, flds in con.execute("SELECT id, mid, flds FROM notes"):
        info = card_info.get(nid)
        if info is None or info["st"] == "new":
            continue  # new cards don't appear in the snapshot

        term, reading = _extract_term_reading(field_names.get(mid, []), flds.split("\x1f"))
        if not term:
            continue

        st = info["st"]
        _record_state(states, term, st)
        if reading and reading != term:
            _record_state(states, reading, st)
    return states


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
    states = _build_states(con, card_info, field_names)
    return KnownSnap(_states=states)


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

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
    for z in ("​", "﻿", "‎", "‏"):
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


def _read(
    con: sqlite3.Connection,
    *,
    forgotten_r: float,
    mature_ivl: int,
    decay_override: float | None,
) -> KnownSnap:
    now_ms = time.time() * 1000.0

    # Build last-review timestamp per card
    last_rev: dict[int, int] = dict(con.execute("SELECT cid, MAX(id) FROM revlog GROUP BY cid"))

    # Card data: type (0=new,1=lrn,2=rev,3=relearn), interval, FSRS data
    card_info: dict[int, dict] = {}  # nid → best card info
    for cid, nid, ctype, ivl, _queue, data in con.execute(
        "SELECT id,nid,type,ivl,queue,data FROM cards"
    ):
        s = d_card = None
        if data:
            try:
                j = json.loads(data)
                s = j.get("s")
                d_card = j.get("decay")
            except Exception:
                pass
        decay = decay_override or (-d_card if d_card else FSRS_DEFAULT_DECAY)
        elapsed = (now_ms - last_rev[cid]) / 86_400_000.0 if cid in last_rev else None
        r = retrievability(s, elapsed, decay) if elapsed is not None else None
        # classify
        if ctype == 0:
            st = "new"
        elif ctype in (1, 3):
            st = "learning"
        elif r is not None and r < forgotten_r:
            st = "forgotten"
        elif ivl and ivl >= mature_ivl:
            st = "known"
        else:
            st = "young"
        # knowledge score for deduplication (higher = better)
        k = (s or 0.0) * (r if r is not None else 1.0) if ctype == 2 else 0.0
        # keep best card per note
        prev = card_info.get(nid)
        if prev is None or k > prev["k"]:
            card_info[nid] = {"st": st, "k": k}

    # Map notes → words
    # We need note fields: field order from the `fields` table (Anki 2.1 schema)
    # Fall back to splitting on \x1f and trying TERM_FIELDS / READING_FIELDS by name.
    field_names: dict[int, list[str]] = {}  # ntid → [fname_ord0, fname_ord1, …]
    try:
        for ntid, _ord, name in con.execute(
            "SELECT ntid, ord, name FROM fields ORDER BY ntid, ord"
        ):
            field_names.setdefault(ntid, []).append(name.lower())
    except Exception:
        pass  # older schema or missing table — fall back

    states: dict[str, str] = {}

    for nid, mid, flds in con.execute("SELECT id, mid, flds FROM notes"):
        info = card_info.get(nid)
        if info is None or info["st"] == "new":
            continue  # new cards don't appear in the snapshot

        parts = flds.split("\x1f")
        names = field_names.get(mid, [])

        # Find term and reading fields by position
        term = reading = ""
        for i, fname in enumerate(names):
            val = parts[i] if i < len(parts) else ""
            cleaned = _strip_markup(val)
            base = _term_base(cleaned)
            if fname in _TERM_FIELDS and not term and _wordlike(base):
                term = base
            if fname in _READING_FIELDS and not reading:
                reading = _to_reading(cleaned)

        # Fallback: if no field map, scan all fields
        if not term and parts:
            for i, p in enumerate(parts):
                cleaned = _strip_markup(p)
                base = _term_base(cleaned)
                if _wordlike(base):
                    term = base
                    # next field might be reading
                    if i + 1 < len(parts):
                        reading = _to_reading(_strip_markup(parts[i + 1]))
                    break

        if not term:
            continue

        st = info["st"]
        # Prefer states with higher priority: known > forgotten > young > learning
        _priority = {"known": 4, "forgotten": 3, "young": 2, "learning": 1}
        cur_st = states.get(term)
        if cur_st is None or _priority.get(st, 0) > _priority.get(cur_st, 0):
            states[term] = st
        if reading and reading != term:
            cur_st_r = states.get(reading)
            if cur_st_r is None or _priority.get(st, 0) > _priority.get(cur_st_r, 0):
                states[reading] = st

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

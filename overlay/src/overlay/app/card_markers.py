"""Yomitan-style ``{marker}`` templates for mined cards ([mine.card_format]).

``build_markers`` turns the data a mine already produces (card fields, sentence, media, freq, pitch)
into a ``marker -> value`` map using Yomitan's marker names; ``render_card_format`` substitutes those
into a per-field template. Every marker is filled from real data (readings/pitch from dictionaries) —
markers Saitenka can't yet ground (word ``audio`` (#93), ``pitch-accent-graphs``, ``sentence-furigana``)
are deliberately NOT in :data:`MARKERS`, so the doctor flags them instead of shipping an empty field.

No import of :mod:`overlay.app.anki` — that module imports this one for ``build_note``'s template path,
so the cloze markers read the already-bolded ``sentence_html`` (its lone real ``<b>`` is the surface)
rather than re-bolding here.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from overlay.app.lookup import _is_kana

if TYPE_CHECKING:
    from overlay.app.lookup import CardData

log = logging.getLogger(__name__)

# The markers Saitenka can fill from real data. The doctor validates a [mine.card_format] template's
# {markers} against this set; anything else won't render, so warn rather than ship an empty field.
MARKERS = frozenset(
    {
        "expression",
        "reading",
        "furigana",
        "glossary",
        "glossary-plain",
        "sentence",
        "cloze-prefix",
        "cloze-body",
        "cloze-suffix",
        "screenshot",
        "sentence-audio",
        "frequencies",
        "frequency-rank",
        "pitch-accents",
        "pitch-accent-positions",
        "part-of-speech",
        "document-title",
        "misc",
        "ent-seq",
        "tags",
    }
)

# \w (not just [a-z]) so a miscased/typo marker like {Reading} is captured too — it then renders empty
# with a warning and the doctor flags it, instead of landing on the card as literal "{Reading}" text.
_MARKER_RE = re.compile(r"\{([\w-]+)\}")


def markers_in(template: str) -> set[str]:
    """The ``{marker}`` names referenced in one template string (for doctor validation)."""
    return set(_MARKER_RE.findall(template))


def _kana_runs(expression: str) -> list[tuple[bool, str]]:
    """``expression`` as maximal ``(is_kana, text)`` runs, e.g. 話し合う → [(F,話),(T,し),(F,合),(T,う)]."""
    runs: list[tuple[bool, str]] = []
    for ch in expression:
        k = _is_kana(ch)
        if runs and runs[-1][0] == k:
            runs[-1] = (k, runs[-1][1] + ch)
        else:
            runs.append((k, ch))
    return runs


def _simple_furigana(expression: str, reading: str) -> str:
    """Head/tail okurigana strip → one bracketed kanji core (the fallback when segmenting can't align):
    ``読[よ]む``, ``小僧[こぞう]``, ``お 前[まえ]`` (space so the reading binds to 前, not お)."""
    s, r = expression, reading
    tail = ""
    while s and r and s[-1] == r[-1] and _is_kana(s[-1]):
        tail = s[-1] + tail
        s, r = s[:-1], r[:-1]
    head = ""
    while s and r and s[0] == r[0] and _is_kana(s[0]):
        head += s[0]
        s, r = s[1:], r[1:]
    if not s:  # fully reduced to matching kana — nothing to annotate
        return expression
    core = f"{s}[{r}]" if r else s
    return f"{head} {core}{tail}" if head else f"{core}{tail}"


def _emit_run(
    run: tuple[bool, str], reading: str, ri: int, nxt: str, *, first: bool
) -> tuple[str, int] | None:
    """One expression run against the reading cursor: ``(emitted, new_ri)``, or ``None`` when it can't
    align (a kana anchor mismatch / no room for a kanji reading). Kana runs echo through; a kanji run
    takes the reading up to the next kana anchor and gets a leading space unless it's first."""
    is_kana, text = run
    if is_kana:
        return (text, ri + len(text)) if reading[ri : ri + len(text)] == text else None
    end = reading.find(nxt, ri) if nxt else len(reading)
    if end <= ri:  # nxt not found (find → -1 ≤ ri) or empty kanji reading → can't align
        return None
    core = f"{text}[{reading[ri:end]}]"
    return (core if first else f" {core}"), end


def anki_furigana(expression: str, reading: str) -> str:
    """Anki furigana bracket form. Segments the word by kana/kanji runs and binds the reading between
    kana anchors to each kanji run — so interior okurigana aligns: 話し合う/はなしあう → ``話[はな]し 合[あ]う``
    (not the whole-core ``話し合[はなしあ]う``). Falls back to :func:`_simple_furigana` whenever a run can't
    align (or the reading isn't fully consumed), so output is never worse than the head/tail approximation."""
    if not expression:
        return reading
    if not reading or expression == reading:
        return expression
    runs = _kana_runs(expression)
    out: list[str] = []
    ri = 0
    for idx, run in enumerate(runs):
        nxt = runs[idx + 1][1] if idx + 1 < len(runs) else ""  # the next run is always kana
        res = _emit_run(run, reading, ri, nxt, first=not out)
        if res is None:
            return _simple_furigana(expression, reading)
        emitted, ri = res
        out.append(emitted)
    return "".join(out) if ri == len(reading) else _simple_furigana(expression, reading)


def _cloze(sentence_html: str) -> tuple[str, str, str]:
    """``(prefix, body, suffix)`` split around the bolded mined surface. ``bold_word`` HTML-escapes the
    sentence before wrapping the surface, so the only real ``<b>…</b>`` is that surface. No bold (surface
    absent) → the whole sentence is the prefix, body/suffix empty."""
    i, j = sentence_html.find("<b>"), sentence_html.find("</b>")
    if i < 0 or j < i:
        return sentence_html, "", ""
    return sentence_html[:i], sentence_html[i + 3 : j], sentence_html[j + 4 :]


def _img(name: str) -> str:
    return f'<img src="{name}">' if name else ""


def _sound(name: str) -> str:
    return f"[sound:{name}]" if name else ""


def build_markers(
    card: CardData,
    *,
    sentence_html: str,
    picture: str,
    audio: str,
    misc: str,
    doc_title: str,
    freq_html: str,
    freq_rank: str,
    pos_en: str,
    tags: tuple[str, ...] | list[str],
    pitch_html: str = "",
    pitch_positions: str = "",
) -> dict[str, str]:
    """The ``marker -> value`` map for a mine. ``pitch_*`` are passed in (the caller has ``dict_set``);
    they default to empty so a caller without pitch still produces a valid map. Media markers carry the
    Anki-ready wrappers (``<img src>`` / ``[sound:]``) so a bare ``{screenshot}`` renders the image."""
    prefix, body, suffix = _cloze(sentence_html)
    return {
        "expression": card.expression,
        "reading": card.reading,
        "furigana": anki_furigana(card.expression, card.reading),
        "glossary": card.glossary_html,
        "glossary-plain": "; ".join(card.glosses),
        "sentence": sentence_html,
        "cloze-prefix": prefix,
        "cloze-body": body,
        "cloze-suffix": suffix,
        "screenshot": _img(picture),
        "sentence-audio": _sound(audio),
        "frequencies": freq_html,
        "frequency-rank": freq_rank,
        "pitch-accents": pitch_html,
        "pitch-accent-positions": pitch_positions,
        "part-of-speech": pos_en,
        "document-title": doc_title,
        "misc": misc,
        "ent-seq": card.idseq,
        "tags": " ".join(tags),
    }


def render_card_format(card_format: dict[str, str], markers: dict[str, str]) -> dict[str, str]:
    """Render each ``field: template`` by substituting ``{marker}`` from ``markers``. An unknown marker
    renders empty (the doctor is the loud check). Regex substitution — a stray ``{`` in content survives
    (``str.format`` would raise)."""

    def sub(template: str) -> str:
        def repl(m: re.Match) -> str:
            name = m.group(1)
            if name not in markers:
                log.warning("[mine.card_format] uses unknown marker {%s} — rendered empty", name)
                return ""
            return markers[name]

        return _MARKER_RE.sub(repl, template)

    return {field: sub(template) for field, template in card_format.items()}

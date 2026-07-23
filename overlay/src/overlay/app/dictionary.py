"""Yomitan term-bank dictionaries → an ordered multi-dictionary lookup (the Yomitan experience).

Each :class:`Dictionary` is a **read-only view of one imported dictionary** inside the consolidated
:class:`~overlay.app.dictdb.DictionaryDb` (scoped by ``dict_id``) — the dictionaries were built into that
DB **once, at import time**; nothing is parsed or rebuilt here. A word is looked up across an **ordered
list** of them, building a panel :class:`Entry` with one dictionary section per source. The structured-
content walker + panel renderer draw the dict-name pills and rich glossaries.

Term-bank v3 entry: ``[term, reading, defTags, rules, score, glossary[], sequence, termTags]``. A glossary
item is a plain string or ``{"type": "structured-content", "content": <node>}`` (also ``image``/``text``).
The dictionary form comes from the tokenizer lemma; the optional ``saitenka-overlay-deinflect``
add-on (GPL-3.0, Yomitan-derived) supplies the inflection chain when installed.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec.json as msgspec_json

from overlay import otel_metrics

try:
    # Optional GPL-3.0 add-on (derived from Yomitan). When installed, the panel shows the
    # inflection chain (🧩 -て « -いる « -た); without it the chain is empty and nothing is drawn.
    from saitenka_deinflect import inflection_chain
except ImportError:  # pragma: no cover — exercised via the deinflect-absent path

    def inflection_chain(surface: str, *targets: str) -> list[str]:
        return []


from overlay.app.lookup import CardData, furigana
from overlay.app.tokenize import Token
from overlay.app.wordlists import FreqSource, PitchSource
from overlay.panel import Definition, Entry, Freq

if TYPE_CHECKING:
    from overlay.app.dictdb import DictionaryDb, DictRow

log = logging.getLogger(__name__)


class DictionaryError(RuntimeError):
    """A requested dictionary can't be used (e.g. a configured title was never imported)."""


_MISSING_HINT = (
    "These are dictionary TITLES with no imported dictionary. Import the source .zip files first: "
    "`saitenka-overlay import <dir-with-zips>` (or `import-settings <settings.json> --scan-dir <dir>`), "
    "then they resolve by title. Run `saitenka-overlay doctor` to see what's imported."
)


def split_existing(paths: Sequence[str | Path]) -> tuple[list[str], list[str]]:
    """Partition ``paths`` into (existing, missing) files — used by the import command to keep the
    zips that exist and report the rest, rather than raising a raw ``FileNotFoundError``."""
    existing: list[str] = []
    missing: list[str] = []
    for p in paths:
        (existing if Path(str(p)).expanduser().exists() else missing).append(str(p))
    return existing, missing


FREQ_COLOR = (74, 158, 92, 255)  # green pill, like SubMiner's frequency row
PITCH_COLOR = (126, 96, 168, 255)  # purple pill, for pitch-accent dicts


def _short_freq_name(title: str) -> str:
    """Freq-pill display name: strip the ``Saitenka`` product prefix (``Saitenka Known`` → ``Known``)
    so our own frequency lists don't waste pill width. Case-insensitive; other dicts pass through."""
    for prefix in ("Saitenka ", "saitenka-"):
        if title.lower().startswith(prefix.lower()):
            return title[len(prefix) :]
    return title


@dataclass
class DictEntry:
    term: str
    reading: str
    glossary: list
    tags: str = ""
    # The exact JSON text the glossary was decoded from — kept so a dedup key (see
    # DictionarySet.entry_for) can compare entries by their *source* bytes instead of re-encoding the
    # already-decoded glossary, which is expensive for large monolingual entries.
    raw_glossary: str = ""


def _to_glob(pattern: str) -> str:
    """Normalise a user wildcard pattern to a SQLite GLOB pattern: fullwidth ＊/？ → ASCII ``*``/``?``."""
    return pattern.replace("＊", "*").replace("？", "?")


def _glosses_of(glossary: list) -> list[str]:
    """Every glossary item flattened to plain text (SC/HTML stripped, whitespace collapsed)."""
    from overlay.sc.walk import _text_of

    out: list[str] = []
    for it in glossary:
        text = (
            it
            if isinstance(it, str)
            else (
                _text_of(it.get("content"))
                if isinstance(it, dict) and it.get("type") == "structured-content"
                else it.get("text", "")
                if isinstance(it, dict)
                else ""
            )
        )
        text = re.sub(r"\s+", " ", text or "").strip()
        if text:
            out.append(text)
    return out


def _first_gloss(glossary: list, limit: int = 40) -> str:
    """A short plain-text first-gloss preview for a search-result row (strips SC/HTML, truncates)."""
    glosses = _glosses_of(glossary)
    if not glosses:
        return ""
    return glosses[0][:limit] + ("…" if len(glosses[0]) > limit else "")


def _glossary_to_nodes(glossary: list) -> list:
    """Flatten a term's glossary items into structured-content nodes the walker understands."""
    nodes: list = []
    for it in glossary:
        if isinstance(it, str):
            nodes.append(it)
        elif isinstance(it, dict):
            t = it.get("type")
            if t == "structured-content":
                nodes.append(it.get("content"))
            elif t == "text":
                nodes.append(it.get("text", ""))
            elif t == "image":
                nodes.append({"tag": "img", "path": it.get("path", "")})
            else:
                nodes.append(it)
    return nodes


class Dictionary:
    """A read-only view of one imported dictionary inside the consolidated DB (scoped by ``dict_id``)."""

    def __init__(self, db: DictionaryDb, row: DictRow):
        self.db = db
        self.dict_id = row.id
        self.title = row.title
        self._tags: dict | None = None  # defTag code -> [display_name, order]; loaded lazily
        # LRU cache of decoded entries (entries.id -> DictEntry), below the panel-cache layer so a
        # re-lookup of the same word survives panel-cache eviction without re-decoding its glossary —
        # decoding a large monolingual entry's JSON was the single biggest cost in a --stress profile
        # (51% of samples), and it's pure repeat work for words already seen this session. Bounded, not
        # unlimited, so it doesn't grow forever over a long session (`[dictdb].entry_cache_max`).
        self._entry_cache: OrderedDict[int, DictEntry] = OrderedDict()
        self._entry_cache_max = db._opts.entry_cache_max

    @property
    def tags(self) -> dict:
        if self._tags is None:
            rows = self.db._conn().execute(
                "SELECT code, name, ord FROM tags WHERE dict_id=?", (self.dict_id,)
            )
            self._tags = {code: [name, order] for code, name, order in rows}
        return self._tags

    def kanji_lookup(self, char: str) -> dict | None:
        """The kanji_bank entry for one ideograph, or None."""
        row = (
            self.db._conn()
            .execute(
                "SELECT chr, onyomi, kunyomi, tags, meanings, stats FROM kanji "
                "WHERE dict_id=? AND chr=?",
                (self.dict_id, char),
            )
            .fetchone()
        )
        if row is None:
            return None
        return {
            "char": row[0],
            "onyomi": row[1],
            "kunyomi": row[2],
            "tags": row[3],
            "meanings": msgspec_json.decode(row[4] or "[]"),
            "stats": msgspec_json.decode(row[5] or "{}"),
        }

    def resolve_deftags(self, deftags: str) -> list[str]:
        """defTags string (``★ priority\xa0form``) → display names, ordered as Yomitan shows them."""
        if not deftags:
            return []
        out = []
        for tok in deftags.split(" "):
            info = self.tags.get(tok)
            name = (info[0] if info else tok).replace("\xa0", " ")
            order = info[1] if info else 999
            out.append((order, name))
        out.sort()
        return [n for _, n in out]

    def lookup(
        self, *forms: str | None, wildcard: bool = False, limit: int = 50
    ) -> list[DictEntry]:
        """Look terms/readings up in this dictionary. With ``wildcard`` the forms are GLOB patterns
        (``*`` = any run, ``?`` = one char; fullwidth ＊/？ normalised) capped at ``limit`` rows — a
        prefix pattern (``たべ*``) uses the key index; a leading-wildcard suffix scan is
        LIMIT-bounded."""
        formset = {f for f in forms if f}
        seen: set[int] = set()
        out: list[DictEntry] = []
        conn = self.db._conn()
        did = self.dict_id
        # Wildcard forms GLOB the key column, capping DISTINCT entry ids (a term keys itself twice —
        # by term AND reading — so a raw key LIMIT would under-count entries after dedup).
        exact_q = (
            "SELECT e.id, e.term, e.reading, e.glossary, e.tags FROM keys k "
            "JOIN entries e ON k.dict_id = e.dict_id AND k.id = e.id "
            "WHERE k.dict_id = ? AND k.key = ?"
        )
        glob_q = (
            "SELECT e.id, e.term, e.reading, e.glossary, e.tags FROM entries e "
            "WHERE e.dict_id = ? AND e.id IN "
            "(SELECT DISTINCT id FROM keys WHERE dict_id = ? AND key GLOB ? LIMIT ?)"
        )
        for f in forms:
            if not f:
                continue
            with otel_metrics.instrumented(
                otel_metrics.dict_sql_duration_ms, "dict_sql", dict=self.title
            ):
                cursor = (
                    conn.execute(glob_q, (did, did, _to_glob(f), limit))
                    if wildcard
                    else conn.execute(exact_q, (did, f))
                )
                rows = cursor.fetchall()
            for row in rows:
                eid = row[0]
                if eid in seen:
                    continue
                seen.add(eid)
                cached = self._entry_cache.get(eid)
                if cached is not None:
                    self._entry_cache.move_to_end(eid)
                    out.append(cached)
                    if otel_metrics.dict_cache_hits is not None:
                        otel_metrics.dict_cache_hits.add(1)
                else:
                    if otel_metrics.dict_cache_misses is not None:
                        otel_metrics.dict_cache_misses.add(1)
                    entry = DictEntry(
                        row[1], row[2], msgspec_json.decode(row[3]), row[4], raw_glossary=row[3]
                    )
                    self._entry_cache[eid] = entry
                    if len(self._entry_cache) > self._entry_cache_max:
                        self._entry_cache.popitem(last=False)
                    out.append(entry)
                if wildcard and len(out) >= limit:
                    break
        # Rank exact-term (headword) matches above reading-only ones, like Yomitan — so a common
        # particle の (term=の) wins over an obscure kanji that merely *reads* の (箆/の). Stable, so
        # dict order and same-rank ties are preserved.
        out.sort(key=lambda e: e.term not in formset)
        return out


@dataclass
class DictionarySet:
    dicts: list[Dictionary]
    freqs: list[FreqSource] = field(default_factory=list)
    pitches: list[PitchSource] = field(default_factory=list)

    @classmethod
    def from_rows(
        cls,
        db: DictionaryDb,
        dict_rows: Sequence[DictRow],
        freq_rows: Sequence[DictRow] = (),
        pitch_rows: Sequence[DictRow] = (),
    ) -> DictionarySet:
        """Build an ordered dictionary set from already-resolved :class:`DictRow`s of the given DB."""
        return cls(
            dicts=[Dictionary(db, r) for r in dict_rows],
            freqs=[FreqSource(db, r) for r in freq_rows],
            pitches=[PitchSource(db, r) for r in pitch_rows],
        )

    @classmethod
    def from_db(
        cls,
        db: DictionaryDb,
        dict_titles: Sequence[str] = (),
        freq_titles: Sequence[str] = (),
        pitch_titles: Sequence[str] = (),
        *,
        strict: bool = False,
    ) -> DictionarySet:
        """Resolve config **titles** to imported dictionaries of ``db`` and build the set, preserving
        order. Missing titles are skipped; with ``strict`` a single missing title raises
        :class:`DictionaryError` (the explicit-CLI / doctor path)."""
        d_rows, d_miss = db.resolve(dict_titles)
        f_rows, f_miss = db.resolve(freq_titles)
        p_rows, p_miss = db.resolve(pitch_titles)
        missing = [*d_miss, *f_miss, *p_miss]
        if strict and missing:
            raise DictionaryError(
                "dictionary title(s) not imported: "
                + ", ".join(repr(m) for m in missing)
                + ". "
                + _MISSING_HINT
            )
        return cls.from_rows(db, d_rows, f_rows, p_rows)

    def has_term(self, *forms: str | None) -> bool:
        """Any exact term/reading hit across the dictionaries? (kanji-fallback gate.)"""
        return any(d.lookup(*forms) for d in self.dicts)

    def kanji_for(self, char: str) -> Entry | None:
        """A panel :class:`Entry` for one kanji, from the first dict whose kanji_bank has it: big
        glyph headword, 音/訓 reading rows + numbered meanings in the def body, stroke count and
        stats as pills — rendered through the normal panel path."""
        for d in self.dicts:
            k = d.kanji_lookup(char)
            if k is None:
                continue
            stats = dict(k["stats"])
            freqs: list[Freq] = []
            strokes = stats.pop("strokes", None)
            if strokes:
                freqs.append(Freq("画数", str(strokes), (96, 125, 175, 255)))
            freqs.extend(
                Freq(name, str(val), FREQ_COLOR) for name, val in sorted(stats.items())[:6]
            )
            nodes: list = []
            if k["onyomi"]:
                nodes.append({"tag": "div", "content": [f"音　{k['onyomi']}"]})
            if k["kunyomi"]:
                nodes.append({"tag": "div", "content": [f"訓　{k['kunyomi']}"]})
            if k["meanings"]:
                nodes.append(
                    {"tag": "ol", "content": [{"tag": "li", "content": m} for m in k["meanings"]]}
                )
            kun = (k["kunyomi"].split() or [""])[0].split(".")[0]
            return Entry(
                headword=[char],
                tags=[t for t in (k["tags"] or "").split() if t][:3],
                freqs=freqs,
                defs=[Definition(d.title, nodes or ["（データなし）"])],
                reading=kun or (k["onyomi"].split() or [""])[0],
            )
        return None

    def frequency_field(self, token) -> tuple[str, str]:
        """(Frequency field HTML, FreqSort number) for a mined Lapis card — the same values the tooltip
        shows as green pills. Empty when no freq source has the word. The plan maps ``freq → Frequency``."""
        forms = (token.lemma, token.surface, token.reading)
        rows = [(fs.title, disp) for fs in self.freqs if (disp := fs.display(forms, token.reading))]
        if not rows:
            return "", ""
        items = "".join(f"<li>{name}: {value}</li>" for name, value in rows)
        html = f'<ul style="text-align:left;margin:0;padding-left:1.1em;">{items}</ul>'
        nums = [int(n) for _, value in rows for n in re.findall(r"\d+", value)]
        return html, (str(min(nums)) if nums else "")

    def card_for(self, token: Token) -> CardData:
        """Mined-card fields (expression / reading / glossary) from the USER's dictionaries — the
        dict-first mining path. Returns the first dictionary that has the word with a non-empty
        glossary; otherwise an expression-only CardData (empty ``glossary_html``) so the caller can
        fall back to the JMdict/jamdict source. No JMdict sequence id — Yomitan terms carry none."""
        forms = (token.lemma, token.surface, token.reading)
        for d in self.dicts:
            hits = d.lookup(*forms)
            if not hits:
                continue
            glosses = _glosses_of(hits[0].glossary)
            if not glosses:
                continue
            glossary_html = "<ol>" + "".join(f"<li>{g}</li>" for g in glosses) + "</ol>"
            return CardData(
                expression=hits[0].term or token.lemma or token.surface,
                reading=hits[0].reading or token.reading,
                glossary_html=glossary_html,
                glosses=tuple(glosses),
            )
        return CardData(
            expression=token.lemma or token.surface, reading=token.reading, glossary_html=""
        )

    def search(self, pattern: str, limit: int = 30) -> Entry:
        """Wildcard/prefix/suffix search across the dictionaries → a results :class:`Entry` that
        lists each matching headword as a **clickable** link: drilling into a result opens that
        exact term. ``pattern`` uses GLOB wildcards (``*``/``?``); a bare term prefix-matches via
        ``term*``."""
        glob = _to_glob(pattern)
        if not any(c in glob for c in "*?"):
            glob = glob + "*"  # a bare query → prefix search
        seen: set[tuple[str, str]] = set()
        items: list[tuple[str, str, str]] = []  # (term, reading, gloss)
        for d in self.dicts:
            for h in d.lookup(glob, wildcard=True, limit=limit):
                key = (h.term, h.reading)
                if key in seen:
                    continue
                seen.add(key)
                items.append((h.term, h.reading, _first_gloss(h.glossary)))
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
        li_nodes: list = []
        for term, reading, gloss in items:
            li: list = [{"tag": "a", "href": f"?query={term}", "content": term}]
            if reading and reading != term:
                li.append(f"【{reading}】")
            if gloss:
                li.append({"tag": "span", "style": {"color": "#6a6a6a"}, "content": f" — {gloss}"})
            li_nodes.append({"tag": "li", "content": li})
        content = (
            [{"tag": "ul", "content": li_nodes}] if li_nodes else ["（一致する語がありません）"]
        )
        return Entry(
            headword=[pattern],
            tags=[],
            freqs=[],
            defs=[Definition(f"検索 “{pattern}” · {len(items)}件", content)],
            reading="",
        )

    def _freq_pills(self, forms, reading: str | None) -> list[Freq]:
        pills: list[Freq] = []
        for fs in self.freqs:
            disp = fs.display(forms, reading)
            if disp:
                pills.append(Freq(_short_freq_name(fs.title), disp, FREQ_COLOR))
        for ps in self.pitches:
            disp = ps.display(forms, reading)
            if disp:
                pills.append(Freq(ps.title, disp, PITCH_COLOR))
        return pills

    def entry_for(self, token: Token, inflected: str | None = None) -> Entry:
        # `inflected` is the full inflected surface incl. trailing auxiliaries (習わ + ぬ → 習わぬ) so
        # the chain deinflects the whole word; the tokenizer splits those into separate tokens.
        forms = (token.lemma, token.surface, token.reading)
        headword = None
        reading = token.reading
        defs: list[Definition] = []
        for d in self.dicts:
            hits = d.lookup(*forms)
            if not hits:
                continue
            if headword is None:
                headword, reading = hits[0].term, hits[0].reading
            nodes: list = []
            seen_gloss: set = set()
            for h in hits:
                # dedupe by glossary alone: some monolingual dicts store one entry twice, keyed by
                # kanji (本命) AND by kana (ほんめい) with identical content. Compare the RAW JSON text
                # (already read from the DB) rather than re-encoding the just-decoded glossary — the two
                # rows share the same source glossary object from import time (dictdb.py's bulk insert
                # serializes it once), so their stored JSON text is byte-identical; re-encoding it here
                # was pure waste (and the single largest hotspot in a `--stress` profile).
                gkey = h.raw_glossary
                if gkey in seen_gloss:
                    continue
                seen_gloss.add(gkey)
                nodes.extend(_glossary_to_nodes(h.glossary))
            defs.append(Definition(d.title, nodes, tags=d.resolve_deftags(hits[0].tags)))
        if headword is None:
            headword = token.lemma or token.surface
        pitches: list[tuple[str, tuple[int, ...]]] = []
        for ps in self.pitches:
            got = ps.accents(forms, reading)
            if got is not None:
                r, positions = got
                item = (r, tuple(positions))
                if item not in pitches:
                    pitches.append(item)
        return Entry(
            headword=furigana(headword, reading),
            tags=[],
            freqs=self._freq_pills(forms, reading),
            defs=defs or [Definition("—", ["（辞書に見つかりませんでした）"])],
            inflection_chain=inflection_chain(inflected or token.surface, token.lemma, headword),
            reading=reading or token.reading,
            pitches=pitches,
        )

"""Yomitan term-bank dictionaries → an ordered multi-dictionary lookup (the Yomitan experience).

Each :class:`Dictionary` is a **read-only view of one imported dictionary** inside the consolidated
:class:`~overlay.app.dictdb.DictionaryDb` (scoped by ``dict_id``) — the dictionaries were built into that
DB **once, at import time**; nothing is parsed or rebuilt here. A word is looked up across an **ordered
list** of them, building a panel :class:`Entry` with one dictionary section per source. The structured-
content walker + panel renderer draw the dict-name pills and rich glossaries.

Term-bank v3 entry: ``[term, reading, defTags, rules, score, glossary[], sequence, termTags]``. A glossary
item is a plain string or ``{"type": "structured-content", "content": <node>}`` (also ``image``/``text``).
The dictionary form comes from the tokenizer lemma; the optional ``saitenka-deinflect``
add-on (GPL-3.0, Yomitan-derived) supplies the inflection chain when installed.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
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

    def inflection_chain(surface: str, *targets: str) -> list[str]:  # noqa: ARG001  # must match saitenka_deinflect.inflection_chain's signature (structural compat check between the two try/except branches)
        return []


from overlay.app.lookup import CardData, furigana
from overlay.app.wordlists import FreqSource, PitchSource
from overlay.panel import Definition, Entry, Freq

if TYPE_CHECKING:
    from collections.abc import Sequence

    from overlay.app.dictdb import DictionaryDb, DictRow
    from overlay.app.tokenize import Token

log = logging.getLogger(__name__)


class DictionaryError(RuntimeError):
    """A requested dictionary can't be used (e.g. a configured title was never imported)."""


_MISSING_HINT = (
    "These are dictionary TITLES with no imported dictionary. Import the source .zip files first: "
    "`saitenka import <dir-with-zips>` (or `import-settings <settings.json> --scan-dir <dir>`), "
    "then they resolve by title. Run `saitenka doctor` to see what's imported."
)

# Fanning `Dictionary.lookup()` out across a thread pool in `entry_for` (one dict per worker) was
# tried and measured under `--stress` with the GIL confirmed off (PYTHON_GIL=0): peak RSS rose ~54%
# (1.1 GB → 1.7 GB, the free-threaded allocator's per-thread arenas) with latency flat-to-worse at
# every percentile — msgspec.json.decode doesn't release the GIL enough for the dispatch overhead to
# pay for itself at this width (9 dicts). Reverted in favour of the plain sequential loop below.


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


def _reading_affinity(dict_reading: str, ctx_reading: str) -> int:
    """How well a dictionary headword reading matches the token's contextual (surface) reading, so a
    multi-reading kanji picks the reading actually used. Exact match wins; otherwise the longest common
    kana prefix — 退いた's surface reading のいた shares の with のく but nothing with しりぞく. Survives
    inflection because the stem reading is a prefix of the conjugated surface reading for godan/ichidan
    verbs (irregulars like する/来る carry a single reading, so there is nothing to disambiguate)."""
    if not dict_reading or not ctx_reading:
        return 0
    if dict_reading == ctx_reading:
        return len(dict_reading) + 1  # exact reading beats any prefix
    n = 0
    for a, b in zip(dict_reading, ctx_reading, strict=False):
        if a != b:
            break
        n += 1
    return n


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


def _search_result_nodes(items: list[tuple[str, str, str]]) -> list:
    li_nodes: list = []
    for term, reading, gloss in items:
        li: list = [{"tag": "a", "href": f"?query={term}", "content": term}]
        if reading and reading != term:
            li.append(f"【{reading}】")
        if gloss:
            li.append({"tag": "span", "style": {"color": "#6a6a6a"}, "content": f" — {gloss}"})
        li_nodes.append({"tag": "li", "content": li})
    return li_nodes


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

    def _entry_from_row(self, row) -> DictEntry:
        eid = row[0]
        cached = self._entry_cache.get(eid)
        if cached is not None:
            self._entry_cache.move_to_end(eid)
            if otel_metrics.dict_cache_hits is not None:
                otel_metrics.dict_cache_hits.add(1)
            return cached
        if otel_metrics.dict_cache_misses is not None:
            otel_metrics.dict_cache_misses.add(1)
        entry = DictEntry(row[1], row[2], msgspec_json.decode(row[3]), row[4], raw_glossary=row[3])
        self._entry_cache[eid] = entry
        if len(self._entry_cache) > self._entry_cache_max:
            self._entry_cache.popitem(last=False)
        return entry

    # ORDER BY e.id makes row order deterministic so DictionarySet._batch_exact (one IN-list query
    # for every dict at once) reassembles byte-identically to these per-(dict,form) point queries.
    _EXACT_Q = (
        "SELECT e.id, e.term, e.reading, e.glossary, e.tags FROM keys k "
        "JOIN entries e ON k.dict_id = e.dict_id AND k.id = e.id "
        "WHERE k.dict_id = ? AND k.key = ? ORDER BY e.id"
    )
    # Wildcard forms GLOB the key column, capping DISTINCT entry ids (a term keys itself twice — by
    # term AND reading — so a raw key LIMIT would under-count entries after dedup).
    _GLOB_Q = (
        "SELECT e.id, e.term, e.reading, e.glossary, e.tags FROM entries e "
        "WHERE e.dict_id = ? AND e.id IN "
        "(SELECT DISTINCT id FROM keys WHERE dict_id = ? AND key GLOB ? LIMIT ?)"
    )

    def lookup(
        self,
        *forms: str | None,
        wildcard: bool = False,
        limit: int = 50,
        rows_by_key: dict[str, list] | None = None,
    ) -> list[DictEntry]:
        """Look terms/readings up in this dictionary. With ``wildcard`` the forms are GLOB patterns
        (``*`` = any run, ``?`` = one char; fullwidth ＊/？ normalised) capped at ``limit`` rows — a
        prefix pattern (``たべ*``) uses the key index; a leading-wildcard suffix scan is LIMIT-bounded.

        ``rows_by_key`` (from :meth:`DictionarySet._batch_exact`) supplies rows already fetched for
        this dict in one whole-set query, so the exact path issues zero SQL here — the assembly below
        is identical either way."""
        formset = {f for f in forms if f}
        seen: set[int] = set()
        out: list[DictEntry] = []
        # dict.fromkeys dedups while keeping order: forms=(lemma, surface, reading) collide (lemma==
        # surface for any uninflected word), and the identical query/rows must not be processed twice.
        for f in dict.fromkeys(x for x in forms if x):
            rows = (
                rows_by_key.get(f, ())
                if rows_by_key is not None
                else self._fetch(f, wildcard=wildcard, limit=limit)
            )
            if self._collect(rows, seen, out, wildcard=wildcard, limit=limit):
                break  # wildcard limit reached
        # Rank exact-term (headword) matches above reading-only ones, like Yomitan — so a common
        # particle の (term=の) wins over an obscure kanji that merely *reads* の (箆/の). Stable, so
        # dict order and same-rank ties are preserved.
        out.sort(key=lambda e: e.term not in formset)
        return out

    def _collect(
        self, rows, seen: set[int], out: list[DictEntry], *, wildcard: bool, limit: int
    ) -> bool:
        """Append each not-yet-``seen`` row's entry to *out*; return True once the wildcard ``limit``
        is reached (the caller stops iterating forms)."""
        for row in rows:
            eid = row[0]
            if eid in seen:
                continue
            seen.add(eid)
            out.append(self._entry_from_row(row))
            if wildcard and len(out) >= limit:
                return True
        return False

    def _fetch(self, form: str, *, wildcard: bool, limit: int) -> list:
        """One SQLite lookup for a single form (the ``dict_sql`` span site). The batched exact path
        (see :meth:`DictionarySet._batch_exact`) bypasses this entirely."""
        conn = self.db._conn()
        did = self.dict_id
        with otel_metrics.instrumented(
            otel_metrics.dict_sql_duration_ms, "dict_sql", dict=self.title
        ):
            cursor = (
                conn.execute(self._GLOB_Q, (did, did, _to_glob(form), limit))
                if wildcard
                else conn.execute(self._EXACT_Q, (did, form))
            )
            return cursor.fetchall()


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

    def decoded_entry_count(self) -> int:
        """Decoded :class:`DictEntry` objects currently cached across every dictionary — the
        ``dict_cache.size`` gauge (each dict's ``_entry_cache`` is bounded by ``entry_cache_max``)."""
        return sum(len(d._entry_cache) for d in self.dicts)

    @staticmethod
    def _kanji_freqs(stats: dict) -> list[Freq]:
        stats = dict(stats)
        freqs: list[Freq] = []
        strokes = stats.pop("strokes", None)
        if strokes:
            freqs.append(Freq("画数", str(strokes), (96, 125, 175, 255)))
        freqs.extend(Freq(name, str(val), FREQ_COLOR) for name, val in sorted(stats.items())[:6])
        return freqs

    @staticmethod
    def _kanji_nodes(k: dict) -> list:
        nodes: list = []
        if k["onyomi"]:
            nodes.append({"tag": "div", "content": [f"音　{k['onyomi']}"]})
        if k["kunyomi"]:
            nodes.append({"tag": "div", "content": [f"訓　{k['kunyomi']}"]})
        if k["meanings"]:
            nodes.append(
                {"tag": "ol", "content": [{"tag": "li", "content": m} for m in k["meanings"]]}
            )
        return nodes

    def kanji_for(self, char: str) -> Entry | None:
        """A panel :class:`Entry` for one kanji, from the first dict whose kanji_bank has it: big
        glyph headword, 音/訓 reading rows + numbered meanings in the def body, stroke count and
        stats as pills — rendered through the normal panel path."""
        for d in self.dicts:
            k = d.kanji_lookup(char)
            if k is None:
                continue
            freqs = self._kanji_freqs(k["stats"])
            nodes = self._kanji_nodes(k)
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

    def _freq_rank(self, term: str, reading: str) -> int | None:
        """Lowest (most-common) frequency rank across the freq sources for this ``(term, reading)`` —
        the tie-breaker's commonness signal. ``None`` when no source ranks it."""
        ranks = [r for fs in self.freqs if (r := fs.rank((term,), reading)) is not None]
        return min(ranks) if ranks else None

    def _best_hit(self, hits: list[DictEntry], token: Token, formset: set[str]) -> DictEntry:
        """Pick the entry to mine among a dictionary's hits: exact-headword first (like Yomitan and
        :meth:`Dictionary.lookup`), then the reading closest to the token's contextual reading (退いた
        prefers のく over しりぞく), then the more common reading by frequency rank. ``min`` is stable, so
        ties fall back to the dict's own order — the prior ``hits[0]`` behaviour."""
        return min(
            hits,
            key=lambda h: (
                h.term not in formset,
                -_reading_affinity(h.reading, token.reading),
                self._freq_rank(h.term, h.reading) or float("inf"),
            ),
        )

    def card_for(self, token: Token) -> CardData:
        """Mined-card fields (expression / reading / glossary) from the USER's dictionaries — the
        dict-first mining path. Returns the best entry (see :meth:`_best_hit`) of the first dictionary
        that has the word with a non-empty glossary; otherwise an expression-only CardData (empty
        ``glossary_html``) so the caller can fall back to the JMdict/jamdict source. No JMdict sequence
        id — Yomitan terms carry none."""
        forms = (token.lemma, token.surface, token.reading)
        formset = {f for f in forms if f}
        for d in self.dicts:
            hits = [h for h in d.lookup(*forms) if _glosses_of(h.glossary)]
            if not hits:
                continue
            best = self._best_hit(hits, token, formset)
            glosses = _glosses_of(best.glossary)
            glossary_html = "<ol>" + "".join(f"<li>{g}</li>" for g in glosses) + "</ol>"
            return CardData(
                expression=best.term or token.lemma or token.surface,
                reading=best.reading or token.reading,
                glossary_html=glossary_html,
                glosses=tuple(glosses),
            )
        return CardData(
            expression=token.lemma or token.surface, reading=token.reading, glossary_html=""
        )

    def _collect_search_hits(self, glob: str, limit: int) -> list[tuple[str, str, str]]:
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
        return items

    def search(self, pattern: str, limit: int = 30) -> Entry:
        """Wildcard/prefix/suffix search across the dictionaries → a results :class:`Entry` that
        lists each matching headword as a **clickable** link: drilling into a result opens that
        exact term. ``pattern`` uses GLOB wildcards (``*``/``?``); a bare term prefix-matches via
        ``term*``."""
        glob = _to_glob(pattern)
        if not any(c in glob for c in "*?"):
            glob = glob + "*"  # a bare query → prefix search
        items = self._collect_search_hits(glob, limit)
        li_nodes = _search_result_nodes(items)
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

    def _batch_exact(self, forms: tuple[str, ...]) -> dict[int, dict[str, list]]:
        """One IN-list query across ALL dicts for the exact forms → ``{dict_id: {key: rows}}``,
        replacing 3 forms × N dicts point queries (all dicts share the one consolidated DB, scoped by
        ``dict_id``) with a single index scan. Each dict's rows are shaped exactly as its own
        :meth:`Dictionary._fetch` returns, so ``lookup(rows_by_key=...)`` reassembles byte-identically.
        Exact path only — wildcard keeps its per-dict LIMIT (a global SQL LIMIT can't cap per dict)."""
        keys = [
            f for f in dict.fromkeys(forms) if f
        ]  # dedup, order-preserving (lemma > surface > reading)
        if not self.dicts or not keys:
            return {}
        dids = [d.dict_id for d in self.dicts]
        din, kin = ",".join("?" * len(dids)), ",".join("?" * len(keys))
        query = (
            "SELECT k.dict_id, k.key, e.id, e.term, e.reading, e.glossary, e.tags "  # noqa: S608 — only bind-placeholder counts (din/kin) interpolated; every value is parameterized
            "FROM keys k JOIN entries e ON k.dict_id = e.dict_id AND k.id = e.id "
            f"WHERE k.dict_id IN ({din}) AND k.key IN ({kin}) ORDER BY e.id"
        )
        conn = self.dicts[0].db._conn()
        with otel_metrics.instrumented(otel_metrics.dict_sql_duration_ms, "dict_sql"):
            rows = conn.execute(query, (*dids, *keys)).fetchall()
        out: dict[int, dict[str, list]] = {}
        for r in rows:  # r = (dict_id, key, e.id, e.term, e.reading, e.glossary, e.tags)
            out.setdefault(r[0], {}).setdefault(r[1], []).append(r[2:])
        return out

    def _dict_defs(
        self, forms: tuple[str, str, str], termforms: set[str], default_reading: str
    ) -> tuple[list[Definition], str | None, str]:
        # Yomitan groups results on the EXPRESSION (term), never the reading, so a reading collision
        # (き → 気/木/生/期/器…) is shown as separate entries, not fused into one. We anchor on the
        # subtitle's own surface: when a dict has an exact-term hit for this word, keep ONLY those and
        # drop the reading-only homophones (hovering 気 must not dump 木/生/期 into its tooltip). With no
        # exact-term hit — a kana word whose dictionary forms are all kanji (かける → 掛ける/懸ける/架ける)
        # — keep every reading match, which IS the intended polysemy. `lookup` already sorts exact-term
        # first, so the kept order (and hits[0] headword) is unchanged.
        headword = None
        reading = default_reading
        defs: list[Definition] = []
        batched = self._batch_exact(forms)  # one query for every dict, not 3 forms × N dicts
        for d in self.dicts:
            hits = d.lookup(*forms, rows_by_key=batched.get(d.dict_id, {}))
            if not hits:
                continue
            hits = [h for h in hits if h.term in termforms] or hits  # exact-term preference
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
        return defs, headword, reading

    def _pitch_accents(
        self, forms: tuple[str, str, str], reading: str
    ) -> list[tuple[str, tuple[int, ...]]]:
        pitches: list[tuple[str, tuple[int, ...]]] = []
        for ps in self.pitches:
            got = ps.accents(forms, reading)
            if got is not None:
                r, positions = got
                item = (r, tuple(positions))
                if item not in pitches:
                    pitches.append(item)
        return pitches

    def entry_for(self, token: Token, inflected: str | None = None) -> Entry:
        # `inflected` is the full inflected surface incl. trailing auxiliaries (習わ + ぬ → 習わぬ) so
        # the chain deinflects the whole word; the tokenizer splits those into separate tokens.
        forms = (token.lemma, token.surface, token.reading)
        termforms = {f for f in (token.lemma, token.surface) if f}
        defs, headword, reading = self._dict_defs(forms, termforms, token.reading)
        if headword is None:
            headword = token.lemma or token.surface
        pitches = self._pitch_accents(forms, reading)
        return Entry(
            headword=furigana(headword, reading),
            tags=[],
            freqs=self._freq_pills(forms, reading),
            defs=defs or [Definition("—", ["（辞書に見つかりませんでした）"])],
            inflection_chain=inflection_chain(inflected or token.surface, token.lemma, headword),
            reading=reading or token.reading,
            pitches=pitches,
        )

"""Yomitan term-bank dictionaries → an ordered multi-dictionary lookup (the Yomitan experience, MVP).

Loads Yomitan ``term_bank_*.json`` v3 dictionaries (bilingual + monolingual, in tooltip order) into a
**SQLite** index on first use (build once, then instant lookups with near-zero RAM — big monolingual dicts are 100k–500k
entries), and looks a word up across an **ordered list** of them, building a panel :class:`Entry` with one
dictionary section per source. The existing structured-content walker + panel renderer draw the dict-name
pills and rich glossaries.

Term-bank v3 entry: ``[term, reading, defTags, rules, score, glossary[], sequence, termTags]``. A glossary
item is a plain string or ``{"type": "structured-content", "content": <node>}`` (also ``image``/``text``).
The dictionary form comes from the tokenizer lemma; the optional ``saitenka-overlay-deinflect``
add-on (GPL-3.0, Yomitan-derived) supplies the inflection chain when installed.
"""

from __future__ import annotations

import logging
import json
import re
import sqlite3
import threading
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

try:
    # Optional GPL-3.0 add-on (derived from Yomitan). When installed, the panel shows the
    # inflection chain (🧩 -て « -いる « -た); without it the chain is empty and nothing is drawn.
    from saitenka_deinflect import inflection_chain
except ImportError:  # pragma: no cover — exercised via the deinflect-absent path

    def inflection_chain(surface: str, *targets: str) -> list[str]:
        return []


from overlay.app import paths
from overlay.app.lookup import furigana
from overlay.app.tokenize import Token
from overlay.app.wordlists import FreqSource, PitchSource, read_json_bank
from overlay.panel import Definition, Entry, Freq

log = logging.getLogger(__name__)


class DictionaryError(RuntimeError):
    """A configured dictionary path can't be loaded (missing file / not a real zip)."""


_MISSING_HINT = (
    "If these are Yomitan dictionary TITLES (from `import-yomitan` without --scan-dir) rather than "
    "file paths, re-run `saitenka-overlay import-yomitan <settings.json> --scan-dir <dir>` to map "
    "them to your .zip files, or set real paths in overlay.toml. Run `saitenka-overlay doctor`."
)


def split_existing(paths: Sequence[str | Path]) -> tuple[list[str], list[str]]:
    """Partition ``paths`` into (existing, missing). A missing entry is usually a bare Yomitan title
    written into the config — loading it would raise a raw ``FileNotFoundError``. Callers filter to
    the existing subset (keeping the user's working dicts) and warn about the rest."""
    existing: list[str] = []
    missing: list[str] = []
    for p in paths:
        (existing if Path(str(p)).expanduser().exists() else missing).append(str(p))
    return existing, missing


CACHE_DIR = paths.cache_dir() / "dicts"
_SCHEMA = 2  # v2: + kanji table. Bumping forces a one-time index rebuild.
FREQ_COLOR = (74, 158, 92, 255)  # green pill, like SubMiner's frequency row
PITCH_COLOR = (126, 96, 168, 255)  # purple pill, for pitch-accent dicts


@dataclass
class DictEntry:
    term: str
    reading: str
    glossary: list
    tags: str = ""


def _to_glob(pattern: str) -> str:
    """Normalise a user wildcard pattern to a SQLite GLOB pattern: fullwidth ＊/？ → ASCII ``*``/``?``."""
    return pattern.replace("＊", "*").replace("？", "?")


def _first_gloss(glossary: list, limit: int = 40) -> str:
    """A short plain-text first-gloss preview for a search-result row (strips SC/HTML, truncates)."""
    from overlay.sc.walk import _text_of

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
            return text[:limit] + ("…" if len(text) > limit else "")
    return ""


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


def _extract_tags(zip_path: Path) -> dict:
    """Yomitan ``tag_bank_*.json`` → {code: [display_name, order]} for defTag pills (★ / priority form)."""
    tags: dict = {}
    with zipfile.ZipFile(zip_path) as zf:
        for name in sorted(zf.namelist()):
            if name.startswith("tag_bank") and name.endswith(".json"):
                for t in read_json_bank(zf, name) or []:
                    if t and isinstance(t[0], str):  # [name, category, order, notes, score]
                        tags[t[0]] = [t[0], t[2] if len(t) > 2 else 0]
    return tags


def _tags_sidecar(db_path: Path) -> Path:
    return db_path.with_name(db_path.stem + ".tags.json")


def _build_db(zip_path: Path, db_path: Path) -> None:
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    try:
        _build_db_into(zip_path, db_path, tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)  # a failed build (e.g. disk full) must not leave a corpse
        raise


def _build_db_into(zip_path: Path, db_path: Path, tmp: Path) -> None:
    conn = sqlite3.connect(tmp)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("CREATE TABLE meta(k TEXT, v TEXT)")
    conn.execute(
        "CREATE TABLE entries(id INTEGER PRIMARY KEY, term TEXT, reading TEXT, "
        "glossary TEXT, tags TEXT)"
    )
    conn.execute("CREATE TABLE keys(key TEXT, id INT)")
    conn.execute(
        "CREATE TABLE kanji(chr TEXT PRIMARY KEY, onyomi TEXT, kunyomi TEXT, "
        "tags TEXT, meanings TEXT, stats TEXT)"
    )
    with zipfile.ZipFile(zip_path) as zf:
        title = zip_path.stem
        try:
            title = json.loads(zf.read("index.json")).get("title", title)
        except Exception:
            log.debug("index.json title read failed", exc_info=True)
        conn.execute("INSERT INTO meta VALUES('title', ?)", (title,))
        rid = 0
        for name in sorted(zf.namelist()):
            if not (name.startswith("term_bank") and name.endswith(".json")):
                continue
            bank = read_json_bank(zf, name)  # tolerant of wrong-CRC Yomitan zips (data intact)
            if bank is None:
                continue
            rows, keys = [], []
            for e in bank:
                rid += 1
                term, reading = e[0], e[1] or e[0]
                rows.append((rid, term, reading, json.dumps(e[5], ensure_ascii=False), e[2]))
                keys.append((term, rid))
                if reading != term:
                    keys.append((reading, rid))
            conn.executemany("INSERT INTO entries VALUES(?,?,?,?,?)", rows)
            conn.executemany("INSERT INTO keys VALUES(?,?)", keys)
        # kanji_bank v3: [char, onyomi, kunyomi, tags, meanings[], stats{}]
        for name in sorted(zf.namelist()):
            if not (name.startswith("kanji_bank") and name.endswith(".json")):
                continue
            bank = read_json_bank(zf, name)
            if bank is None:
                continue
            krows = [
                (
                    e[0],
                    e[1] or "",
                    e[2] or "",
                    e[3] or "",
                    json.dumps(e[4] if len(e) > 4 else [], ensure_ascii=False),
                    json.dumps(e[5] if len(e) > 5 else {}, ensure_ascii=False),
                )
                for e in bank
                if e and isinstance(e[0], str)
            ]
            conn.executemany("INSERT OR IGNORE INTO kanji VALUES(?,?,?,?,?,?)", krows)
    conn.execute("CREATE INDEX idx_keys ON keys(key)")
    conn.commit()
    conn.close()
    tmp.rename(db_path)
    try:
        _tags_sidecar(db_path).write_text(json.dumps(_extract_tags(zip_path), ensure_ascii=False))
    except Exception:
        log.debug("tag sidecar write failed", exc_info=True)


class Dictionary:
    def __init__(self, title: str, db_path: str, tags: dict | None = None):
        self.title = title
        self._db_path = db_path
        self._local = (
            threading.local()
        )  # one read-only connection PER THREAD (safe parallel lookups)
        self.tags = tags or {}  # defTag code -> [display_name, order]

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True, check_same_thread=False)
            # mmap the index (up to 1 GiB — the biggest dict is ~400 MB) so cold lookups hit
            # page-cache-backed memory instead of pread syscalls, and give the page cache room
            # (64 MiB; negative cache_size = KiB units). Read-only conns, so no write-side effects.
            c.execute("PRAGMA mmap_size=1073741824")
            c.execute("PRAGMA cache_size=-65536")
            self._local.conn = c
        return c

    @classmethod
    @lru_cache(maxsize=16)
    def load(cls, zip_path: str | Path) -> Dictionary:
        zp = Path(zip_path)
        st = zp.stat()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        db = CACHE_DIR / f"{zp.stem}-{int(st.st_mtime)}-{st.st_size}-v{_SCHEMA}.sqlite"
        # GC stale generations of THIS zip's cache (older mtime/size/schema variants) so a schema
        # bump rebuilds once instead of doubling the footprint (the indexes total gigabytes).
        # NOT glob: dict stems contain [brackets] which glob would read as character classes.
        stale_re = re.compile(re.escape(zp.stem) + r"-\d+-\d+(-v\d+)?\.sqlite$")
        for stale in CACHE_DIR.iterdir():
            if stale.name != db.name and stale_re.fullmatch(stale.name):
                stale.unlink(missing_ok=True)
                _tags_sidecar(stale).unlink(missing_ok=True)
        if not db.exists():
            # Serialise the (expensive, 25–66s) build across processes: two mpv instances opening the
            # same dict in plugin mode must not both build it. Re-check under the lock.
            from filelock import FileLock

            with FileLock(str(db) + ".lock"):
                if not db.exists():
                    _build_db(zp, db)
        c0 = sqlite3.connect(f"file:{db}?mode=ro", uri=True)  # transient, just to read the title
        title = c0.execute("SELECT v FROM meta WHERE k='title'").fetchone()[0]
        c0.close()
        # tag_bank lives in a small sidecar; build it lazily for caches made before this existed
        sidecar = _tags_sidecar(db)
        if not sidecar.exists():
            try:
                sidecar.write_text(json.dumps(_extract_tags(zp), ensure_ascii=False))
            except Exception:
                log.debug("lazy tag sidecar write failed", exc_info=True)
        tags = {}
        try:
            tags = json.loads(sidecar.read_text())
        except Exception:
            log.debug("tag sidecar read failed", exc_info=True)
        return cls(title, str(db), tags)

    def kanji_lookup(self, char: str) -> dict | None:
        """The kanji_bank entry for one ideograph, or None."""
        row = (
            self._conn()
            .execute(
                "SELECT chr, onyomi, kunyomi, tags, meanings, stats FROM kanji WHERE chr = ?",
                (char,),
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
            "meanings": json.loads(row[4] or "[]"),
            "stats": json.loads(row[5] or "{}"),
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
        conn = self._conn()
        # Wildcard forms GLOB the key column, capping DISTINCT entry ids (a term keys itself twice —
        # by term AND reading — so a raw key LIMIT would under-count entries after dedup).
        exact_q = (
            "SELECT e.id, e.term, e.reading, e.glossary, e.tags FROM keys k "
            "JOIN entries e ON k.id = e.id WHERE k.key = ?"
        )
        glob_q = (
            "SELECT e.id, e.term, e.reading, e.glossary, e.tags FROM entries e "
            "WHERE e.id IN (SELECT DISTINCT id FROM keys WHERE key GLOB ? LIMIT ?)"
        )
        for f in forms:
            if not f:
                continue
            rows = (
                conn.execute(glob_q, (_to_glob(f), limit))
                if wildcard
                else conn.execute(exact_q, (f,))
            )
            for row in rows:
                if row[0] in seen:
                    continue
                seen.add(row[0])
                out.append(DictEntry(row[1], row[2], json.loads(row[3]), row[4]))
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
    def load(
        cls,
        paths: Sequence[str | Path],
        freq_paths: Sequence[str | Path] = (),
        pitch_paths: Sequence[str | Path] = (),
    ) -> DictionarySet:
        # Validate every path up front so a bad entry raises ONE actionable DictionaryError instead
        # of a raw FileNotFoundError traceback from Path.stat() deep in Dictionary.load (the WinError 2
        # crash on a name-only config). Callers that want to keep working dicts pre-filter with
        # split_existing(); this is the strict path (explicit CLI --dict, doctor, tests).
        _, missing = split_existing([*paths, *freq_paths, *pitch_paths])
        if missing:
            raise DictionaryError(
                "dictionary file(s) not found: "
                + ", ".join(repr(m) for m in missing)
                + ". "
                + _MISSING_HINT
            )
        return cls(
            [Dictionary.load(p) for p in paths],
            [FreqSource.load(p) for p in freq_paths],
            [PitchSource.load(p) for p in pitch_paths],
        )

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
                pills.append(Freq(fs.title, disp, FREQ_COLOR))
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
                # kanji (本命) AND by kana (ほんめい) with identical content.
                gkey = json.dumps(h.glossary, ensure_ascii=False, sort_keys=True)
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

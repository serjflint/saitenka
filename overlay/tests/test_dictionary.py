"""Multi-dictionary engine: Yomitan term-bank loading, ordered lookup, glossary unwrap."""

import json
import zipfile

import pytest

from overlay.app.dictionary import (
    FREQ_COLOR,
    PITCH_COLOR,
    Dictionary,
    DictionaryError,
    DictionarySet,
    _glossary_to_nodes,
    split_existing,
)
from overlay.app.tokenize import Token, tokenize
from overlay.app.wordlists import FreqSource, PitchSource


def test_dictionary_set_load_missing_path_raises_friendly_error(tmp_path):
    """A bare Yomitan title in the config (import-yomitan without --scan-dir) must raise ONE
    actionable DictionaryError, not a raw FileNotFoundError traceback (the WinError 2 crash)."""
    with pytest.raises(DictionaryError) as ei:
        DictionarySet.load(["JMdict [2026-06-27]", str(tmp_path / "nope.zip")])
    msg = str(ei.value)
    assert "JMdict [2026-06-27]" in msg
    assert "import-yomitan" in msg and "doctor" in msg


def test_split_existing_partitions(tmp_path):
    real = tmp_path / "d.zip"
    real.write_text("x")
    existing, missing = split_existing([str(real), "Some Dict Title", str(tmp_path / "gone.zip")])
    assert existing == [str(real)]
    assert missing == ["Some Dict Title", str(tmp_path / "gone.zip")]


def _make_dict(path, title, entries):
    """entries: list of [term, reading, glossary]. Writes a minimal Yomitan v3 dict zip."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": title, "format": 3}))
        bank = [[t, r, "", "", 0, g, i + 1, ""] for i, (t, r, g) in enumerate(entries)]
        zf.writestr("term_bank_1.json", json.dumps(bank, ensure_ascii=False))
    return str(path)


SC = {"type": "structured-content", "content": [{"tag": "div", "content": "定義文"}]}


def test_glossary_unwrap():
    nodes = _glossary_to_nodes(
        ["plain", SC, {"type": "text", "text": "t"}, {"type": "image", "path": "x.png"}]
    )
    assert nodes[0] == "plain"
    assert nodes[1] == SC["content"]  # structured-content unwrapped
    assert nodes[2] == "t"
    assert nodes[3]["tag"] == "img"


def test_load_and_lookup(tmp_path):
    p = _make_dict(
        tmp_path / "d1.zip", "TestDict", [["読む", "よむ", ["to read"]], ["本", "ほん", ["book"]]]
    )
    d = Dictionary.load(p)
    assert d.title == "TestDict"
    hits = d.lookup("読む")
    assert len(hits) == 1 and hits[0].glossary == ["to read"]
    assert d.lookup("よむ")[0].term == "読む"  # reading key works
    assert d.lookup("nope") == []


def test_lookup_ranks_exact_term_above_reading_only(tmp_path):
    # の (a particle, term=の) must outrank 箆 — an obscure noun that merely READS の. Without ranking
    # 箆 sorts first in the term bank and becomes the headword (the screenshot bug).
    d = _make_dict(
        tmp_path / "n.zip",
        "N",
        [["箆", "の", ["shaft of an arrow"]], ["の", "の", ["possessive particle"]]],
    )
    dic = Dictionary.load(d)
    hits = dic.lookup("の", "の", "の")
    assert hits[0].term == "の"  # exact-term match wins, not reading-only 箆


def test_entry_for_particle_prefers_particle_headword(tmp_path):
    d = _make_dict(
        tmp_path / "n2.zip",
        "N",
        [["箆", "の", ["arrow shaft"]], ["の", "の", ["possessive particle"]]],
    )
    ds = DictionarySet.load([d])
    tok = Token(surface="の", lemma="の", reading="の", pos="助詞", start=0, end=1)
    assert ds.entry_for(tok).headword == ["の"]  # headword is the particle, not 箆


def test_dictionary_set_orders_sections(tmp_path):
    a = _make_dict(tmp_path / "a.zip", "AAA", [["読む", "よむ", ["read (A)"]]])
    b = _make_dict(tmp_path / "b.zip", "BBB", [["読む", "よむ", [SC]]])
    ds = DictionarySet.load([a, b])
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    entry = ds.entry_for(tok)
    assert [d.dict_name for d in entry.defs] == ["AAA", "BBB"]  # dict order preserved


def test_dictionary_set_miss_falls_back(tmp_path):
    a = _make_dict(tmp_path / "c.zip", "AAA", [["猫", "ねこ", ["cat"]]])
    ds = DictionarySet.load([a])
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    entry = ds.entry_for(tok)
    assert entry.defs[0].dict_name == "—"  # not found placeholder


def test_dictionary_dedupes_kanji_and_kana_duplicate_rows(tmp_path):
    # some monolingual dicts store one entry twice: keyed by kanji AND by kana, identical glossary.
    g = ["identical gloss"]
    d = _make_dict(tmp_path / "m.zip", "M", [["本命", "ほんめい", g], ["ほんめい", "ほんめい", g]])
    ds = DictionarySet.load([d])
    tok = Token(surface="本命", lemma="本命", reading="ほんめい", pos="名詞", start=0, end=2)
    entry = ds.entry_for(tok)
    assert len(entry.defs) == 1
    assert entry.defs[0].content.count("identical gloss") == 1  # not rendered twice


def test_deftags_resolved_ordered_and_normalized(tmp_path):
    p = tmp_path / "d.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": "D", "format": 3}))
        bank = [["聞こえる", "きこえる", "★ priority form", "v1", 0, ["to be heard"], 1, ""]]
        zf.writestr("term_bank_1.json", json.dumps(bank, ensure_ascii=False))
        tb = [
            ["★", "popular", 2, "high priority entry", 2],
            ["priority form", "frequent", 1, "high priority spelling", 1],
        ]
        zf.writestr("tag_bank_1.json", json.dumps(tb, ensure_ascii=False))
    ds = DictionarySet.load([str(p)])
    tok = Token(
        surface="聞こえる", lemma="聞こえる", reading="きこえる", pos="動詞", start=0, end=4
    )
    # ordered by tag order (priority form=1 before ★=2), nbsp normalized to a space
    assert ds.entry_for(tok).defs[0].tags == ["priority form", "★"]


def test_entry_for_sets_inflection_chain(tmp_path):
    d = _make_dict(tmp_path / "d.zip", "D", [["食べる", "たべる", ["to eat"]]])
    ds = DictionarySet.load([d])
    tok = Token(surface="食べた", lemma="食べる", reading="たべた", pos="動詞", start=0, end=3)
    assert ds.entry_for(tok).inflection_chain == ["-た"]


def test_wildcard_lookup_prefix_suffix_and_limit(tmp_path):
    # R7: GLOB wildcard lookup — prefix (たべ*), suffix (*べる), single-char (?べる), and a LIMIT cap.
    d = _make_dict(
        tmp_path / "w.zip",
        "W",
        [
            ["食べる", "たべる", ["to eat"]],
            ["食べ物", "たべもの", ["food"]],
            ["調べる", "しらべる", ["to look up"]],
            ["並べる", "ならべる", ["to line up"]],
            ["本", "ほん", ["book"]],
        ],
    )
    dic = Dictionary.load(d)
    assert {h.term for h in dic.lookup("たべ*", wildcard=True)} == {"食べる", "食べ物"}  # prefix
    assert {h.term for h in dic.lookup("*べる", wildcard=True)} == {
        "食べる",
        "調べる",
        "並べる",
    }  # suffix
    assert {h.term for h in dic.lookup("食べ?", wildcard=True)} == {
        "食べる",
        "食べ物",
    }  # 食べ + one char
    assert dic.lookup("たべ", wildcard=False) == []  # non-wildcard = exact
    assert len(dic.lookup("*", wildcard=True, limit=2)) == 2  # LIMIT bounds a broad glob


def test_wildcard_normalizes_fullwidth_star(tmp_path):
    d = _make_dict(tmp_path / "fw.zip", "FW", [["食べる", "たべる", ["to eat"]]])
    dic = Dictionary.load(d)
    assert {h.term for h in dic.lookup("たべ＊", wildcard=True)} == {"食べる"}  # fullwidth ＊ → *


def test_search_lists_matches_as_clickable_links(tmp_path):
    d = _make_dict(
        tmp_path / "s.zip",
        "S",
        [
            ["食べる", "たべる", ["to eat"]],
            ["調べる", "しらべる", ["to look up"]],
        ],
    )
    ds = DictionarySet.load([d])
    entry = ds.search("*べる")
    # one results section; each match is an <a href=?query=…> so it can be drilled into (R4b)
    body = entry.defs[0].content
    dumped = json.dumps(body, ensure_ascii=False)
    assert '"?query=食べる"' in dumped and '"?query=調べる"' in dumped
    assert "たべる" in dumped and "to eat" in dumped  # reading + gloss preview shown
    assert "件" in entry.defs[0].dict_name  # result count in the section header


def test_search_bare_query_prefix_matches(tmp_path):
    d = _make_dict(
        tmp_path / "b.zip", "B", [["食べる", "たべる", ["eat"]], ["食う", "くう", ["eat (rough)"]]]
    )
    ds = DictionarySet.load([d])
    dumped = json.dumps(ds.search("食").defs[0].content, ensure_ascii=False)  # bare '食' → 食*
    assert "食べる" in dumped and "食う" in dumped


def test_search_no_match_shows_placeholder(tmp_path):
    d = _make_dict(tmp_path / "nm.zip", "NM", [["本", "ほん", ["book"]]])
    ds = DictionarySet.load([d])
    assert "一致する語がありません" in json.dumps(
        ds.search("存在しない語*").defs[0].content, ensure_ascii=False
    )


def _make_meta(path, title, mode, entries):
    """entries: list of [term, data]. Writes a minimal Yomitan term_meta_bank zip."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": title, "format": 3}))
        bank = [[t, mode, data] for t, data in entries]
        zf.writestr("term_meta_bank_1.json", json.dumps(bank, ensure_ascii=False))
    return str(path)


def test_freq_source_display_prefers_displayvalue_and_reading(tmp_path):
    # 本命 (kanji) entry should win over the ほんめい (kana) entry, and displayValue is preferred.
    p = _make_meta(
        tmp_path / "freqa.zip",
        "FreqA",
        "freq",
        [
            [
                "本命",
                {
                    "reading": "ほんめい",
                    "frequency": {"value": 8912, "displayValue": "8912, 143969㋕"},
                },
            ],
            ["ほんめい", {"value": 143969, "displayValue": "143969㋕"}],
        ],
    )
    fs = FreqSource.load(p)
    assert fs.title == "FreqA"
    assert fs.display(("本命", "本命", "ほんめい"), "ほんめい") == "8912, 143969㋕"


def test_freq_source_joins_multiple_entries(tmp_path):
    # some freq lists give SUW+LUW as two entries for one term → joined ("12813, 14117").
    p = _make_meta(
        tmp_path / "freqb.zip",
        "FreqB",
        "freq",
        [
            ["本命", {"reading": "ほんめい", "frequency": 12813}],
            ["本命", {"reading": "ほんめい", "frequency": 14117}],
            ["本命", 14086],  # plain-int form — deduped display strings
        ],
    )
    fs = FreqSource.load(p)
    assert fs.display(("本命",), "ほんめい") == "12813, 14117, 14086"


def test_pitch_source_reading_and_positions(tmp_path):
    p = _make_meta(
        tmp_path / "pitch.zip",
        "Pitch",
        "pitch",
        [
            ["本命", {"reading": "ほんめい", "pitches": [{"position": 0}]}],
        ],
    )
    ps = PitchSource.load(p)
    assert ps.display(("本命",), None) == "ほんめい [0]"


def test_read_json_bank_recovers_wrong_crc(tmp_path):
    import pytest

    from overlay.app.wordlists import read_json_bank

    p = tmp_path / "meta.zip"
    entry = [["本命", "pitch", {"reading": "ほんめい", "pitches": [{"position": 0}]}]]
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("term_meta_bank_1.json", json.dumps(entry, ensure_ascii=False))
    # Corrupt the stored CRC-32 (central dir +16, local header +14) — mimics real bad-CRC pitch zips.
    raw = bytearray(p.read_bytes())
    ci = raw.find(b"PK\x01\x02")
    raw[ci + 16 : ci + 20] = b"\x00\x00\x00\x00"
    li = raw.find(b"PK\x03\x04")
    raw[li + 14 : li + 18] = b"\x00\x00\x00\x00"
    p.write_bytes(raw)
    with zipfile.ZipFile(p) as zf:
        with pytest.raises(zipfile.BadZipFile):
            zf.read("term_meta_bank_1.json")  # strict read rejects it
        bank = read_json_bank(zf, "term_meta_bank_1.json")  # lenient reader recovers it
    assert bank[0][0] == "本命"


def test_frequency_field_html_and_sort(tmp_path):
    d = _make_dict(tmp_path / "d.zip", "Def", [["本命", "ほんめい", ["favorite"]]])
    fz = _make_meta(
        tmp_path / "f.zip",
        "FreqA",
        "freq",
        [
            [
                "本命",
                {
                    "reading": "ほんめい",
                    "frequency": {"value": 8912, "displayValue": "8912, 143969"},
                },
            ]
        ],
    )
    ds = DictionarySet.load([d], freq_paths=[fz])
    tok = Token(surface="本命", lemma="本命", reading="ほんめい", pos="名詞", start=0, end=2)
    html, sort = ds.frequency_field(tok)
    assert html.startswith("<ul") and "FreqA" in html and "8912, 143969" in html
    assert sort == "8912"  # smallest value = most frequent, for FreqSort


def test_frequency_field_empty_without_source(tmp_path):
    d = _make_dict(tmp_path / "d.zip", "Def", [["本命", "ほんめい", ["favorite"]]])
    ds = DictionarySet.load([d])  # no frequency dict
    tok = Token(surface="本命", lemma="本命", reading="ほんめい", pos="名詞", start=0, end=2)
    assert ds.frequency_field(tok) == ("", "")


def test_dictionary_set_populates_freq_and_pitch_pills(tmp_path):
    d = _make_dict(tmp_path / "d.zip", "Def", [["本命", "ほんめい", ["favorite"]]])
    fz = _make_meta(tmp_path / "f.zip", "Freq", "freq", [["本命", 5386]])
    pz = _make_meta(
        tmp_path / "p.zip",
        "Pitch",
        "pitch",
        [["本命", {"reading": "ほんめい", "pitches": [{"position": 0}]}]],
    )
    ds = DictionarySet.load([d], freq_paths=[fz], pitch_paths=[pz])
    tok = Token(surface="本命", lemma="本命", reading="ほんめい", pos="名詞", start=0, end=2)
    entry = ds.entry_for(tok)
    kinds = [(f.name, f.value, f.color) for f in entry.freqs]
    assert ("Freq", "5386", FREQ_COLOR) in kinds
    assert ("Pitch", "ほんめい [0]", PITCH_COLOR) in kinds


# --- Stage 7b: read-only per-thread connections get mmap + a larger page cache --------------------


def test_readonly_conn_has_mmap_and_cache_pragmas(tmp_path):
    """Dictionary._conn() must set PRAGMA mmap_size=1GiB and a larger cache_size on the read-only
    per-thread connections, so cold first lookups avoid pread round-trips (Stage 7b)."""
    import sqlite3

    db = tmp_path / "d.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE meta(k TEXT, v TEXT)")
    conn.execute("INSERT INTO meta VALUES('title', 'T')")
    conn.commit()
    conn.close()

    d = Dictionary("T", str(db))
    c = d._conn()
    assert c.execute("PRAGMA mmap_size").fetchone()[0] == 1073741824
    assert c.execute("PRAGMA cache_size").fetchone()[0] == -65536  # 64 MiB (negative = KiB units)

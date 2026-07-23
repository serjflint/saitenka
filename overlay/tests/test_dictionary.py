"""Multi-dictionary engine: DB-backed ordered lookup, glossary unwrap, freq/pitch pills."""

import json
import zipfile

import dicthelp
import pytest

from overlay.app.dictdb import DictionaryDb
from overlay.app.dictionary import (
    FREQ_COLOR,
    PITCH_COLOR,
    DictionaryError,
    DictionarySet,
    _glossary_to_nodes,
    _short_freq_name,
    split_existing,
)
from overlay.app.tokenize import Token, tokenize


def test_short_freq_name_strips_saitenka_prefix():
    assert _short_freq_name("Saitenka Known") == "Known"
    assert _short_freq_name("saitenka-reactivate") == "reactivate"  # zip-style title
    assert _short_freq_name("JPDB v2.2") == "JPDB v2.2"  # other dicts pass through untouched


def test_dictionary_set_from_db_missing_title_raises_friendly_error():
    """A configured title with no imported dictionary must raise ONE actionable DictionaryError
    (naming the title + pointing at `import`/`doctor`), not resolve to a silent empty set."""
    db = DictionaryDb.open()
    with pytest.raises(DictionaryError) as ei:
        DictionarySet.from_db(db, ["JMdict [2026-06-27]"], strict=True)
    msg = str(ei.value)
    assert "JMdict [2026-06-27]" in msg
    assert "import" in msg and "doctor" in msg


def test_dictionary_set_from_db_skips_missing_when_not_strict(tmp_path):
    d = _make_dict(tmp_path / "d.zip", "Present", [["猫", "ねこ", ["cat"]]])
    db = DictionaryDb.open()
    db.import_zip(d, imported_at=dicthelp.AT)
    ds = DictionarySet.from_db(db, ["Present", "Absent"])  # non-strict → keep what's imported
    assert [d.title for d in ds.dicts] == ["Present"]


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
    d = dicthelp.load_dict(p)
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
    dic = dicthelp.load_dict(d)
    hits = dic.lookup("の", "の", "の")
    assert hits[0].term == "の"  # exact-term match wins, not reading-only 箆


def test_entry_for_particle_prefers_particle_headword(tmp_path):
    d = _make_dict(
        tmp_path / "n2.zip",
        "N",
        [["箆", "の", ["arrow shaft"]], ["の", "の", ["possessive particle"]]],
    )
    ds = dicthelp.load_set([d])
    tok = Token(surface="の", lemma="の", reading="の", pos="助詞", start=0, end=1)
    assert ds.entry_for(tok).headword == ["の"]  # headword is the particle, not 箆


def test_dictionary_set_orders_sections(tmp_path):
    a = _make_dict(tmp_path / "a.zip", "AAA", [["読む", "よむ", ["read (A)"]]])
    b = _make_dict(tmp_path / "b.zip", "BBB", [["読む", "よむ", [SC]]])
    ds = dicthelp.load_set([a, b])
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    entry = ds.entry_for(tok)
    assert [d.dict_name for d in entry.defs] == ["AAA", "BBB"]  # dict order preserved


def test_dictionary_set_miss_falls_back(tmp_path):
    a = _make_dict(tmp_path / "c.zip", "AAA", [["猫", "ねこ", ["cat"]]])
    ds = dicthelp.load_set([a])
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    entry = ds.entry_for(tok)
    assert entry.defs[0].dict_name == "—"  # not found placeholder


def test_card_for_uses_user_dictionary(tmp_path):
    """Dict-first mining: the mined card's expression / reading / glossary come from the user's dict."""
    d = _make_dict(tmp_path / "cf.zip", "TestDict", [["読む", "よむ", ["to read", "to peruse"]]])
    ds = dicthelp.load_set([d])
    tok = Token(surface="読む", lemma="読む", reading="よむ", pos="動詞", start=0, end=2)
    card = ds.card_for(tok)
    assert card.expression == "読む"
    assert card.reading == "よむ"
    assert card.glossary_html == "<ol><li>to read</li><li>to peruse</li></ol>"
    assert card.glosses == ("to read", "to peruse")


def test_card_for_miss_returns_empty_glossary(tmp_path):
    """A word in no configured dict → expression-only card with empty glossary_html, so the miner
    can fall back to the JMdict/jamdict source."""
    d = _make_dict(tmp_path / "cf2.zip", "TestDict", [["猫", "ねこ", ["cat"]]])
    ds = dicthelp.load_set([d])
    tok = Token(surface="犬", lemma="犬", reading="いぬ", pos="名詞", start=0, end=1)
    card = ds.card_for(tok)
    assert card.expression == "犬"  # from the token
    assert card.glossary_html == ""


def test_dictionary_dedupes_kanji_and_kana_duplicate_rows(tmp_path):
    # some monolingual dicts store one entry twice: keyed by kanji AND by kana, identical glossary.
    g = ["identical gloss"]
    d = _make_dict(tmp_path / "m.zip", "M", [["本命", "ほんめい", g], ["ほんめい", "ほんめい", g]])
    ds = dicthelp.load_set([d])
    tok = Token(surface="本命", lemma="本命", reading="ほんめい", pos="名詞", start=0, end=2)
    entry = ds.entry_for(tok)
    assert len(entry.defs) == 1
    assert entry.defs[0].content.count("identical gloss") == 1  # not rendered twice


def test_lookup_reuses_cached_entry_object_on_repeat_lookup(tmp_path):
    """A second lookup of the same term returns the SAME DictEntry object (identity, not just equal
    value) — confirms the decode is skipped on a cache hit, not just that the result is correct."""
    d = _make_dict(tmp_path / "cache1.zip", "C", [["猫", "ねこ", ["cat"]]])
    dic = dicthelp.load_dict(d)
    first = dic.lookup("猫")
    second = dic.lookup("猫")
    assert first[0] is second[0]


def test_lookup_cache_evicts_oldest_beyond_entry_cache_max(tmp_path):
    entries = [[f"語{i}", f"ご{i}", [f"gloss {i}"]] for i in range(5)]
    d = _make_dict(tmp_path / "cache2.zip", "C", entries)
    dic = dicthelp.load_dict(d)
    dic._entry_cache_max = 3
    for i in range(5):
        dic.lookup(f"語{i}")
    assert len(dic._entry_cache) == 3
    # the two oldest (語0, 語1) were evicted; re-looking them up must decode fresh, not reuse a stale
    # cache slot that should no longer exist — and the cache stays bounded after the refill.
    assert dic.lookup("語0")
    assert len(dic._entry_cache) == 3


def test_deftags_resolved_ordered_and_normalized(tmp_path):
    p = tmp_path / "d.zip"
    # the multi-word tag code uses an nbsp (\xa0) internally; defTags separate codes with a plain space
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": "D", "format": 3}))
        bank = [["聞こえる", "きこえる", "★ priority\xa0form", "v1", 0, ["to be heard"], 1, ""]]
        zf.writestr("term_bank_1.json", json.dumps(bank, ensure_ascii=False))
        tb = [
            ["★", "popular", 2, "high priority entry", 2],
            ["priority\xa0form", "frequent", 1, "high priority spelling", 1],
        ]
        zf.writestr("tag_bank_1.json", json.dumps(tb, ensure_ascii=False))
    ds = dicthelp.load_set([str(p)])
    tok = Token(
        surface="聞こえる", lemma="聞こえる", reading="きこえる", pos="動詞", start=0, end=4
    )
    # ordered by tag order (priority form=1 before ★=2), nbsp normalized to a space
    assert ds.entry_for(tok).defs[0].tags == ["priority form", "★"]


def test_entry_for_sets_inflection_chain(tmp_path):
    d = _make_dict(tmp_path / "d.zip", "D", [["食べる", "たべる", ["to eat"]]])
    ds = dicthelp.load_set([d])
    tok = Token(surface="食べた", lemma="食べる", reading="たべた", pos="動詞", start=0, end=3)
    assert ds.entry_for(tok).inflection_chain == ["-た"]


def test_wildcard_lookup_prefix_suffix_and_limit(tmp_path):
    # GLOB wildcard lookup — prefix (たべ*), suffix (*べる), single-char (?べる), and a LIMIT cap.
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
    dic = dicthelp.load_dict(d)
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
    dic = dicthelp.load_dict(d)
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
    ds = dicthelp.load_set([d])
    entry = ds.search("*べる")
    # one results section; each match is an <a href=?query=…> so it can be drilled into
    body = entry.defs[0].content
    dumped = json.dumps(body, ensure_ascii=False)
    assert '"?query=食べる"' in dumped and '"?query=調べる"' in dumped
    assert "たべる" in dumped and "to eat" in dumped  # reading + gloss preview shown
    assert "件" in entry.defs[0].dict_name  # result count in the section header


def test_search_bare_query_prefix_matches(tmp_path):
    d = _make_dict(
        tmp_path / "b.zip", "B", [["食べる", "たべる", ["eat"]], ["食う", "くう", ["eat (rough)"]]]
    )
    ds = dicthelp.load_set([d])
    dumped = json.dumps(ds.search("食").defs[0].content, ensure_ascii=False)  # bare '食' → 食*
    assert "食べる" in dumped and "食う" in dumped


def test_search_no_match_shows_placeholder(tmp_path):
    d = _make_dict(tmp_path / "nm.zip", "NM", [["本", "ほん", ["book"]]])
    ds = dicthelp.load_set([d])
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
    fs = dicthelp.load_freqsource(p)
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
    fs = dicthelp.load_freqsource(p)
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
    ps = dicthelp.load_pitchsource(p)
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
    ds = dicthelp.load_set([d], freq_zips=[fz])
    tok = Token(surface="本命", lemma="本命", reading="ほんめい", pos="名詞", start=0, end=2)
    html, sort = ds.frequency_field(tok)
    assert html.startswith("<ul") and "FreqA" in html and "8912, 143969" in html
    assert sort == "8912"  # smallest value = most frequent, for FreqSort


def test_frequency_field_empty_without_source(tmp_path):
    d = _make_dict(tmp_path / "d.zip", "Def", [["本命", "ほんめい", ["favorite"]]])
    ds = dicthelp.load_set([d])  # no frequency dict
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
    ds = dicthelp.load_set([d], freq_zips=[fz], pitch_zips=[pz])
    tok = Token(surface="本命", lemma="本命", reading="ほんめい", pos="名詞", start=0, end=2)
    entry = ds.entry_for(tok)
    kinds = [(f.name, f.value, f.color) for f in entry.freqs]
    assert ("Freq", "5386", FREQ_COLOR) in kinds
    assert ("Pitch", "ほんめい [0]", PITCH_COLOR) in kinds

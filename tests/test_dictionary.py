"""Multi-dictionary engine: DB-backed ordered lookup, glossary unwrap, freq/pitch pills."""

import json
import zipfile

import dicthelp
import pytest
from saitenka_tokenize.japanese import Token, tokenize

from saitenka.app.config import DictDbOptions
from saitenka.app.dictdb import DictionaryDb
from saitenka.app.dictionary import DictionaryError, DictionarySet, split_existing
from saitenka.app.dictionary_surface import FREQ_COLOR, PITCH_COLOR
from saitenka.app.dictionary_surface import glossary_to_nodes as _glossary_to_nodes
from saitenka.app.dictionary_surface import glosses_of as _glosses_of
from saitenka.model import PitchAccent


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
    d = dicthelp.term_zip(tmp_path / "d.zip", "Present", [["猫", "ねこ", ["cat"]]])
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


SC = {"type": "structured-content", "content": [{"tag": "div", "content": "定義文"}]}


def test_glossary_unwrap():
    # Per-type extraction, exercised one item at a time (a single-item glossary is NOT block-wrapped, so
    # this asserts the raw unwrap: sc→content, text→str, image→img tag).
    assert _glossary_to_nodes(["plain"]) == ["plain"]
    assert _glossary_to_nodes([SC]) == [SC["content"]]  # structured-content unwrapped
    assert _glossary_to_nodes([{"type": "text", "text": "t"}]) == ["t"]
    assert _glossary_to_nodes([{"type": "image", "path": "x.png"}])[0]["tag"] == "img"


def test_multi_item_glossary_blocks_each_sense_on_its_own_line():
    # Regression (大辞林 相手 → 相手方/相手次第/相手役): each glossary ARRAY item is a separate cross-ref;
    # they must render as separate blocks, not one flowing underlined run. Multi-item ⇒ each div-wrapped.
    from saitenka.render.sc_adapter import walk

    glossary = [
        {
            "type": "structured-content",
            "content": {"tag": "a", "href": "?query=相手方", "content": "相手方"},
        },
        {
            "type": "structured-content",
            "content": {"tag": "a", "href": "?query=相手次第", "content": "相手次第"},
        },
        {
            "type": "structured-content",
            "content": {"tag": "a", "href": "?query=相手役", "content": "相手役"},
        },
    ]
    nodes = _glossary_to_nodes(glossary)
    blocks = walk(nodes)
    texts = ["".join(getattr(s, "text", "") for s in b.flow) for b in blocks]
    assert texts == ["相手方", "相手次第", "相手役"]  # three separate lines, not one run


def test_glosses_of_separates_block_and_chip_items():
    # A JMdict-style sense flattens to ONE gloss string; block items (<li>) and pill chips (styled
    # spans) sit apart on screen but have no textual whitespace — they must not glue into
    # `dramaticexcitingtouching` / `na-adjcolloquial` in the card preview.
    sense = {
        "type": "structured-content",
        "content": [
            {"tag": "span", "style": {"backgroundColor": "#565656"}, "content": "na-adj"},
            {"tag": "span", "style": {"backgroundColor": "#565656"}, "content": "colloquial"},
            {
                "tag": "ul",
                "content": [
                    {"tag": "li", "content": g} for g in ("dramatic", "exciting", "touching")
                ],
            },
        ],
    }
    assert _glosses_of([sense]) == ["na-adj colloquial dramatic exciting touching"]


def test_load_and_lookup(tmp_path):
    p = dicthelp.term_zip(
        tmp_path / "d1.zip", "TestDict", [["読む", "よむ", ["to read"]], ["本", "ほん", ["book"]]]
    )
    ds = dicthelp.load_set([p])
    assert [d.title for d in ds.dicts] == ["TestDict"]
    entry = ds.entry_for(
        Token(surface="読む", lemma="読む", reading="よむ", pos="動詞", start=0, end=2)
    )
    assert _glosses_of(entry.defs[0].content) == ["to read"]
    assert ds.has_term("よむ")  # a term is keyed by its reading too
    assert not ds.has_term("nope")


def test_lookup_ranks_exact_term_above_reading_only(tmp_path):
    # の (a particle, term=の) must outrank 箆 — an obscure noun that merely READS の. Without ranking
    # 箆 sorts first in the term bank and becomes the headword (the screenshot bug).
    d = dicthelp.term_zip(
        tmp_path / "n.zip",
        "N",
        [["箆", "の", ["shaft of an arrow"]], ["の", "の", ["possessive particle"]]],
    )
    ds = dicthelp.load_set([d])
    tok = Token(surface="の", lemma="の", reading="の", pos="助詞", start=0, end=1)
    # exact-term match heads the entry, not the reading-only 箆
    assert ds.entry_for(tok).headword == ["の"]


def test_entry_for_drops_reading_only_homophones_when_a_term_matches(tmp_path):
    # Hovering 気 (reading き) must NOT merge every き-homophone (木/生) into its tooltip — Yomitan groups
    # on the term, so a reading collision is a separate entry, never fused. With an exact-term hit we
    # keep only it and drop the reading-only homophones.
    d = dicthelp.term_zip(
        tmp_path / "h.zip",
        "H",
        [["気", "き", ["spirit; mind"]], ["木", "き", ["tree"]], ["生", "き", ["raw"]]],
    )
    ds = dicthelp.load_set([d])
    tok = Token(surface="気", lemma="気", reading="き", pos="名詞", start=0, end=1)
    entry = ds.entry_for(tok)
    assert "気" in json.dumps(
        entry.headword, ensure_ascii=False
    )  # headword is 気 (ruby'd 気【き】)
    glosses = json.dumps(entry.defs[0].content, ensure_ascii=False)
    assert "spirit" in glosses  # 気's own gloss is shown
    assert "tree" not in glosses and "raw" not in glosses  # 木/生 homophones are NOT merged in


def test_entry_for_keeps_all_reading_matches_for_a_kana_word(tmp_path):
    # A kana word whose dictionary forms are all kanji (かける → 掛ける/懸ける) has NO exact-term hit, so
    # the reading fallback keeps every form — that IS the intended polysemy, not a homophone collision.
    d = dicthelp.term_zip(
        tmp_path / "k.zip",
        "K",
        [["掛ける", "かける", ["to hang"]], ["懸ける", "かける", ["to wager"]]],
    )
    ds = dicthelp.load_set([d])
    tok = Token(surface="かける", lemma="かける", reading="かける", pos="動詞", start=0, end=3)
    glosses = json.dumps(ds.entry_for(tok).defs[0].content, ensure_ascii=False)
    assert "hang" in glosses and "wager" in glosses  # both かける forms kept


def test_dictionary_set_miss_falls_back(tmp_path):
    a = dicthelp.term_zip(tmp_path / "c.zip", "AAA", [["猫", "ねこ", ["cat"]]])
    ds = dicthelp.load_set([a])
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    entry = ds.entry_for(tok)
    assert entry.defs[0].dict_name == "—"  # not found placeholder


def test_entry_for_headword_comes_from_the_first_dict_with_a_hit(tmp_path):
    a = dicthelp.term_zip(tmp_path / "first.zip", "First", [["行く", "いく", ["to go"]]])
    b = dicthelp.term_zip(tmp_path / "second.zip", "Second", [["行く", "ゆく", ["to go (poetic)"]]])
    ds = dicthelp.load_set([a, b])
    tok = next(t for t in tokenize("学校に行く") if t.surface == "行く")
    entry = ds.entry_for(tok)
    assert entry.reading == "いく"  # First dict's reading wins, not Second's


def test_entry_for_with_many_dicts_preserves_order_and_content(tmp_path):
    paths = [
        dicthelp.term_zip(tmp_path / f"d{i}.zip", f"Dict{i}", [["猫", "ねこ", [f"cat ({i})"]]])
        for i in range(6)
    ]
    ds = dicthelp.load_set(paths)
    tok = next(t for t in tokenize("猫がいる") if t.surface == "猫")
    entry = ds.entry_for(tok)
    assert [d.dict_name for d in entry.defs] == [f"Dict{i}" for i in range(6)]
    assert [d.content[0] for d in entry.defs] == [f"cat ({i})" for i in range(6)]


def test_a_hover_does_not_issue_one_query_per_dictionary(tmp_path):
    """A hover issues the same nonzero SQL count with one or four dictionaries."""
    tok = Token(surface="猫", lemma="猫", reading="ねこ", pos="名詞", start=0, end=1)

    def statements(count: int) -> int:
        paths = [
            dicthelp.term_zip(
                tmp_path / f"n{count}_{i}.zip", f"N{count}D{i}", [["猫", "ねこ", [f"cat{i}"]]]
            )
            for i in range(count)
        ]
        ds = dicthelp.load_set(paths)
        ds.entry_for(tok)  # warm the adapter, its connection, and the schema probes
        assert [d.dict_name for d in ds.entry_for(tok).defs] == [
            f"N{count}D{i}" for i in range(count)
        ]
        executed: list[str] = []
        connection = ds.source.store._conn()
        connection.set_trace_callback(executed.append)
        try:
            ds.entry_for(tok)
        finally:
            connection.set_trace_callback(None)
        return len(executed)

    wide, narrow = statements(4), statements(1)

    assert narrow > 0  # a lookup that issued no SQL would make the comparison below vacuous
    assert wide == narrow


def test_card_for_uses_user_dictionary(tmp_path):
    """Dict-first mining: the mined card's expression / reading / glossary come from the user's dict."""
    d = dicthelp.term_zip(
        tmp_path / "cf.zip", "TestDict", [["読む", "よむ", ["to read", "to peruse"]]]
    )
    ds = dicthelp.load_set([d])
    tok = Token(surface="読む", lemma="読む", reading="よむ", pos="動詞", start=0, end=2)
    card = ds.card_for(tok)
    assert card.expression == "読む"
    assert card.reading == "よむ"
    assert card.glossary_html == "<ol><li>to read</li><li>to peruse</li></ol>"
    assert card.glosses == ("to read", "to peruse")


@pytest.mark.parametrize(
    ("title", "persist_seq", "expected"),
    [
        ("Jitendex", True, "1"),
        ("MyOwnDict", True, ""),
        ("JMdict", False, ""),
        ("JMdict [2026-06-27]", True, "1"),
        ("Jitendex.zip [2026-06-27]", True, "1"),
        ("jitendex-yomitan", True, "1"),
        ("Saitenka Known", True, ""),
        ("BCCWJ Frequency", True, ""),
    ],
)
def test_card_sequence_requires_persisted_jmdict_provenance(tmp_path, title, persist_seq, expected):
    archive = dicthelp.term_zip(tmp_path / "sequence.zip", title, [["読む", "よむ", ["to read"]]])
    db = DictionaryDb.open(tmp_path / "sequence.sqlite", DictDbOptions(persist_seq=persist_seq))
    dictionary = dicthelp.load_set([archive], on=db)
    token = Token("読む", "読む", "よむ", "動詞", 0, 2)

    assert dictionary.card_for(token).idseq == expected


def test_card_for_breaks_reading_tie_by_frequency(tmp_path):
    """No entry's reading matches the (absent) context reading → the tie falls through to commonness:
    the lower freq rank (おもて) wins over the first-listed ひょう."""
    d = dicthelp.term_zip(
        tmp_path / "fq.zip",
        "Multi",
        [["表", "ひょう", ["table"]], ["表", "おもて", ["surface"]]],
    )
    freq = dicthelp.meta_zip(
        tmp_path / "fr.zip",
        "Freq",
        "freq",
        [
            ["表", {"reading": "おもて", "frequency": 500}],
            ["表", {"reading": "ひょう", "frequency": 9000}],
        ],
    )
    ds = dicthelp.load_set([d], [freq])
    tok = Token(surface="表", lemma="表", reading="", pos="名詞", start=0, end=1)
    card = ds.card_for(tok)
    assert card.reading == "おもて"


def test_cards_for_returns_one_card_per_reading_best_first(tmp_path):
    """The per-entry mine choices: one CardData per distinct (term, reading), ordered best-first —
    退いた's contextual reading のいた puts のく ahead of the first-listed しりぞく, and each card carries
    only its own reading's glosses. cards_for[0] is card_for's default."""
    d = dicthelp.term_zip(
        tmp_path / "cff.zip",
        "Multi",
        [["退く", "しりぞく", ["to retreat"]], ["退く", "のく", ["to step aside"]]],
    )
    ds = dicthelp.load_set([d])
    tok = Token(surface="退いた", lemma="退く", reading="のいた", pos="動詞", start=0, end=3)
    cards = ds.cards_for(tok)
    assert [(c.reading, c.glosses) for c in cards] == [
        ("のく", ("to step aside",)),
        ("しりぞく", ("to retreat",)),
    ]
    assert cards[0] == ds.card_for(tok)  # default == first offered


def test_entry_for_builds_stacked_groups_for_multi_reading(tmp_path):
    """entry_for exposes one EntryGroup per distinct reading, ordered like cards_for (退いた context →
    のく first), each carrying its card_index for the per-entry ⊕ and only its reading's definition."""
    d = dicthelp.term_zip(
        tmp_path / "grp.zip",
        "Multi",
        [["退く", "しりぞく", ["to retreat"]], ["退く", "のく", ["to step aside"]]],
    )
    ds = dicthelp.load_set([d])
    tok = Token(surface="退いた", lemma="退く", reading="のいた", pos="動詞", start=0, end=3)
    entry = ds.entry_for(tok)
    assert [(g.reading, g.card_index) for g in entry.groups] == [("のく", 0), ("しりぞく", 1)]
    cards = ds.cards_for(tok)
    # each group's card_index points at its own entry in cards_for
    assert all(cards[g.card_index].reading == g.reading for g in entry.groups)
    assert "to step aside" in json.dumps(entry.groups[0].defs[0].content, ensure_ascii=False)
    assert "to retreat" not in json.dumps(entry.groups[0].defs[0].content, ensure_ascii=False)
    assert entry.reading == entry.groups[0].reading == "のく"
    assert entry.headword == entry.groups[0].headword


def test_stacked_entry_group_defs_preload_media(tmp_path):
    # #283 regression: the stacked-entry path (a word with ≥2 readings, like 鳥 = とり/ちょう) must
    # preload inline-img media just like the fused path — the two builders drifted and this one forgot,
    # so every gaiji in a multi-reading word rendered ▢ even though the fused Entry.defs had media.
    pytest.importorskip("resvg_py")  # media only populates when the rasterizer is installed
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        b'<rect x="10" y="10" width="80" height="80" fill="black"/></svg>'
    )
    zp = dicthelp.term_zip(
        tmp_path / "grp.zip",
        "Multi",
        [
            ("退く", "しりぞく", ["to retreat", {"type": "image", "path": "m/a.svg"}]),
            ("退く", "のく", ["to step aside", {"type": "image", "path": "m/b.svg"}]),
        ],
        media={"m/a.svg": svg, "m/b.svg": svg},
    )
    ds = dicthelp.load_set(dict_zips=[zp])
    tok = Token(surface="退いた", lemma="退く", reading="のいた", pos="動詞", start=0, end=3)
    entry = ds.entry_for(tok)
    assert len(entry.groups) == 2  # のく / しりぞく stacked
    for g in entry.groups:
        assert g.defs and all(d.media for d in g.defs)  # every stacked def preloaded its img media


def test_entry_for_stacks_phrase_terms_longest_first(tmp_path):
    """A multi-token phrase (数ある) passed as an extra term stacks ABOVE the bare word (数), longest
    first — Yomitan shows the longest match first. Hovering 数 in 数ある must surface 数ある, not just 数."""
    d = dicthelp.term_zip(
        tmp_path / "ph.zip",
        "Phrase",
        [["数ある", "かずある", ["many; numerous"]], ["数", "かず", ["number"]]],
    )
    ds = dicthelp.load_set([d])
    tok = Token(surface="数", lemma="数", reading="かず", pos="名詞", start=0, end=1)
    entry = ds.entry_for(tok, extra_terms=("数ある",))
    assert [g.reading for g in entry.groups] == ["かずある", "かず"]  # phrase first, then bare word
    assert "many; numerous" in json.dumps(entry.groups[0].defs[0].content, ensure_ascii=False)
    assert entry.reading == "かずある"  # fused header tracks the longest (top) entry


def test_entry_for_stacks_honorific_phrase_from_the_content_word(tmp_path):
    """お休み splits into お(prefix)+休み; hovering 休み folds the leading お back in, so passing お休み as
    an extra term stacks it ABOVE the bare 休み — the natural hover (the kanji, not the tiny お) surfaces
    the whole word."""
    d = dicthelp.term_zip(
        tmp_path / "oy.zip",
        "Honorific",
        [["お休み", "おやすみ", ["rest; good night"]], ["休み", "やすみ", ["holiday"]]],
    )
    ds = dicthelp.load_set([d])
    tok = Token(surface="休み", lemma="休む", reading="やすみ", pos="動詞", start=1, end=3)
    entry = ds.entry_for(tok, extra_terms=("お休み",))
    assert [g.reading for g in entry.groups] == ["おやすみ", "やすみ"]  # お休み first, then 休み
    assert "rest; good night" in json.dumps(entry.groups[0].defs[0].content, ensure_ascii=False)


def test_phrase_cards_align_with_groups_for_mining(tmp_path):
    """The per-entry ⊕ mines cards_for(...)[card_index]; with phrase terms the card list must span the
    same stacked entries in the same order, so a group's ⊕ mines that exact entry (phrase default first)."""
    d = dicthelp.term_zip(
        tmp_path / "phm.zip",
        "Phrase",
        [["数ある", "かずある", ["many"]], ["数", "かず", ["number"]]],
    )
    ds = dicthelp.load_set([d])
    tok = Token(surface="数", lemma="数", reading="かず", pos="名詞", start=0, end=1)
    entry = ds.entry_for(tok, extra_terms=("数ある",))
    cards = ds.cards_for(tok, extra_terms=("数ある",))
    assert cards[0].expression == "数ある"  # default mine is the longest match
    assert all(cards[g.card_index].reading == g.reading for g in entry.groups)


def test_entry_for_single_reading_has_no_groups(tmp_path):
    """A single-reading word keeps the fused single-header panel (no stacking) — groups is empty."""
    d = dicthelp.term_zip(tmp_path / "one.zip", "One", [["読む", "よむ", ["to read"]]])
    ds = dicthelp.load_set([d])
    tok = Token(surface="読む", lemma="読む", reading="よむ", pos="動詞", start=0, end=2)
    assert ds.entry_for(tok).groups == []


def test_card_for_miss_returns_empty_glossary(tmp_path):
    """A word in no configured dict → expression-only card with empty glossary_html, so the miner
    can fall back to the JMdict/jamdict source."""
    d = dicthelp.term_zip(tmp_path / "cf2.zip", "TestDict", [["猫", "ねこ", ["cat"]]])
    ds = dicthelp.load_set([d])
    tok = Token(surface="犬", lemma="犬", reading="いぬ", pos="名詞", start=0, end=1)
    card = ds.card_for(tok)
    assert ds.cards_for(tok) == []
    assert card.expression == "犬"  # from the token
    assert card.glossary_html == ""


def test_dictionary_dedupes_kanji_and_kana_duplicate_rows(tmp_path):
    # some monolingual dicts store one entry twice: keyed by kanji AND by kana, identical glossary.
    g = ["identical gloss"]
    d = dicthelp.term_zip(
        tmp_path / "m.zip", "M", [["本命", "ほんめい", g], ["ほんめい", "ほんめい", g]]
    )
    ds = dicthelp.load_set([d])
    tok = Token(surface="本命", lemma="本命", reading="ほんめい", pos="名詞", start=0, end=2)
    entry = ds.entry_for(tok)
    assert len(entry.defs) == 1
    assert entry.defs[0].content.count("identical gloss") == 1  # not rendered twice


def test_lookup_records_otel_dict_sql_and_cache_metrics(tmp_path):
    """Stage 8 instrumentation: a lookup should record dict_sql duration + a cache miss (first call)
    then a cache hit (repeat call), when telemetry is configured."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from saitenka import otel_metrics

    d = dicthelp.term_zip(tmp_path / "otel.zip", "OtelDict", [["猫", "ねこ", ["cat"]]])
    ds = dicthelp.load_set([d])
    tok = Token(surface="猫", lemma="猫", reading="ねこ", pos="名詞", start=0, end=1)

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.register(reader, provider.get_meter("test"))
    try:
        ds.entry_for(tok)
        after_first = ds.decoded_entry_count()
        ds.entry_for(tok)
        snap = otel_metrics.snapshot()
        assert after_first == 1
        assert ds.decoded_entry_count() == after_first
        assert snap["saitenka.dict_sql.duration_ms"]["count"] == 2
        assert snap["saitenka.dict_cache.misses"]["value"] == 1
        assert snap["saitenka.dict_cache.hits"]["value"] == 1
    finally:
        otel_metrics.unregister()
        provider.shutdown()


@pytest.mark.timeout(5)
def test_dict_sql_span_always_on_foreground_sampled_on_prefetch_workers():
    # dict_sql keeps full step-resolution on the interactive path but is sampled on the background
    # prefetch workers, where it floods the trace and prefetch_decode already covers the phase.
    import threading

    from saitenka.app.source_adapter import _BG_SQL_SPAN_SAMPLE, _emit_sql_span

    assert _emit_sql_span() is True  # main thread → always traced

    results: list[bool] = []

    def worker():
        results.extend(_emit_sql_span() for _ in range(_BG_SQL_SPAN_SAMPLE * 2))

    t = threading.Thread(target=worker, name="saitenka-prefetch-0")
    t.start()
    t.join()
    # exactly 1-in-N on a prefetch worker (2 of 2N calls)
    assert sum(results) == 2
    assert results[0] is True  # first call of the worker samples in


def test_lookup_records_dict_cache_eviction_counter(tmp_path):
    # An eviction is a CAPACITY miss — the signal for whether raising entry_cache_max would cut the
    # 10k+ misses a real session shows into hits, vs them being unavoidable cold first-decodes.
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from saitenka import otel_metrics

    entries = [[f"語{i}", f"ご{i}", [f"gloss {i}"]] for i in range(5)]
    db = DictionaryDb.open(tmp_path / "evict.sqlite", DictDbOptions(entry_cache_max=3))
    ds = dicthelp.load_set([dicthelp.term_zip(tmp_path / "evict.zip", "C", entries)], on=db)

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.register(reader, provider.get_meter("test"))
    try:
        for i in range(5):  # 5 distinct decodes over a cap of 3 → 2 evictions
            ds.entry_for(
                Token(
                    surface=f"語{i}", lemma=f"語{i}", reading=f"ご{i}", pos="名詞", start=0, end=2
                )
            )
        assert ds.decoded_entry_count() == 3
        assert otel_metrics.snapshot()["saitenka.dict_cache.evictions"]["value"] == 2
        ds.entry_for(Token("語0", "語0", "ご0", "名詞", 0, 2))
        assert ds.decoded_entry_count() == 3
        assert otel_metrics.snapshot()["saitenka.dict_cache.evictions"]["value"] == 3
    finally:
        otel_metrics.unregister()
        provider.shutdown()


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
    d = dicthelp.term_zip(tmp_path / "d.zip", "D", [["食べる", "たべる", ["to eat"]]])
    ds = dicthelp.load_set([d])
    tok = Token(surface="食べた", lemma="食べる", reading="たべた", pos="動詞", start=0, end=3)
    assert ds.entry_for(tok).inflection_chain == ["-た"]


def test_wildcard_search_matches_prefix_suffix_and_middle(tmp_path):
    """The GLOB forms users type into the search box: `*` any run, `?` one character."""
    d = dicthelp.term_zip(
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
    ds = dicthelp.load_set([d])

    def matched(pattern: str, limit: int = 30) -> set[str]:
        dumped = json.dumps(ds.search(pattern, limit).defs[0].content, ensure_ascii=False)
        return {term for term in ("食べる", "食べ物", "調べる", "並べる", "本") if term in dumped}

    assert matched("たべ*") == {"食べる", "食べ物"}
    assert matched("*べる") == {"食べる", "調べる", "並べる"}
    assert matched("食べ?") == {"食べる", "食べ物"}
    assert len(matched("*", limit=2)) == 2  # a broad glob stays bounded by the limit


def test_wildcard_normalizes_fullwidth_star(tmp_path):
    d = dicthelp.term_zip(tmp_path / "fw.zip", "FW", [["食べる", "たべる", ["to eat"]]])
    ds = dicthelp.load_set([d])
    dumped = json.dumps(ds.search("たべ＊").defs[0].content, ensure_ascii=False)
    assert "食べる" in dumped  # fullwidth ＊ → *


def test_search_lists_matches_as_clickable_links(tmp_path):
    d = dicthelp.term_zip(
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
    d = dicthelp.term_zip(
        tmp_path / "b.zip", "B", [["食べる", "たべる", ["eat"]], ["食う", "くう", ["eat (rough)"]]]
    )
    ds = dicthelp.load_set([d])
    dumped = json.dumps(ds.search("食").defs[0].content, ensure_ascii=False)  # bare '食' → 食*
    assert "食べる" in dumped and "食う" in dumped


def test_search_no_match_shows_placeholder(tmp_path):
    d = dicthelp.term_zip(tmp_path / "nm.zip", "NM", [["本", "ほん", ["book"]]])
    ds = dicthelp.load_set([d])
    assert "一致する語がありません" in json.dumps(
        ds.search("存在しない語*").defs[0].content, ensure_ascii=False
    )


_TOKEN = Token("本命", "本命", "ほんめい", "名詞", 0, 2)


def test_a_kana_keyed_row_does_not_double_the_pill_its_own_dictionary_already_answered(tmp_path):
    """Both keyings in one dictionary: the kanji row is the precise one, so the kana row it also
    carries must not appear beside it (`prefer_term_keyed`)."""
    p = dicthelp.meta_zip(
        tmp_path / "freqk.zip",
        "FreqK",
        "freq",
        [
            ["本命", {"reading": "ほんめい", "frequency": 8912}],
            ["ほんめい", {"value": 143969, "displayValue": "143969㋕"}],
        ],
    )
    d = dicthelp.term_zip(tmp_path / "terms.zip", "Terms", [["本命", "ほんめい", ["favorite"]]])
    dictionary = dicthelp.load_set([d], freq_zips=[p])
    assert [pill.value for pill in dictionary.entry_for(_TOKEN).freqs] == ["8912"]


def test_several_entries_for_one_term_are_all_shown(tmp_path):
    # some freq lists give SUW+LUW as two entries for one term → joined ("12813, 14117").
    p = dicthelp.meta_zip(
        tmp_path / "freqb.zip",
        "FreqB",
        "freq",
        [
            ["本命", {"reading": "ほんめい", "frequency": 12813}],
            ["本命", {"reading": "ほんめい", "frequency": 14117}],
            ["本命", 14086],  # plain-int form — deduped display strings
        ],
    )
    d = dicthelp.term_zip(tmp_path / "terms.zip", "Terms", [["本命", "ほんめい", ["favorite"]]])
    dictionary = dicthelp.load_set([d], freq_zips=[p])
    assert [pill.value for pill in dictionary.entry_for(_TOKEN).freqs] == [
        "12813",
        "14117",
        "14086",
    ]


def test_the_blend_takes_the_minimum_rank_a_dictionary_offers(tmp_path):
    # SUW+LUW give two entries for one term → the blend consumes the most-frequent (min) rank.
    p = dicthelp.meta_zip(
        tmp_path / "freqr.zip",
        "FreqR",
        "freq",
        [
            ["本命", {"reading": "ほんめい", "frequency": 14117}],
            ["本命", {"reading": "ほんめい", "frequency": 12813}],
        ],
    )
    assert dicthelp.load_set(freq_zips=[p]).rareness_rank(_TOKEN) == 12813


def test_the_blend_is_none_when_no_dictionary_has_the_word(tmp_path):
    p = dicthelp.meta_zip(tmp_path / "freqr2.zip", "FreqR2", "freq", [["猫", {"frequency": 500}]])
    ds = dicthelp.load_set(freq_zips=[p])
    assert ds.rareness_rank(Token("存在しない語", "存在しない語", "", "名詞", 0, 6)) is None


def test_an_occurrence_based_dictionary_is_named_but_never_blended(tmp_path):
    """Occurrence counts become ranks at import, so the ORIGINAL mode has to be persisted — a dense
    per-corpus rank is not comparable with a real one and must stay out of the mean."""
    rank_zip = dicthelp.meta_zip(
        tmp_path / "rank.zip", "RankDict", "freq", [["本命", {"frequency": 500}]]
    )
    occ_zip = dicthelp.meta_zip(
        tmp_path / "occ.zip",
        "OccDict",
        "freq",
        [["本命", 99999]],
        frequency_mode="occurrence-based",
    )
    d = dicthelp.term_zip(tmp_path / "terms.zip", "Terms", [["本命", "ほんめい", ["favorite"]]])
    ds = dicthelp.load_set([d], freq_zips=[rank_zip, occ_zip])

    assert [pill.value for pill in ds.entry_for(_TOKEN).freqs] == ["500", "99999"]
    assert ds.rareness_rank(_TOKEN) == 500


def test_pitch_reading_and_positions_reach_the_entry(tmp_path):
    d = dicthelp.term_zip(tmp_path / "pd.zip", "PD", [["本命", "ほんめい", ["favourite"]]])
    p = dicthelp.meta_zip(
        tmp_path / "pitch.zip",
        "Pitch",
        "pitch",
        [["本命", {"reading": "ほんめい", "pitches": [{"position": 0}]}]],
    )
    ds = dicthelp.load_set([d], pitch_zips=[p])
    assert ds.pitch_field(_TOKEN)[1] == "0"
    assert ds.entry_for(_TOKEN).pitches == [("ほんめい", (PitchAccent(0),))]


def test_read_json_bank_recovers_wrong_crc(tmp_path):
    import pytest

    from saitenka.app.bankreader import read_json_bank

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


@pytest.mark.parametrize("display", ["8912, 143969", "8912, 143969㋕"])
def test_frequency_field_html_and_sort(tmp_path, display):
    d = dicthelp.term_zip(tmp_path / "d.zip", "Def", [["本命", "ほんめい", ["favorite"]]])
    fz = dicthelp.meta_zip(
        tmp_path / "f.zip",
        "FreqA",
        "freq",
        [
            [
                "本命",
                {
                    "reading": "ほんめい",
                    "frequency": {"value": 8912, "displayValue": display},
                },
            ]
        ],
    )
    ds = dicthelp.load_set([d], freq_zips=[fz])
    tok = Token(surface="本命", lemma="本命", reading="ほんめい", pos="名詞", start=0, end=2)
    html, sort = ds.frequency_field(tok)
    assert html.startswith("<ul") and "FreqA" in html and display in html
    assert sort == "8912"  # smallest value = most frequent, for FreqSort


def test_frequency_field_empty_without_source(tmp_path):
    d = dicthelp.term_zip(tmp_path / "d.zip", "Def", [["本命", "ほんめい", ["favorite"]]])
    ds = dicthelp.load_set([d])  # no frequency dict
    tok = Token(surface="本命", lemma="本命", reading="ほんめい", pos="名詞", start=0, end=2)
    assert ds.frequency_field(tok) == ("", "")


def test_dictionary_set_populates_freq_and_pitch_pills(tmp_path):
    d = dicthelp.term_zip(tmp_path / "d.zip", "Def", [["本命", "ほんめい", ["favorite"]]])
    fz = dicthelp.meta_zip(tmp_path / "f.zip", "Freq", "freq", [["本命", 5386]])
    pz = dicthelp.meta_zip(
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


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Saitenka Known", "Known"),
        ("saitenka-reactivate", "reactivate"),
        ("JPDB v2.2", "JPDB v2.2"),
    ],
)
def test_a_freq_pill_drops_the_product_prefix_from_our_own_lists(tmp_path, title, expected):
    d = dicthelp.term_zip(tmp_path / "d.zip", "Def", [["本命", "ほんめい", ["favorite"]]])
    fz = dicthelp.meta_zip(tmp_path / "f.zip", title, "freq", [["本命", 5386]])
    ds = dicthelp.load_set([d], freq_zips=[fz])
    tok = Token(surface="本命", lemma="本命", reading="ほんめい", pos="名詞", start=0, end=2)

    assert [f.name for f in ds.entry_for(tok).freqs] == [expected]


def test_pitch_field_returns_html_and_positions(tmp_path):
    # #192: the mined-card {pitch-accents}/{pitch-accent-positions} markers, mirroring frequency_field
    pz = dicthelp.meta_zip(
        tmp_path / "p.zip",
        "Pitch",
        "pitch",
        [["本命", {"reading": "ほんめい", "pitches": [{"position": 0}, {"position": 2}]}]],
    )
    ds = dicthelp.load_set([], pitch_zips=[pz])
    tok = Token(surface="本命", lemma="本命", reading="ほんめい", pos="名詞", start=0, end=2)
    html, positions = ds.pitch_field(tok)
    assert "ほんめい" in html and "[0]" in html and "[2]" in html
    assert positions == "0, 2"


def test_pitch_field_empty_when_no_pitch_source(tmp_path):
    ds = dicthelp.load_set([dicthelp.term_zip(tmp_path / "d.zip", "D", [["猫", "ねこ", ["cat"]]])])
    tok = Token(surface="猫", lemma="猫", reading="ねこ", pos="名詞", start=0, end=1)
    assert ds.pitch_field(tok) == ("", "")


@pytest.mark.timeout(30)
def test_the_decoded_entry_cache_survives_concurrent_prefetch_workers(tmp_path):
    """The decoded-entry LRU is touched by the main thread AND every prefetch worker at once
    (free-threaded build). A concurrent eviction must not drop a key between another thread's get()
    and move_to_end() — the KeyError a report's prefetch worker hit. Guard: no thread raises and the
    cache stays bounded. (Reproduces only on a free-threaded build; a safety net under the GIL.)"""
    import threading

    words = [[f"語{i}", f"ご{i}", [f"gloss {i}"]] for i in range(16)]
    db = DictionaryDb.open(tmp_path / "race.sqlite", DictDbOptions(entry_cache_max=8))
    ds = dicthelp.load_set([dicthelp.term_zip(tmp_path / "race.zip", "C", words)], on=db)
    # A working set ~2x the cap so every thread constantly hits AND overflows — the interleaving
    # that raced.
    tokens = [
        Token(surface=t, lemma=t, reading=r, pos="名詞", start=0, end=len(t)) for t, r, _g in words
    ]
    ds.entry_for(tokens[0])  # build the adapter once; the race is in the cache, not in setup
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def worker():
        start.wait()  # release all threads together to overlap the get/evict windows
        try:
            for _ in range(40):
                for token in tokens:
                    ds.entry_for(token)
        except BaseException as exc:  # noqa: BLE001 — capture the race, don't die silently in a thread
            errors.append(exc)

    threads = [threading.Thread(target=worker, name=f"saitenka-prefetch-{k}") for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"entry cache raced: {errors[:3]!r}"
    # Exactly the cap: `<=` would also pass a store that cached nothing at all.
    assert ds.decoded_entry_count() == 8

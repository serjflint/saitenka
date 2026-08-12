from __future__ import annotations

import json
import sqlite3

from saitenka_dict import KanjiQuery, SearchQuery, SqliteDictionaryStore, TermQuery, Translator


def make_legacy_db(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE dictionaries(id INTEGER PRIMARY KEY, title TEXT, import_order INTEGER);
        CREATE TABLE entries(dict_id INTEGER, id INTEGER, term TEXT, reading TEXT, glossary TEXT, tags TEXT, seq INTEGER);
        CREATE TABLE keys(dict_id INTEGER, key TEXT, id INTEGER);
        CREATE TABLE tags(dict_id INTEGER, code TEXT, name TEXT, ord INTEGER, category TEXT, notes TEXT);
        CREATE TABLE term_meta(dict_id INTEGER, term TEXT, mode TEXT, reading TEXT, rank INTEGER, disp TEXT, positions TEXT);
        CREATE TABLE kanji(dict_id INTEGER, chr TEXT, onyomi TEXT, kunyomi TEXT, tags TEXT, meanings TEXT, stats TEXT);
        """
    )
    connection.execute("INSERT INTO dictionaries VALUES (1, 'Core', 0)")
    connection.execute(
        "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, 1, "読む", "よむ", json.dumps(["to read"]), "v1", 1456360),
    )
    connection.executemany("INSERT INTO keys VALUES (?, ?, ?)", [(1, "読む", 1), (1, "よむ", 1)])
    connection.execute("INSERT INTO tags VALUES (1, 'v1', 'ichidan verb', 2, 'pos', 'verb')")
    connection.execute("INSERT INTO term_meta VALUES (1, '読む', 'freq', 'よむ', 42, '42', NULL)")
    connection.execute(
        "INSERT INTO term_meta VALUES (1, '読む', 'pitch', 'よむ', NULL, NULL, '[0]')"
    )
    connection.execute(
        "INSERT INTO kanji VALUES (1, '読', 'ドク', 'よ.む', 'v1', '[\"reading\"]', '{\"strokes\": \"14\"}')"
    )
    connection.commit()
    connection.close()


def test_legacy_database_projects_complete_term_semantics(tmp_path):
    path = tmp_path / "dictionary.sqlite"
    make_legacy_db(path)

    result = Translator(SqliteDictionaryStore(path)).lookup_terms(TermQuery("読む"))

    entry = result.entries[0]
    assert entry.headwords[0].term == "読む"
    assert entry.definitions[0].content == ("to read",)
    assert entry.definitions[0].tags[0].name == "ichidan verb"
    assert entry.sequence == 1456360
    assert entry.frequencies[0].value == 42
    assert entry.pronunciations[0].pitch_positions == (0,)


def test_legacy_database_projects_kanji_metadata(tmp_path):
    path = tmp_path / "dictionary.sqlite"
    make_legacy_db(path)

    result = Translator(SqliteDictionaryStore(path)).lookup_kanji(KanjiQuery("読"))

    assert result.entries[0].meanings == ("reading",)
    assert result.entries[0].stats == (("strokes", "14"),)


def test_cached_tag_provenance_belongs_to_each_result_record(tmp_path):
    path = tmp_path / "dictionary.sqlite"
    make_legacy_db(path)
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, 2, "詠む", "よむ", json.dumps(["to compose"]), "v1", 1456361),
    )
    connection.execute("INSERT INTO keys VALUES (?, ?, ?)", (1, "詠む", 2))
    connection.commit()
    connection.close()
    translator = Translator(SqliteDictionaryStore(path))

    first = translator.lookup_terms(TermQuery("読む")).entries[0]
    second = translator.lookup_terms(TermQuery("詠む")).entries[0]

    assert first.definitions[0].tags[0].source.record_id == 1
    assert second.definitions[0].tags[0].source.record_id == 2


def test_decoded_entry_cache_honors_configured_limit(tmp_path):
    path = tmp_path / "dictionary.sqlite"
    make_legacy_db(path)
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, 2, "詠む", "よむ", json.dumps(["to compose"]), "v1", 1456361),
    )
    connection.execute("INSERT INTO keys VALUES (?, ?, ?)", (1, "詠む", 2))
    connection.commit()
    connection.close()
    events: list[str] = []

    class Observer:
        @staticmethod
        def hit():
            events.append("hit")

        @staticmethod
        def miss():
            events.append("miss")

        @staticmethod
        def eviction():
            events.append("eviction")

    store = SqliteDictionaryStore(path, entry_cache_max=1, cache_observer=Observer())
    translator = Translator(store)

    translator.lookup_terms(TermQuery("読む"))
    translator.lookup_terms(TermQuery("読む"))
    translator.lookup_terms(TermQuery("詠む"))

    assert translator.decoded_entry_count() == 1
    assert events == ["miss", "hit", "miss", "eviction"]


def test_search_applies_configured_dictionary_priority_before_limit(tmp_path):
    path = tmp_path / "dictionary.sqlite"
    make_legacy_db(path)
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO dictionaries VALUES (2, 'Preferred', 1)")
    connection.execute(
        "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?)",
        (2, 1, "読める", "よめる", json.dumps(["can read"]), "", 2),
    )
    connection.commit()
    connection.close()

    result = Translator(SqliteDictionaryStore(path)).search_terms(
        SearchQuery("読*", dictionaries=("Preferred", "Core"), max_results=1)
    )

    assert result.entries[0].definitions[0].source.dictionary == "Preferred"

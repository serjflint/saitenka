from __future__ import annotations

import json
import sqlite3
import zipfile

import pytest
from yomitanlite.archive import DictionaryArchive
from yomitanlite.store import TermSearch

from yomitanlite import (
    ArchiveLimits,
    Capability,
    DictionaryArchiveError,
    DictionaryDatabase,
    ImportRequest,
    KanjiQuery,
    SearchQuery,
    SqliteDictionaryStore,
    TermQuery,
    Translator,
)


def write_dictionary(path, *, root="", title="Core", term="読む", extra_terms=(), malformed=False):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            root + "index.json",
            json.dumps({"title": title, "format": 3, "revision": "r1"}),
        )
        terms = [
            [value, "よむ", "v1", "v1", 5, ["to read"], 1456360 + index, "common"]
            for index, value in enumerate((term, *extra_terms))
        ]
        archive.writestr(root + "term_bank_1.json", "{" if malformed else json.dumps(terms))
        archive.writestr(
            root + "tag_bank_1.json",
            json.dumps(
                [
                    ["v1", "partOfSpeech", 2, "ichidan verb", 1],
                    ["common", "frequency", 1, "common term", 2],
                ]
            ),
        )
        archive.writestr(
            root + "term_meta_bank_1.json",
            json.dumps(
                [
                    [term, "freq", {"reading": "よむ", "frequency": 42}],
                    [
                        term,
                        "pitch",
                        {
                            "reading": "よむ",
                            "pitches": [{"position": 0, "nasal": 1, "devoice": [2]}],
                        },
                    ],
                    [
                        term,
                        "ipa",
                        {"reading": "よむ", "transcriptions": [{"ipa": "[jo̞mɯ]"}]},
                    ],
                ]
            ),
        )
        archive.writestr(
            root + "kanji_bank_1.json",
            json.dumps([["読", "ドク", "よ.む", "common", ["reading"], {"strokes": "14"}]]),
        )
        archive.writestr(root + "kanji_meta_bank_1.json", json.dumps([["読", "freq", 123]]))
        archive.writestr(root + "media/icon.svg", b"<svg/>")
    return path


def test_nested_dictionary_import_preserves_yomitan_fields(tmp_path):
    archive = write_dictionary(tmp_path / "core.zip", root="bundle/")
    database = DictionaryDatabase(tmp_path / "dictionary.sqlite")

    info = database.import_dictionary(ImportRequest(archive, imported_at="2026-08-11T00:00:00Z"))
    translator = Translator(SqliteDictionaryStore(database.path))
    term = translator.lookup_terms(TermQuery("読む")).entries[0]
    kanji = translator.lookup_kanji(KanjiQuery("読")).entries[0]

    assert info.title == "Core" and info.revision == "r1"
    assert term.headwords[0].tags[0].name == "common"
    assert term.definitions[0].tags[0].notes == "ichidan verb"
    assert term.score == 5 and term.sequence == 1456360
    assert term.frequencies[0].value == 42
    assert term.pronunciations[0].pitch_positions == (0,)
    assert term.pronunciations[0].nasal_morae == (1,)
    assert term.pronunciations[0].devoiced_morae == (2,)
    assert term.pronunciations[1].ipa == "[jo̞mɯ]"
    assert kanji.frequencies[0].value == 123
    assert translator.exact_terms(("読む", "よむ")) == frozenset({"読む"})
    assert translator.search_terms(SearchQuery("読*")).entries[0].headwords[0].term == "読む"
    assert translator.media_for("Core", ("media/icon.svg",)) == {"media/icon.svg": b"<svg/>"}


def test_search_limit_is_enforced_by_the_store(tmp_path):
    archive = write_dictionary(tmp_path / "many.zip", extra_terms=("詠む", "読み手", "読者"))
    database = DictionaryDatabase(tmp_path / "dictionary.sqlite")
    database.import_dictionary(archive)

    result = SqliteDictionaryStore(database.path).search_terms(TermSearch(("*",), limit=2))

    assert len(result) == 2


def test_reimport_atomically_replaces_one_title(tmp_path):
    database = DictionaryDatabase(tmp_path / "dictionary.sqlite")
    database.import_dictionary(write_dictionary(tmp_path / "first.zip", term="読む"))
    database.import_dictionary(write_dictionary(tmp_path / "second.zip", term="詠む"))

    translator = Translator(SqliteDictionaryStore(database.path))
    assert not translator.lookup_terms(TermQuery("読む")).entries
    assert translator.lookup_terms(TermQuery("詠む")).entries
    assert [item.title for item in database.list_dictionaries()] == ["Core"]


def test_failed_reimport_leaves_previous_dictionary_intact(tmp_path):
    database = DictionaryDatabase(tmp_path / "dictionary.sqlite")
    database.import_dictionary(write_dictionary(tmp_path / "good.zip"))

    with pytest.raises(DictionaryArchiveError, match="invalid JSON"):
        database.import_dictionary(write_dictionary(tmp_path / "bad.zip", malformed=True))

    assert Translator(SqliteDictionaryStore(database.path)).lookup_terms(TermQuery("読む")).entries


def test_cancelled_import_rolls_back_every_table(tmp_path):
    database = DictionaryDatabase(tmp_path / "dictionary.sqlite")
    archive = write_dictionary(tmp_path / "core.zip")

    with pytest.raises(DictionaryArchiveError, match="cancelled"):
        database.import_dictionary(ImportRequest(archive, is_cancelled=lambda: True))

    assert database.list_dictionaries() == ()


def test_unsafe_member_is_rejected_without_extraction(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("index.json", json.dumps({"title": "Unsafe", "format": 3}))
        output.writestr("../outside.txt", "nope")

    with pytest.raises(DictionaryArchiveError, match="unsafe archive member"):
        DictionaryDatabase(tmp_path / "dictionary.sqlite").import_dictionary(archive)
    assert not (tmp_path.parent / "outside.txt").exists()


def test_archive_constructor_closes_zip_when_validation_fails(monkeypatch, tmp_path):
    class UnsafeMember:
        filename = "../outside.txt"
        flag_bits = 0
        file_size = 0

    class FakeZip:
        closed = False

        def infolist(self):
            return [UnsafeMember()]

        def close(self):
            self.closed = True

    opened = FakeZip()
    monkeypatch.setattr(zipfile, "ZipFile", lambda _path: opened)

    with pytest.raises(DictionaryArchiveError, match="unsafe archive member"):
        DictionaryArchive(tmp_path / "unsafe.zip")

    assert opened.closed


def test_archive_file_limit_is_enforced(tmp_path):
    archive = write_dictionary(tmp_path / "core.zip")
    request = ImportRequest(archive, limits=ArchiveLimits(max_files=1))

    with pytest.raises(DictionaryArchiveError, match="too many files"):
        DictionaryDatabase(tmp_path / "dictionary.sqlite").import_dictionary(request)


def test_import_tolerates_wrong_stored_crc(tmp_path):
    archive = write_dictionary(tmp_path / "core.zip")
    raw = bytearray(archive.read_bytes())
    central = raw.find(b"PK\x01\x02")
    local = raw.find(b"PK\x03\x04")
    raw[central + 16 : central + 20] = b"\x00\x00\x00\x00"
    raw[local + 14 : local + 18] = b"\x00\x00\x00\x00"
    archive.write_bytes(raw)

    info = DictionaryDatabase(tmp_path / "dictionary.sqlite").import_dictionary(archive)

    assert info.title == "Core"


def test_occurrence_counts_become_dense_frequency_ranks(tmp_path):
    archive = tmp_path / "frequency.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(
            "index.json",
            json.dumps(
                {
                    "title": "Frequency",
                    "format": 3,
                    "frequencyMode": "occurrence-based",
                }
            ),
        )
        output.writestr(
            "term_meta_bank_1.json",
            json.dumps(
                [["一", "freq", 100], ["二", "freq", 100], ["三", "freq", 50], ["四", "freq", 10]]
            ),
        )
    database = DictionaryDatabase(tmp_path / "dictionary.sqlite")
    database.import_dictionary(archive)

    frequencies = SqliteDictionaryStore(database.path).find_frequencies(
        (("一", ""), ("二", ""), ("三", ""), ("四", "")), ()
    )

    assert [item.value for item in frequencies] == [1, 1, 2, 3]
    assert database.list_dictionaries()[0].capabilities == frozenset({Capability.IMPORT})


def test_import_migrates_the_existing_saitenka_schema(tmp_path):
    path = tmp_path / "dictionary.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE dictionaries(
          id INTEGER PRIMARY KEY, title TEXT UNIQUE, kind TEXT, import_order INTEGER,
          source_name TEXT, revision TEXT, imported_at TEXT, schema_version INTEGER);
        CREATE TABLE entries(
          dict_id INTEGER, id INTEGER, term TEXT, reading TEXT, glossary TEXT, tags TEXT, seq INTEGER,
          PRIMARY KEY(dict_id, id));
        CREATE TABLE keys(dict_id INTEGER, key TEXT, id INTEGER);
        CREATE TABLE kanji(
          dict_id INTEGER, chr TEXT, onyomi TEXT, kunyomi TEXT, tags TEXT, meanings TEXT, stats TEXT,
          PRIMARY KEY(dict_id, chr));
        CREATE TABLE term_meta(
          dict_id INTEGER, term TEXT, mode TEXT, reading TEXT, rank INTEGER, disp TEXT, positions TEXT);
        CREATE TABLE tags(
          dict_id INTEGER, code TEXT, name TEXT, ord INTEGER, category TEXT, notes TEXT);
        CREATE TABLE media(
          dict_id INTEGER, path TEXT, png BLOB, PRIMARY KEY(dict_id, path));
        """
    )
    connection.close()

    database = DictionaryDatabase(path)
    database.import_dictionary(write_dictionary(tmp_path / "core.zip"))

    result = Translator(SqliteDictionaryStore(path)).lookup_terms(TermQuery("読む"))
    assert result.entries[0].score == 5


def test_result_limit_is_applied_after_score_ordering(tmp_path):
    archive = tmp_path / "scores.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("index.json", json.dumps({"title": "Scores", "format": 3}))
        output.writestr(
            "term_bank_1.json",
            json.dumps(
                [
                    ["語", "ご", "", "", 1, ["low"]],
                    ["語", "かたり", "", "", 100, ["high"]],
                ]
            ),
        )
    database = DictionaryDatabase(tmp_path / "dictionary.sqlite")
    database.import_dictionary(archive)

    result = Translator(SqliteDictionaryStore(database.path)).lookup_terms(
        TermQuery("語", max_results=1)
    )

    assert result.entries[0].definitions[0].content == ("high",)

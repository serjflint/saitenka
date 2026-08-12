"""Characterization bridge between Saitenka's facade and saitenka-dict's semantic surface."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import dicthelp
from saitenka_dict import (
    Capability,
    Definition,
    DictionaryDatabase,
    Headword,
    KanjiEntry,
    KanjiResult,
    Pronunciation,
    SourceTrace,
    SqliteDictionaryStore,
    Tag,
    TermEntry,
    TermQuery,
    TermResult,
    TermResultMode,
    Translator,
)

from saitenka.app.config import DictDbOptions
from saitenka.app.dictdb import DictionaryDb
from saitenka.app.dictionary import DictionarySet
from saitenka.app.source_adapter import DictionarySourceAdapter, SourceAdapterOptions
from saitenka.app.tokenize import Token
from saitenka.model import Style
from saitenka.render.sc_adapter import walk

FIXTURES = Path(__file__).parent / "fixtures"


def _dictionary(
    path,
    title,
    glossary,
    *,
    term="読む",
    reading="よむ",
    score=5,
    sequence=1456360,
    sequenced=False,
):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "index.json", json.dumps({"title": title, "format": 3, "sequenced": sequenced})
        )
        archive.writestr(
            "term_bank_1.json",
            json.dumps(
                [[term, reading, "v1", "v1", score, glossary, sequence, "v1"]],
                ensure_ascii=False,
            ),
        )
        archive.writestr(
            "tag_bank_1.json",
            json.dumps([["v1", "partOfSpeech", 2, "ichidan verb", 1]]),
        )
        archive.writestr(
            "image.gif",
            b"GIF89a" + (35).to_bytes(2, "little") + (20).to_bytes(2, "little"),
        )
    return str(path)


def test_extracted_split_surface_agrees_with_the_stable_dictionary_facade(tmp_path):
    first = _dictionary(tmp_path / "first.zip", "First", ["to read"])
    second = _dictionary(
        tmp_path / "second.zip",
        "Second",
        [{"type": "structured-content", "content": {"tag": "b", "content": "読むこと"}}],
    )
    current = dicthelp.load_set([first, second])

    extracted = Translator(SqliteDictionaryStore(current.dicts[0].db.path)).lookup_terms(
        TermQuery("読む", mode=TermResultMode.SPLIT)
    )

    current_rows = [
        (
            hit.term,
            hit.reading,
            hit.glossary,
            hit.seq if hit.seq is not None else -1,
            dictionary.title,
        )
        for dictionary in current.dicts
        for hit in dictionary.lookup("読む")
    ]
    extracted_rows = [
        (
            entry.headwords[0].term,
            entry.headwords[0].reading,
            list(entry.definitions[0].content),
            entry.sequence,
            entry.definitions[0].source.dictionary,
        )
        for entry in extracted.entries
    ]
    assert extracted_rows == current_rows


def test_extracted_group_surface_preserves_panel_source_order_and_tags(tmp_path):
    first = _dictionary(tmp_path / "first.zip", "First", ["to read"])
    second = _dictionary(tmp_path / "second.zip", "Second", ["reading"])
    current = dicthelp.load_set([first, second])
    token = Token("読む", "読む", "よむ", "動詞", 0, 2)

    panel_entry = current.entry_for(token)
    semantic_entry = (
        Translator(SqliteDictionaryStore(current.dicts[0].db.path))
        .lookup_terms(TermQuery("読む", mode=TermResultMode.GROUP))
        .entries[0]
    )

    assert [definition.source.dictionary for definition in semantic_entry.definitions] == [
        definition.dict_name for definition in panel_entry.defs
    ]
    assert [tag.name for tag in semantic_entry.definitions[0].tags] == panel_entry.defs[0].tags


def test_overlay_can_swap_to_the_extracted_source_without_renderer_changes(tmp_path):
    archive = _dictionary(tmp_path / "core.zip", "Core", ["to read"])
    database = DictionaryDatabase(tmp_path / "dictionary.sqlite")
    database.import_dictionary(archive)
    adapter = DictionarySourceAdapter(Translator(SqliteDictionaryStore(database.path)))
    token = Token("読む", "読む", "よむ", "動詞", 0, 2)

    entry = adapter.entry_for(token)
    card = adapter.card_for(token)

    assert entry.reading == "よむ"
    assert entry.defs[0].dict_name == "Core" and entry.defs[0].content == ["to read"]
    assert card.expression == "読む" and card.glosses == ("to read",)


def test_production_source_unwraps_jitendex_structured_content_before_rendering(tmp_path):
    structured = json.loads((FIXTURES / "sc_jitendex_nested.json").read_text(encoding="utf-8"))
    archive = _dictionary(
        tmp_path / "jitendex.zip",
        "Jitendex",
        [structured],
        term="鳥",
        reading="とり",
    )
    database = DictionaryDb.open(tmp_path / "dictionary.sqlite")
    database.import_zip(archive, imported_at=dicthelp.AT)
    current = DictionarySet.from_db(database, ["Jitendex"])
    token = Token("鳥", "鳥", "とり", "名詞", 0, 1)

    assert current.source is not None
    entry = current.entry_for(token)
    blocks = walk(entry.defs[0].content, Style(size=26))

    assert [
        (
            block.marker,
            block.indent,
            "".join(
                "".join(span.text for span in item.base)
                if hasattr(item, "base")
                else getattr(item, "text", "")
                for item in block.flow
            ),
        )
        for block in blocks
    ] == [
        ("＊", 0, "noun"),
        ("①", 1, "bird"),
        (None, 2, "鳥が鳴いた。"),
        (None, 2, "A bird sang."),
        ("②", 1, "bird meat"),
        ("", 1, "fowl"),
        ("", 1, "poultry"),
        (None, 2, "See: 鶏"),
        (None, 0, "JMdict"),
    ]


def test_overlay_dispatches_search_and_exact_headword_attestation(tmp_path):
    archive = _dictionary(tmp_path / "core.zip", "Core", ["to read"])
    database = DictionaryDatabase(tmp_path / "dictionary.sqlite")
    database.import_dictionary(archive)
    adapter = DictionarySourceAdapter(Translator(SqliteDictionaryStore(database.path)))

    result = adapter.search("読")

    assert result.defs[0].dict_name == "検索 “読” · 1件"
    assert adapter.terms_exist(("読む", "よむ")) == {"読む"}


def test_overlay_preloads_structured_content_media_from_the_source(tmp_path):
    archive = _dictionary(
        tmp_path / "media.zip",
        "Media",
        [{"type": "structured-content", "content": {"tag": "img", "path": "image.gif"}}],
    )
    database = DictionaryDatabase(tmp_path / "dictionary.sqlite")
    database.import_dictionary(archive)
    adapter = DictionarySourceAdapter(Translator(SqliteDictionaryStore(database.path)))
    token = Token("読む", "読む", "よむ", "動詞", 0, 2)

    entry = adapter.entry_for(token)

    assert entry.defs[0].media["image.gif"].startswith(b"GIF89a")


def test_overlay_lookup_accepts_the_caller_inflected_form(tmp_path):
    archive = _dictionary(tmp_path / "core.zip", "Core", ["to read"])
    current = dicthelp.load_set([archive])
    adapter = DictionarySourceAdapter(Translator(SqliteDictionaryStore(current.dicts[0].db.path)))
    unknown = Token("missing", "missing", "よむ", "動詞", 0, 7)

    entry = adapter.entry_for(unknown, inflected="読む")

    assert entry.reading == "よむ"


def test_production_source_preserves_french_deinflection_and_chain(tmp_path):
    archive = _dictionary(
        tmp_path / "french.zip",
        "French",
        ["umbrella"],
        term="parapluie",
        reading="",
    )
    current = dicthelp.load_set([archive])
    current.language = "fr"
    token = Token("parapluies", "parapluies", "", "NOUN", 0, 10)

    entry = current.entry_for(token)

    assert "parapluie" in json.dumps(entry.headword, ensure_ascii=False)
    assert entry.inflection_chain


def test_production_source_preserves_configured_priority_and_card_provenance(tmp_path):
    imported_first = _dictionary(
        tmp_path / "plain.zip", "Plain", ["plain gloss"], score=100, sequence=999
    )
    configured_first = _dictionary(
        tmp_path / "jmdict.zip",
        "JMdict",
        ["jmdict gloss"],
        score=1,
        sequence=1456360,
        sequenced=True,
    )
    database = DictionaryDb.open(tmp_path / "dictionary.sqlite", DictDbOptions(persist_seq=True))
    current = dicthelp.load_set([imported_first, configured_first], on=database)
    current.dicts.reverse()
    token = Token("読む", "読む", "よむ", "動詞", 0, 2)

    entry = current.entry_for(token)
    card = current.card_for(token)
    semantic = current.source.lookup_terms(TermQuery("読む", dictionaries=("JMdict", "Plain")))

    assert [definition.dict_name for definition in entry.defs] == ["JMdict", "Plain"]
    assert semantic.entries[0].sequence == 1456360
    assert card.glosses == ("jmdict gloss",)
    assert card.idseq == "1456360"


def test_adapter_search_deduplicates_headword_identity_and_honors_limit():
    class Source:
        capabilities = frozenset({Capability.TERM_LOOKUP, Capability.SEARCH})

        def lookup_terms(self, query):
            del query
            return TermResult((), 0, 0)

        def lookup_kanji(self, query):
            del query
            return KanjiResult(())

        def search_terms(self, query):
            del query
            duplicate = TermEntry((Headword("読む", "よむ"),), (Definition(("to read",)),))
            extra = TermEntry((Headword("読者", "どくしゃ"),), (Definition(("reader",)),))
            return TermResult((duplicate, duplicate, extra), 1, 0)

    result = DictionarySourceAdapter(Source()).search("読", limit=1)

    assert result.defs[0].dict_name == "検索 “読” · 1件"


def test_card_sequence_comes_from_the_definition_that_supplies_the_gloss():
    class Source:
        capabilities = frozenset({Capability.TERM_LOOKUP})

        def lookup_terms(self, query):
            del query
            trace = SourceTrace("JMdict")
            return TermResult(
                (
                    TermEntry(
                        (Headword("読む", "よむ"),),
                        (
                            Definition((), source=trace, sequence=999),
                            Definition(("to read",), source=trace, sequence=1456360),
                        ),
                        sequence=999,
                    ),
                ),
                2,
                2,
            )

        def lookup_kanji(self, query):
            del query
            return KanjiResult(())

    adapter = DictionarySourceAdapter(
        Source(), SourceAdapterOptions(sequence_dictionaries=("JMdict",))
    )
    token = Token("読む", "読む", "よむ", "動詞", 0, 2)

    card = adapter.card_for(token)

    assert card.glosses == ("to read",)
    assert card.idseq == "1456360"


def test_overlay_lookup_falls_back_to_the_token_reading(tmp_path):
    archive = _dictionary(tmp_path / "core.zip", "Core", ["to read"])
    current = dicthelp.load_set([archive])
    adapter = DictionarySourceAdapter(Translator(SqliteDictionaryStore(current.dicts[0].db.path)))
    unknown = Token("missing", "missing", "よむ", "動詞", 0, 7)

    entry = adapter.entry_for(unknown)

    assert entry.reading == "よむ"


def test_overlay_keeps_string_pitch_notation_out_of_the_numeric_graph():
    class Source:
        capabilities = frozenset({Capability.TERM_LOOKUP})

        def lookup_terms(self, query):
            del query
            return TermResult(
                (
                    TermEntry(
                        (Headword("読む", "よむ"),),
                        (Definition(("to read",)),),
                        pronunciations=(
                            Pronunciation("IPA", "よむ", (), "[jo̞mɯ]"),
                            Pronunciation("Pattern", "よむ", ("LHH",)),
                            Pronunciation("Pitch", "よむ", (0,)),
                        ),
                    ),
                ),
                2,
                2,
            )

        def lookup_kanji(self, query):
            del query
            return KanjiResult(())

    token = Token("読む", "読む", "よむ", "動詞", 0, 2)

    entry = DictionarySourceAdapter(Source()).entry_for(token)

    assert entry.pitches == [("よむ", ((0, (), ()),))]
    assert [pill.value for pill in entry.freqs] == ["よむ [LHH]", "よむ [0]"]


def test_overlay_preserves_kanji_stat_labels_sections_and_stroke_font():
    class Source:
        capabilities = frozenset({Capability.KANJI_LOOKUP})

        def lookup_terms(self, query):
            del query
            return TermResult((), 0, 0)

        def lookup_kanji(self, query):
            del query
            source = SourceTrace("KANJIDIC")
            stat_tag = Tag("strokes", "misc", "Stroke count", 1, source=source)
            return KanjiResult(
                (
                    KanjiEntry(
                        "読",
                        ("ドク",),
                        ("よ.む",),
                        ("read",),
                        stats=(("strokes", "14"),),
                        source=source,
                        stat_tags=(("strokes", stat_tag),),
                    ),
                )
            )

    entry = DictionarySourceAdapter(Source()).kanji_for("読", stroke_order=True)

    assert entry is not None and entry.headword_font is not None
    assert entry.defs[0].content[-2]["content"] == "Statistics"
    assert entry.defs[0].content[-1]["content"][0]["content"][0]["content"] == "Stroke count"

"""Lookup reference dictionaries imported through the real dictionary store."""

import json
from pathlib import Path

import pytest
from dicthelp import db, meta_zip, term_zip
from dirty_equals import IsPartialDict
from saitenka_tokenize.japanese import Token

from saitenka.app.dictionary import DictionarySet

DATA = json.loads(
    (Path(__file__).parent / "fixtures/dictionary_reference.json").read_text(encoding="utf-8")
)["lookup"]


@pytest.mark.parametrize("record", DATA, ids=lambda r: r["id"])
def test_imported_dictionary_lookup_reference(tmp_path, record):
    v = record["input"]
    database = db()
    dictionary_rows = [
        database.import_zip(
            term_zip(tmp_path / f"dict-{i}.zip", item["title"], item["entries"]),
            imported_at="2026-07-23T00:00:00",
            import_order=i,
        )
        for i, item in enumerate(v.get("dictionaries", []))
    ]
    frequency_rows = [
        database.import_zip(
            meta_zip(tmp_path / f"freq-{i}.zip", item["title"], "freq", item["entries"]),
            imported_at="2026-07-23T00:00:00",
        )
        for i, item in enumerate(v.get("frequencies", []))
    ]
    dictionaries = DictionarySet.from_rows(
        database, dictionary_rows, frequency_rows, [], language=v.get("language", "jp")
    )
    token = Token(**v["token"])
    terms = tuple(v.get("extra_terms", []))
    entry = dictionaries.entry_for(token, extra_terms=terms)
    cards = dictionaries.cards_for(token, extra_terms=terms)
    card = dictionaries.card_for(token, extra_terms=terms)

    actual = {
        "titles": [item.title for item in dictionaries.dicts],
        "headword": list(entry.headword),
        "reading": entry.reading,
        "definitions": [{"dictionary": d.dict_name, "content": d.content} for d in entry.defs],
        "inflection-chain": list(entry.inflection_chain),
        "groups": [
            {
                "reading": g.reading,
                "card_index": g.card_index,
                "definitions": [d.content for d in g.defs],
            }
            for g in entry.groups
        ],
        "cards": [
            {"expression": c.expression, "reading": c.reading, "glosses": list(c.glosses)}
            for c in cards
        ],
        "default-card": {
            "expression": card.expression,
            "reading": card.reading,
            "glosses": list(card.glosses),
            "glossary_html": card.glossary_html,
        },
        "has-terms": {term: dictionaries.has_term(term) for term in v.get("has_terms", [])},
    }
    assert actual == IsPartialDict(**record["expected"])

from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

PROTOCOL = "saitenka.dictionary/v0"
CAPABILITY = "dictionary-v0"
ANSWER_KEYS = frozenset({"expect"})


def scenario_request(scenario: dict) -> dict:
    if (
        set(scenario) != {"scenario_id", "mode", "cases", "expect"}
        or scenario.get("mode") != "lookup"
    ):
        raise ValueError("invalid dictionary-v0 scenario")
    return {
        "type": "run",
        "protocol": PROTOCOL,
        **{key: value for key, value in scenario.items() if key not in ANSWER_KEYS},
    }


def validate_request(request: dict) -> None:
    if (
        ANSWER_KEYS & request.keys()
        or request.get("type") != "run"
        or request.get("protocol") != PROTOCOL
        or request.get("mode") != "lookup"
        or set(request) != {"type", "protocol", "scenario_id", "mode", "cases"}
    ):
        raise ValueError("invalid dictionary-v0 request envelope")


def _term_zip(path: Path, title: str, entries: list) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("index.json", json.dumps({"title": title, "format": 3}))
        bank = [
            [term, reading, "", "", 0, glossary, i + 1, ""]
            for i, (term, reading, glossary) in enumerate(entries)
        ]
        archive.writestr("term_bank_1.json", json.dumps(bank, ensure_ascii=False))
    return str(path)


def _freq_zip(path: Path, title: str, entries: list) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("index.json", json.dumps({"title": title, "format": 3}))
        archive.writestr(
            "term_meta_bank_1.json",
            json.dumps([[term, "freq", data] for term, data in entries], ensure_ascii=False),
        )
    return str(path)


def evaluate(request: dict) -> list[dict]:
    validate_request(request)
    return [_evaluate_case(case) for case in request["cases"]]


def _evaluate_case(case: dict) -> dict:
    from saitenka_tokenize.japanese import Token

    from saitenka.app.dictdb import DictionaryDb
    from saitenka.app.dictionary import DictionarySet

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        db = DictionaryDb.open(root / "dictionary.sqlite")
        dict_rows = [
            db.import_zip(
                _term_zip(root / f"dict-{i}.zip", item["title"], item["entries"]),
                imported_at="2026-07-23T00:00:00",
                import_order=i,
            )
            for i, item in enumerate(case.get("dictionaries", []))
        ]
        freq_rows = [
            db.import_zip(
                _freq_zip(root / f"freq-{i}.zip", item["title"], item["entries"]),
                imported_at="2026-07-23T00:00:00",
            )
            for i, item in enumerate(case.get("frequencies", []))
        ]
        dictionaries = DictionarySet.from_rows(
            db, dict_rows, freq_rows, [], language=case.get("language", "jp")
        )
        token = Token(**case["token"])
        extra_terms = tuple(case.get("extra_terms", []))
        entry = dictionaries.entry_for(token, extra_terms=extra_terms)
        cards = dictionaries.cards_for(token, extra_terms=extra_terms)
        default_card = dictionaries.card_for(token, extra_terms=extra_terms)
        observed: dict[str, object] = {}
        for key in case["observe"]:
            if key == "titles":
                observed[key] = [item.title for item in dictionaries.dicts]
            elif key == "headword":
                observed[key] = list(entry.headword)
            elif key == "reading":
                observed[key] = entry.reading
            elif key == "definitions":
                observed[key] = [
                    {"dictionary": definition.dict_name, "content": definition.content}
                    for definition in entry.defs
                ]
            elif key == "inflection-chain":
                observed[key] = list(entry.inflection_chain)
            elif key == "groups":
                observed[key] = [
                    {
                        "reading": group.reading,
                        "card_index": group.card_index,
                        "definitions": [definition.content for definition in group.defs],
                    }
                    for group in entry.groups
                ]
            elif key == "cards":
                observed[key] = [
                    {
                        "expression": card.expression,
                        "reading": card.reading,
                        "glosses": list(card.glosses),
                    }
                    for card in cards
                ]
            elif key == "default-card":
                observed[key] = {
                    "expression": default_card.expression,
                    "reading": default_card.reading,
                    "glosses": list(default_card.glosses),
                    "glossary_html": default_card.glossary_html,
                }
            elif key == "has-terms":
                observed[key] = {term: dictionaries.has_term(term) for term in case["has_terms"]}
            else:
                raise ValueError(f"unknown dictionary observation: {key!r}")
        store = dictionaries.source.store
        while store._connections:
            store._connections.pop().close()
        store._local.connection = None
        db._reads.close()
        return observed

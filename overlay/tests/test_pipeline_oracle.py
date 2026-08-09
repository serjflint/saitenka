"""End-to-end lookup oracle: ``(cue, hover position) → resolved Entry`` on the REAL build assembly.

Every French bug of 2026-08-10 shipped with all component unit tests green, because each lived in the
*assembly* — ``(config + profile) → build deps → tokenize(cue) → hit-test(pos) → deinflect → lookup``
— not in any one component. A ``DictionarySet`` unit test constructs the set with ``language="fr"`` by
hand and can't see the wiring bug (the run path built it ``language="jp"`` and deinflection no-oped).

So this drives the ACTUAL run assembly: ``resolve_launch_identity`` → ``_build_run_deps`` (which calls
``build_reader_deps`` and opens the real, hermetic ``DictionaryDb``) → ``Reader.set_subtitle`` (real
tokenize) → ``tooltip.resolve_hover`` (forward longest-match) → ``entry_for_tok``. Fixtures are tiny
REAL Yomitan zips imported through the real importer, so the combined-dict import path is covered too.
Assertions are structural invariants (headword, inflection reasons, which gloss, group order, the
localized not-found message) — never pixels — modelled on Yomitan's ``translator.test.js``.
"""

from __future__ import annotations

import pytest
from dicthelp import AT, db, term_zip
from overlay.app.cli_run import RunDepsRequest, _build_run_deps
from overlay.app.controller import Reader
from overlay.app.languages import MAIN_LANG
from overlay.app.profiles import resolve_launch_identity
from overlay.app.tooltip import entry_for_tok, resolve_hover
from overlay.sc.walk import _text_of
from util import FakeIPC

pytest.importorskip(
    "saitenka_deinflect"
)  # the FR fold/JP chain need the GPL add-on; skip if absent

# One case per shipped bug + regressions. ``at`` is the hover char-offset into ``cue``; keys are the
# invariants to assert (all optional): ``head`` (resolved headword), ``def_contains`` (a gloss
# substring), ``reasons`` (inflection chain), ``groups`` (stacked-entry order), ``not_found`` (which
# localized "not found" message), ``xfail`` (an OPEN bug — strict, so it flips to a failure when fixed).
# Hand-authored + Python-local (not a vendored corpus), so inline, not an external JSON data file.
CASES: list[dict] = [
    {
        "name": "fr_plural_folds_to_base_form",
        "profile": "fr",
        "cue": "de parapluies",
        "at": 3,
        "head": "parapluie",
        "def_contains": "umbrella",
        "groups": ["parapluie", "parapluies"],
    },
    {
        "name": "fr_verb_deinflects_to_infinitive",
        "profile": "fr",
        "cue": "il craint",
        "at": 3,
        "head": "craindre",
        "def_contains": "fear",
    },
    {
        "name": "fr_sentence_initial_article_is_decapitalized",
        "profile": "fr",
        "cue": "Le magasin",
        "at": 0,
        "head": "le",
        "def_contains": "article",
    },
    {
        "name": "fr_elision_clitic_resolves_to_its_lemma",
        "profile": "fr",
        "cue": "n'avait",
        "at": 0,
        "head": "ne",
        "def_contains": "negation",
    },
    {
        "name": "fr_decapitalized_hit_when_capitalized_in_text",
        "profile": "fr",
        "cue": "Ça va",
        "at": 0,
        "head": "ça",
        "def_contains": "that",
    },
    {
        "name": "fr_miss_is_english_not_japanese",
        "profile": "fr",
        "cue": "un truc zzqx",
        "at": 9,
        "not_found": "en",
    },
    {
        "name": "jp_inflected_verb_resolves_to_lemma",
        "profile": "jp",
        "cue": "ご飯を食べた",
        "at": 3,
        "head": "食べる",
        "def_contains": "eat",
        "reasons": ["-た"],
    },
    {
        "name": "jp_miss_stays_japanese",
        "profile": "jp",
        "cue": "本を食べた",
        "at": 0,
        "not_found": "jp",
    },
    {
        "name": "jp_honorific_prefix_should_surface_content_word",
        "profile": "jp",
        "cue": "お考え",
        "at": 0,
        "head": "考える",
        "xfail": "honorific-prefix bug: bare お surfaces 御, not the content word 考える",
    },
]

# Tiny hand-built term-banks — enough to reproduce every 2026-08-10 bug without a real 448k-entry dict.
# [term, reading, gloss]; readings only where a JP entry needs ruby.
_FIXTURES = {
    "fixture-fr": [
        ("parapluie", "", ["umbrella"]),
        ("parapluies", "", ["plural of parapluie"]),  # non-lemma form-entry: base must still win
        ("craindre", "", ["to fear"]),
        ("le", "", ["the (definite article)"]),
        ("magasin", "", ["shop"]),
        ("ne", "", ["not (negation particle)"]),
        ("ça", "", ["that; it"]),
    ],
    "fixture-jp": [
        ("食べる", "たべる", ["to eat"]),
        ("御", "", ["honorific prefix"]),
        ("考える", "かんがえる", ["to think"]),
    ],
}


def _cfg_for(profile: str) -> dict:
    """The raw config a launch of this profile starts from (a French named profile, or the JP default)."""
    if profile == "fr":
        return {
            "active_profile": "fr",
            "profiles": {"fr": {"language": "fr", "dicts": ["fixture-fr"]}},
        }
    return {"dicts": ["fixture-jp"]}


def _import_fixture(title: str, tmp_path) -> None:
    """Import one fixture term-bank into the per-test hermetic DB through the REAL importer."""
    db().import_zip(term_zip(tmp_path / f"{title}.zip", title, _FIXTURES[title]), imported_at=AT)


def _dict_set_via_run(ident):
    """Build the dict set through the REAL run seam (``_build_run_deps`` → ``build_reader_deps``), so a
    language-threading regression (the live bug: the run path defaulted the set to JP) fails here."""
    _scorer, _anki, _mine, dict_set = _build_run_deps(
        RunDepsRequest(
            mine=False,
            mine_deck="",
            mine_model="",
            mine_key="",
            mine_all_key="",
            mine_normalize_audio=False,
            mine_animated_screenshot=False,
            raw_mine={},
            known_cfg=None,
            known="",
            color=False,
            dict_titles=list(ident.cfg.get("dicts") or []),
            freq_titles=[],
            pitch_titles=[],
            language=ident.language,  # exactly what run_impl threads (active_profile.langs.main)
        )
    )
    assert (
        dict_set is not None
    )  # the fixture titles resolve → a real set (never the subs-only None)
    return dict_set


def _index_at(tokens, at: int) -> int:
    """The token whose char span contains hover offset ``at`` (single-line cues)."""
    return next(i for i, t in enumerate(tokens) if t.start <= at < t.end)


def _resolve(profile: str, cue: str, at: int, tmp_path):
    """Drive the whole pipeline and return ``(dict_set, tok, hover_terms, entry)``."""
    fixture = "fixture-fr" if profile == "fr" else "fixture-jp"
    _import_fixture(fixture, tmp_path)
    ident = resolve_launch_identity(_cfg_for(profile), profile_override=None, slang="ja,jpn,jp")
    dict_set = _dict_set_via_run(ident)
    reader = Reader(FakeIPC(), dict_set=dict_set, profile=ident.profile)
    reader.osd = (1920, 1080)
    reader.subtitle_language = MAIN_LANG  # main track → tokenize (not the plain secondary path)
    reader.set_subtitle(cue)
    idx = _index_at(reader.tokens, at)
    resolve_hover(reader, idx)  # forward longest-match → _hover_terms (the phrase/prefix seam)
    tok = reader.tokens[idx]
    entry = entry_for_tok(
        reader, tok, reader._inflected_surface(idx), extra_terms=reader._hover_terms
    )
    return dict_set, tok, reader._hover_terms, entry


def _all_text(entry) -> str:
    """Every gloss the panel would show, flattened — the fused defs AND each stacked group's defs."""
    parts: list[str] = []
    for defs in (entry.defs, *(g.defs for g in entry.groups)):
        for d in defs:
            parts.extend(n if isinstance(n, str) else _text_of(n) for n in d.content)
    return " ".join(parts)


def _params() -> list:
    return [
        pytest.param(
            c,
            id=c["name"],
            marks=(pytest.mark.xfail(reason=c["xfail"], strict=True),) if "xfail" in c else (),
        )
        for c in CASES
    ]


@pytest.mark.parametrize("case", _params())
def test_pipeline_resolves_expected_entry(case, tmp_path):
    dict_set, tok, hover_terms, entry = _resolve(case["profile"], case["cue"], case["at"], tmp_path)

    if "head" in case:
        assert dict_set.card_for(tok, extra_terms=hover_terms).expression == case["head"]
    if "def_contains" in case:
        assert case["def_contains"].lower() in _all_text(entry).lower()
    if "reasons" in case:
        assert entry.inflection_chain == case["reasons"]
    if "groups" in case:
        got = [c.expression for c in dict_set.cards_for(tok, extra_terms=hover_terms)]
        assert got == case["groups"]
    if (
        case.get("not_found") == "en"
    ):  # a French learner must not see a Japanese sentence (the ça bug)
        text = _all_text(entry)
        assert "not found" in text.lower() and "見つかり" not in text
    if case.get("not_found") == "jp":  # the byte-identical JP path keeps the Japanese message
        text = _all_text(entry)
        assert "見つかり" in text and "not found" not in text.lower()


def test_oracle_rejects_the_inflected_surface_as_headword(tmp_path):
    """Negative control: the oracle discriminates. The plural's headword is the base ``parapluie`` — a
    regression that let the inflected surface head the tooltip (the live bug) would flip this."""
    dict_set, tok, hover_terms, _entry = _resolve("fr", "de parapluies", 3, tmp_path)
    assert dict_set.card_for(tok, extra_terms=hover_terms).expression != "parapluies"

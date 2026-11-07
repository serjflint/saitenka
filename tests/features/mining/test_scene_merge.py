"""Kiku's declared grouping contract; no HTML guessing or invented readings."""

import html

import pytest
from hypothesis import given
from hypothesis import strategies as st
from saitenka_card import MineConfig
from saitenka_card.merge import SCENE_FIELDS, merge_scene, scene_ids, supports_kiku


def _scene(text="old"):
    return {
        "Sentence": text,
        "SentenceAudio": "[sound:old.m4a]",
        "Picture": '<img src="old.jpg">',
        "MiscInfo": "old source",
    }


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=50))
def test_kiku_merge_keeps_every_field_in_the_same_scene_and_repeats_are_noop(text):
    old = _scene()
    old.update(SentenceFurigana="古[ふる]い", SentenceTranslation="old translation")
    new = _scene(html.escape(text))
    new["SentenceFurigana"] = new["Sentence"]
    updated = merge_scene(old, new, 42, "123")
    assert scene_ids(updated) == ("42", "123")
    assert all(
        updated[name].startswith(f'<span data-group-id="42">{old[name]}</span>')
        for name in (
            "Sentence",
            "SentenceAudio",
            "MiscInfo",
            "SentenceFurigana",
            "SentenceTranslation",
        )
    )
    assert (
        updated["Picture"]
        == '<img data-group-id="42" src="old.jpg"><img data-group-id="123" src="old.jpg">'
    )
    assert updated["Sentence"].endswith(f'<span data-group-id="123">{html.escape(text)}</span>')
    assert updated["SentenceFurigana"].endswith(
        f'<span data-group-id="123">{html.escape(text)}</span>'
    )
    assert updated["SentenceTranslation"].endswith('<span data-group-id="123"></span>')
    assert merge_scene(updated, new, 42, "123") == {}


@pytest.mark.parametrize(
    "mutation",
    [
        "mismatch",
        "nested",
        "malformed",
        "mixed",
        "picture",
        "duplicate",
        "oversized",
        "empty-id",
        "empty-picture",
    ],
)
def test_kiku_refuses_ambiguous_existing_scenes(mutation):
    fields = merge_scene(_scene(), _scene("new"), 42, "123")
    fields["Sentence"] = {
        "mismatch": fields["Sentence"].replace('"123"', '"124"'),
        "nested": '<span data-group-id="42"><span data-group-id="123">x</span></span>',
        "malformed": '<span data-group-id="42">x',
        "mixed": fields["Sentence"] + "ungrouped text",
        "picture": fields["Sentence"],
        "empty-picture": fields["Sentence"],
        "empty-id": "<span data-group-id>x</span>",
        "duplicate": fields["Sentence"] + '<span data-group-id="123">x</span>',
        "oversized": fields["Sentence"].replace('"123"', '"9007199254740992"'),
    }[mutation]
    if mutation == "empty-picture":
        fields = _scene()
        fields["Picture"] = "<!-- no image -->"
    if mutation == "picture":
        fields["Picture"] = '<div data-group-id="42"><img src="old.jpg"></div>'
    with pytest.raises(ValueError):
        merge_scene(fields, _scene(), 42, "456")


@pytest.mark.parametrize(
    ("config", "supported"),
    [
        (MineConfig(), False),
        (MineConfig(model="Kiku"), False),
        (MineConfig(model="Senren", merge_adapter="kiku"), False),
        (MineConfig(model="Kiku", merge_adapter="kiku"), True),
        (MineConfig(model="Kiku", merge_adapter="kiku", fields={"sentence": "Other"}), False),
        (
            MineConfig(
                model="Kiku",
                merge_adapter="kiku",
                card_format={
                    "Sentence": "{sentence}",
                    "SentenceAudio": "{sentence-audio}",
                    "Picture": "{screenshot}",
                    "MiscInfo": "{misc}",
                    "Expression": "{expression}",
                },
            ),
            True,
        ),
        (
            MineConfig(
                model="Kiku", merge_adapter="kiku", card_format={"Sentence": "prefix {sentence}"}
            ),
            False,
        ),
    ],
)
def test_only_explicit_kiku_contract_enables_merge(config, supported):
    assert supports_kiku(config) is supported


def test_empty_optional_furigana_keeps_surface_visible_and_groups_aligned():
    fields = _scene()
    fields["SentenceFurigana"] = ""
    updated = merge_scene(fields, {**_scene("new"), "SentenceFurigana": "new"}, 42, "123")
    assert updated["SentenceFurigana"] == updated["Sentence"]
    assert scene_ids(updated) == ("42", "123")
    assert set(SCENE_FIELDS) <= set(updated)

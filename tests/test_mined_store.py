"""The durable mined-card ↔ episode/cue store (#253) at its own seam."""

import pytest

from saitenka.app.mined_store import MinedCard, MinedCardStore


def _store(tmp_path):
    return MinedCardStore(tmp_path / "mined.sqlite")


def test_record_stamps_episode_key_and_cue_from_the_video_name(tmp_path):
    store = _store(tmp_path)

    card = store.record(
        note_id=42,
        video_path=str(tmp_path / "[Grp] Show - 03 [1080p].mkv"),
        cue_start=12.5,
        cue_end=14.0,
        expression="本",
        reading="ほん",
        deck="Mining",
    )

    assert isinstance(card, MinedCard)
    assert (card.note_id, card.episode, card.cue_start, card.cue_end) == (42, 3, 12.5, 14.0)
    assert (card.expression, card.reading, card.deck) == ("本", "ほん", "Mining")
    assert card.title_match  # normalized title match-key is populated


def test_for_path_lists_only_this_episode_ordered_by_cue(tmp_path):
    store = _store(tmp_path)
    ep3 = str(tmp_path / "Show - 03.mkv")
    store.record(note_id=2, video_path=ep3, cue_start=9.0, cue_end=9.5, expression="猫")
    store.record(note_id=1, video_path=ep3, cue_start=1.0, cue_end=1.5, expression="本")
    store.record(
        note_id=3,
        video_path=str(tmp_path / "Show - 04.mkv"),
        cue_start=1.0,
        cue_end=1.5,
        expression="犬",
    )

    cards = store.for_path(ep3)

    assert [c.note_id for c in cards] == [1, 2]  # cue_start order, sibling episode excluded
    assert [c.expression for c in cards] == ["本", "猫"]


def test_for_path_survives_a_rename_via_the_shared_match_key(tmp_path):
    store = _store(tmp_path)
    store.record(
        note_id=7,
        video_path=str(tmp_path / "[Grp] Show - 05 [1080p].mkv"),
        cue_start=0.0,
        cue_end=1.0,
        expression="鍵",
    )

    # A differently-named copy of the same title+episode still lists the card (same (title_match, ep)).
    cards = store.for_path(tmp_path / "Show - 05.mkv")

    assert [c.note_id for c in cards] == [7]


def test_unparseable_episode_is_its_own_bucket_not_a_wildcard(tmp_path):
    store = _store(tmp_path)
    store.record(
        note_id=1,
        video_path=str(tmp_path / "random clip.mkv"),
        cue_start=0.0,
        cue_end=1.0,
        expression="あ",
    )
    store.record(
        note_id=2,
        video_path=str(tmp_path / "Show - 01.mkv"),
        cue_start=0.0,
        cue_end=1.0,
        expression="い",
    )

    no_episode = store.for_path(tmp_path / "random clip.mkv")

    assert [c.note_id for c in no_episode] == [
        1
    ]  # the ep-01 card does not leak into the None bucket


def test_re_mining_the_same_note_updates_in_place(tmp_path):
    store = _store(tmp_path)
    video = str(tmp_path / "Show - 02.mkv")
    store.record(note_id=9, video_path=video, cue_start=1.0, cue_end=2.0, expression="旧")
    store.record(note_id=9, video_path=video, cue_start=5.0, cue_end=6.0, expression="新")

    cards = store.for_path(video)

    assert [(c.note_id, c.cue_start, c.expression) for c in cards] == [(9, 5.0, "新")]


def test_by_note_id_round_trips_and_misses_return_none(tmp_path):
    store = _store(tmp_path)
    store.record(
        note_id=55,
        video_path=str(tmp_path / "Show - 01.mkv"),
        cue_start=0.0,
        cue_end=1.0,
        expression="水",
    )

    assert store.by_note_id(55).expression == "水"
    assert store.by_note_id(999) is None


def test_card_lookup_raises_on_a_missing_row(tmp_path):
    store = _store(tmp_path)

    with pytest.raises(KeyError):
        store.card(123)

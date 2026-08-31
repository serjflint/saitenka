"""Coloring: data loaders, N+1 algorithm, and the SubMiner priority model."""

from pathlib import Path

import dicthelp
import pytest
from saitenka_dict import FreqDict
from saitenka_tokenize.japanese import tokenize
from saitenka_wordstate import FUNCTION_POS, Scorer, mark_n_plus_one
from saitenka_wordstate.known import KnownForm, KnownWords

from saitenka.app.scoring import Coloring, Palette

PAL = Palette()


@pytest.fixture
def jlpt():
    """The bundled JLPT dict loaded from a per-test hermetic DB (imported on first use)."""
    return dicthelp.load_jlpt()


# --- data loaders ---------------------------------------------------------------------------------


def test_jlpt_levels(jlpt):
    assert jlpt.level("会う", None, "あう") == "N5"
    assert jlpt.level("相", None, "あい") == "N1"
    assert jlpt.level("完全に無い語", None, None) is None


def test_jlpt_keeps_highest_level(jlpt):
    # every mapped level is one of N1..N5
    assert set(jlpt.by_key.values()) <= {"N1", "N2", "N3", "N4", "N5"}


def test_freq_banding_math():
    assert FreqDict.band(1, top_x=10000, bands=5) == 1
    assert FreqDict.band(2000, top_x=10000, bands=5) == 1
    assert FreqDict.band(2001, top_x=10000, bands=5) == 2
    assert FreqDict.band(10000, top_x=10000, bands=5) == 5
    assert FreqDict.band(10001, top_x=10000, bands=5) is None


def test_known_words_reading_fallback():
    kw = KnownWords.from_set(["読む", "ほん"])
    assert kw.is_known("読む")
    assert kw.is_known("誰も知らない", "ほん")  # matches on the lemma-slot known form
    assert not kw.is_known("知らない語")


def test_known_words_reject_same_spelling_different_reading():
    """A card teaching 床/ゆか must NOT mark a subtitle 床 read とこ as known — the homograph guard.
    The correct reading still matches, and a kanji card resurfaces when written in kana."""
    kw = KnownWords.from_forms([KnownForm("床", "ゆか"), KnownForm("孫", "まご")])
    assert not kw.is_known("床", "床", "とこ")  # same kanji, different reading → not this word
    assert kw.is_known("床", "床", "ゆか")  # the taught reading matches
    assert kw.is_known(
        "まご", "まご", "まご"
    )  # kana token resurfaces the kanji card via its reading
    assert kw.is_known("床")  # no token reading to disambiguate → best-effort surface match holds


def test_known_words_surface_only_card_still_matches():
    """A card with no reading field can't disambiguate, so it keeps the best-effort surface match."""
    kw = KnownWords.from_set(["床"])
    assert kw.is_known("床", "床", "とこ")
    assert kw.is_known("床", "床", "ゆか")


# --- N+1 algorithm --------------------------------------------------------------------------------


def test_n_plus_one_single_unknown():
    toks = tokenize("私は本を読む")  # content: 本, 読む
    known = [t.surface in {"私", "本"} for t in toks]
    targets = mark_n_plus_one(toks, known, min_words=2)
    surfaces = {toks[i].surface for i in targets}
    assert surfaces == {"読む"}  # the one unknown content word


def test_n_plus_one_needs_min_words():
    toks = tokenize("本だ")  # too few content words
    known = [False] * len(toks)
    assert mark_n_plus_one(toks, known, min_words=3) == set()


def test_n_plus_one_not_fired_with_two_unknowns():
    toks = tokenize("新しい本を読む")  # 新しい, 本, 読む all unknown → 3 candidates
    known = [False] * len(toks)
    assert mark_n_plus_one(toks, known, min_words=3) == set()


# --- priority model -------------------------------------------------------------------------------


def _scorer(known_words, jlpt):
    return Coloring(Scorer(known=KnownWords.from_set(known_words), jlpt=jlpt, enable_freq=False))


def test_priority_n_plus_one_over_known_and_base(jlpt):
    line = "私は本を読む"
    toks = tokenize(line)
    styles = _scorer(["私", "本"], jlpt).score_line(toks)
    by = {t.surface: s for t, s in zip(toks, styles, strict=True)}
    assert by["本"].color == PAL.known
    assert by["読む"].tag.startswith("n+1")
    assert by["読む"].color == PAL.n_plus_one
    assert by["は"].color == PAL.base  # function word stays base


def test_function_words_never_colored_by_freq(jlpt):
    zip_path = next(iter(sorted(Path("../tools/freq").glob("*.zip"))), None)
    if zip_path is None:
        pytest.skip("freq zips are user-supplied (not shipped in-repo) — none present")
    fq = dicthelp.load_freqdict(str(zip_path))
    sc = Coloring(Scorer(known=KnownWords.from_set([]), freq=fq, jlpt=jlpt))
    toks = tokenize("私は本を読む")
    styles = sc.score_line(toks)
    for t, s in zip(toks, styles, strict=True):
        if t.pos in FUNCTION_POS:
            assert s.color == PAL.base


def test_jlpt_underline_is_additive(jlpt):
    # a known word that also has a JLPT level → known text color + JLPT underline
    toks = tokenize("会う")
    s = _scorer(["会う"], jlpt).score_line(toks)[0]
    assert s.color == PAL.known
    assert s.underline == PAL.jlpt["N5"]


# --- KnownWords from Anki: furigana fields + missing-field tolerance -------------------------------


def test_field_parse_splits_furigana():
    from saitenka_wordstate.known import _field_parse

    # Kanji Study 'EntryFurigana' → (plain surface, reading)
    assert _field_parse("お 孫[まご]さん") == ("お孫さん", "おまごさん")
    assert _field_parse("通[とお]り") == ("通り", "とおり")
    assert _field_parse("<b>奉書</b>") == ("奉書", None)  # HTML stripped, no brackets
    assert _field_parse("   ") == ("", None)


def test_from_ankiconnect_uses_entry_and_furigana():
    """The requested fields need not exist on a note; a furigana field yields BOTH surface and reading.

    Drives the injected client rather than a faked `urlopen` — the transport is `ankiconnect-client`'s
    contract, and this test is about which note fields become known forms.
    """
    from saitenka_wordstate.known import KnownWords

    notes = {
        1: {
            "modelName": "Kanji Study Word Model v3",
            "fields": {
                "Entry": {"value": "お孫さん"},
                "EntryFurigana": {"value": "お 孫[まご]さん"},
                "Meaning": {"value": "<b>grandchild</b>"},
            },
        }
    }

    class _Notes:
        @staticmethod
        def find_notes(_query):
            return [1]

        @staticmethod
        def notes_info(ids):
            return [notes[i] for i in ids]

    # the user's fields Expression/Word don't exist here; Entry does, and EntryFurigana is auto-scanned
    kw = KnownWords.from_ankiconnect({"Saitenka::Known": ["Entry", "Expression", "Word"]}, _Notes())
    assert kw.is_known("お孫さん")  # via Entry
    assert kw.is_known("おまごさん")  # reading recovered from EntryFurigana


class _FakeAnkiClient:
    """A note store that records the calls made against it.

    Replaces a `monkeypatch.setattr(wl, "_ankiconnect", …)` on a private function. The client is an
    injected collaborator now, so the test hands one in — and what it records is the real question
    these tests ask: WHICH notes got re-fetched, not merely that a fetch happened.
    """

    def __init__(self, state: dict[int, tuple[int, str]], field: str = "Entry"):
        self.state = state
        self.field = field
        self.calls: list[tuple] = []

    def find_notes(self, _query: str) -> list[int]:
        self.calls.append(("findNotes", None))
        return list(self.state)

    def notes_mod_time(self, ids: list[int]) -> list[dict]:
        self.calls.append(("notesModTime", tuple(ids) or None))
        return [{"noteId": i, "mod": self.state[i][0]} for i in ids]

    def notes_info(self, ids: list[int]) -> list[dict]:
        self.calls.append(("notesInfo", tuple(ids) or None))
        return [{"noteId": i, "fields": {self.field: {"value": self.state[i][1]}}} for i in ids]


def test_known_cache_is_cache_first_and_diff_refreshes_by_modtime():
    """The SQLite cache serves the known set instantly; refresh re-fetches ONLY notes whose Anki
    mod-time changed (not the whole deck), and a config-signature change invalidates the cache."""
    import dicthelp
    from saitenka_wordstate import known as wl

    state = {1: (10, "人"), 2: (10, "時間")}  # note_id -> (mod, Entry value)
    client = _FakeAnkiClient(state)
    calls = client.calls
    db = dicthelp.db()
    decks = {"D": ["Entry"]}

    assert KnownWords.from_cache(db, decks) is None  # empty cache → miss
    assert wl.refresh_known_cache(db, decks, client).words == {
        "人",
        "時間",
    }  # full load populates it
    assert KnownWords.from_cache(db, decks).words == {"人", "時間"}  # now an instant hit

    state[2] = (11, "時刻")  # edit only note 2
    calls.clear()
    assert wl.refresh_known_cache(db, decks, client).words == {"人", "時刻"}
    assert [c for c in calls if c[0] == "notesInfo"] == [("notesInfo", (2,))]  # subset: note 2 only
    assert KnownWords.from_cache(db, decks).words == {"人", "時刻"}  # cache reflects the edit

    assert KnownWords.from_cache(db, {"D": ["Entry", "Meaning"]}) is None  # signature change → miss


def test_known_cache_reconciles_external_anki_edits_additions_and_deletions():
    """Anki is edited OUTSIDE the overlay (Anki desktop, another tool). The mod-time diff must pick up
    every kind of external change on the next refresh — an edited note's new value, a brand-new note,
    and a deleted note's removal — while re-fetching ONLY the touched notes, and the cache must reflect
    the reconciled set. This is the durability that matters: the cache never goes permanently stale."""
    import dicthelp
    from saitenka_wordstate import known as wl

    state = {1: (10, "人"), 2: (10, "時間"), 3: (10, "猫")}  # note_id -> (mod, Entry value)
    client = _FakeAnkiClient(state)
    calls = client.calls
    db = dicthelp.db()
    decks = {"D": ["Entry"]}
    assert wl.refresh_known_cache(db, decks, client).words == {"人", "時間", "猫"}  # initial build

    # --- external mutations, all outside the overlay ---
    state[1] = (11, "人間")  # EDIT note 1 (mod bumped by Anki on any field change)
    state[4] = (10, "犬")  # ADD note 4
    del state[3]  # DELETE note 3

    calls.clear()
    reconciled = wl.refresh_known_cache(db, decks, client)
    assert reconciled.words == {"人間", "時間", "犬"}  # edit applied, add included, delete dropped
    fetched = sorted(nid for c in calls if c[0] == "notesInfo" for nid in (c[1] or ()))
    assert fetched == [1, 4]  # ONLY the edited + added notes re-fetched; untouched note 2 was not
    assert KnownWords.from_cache(db, decks).words == {"人間", "時間", "犬"}  # durably in the cache


def test_known_cache_and_its_invalidation_are_durable_across_a_db_reopen(tmp_path):
    """The cache exists to survive process restarts, so both the hit AND the signature invalidation
    must persist on disk — a fresh DictionaryDb (i.e. the next launch) reads the same rows/signature,
    never a stale hit under a config it wasn't built for."""
    from saitenka_wordstate import known as wl

    from saitenka.app.dictdb import DictionaryDb

    client = _FakeAnkiClient({1: (10, "人"), 2: (10, "時間")})
    path = tmp_path / "consolidated.sqlite"
    wl.refresh_known_cache(
        DictionaryDb.open(path), {"D": ["Entry"]}, client
    )  # build + persist, then drop it

    reopened = DictionaryDb.open(path)  # a fresh instance = the next launch reading off disk
    assert KnownWords.from_cache(reopened, {"D": ["Entry"]}).words == {"人", "時間"}  # durable hit
    # invalidation is durable too: the same on-disk cache must NOT satisfy a changed field config,
    # even though its rows are physically present — the signature stored on disk no longer matches.
    assert KnownWords.from_cache(reopened, {"D": ["Entry", "Meaning"]}) is None


def test_known_cache_migrates_the_previous_dictionary_db_state(tmp_path):
    import json
    import sqlite3

    from saitenka_wordstate import known as wl

    from saitenka.app.dictdb import DictionaryDb

    decks = {"D": ["Entry"]}
    path = tmp_path / "dictionaries.sqlite"
    db = DictionaryDb.open(path)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE anki_known_cache("
        "deck TEXT, note_id INTEGER, mod INTEGER, words TEXT, PRIMARY KEY(deck, note_id))"
    )
    connection.execute(
        "INSERT INTO anki_known_cache VALUES(?, ?, ?, ?)",
        ("D", 1, 10, json.dumps([["人", "ひと"]], ensure_ascii=False)),
    )
    connection.execute(
        "INSERT OR REPLACE INTO meta(k, v) VALUES('anki_known_sig', ?)",
        (wl._known_signature(decks),),
    )
    connection.commit()
    connection.close()

    migrated = KnownWords.from_cache(db, decks)

    assert migrated is not None and migrated.words == {"人", "ひと"}

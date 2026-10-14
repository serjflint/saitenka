"""attach/plugin mode builds the SessionController's coloring/dict/mining collaborators from config alone.
Anki-dependent pieces must degrade to None (never raise) so a closed Anki can't block attaching."""

from __future__ import annotations

import pytest

import saitenka.app.features.profiles.dependencies as reader_deps
from saitenka.app import anki as anki_mod


@pytest.fixture(autouse=True)
def _no_anki_launch(monkeypatch):
    """Never launch real Anki from build_reader_deps. The conftest default is Anki-DOWN, so
    _maybe_start_anki takes the launch path here; stub launch_anki → True so it reports 'launching'
    without spawning a real subprocess."""
    monkeypatch.setattr(anki_mod, "launch_anki", lambda *_a, **_k: True)


def test_empty_config_yields_no_deps():
    scorer, anki, mine_conf, dict_set = reader_deps.build_reader_deps({}, color=False)
    assert (scorer, anki, mine_conf, dict_set) == (None, None, None, None)


def test_mining_degrades_when_anki_closed(monkeypatch):
    import saitenka.app.anki as anki_mod

    def boom():
        raise ConnectionError("AnkiConnect down")

    monkeypatch.setattr(anki_mod, "Anki", boom)
    _scorer, anki, mine_conf, _dict_set = reader_deps.build_reader_deps(
        {"mine": {"deck": "D", "model": "M"}}, color=False
    )
    assert anki is None and mine_conf is None  # closed Anki didn't raise


def test_mining_built_when_anki_up(monkeypatch):
    import saitenka.app.anki as anki_mod

    monkeypatch.setattr(anki_mod, "Anki", lambda: "ANKI")
    _, anki, mine_conf, _ = reader_deps.build_reader_deps(
        {"mine": {"deck": "Saitenka::Mining", "model": "Lapis"}}, color=False
    )
    assert anki == "ANKI"
    assert mine_conf.deck == "Saitenka::Mining" and mine_conf.model == "Lapis"
    assert (
        mine_conf.normalize_audio,
        mine_conf.animated.enabled,
        mine_conf.animated.height,
        mine_conf.animated.fmt,
    ) == (False, False, 480, "webp")


def test_mining_threads_normalize_audio_flag(monkeypatch):
    import saitenka.app.anki as anki_mod

    monkeypatch.setattr(anki_mod, "Anki", lambda: "ANKI")
    _, _, mine_conf, _ = reader_deps.build_reader_deps(
        {"mine": {"deck": "D", "model": "Lapis", "normalize_audio": True}}, color=False
    )
    assert mine_conf.normalize_audio is True


def test_mining_threads_animated_screenshot_and_quality_knobs(monkeypatch):
    import saitenka.app.anki as anki_mod

    monkeypatch.setattr(anki_mod, "Anki", lambda: "ANKI")
    _, _, mine_conf, _ = reader_deps.build_reader_deps(
        {"mine": {"animated_screenshot": True, "animated_height": 720, "animated_fps": 15}},
        color=False,
    )
    assert mine_conf.animated.enabled is True
    assert (
        mine_conf.animated.height == 720 and mine_conf.animated.fps == 15
    )  # storage↔quality levers


def test_mining_reads_field_map_and_card_kind_from_config(monkeypatch):
    import saitenka.app.anki as anki_mod

    monkeypatch.setattr(
        anki_mod, "Anki", lambda: "ANKI"
    )  # str → model_field_names AttributeError → validation no-ops
    _, _, mine_conf, _ = reader_deps.build_reader_deps(
        {
            "mine": {
                "model": "Animecards",
                "fields": {"expression": "Word", "reading": "Reading"},
                "card_kind": "sentence",
            }
        },
        color=False,
    )
    assert mine_conf.fields == {"expression": "Word", "reading": "Reading"}  # custom map, not Lapis
    assert mine_conf.flags == {"IsSentenceCard": "1"}  # card_kind = sentence


def test_mining_preset_supplies_map_and_marker(monkeypatch):
    import saitenka.app.anki as anki_mod

    monkeypatch.setattr(anki_mod, "Anki", lambda: "ANKI")
    _, _, mine_conf, _ = reader_deps.build_reader_deps({"mine": {"preset": "Kiku"}}, color=False)
    assert mine_conf.model == "Kiku"
    assert mine_conf.fields["audio"] == "SentenceAudio"  # Kiku inherits Lapis field names
    assert mine_conf.flags == {"IsWordAndSentenceCard": "1"}


def test_mining_reads_card_format_from_config(monkeypatch):
    import saitenka.app.anki as anki_mod

    monkeypatch.setattr(anki_mod, "Anki", lambda: "ANKI")
    _, _, mine_conf, _ = reader_deps.build_reader_deps(
        {"mine": {"card_format": {"Word": "{expression}", "Reading": "{furigana}"}}}, color=False
    )
    assert mine_conf.card_format == {"Word": "{expression}", "Reading": "{furigana}"}


def test_mining_validation_drops_field_absent_from_note_type(monkeypatch):
    """A configured field that the note type doesn't have is dropped (+warned), so mining still
    writes a valid note rather than failing on the unknown field."""
    import saitenka.app.anki as anki_mod

    class _Anki:
        def model_field_names(self, _model):
            return ["Expression", "ExpressionReading"]  # note type has only these two

    monkeypatch.setattr(anki_mod, "Anki", _Anki)
    _, _, mine_conf, _ = reader_deps.build_reader_deps(
        {"mine": {"fields": {"expression": "Expression", "glossary": "Nonexistent"}}}, color=False
    )
    assert mine_conf.fields == {"expression": "Expression"}  # the absent Nonexistent field is gone


def test_color_builds_scorer_even_without_known(monkeypatch):
    from saitenka_dict import JlptDict
    from saitenka_tokenize.japanese import Token

    import saitenka.app.dict_meta as dm

    monkeypatch.setattr(dm, "load_jlpt", lambda _db: JlptDict({"本": "N5"}))

    scorer, _, _, _ = reader_deps.build_reader_deps({}, color=True)

    token = Token("本", "本", "ほん", "名詞", 0, 1)
    assert scorer.score_line([token])[0].tag == "base/jlpt-N5"


def test_dict_set_built_from_db_titles_and_warns_on_missing(tmp_path, capsys):
    """Config dict/freq/pitch are TITLES resolved against the consolidated DB: imported titles build the
    dict set (freq[0] also drives coloring), and an unimported title is warned + skipped, never fatal."""
    import dicthelp

    d = dicthelp.term_zip(tmp_path / "d.zip", "Def", [["本命", "ほんめい", ["favourite"]]])
    f = dicthelp.meta_zip(tmp_path / "f.zip", "Freq", "freq", [["本命", 5386]])
    db = dicthelp.db()
    db.import_zip(d, imported_at=dicthelp.AT)
    db.import_zip(f, imported_at=dicthelp.AT)
    cfg = {"dicts": ["Def", "Nope"], "freq": ["Freq"]}
    scorer, _anki, _mc, dict_set = reader_deps.build_reader_deps(cfg, color=True)
    assert [d.title for d in dict_set.dicts] == ["Def"]  # imported title resolved
    assert dict_set.freq_titles == ["Freq"]
    assert scorer is not None and scorer.freq is not None  # freq[0] drove the coloring FreqDict
    assert "not imported" in capsys.readouterr().err  # the missing title was warned


def test_freqdict_load_caps_at_top_x_and_drops_the_uncolorable_tail(tmp_path):
    """The banded scorer can't color a rank past its cap (band() returns None), so from_db(top_x=…)
    must not load those rows — a startup win that must stay behavior-identical for ranks within the
    cap. A rank beyond the cap looks up as None (uncolored), exactly as a full load + band() would."""
    import dicthelp
    from saitenka_dict import FreqDict
    from saitenka_wordstate import FREQ_BAND_TOP_X

    f = dicthelp.meta_zip(
        tmp_path / "f.zip",
        "Freq",
        "freq",
        [["近い", 5], ["稀語", FREQ_BAND_TOP_X + 500]],  # one within cap, one past it
    )
    db = dicthelp.db()
    row = db.import_zip(f, imported_at=dicthelp.AT)

    capped = FreqDict.from_connection(db.connection(), row.id, top_x=FREQ_BAND_TOP_X)
    assert capped.rank("近い") == 5  # within cap → loaded, colors normally
    assert capped.rank("稀語") is None  # past cap → not loaded, same as band() returning None

    # None cap → full ranking still available for a single-mode caller
    full = FreqDict.from_connection(db.connection(), row.id)
    assert full.rank("稀語") == FREQ_BAND_TOP_X + 500


def test_anki_launch_is_fire_and_forget_off_the_critical_path(monkeypatch):
    """A down Anki must not block the dep build: it's launched fire-and-forget and the build returns
    the dict/mining objects immediately — no synchronous poll for AnkiConnect to come up."""
    monkeypatch.setattr(anki_mod, "anki_reachable", lambda *_a, **_k: False)  # Anki not up at build
    launched = []
    monkeypatch.setattr(anki_mod, "launch_anki", lambda: launched.append(True) or True)
    monkeypatch.setattr(anki_mod, "Anki", lambda: "ANKI")

    _, anki, mine_conf, _ = reader_deps.build_reader_deps(
        {"mine": {"deck": "D", "model": "M"}}, color=False
    )
    assert launched == [True]  # Anki was kicked off...
    assert anki == "ANKI" and mine_conf is not None


@pytest.mark.parametrize("error", [ConnectionError, anki_mod._AnkiRetryable])
def test_known_falls_back_when_ankiconnect_raises(monkeypatch, error):
    import saitenka_wordstate.known as wl
    from saitenka_tokenize.japanese import Token

    import saitenka.app.dict_meta as dm

    def boom(*_a, **_k):
        raise error("AnkiConnect down")

    monkeypatch.setattr(wl, "refresh_known_cache", boom)
    monkeypatch.setattr(dm, "load_jlpt", lambda _db: None)

    scorer, _, _, _ = reader_deps.build_reader_deps({"known": {"Deck": ["Expression"]}}, color=True)

    token = Token("unseen", "unseen", "", "名詞", 0, 6)
    assert scorer.score_line([token])[0].tag == "base"


@pytest.mark.parametrize("source", ["run", "attach"])
def test_dependency_dictionary_uses_the_selected_language(tmp_path, source):
    import dicthelp
    from saitenka_tokenize.japanese import Token

    archive = dicthelp.term_zip(tmp_path / "d.zip", "X", [["present", "", ["definition"]]])
    dicthelp.db().import_zip(archive, imported_at=dicthelp.AT)
    cfg = {"dicts": ["X"]}
    if source == "attach":
        cfg.update(active_profile="fr", profiles={"fr": {"language": "fr", "tokenizer": "latin"}})

    _, _, _, dictionaries = reader_deps.build_reader_deps(
        cfg, color=False, mine=False, language="fr" if source == "run" else None
    )

    token = Token("missing", "missing", "", "WORD", 0, 7)
    entry = dictionaries.entry_for(token)
    assert (dictionaries.language, entry.defs[0].content[0]) == ("fr", "(not found in dictionary)")

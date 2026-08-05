"""attach/plugin mode builds the Reader's coloring/dict/mining collaborators from config alone.
Anki-dependent pieces must degrade to None (never raise) so a closed Anki can't block attaching."""

from __future__ import annotations

import pytest

from overlay.app import anki as anki_mod
from overlay.app import reader_deps


@pytest.fixture(autouse=True)
def _no_anki_launch(monkeypatch):
    """Never launch real Anki from build_reader_deps in tests (conftest already stubs anki_reachable
    → True, so _maybe_start_anki takes the already-up path; this guards the launch seam too)."""
    monkeypatch.setattr(anki_mod, "launch_anki", lambda *_a, **_k: True)


def test_empty_config_yields_no_deps():
    scorer, anki, mine_conf, dict_set = reader_deps.build_reader_deps({}, color=False)
    assert (scorer, anki, mine_conf, dict_set) == (None, None, None, None)


def test_mining_degrades_when_anki_closed(monkeypatch):
    import overlay.app.anki as anki_mod

    def boom():
        raise ConnectionError("AnkiConnect down")

    monkeypatch.setattr(anki_mod, "Anki", boom)
    _scorer, anki, mine_conf, _dict_set = reader_deps.build_reader_deps(
        {"mine": {"deck": "D", "model": "M"}}, color=False
    )
    assert anki is None and mine_conf is None  # closed Anki didn't raise


def test_mining_built_when_anki_up(monkeypatch):
    import overlay.app.anki as anki_mod

    monkeypatch.setattr(anki_mod, "Anki", lambda: "ANKI")
    _, anki, mine_conf, _ = reader_deps.build_reader_deps(
        {"mine": {"deck": "Saitenka::Mining", "model": "Lapis"}}, color=False
    )
    assert anki == "ANKI"
    assert mine_conf.deck == "Saitenka::Mining" and mine_conf.model == "Lapis"
    assert mine_conf.normalize_audio is False  # off unless [mine].normalize_audio is set


def test_mining_threads_normalize_audio_flag(monkeypatch):
    import overlay.app.anki as anki_mod

    monkeypatch.setattr(anki_mod, "Anki", lambda: "ANKI")
    _, _, mine_conf, _ = reader_deps.build_reader_deps(
        {"mine": {"deck": "D", "model": "Lapis", "normalize_audio": True}}, color=False
    )
    assert mine_conf.normalize_audio is True


def test_mining_threads_animated_screenshot_and_quality_knobs(monkeypatch):
    import overlay.app.anki as anki_mod

    monkeypatch.setattr(anki_mod, "Anki", lambda: "ANKI")
    _, _, mine_conf, _ = reader_deps.build_reader_deps(
        {"mine": {"animated_screenshot": True, "animated_height": 720, "animated_fps": 15}},
        color=False,
    )
    assert mine_conf.animated.enabled is True
    assert (
        mine_conf.animated.height == 720 and mine_conf.animated.fps == 15
    )  # storage↔quality levers


def test_mining_animated_screenshot_off_by_default(monkeypatch):
    import overlay.app.anki as anki_mod

    monkeypatch.setattr(anki_mod, "Anki", lambda: "ANKI")
    _, _, mine_conf, _ = reader_deps.build_reader_deps(
        {"mine": {"deck": "D", "model": "Lapis"}}, color=False
    )
    assert mine_conf.animated.enabled is False
    assert mine_conf.animated.height == 480 and mine_conf.animated.fmt == "webp"


def test_color_builds_scorer_even_without_known(monkeypatch):
    import overlay.app.scoring as scoring_mod
    import overlay.app.wordlists as wl

    monkeypatch.setattr(
        wl.KnownWords, "from_set", staticmethod(lambda words: f"known:{len(words)}")
    )
    monkeypatch.setattr(wl.JlptDict, "load", staticmethod(lambda _db: "JLPT"))
    monkeypatch.setattr(
        scoring_mod, "Scorer", lambda known, jlpt, **_kw: {"known": known, "jlpt": jlpt}
    )
    scorer, _, _, _ = reader_deps.build_reader_deps({}, color=True)
    assert scorer == {"known": "known:0", "jlpt": "JLPT"}


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
    assert [f.title for f in dict_set.freqs] == ["Freq"]
    assert scorer is not None and scorer.freq is not None  # freq[0] drove the coloring FreqDict
    assert "not imported" in capsys.readouterr().err  # the missing title was warned


def test_freqdict_load_caps_at_top_x_and_drops_the_uncolorable_tail(tmp_path):
    """The banded scorer can't color a rank past its cap (band() returns None), so from_db(top_x=…)
    must not load those rows — a startup win that must stay behavior-identical for ranks within the
    cap. A rank beyond the cap looks up as None (uncolored), exactly as a full load + band() would."""
    import dicthelp

    from overlay.app.scoring import FREQ_BAND_TOP_X
    from overlay.app.wordlists import FreqDict

    f = dicthelp.meta_zip(
        tmp_path / "f.zip",
        "Freq",
        "freq",
        [["近い", 5], ["稀語", FREQ_BAND_TOP_X + 500]],  # one within cap, one past it
    )
    db = dicthelp.db()
    row = db.import_zip(f, imported_at=dicthelp.AT)

    capped = FreqDict.from_db(db, row, top_x=FREQ_BAND_TOP_X)
    assert capped.rank("近い") == 5  # within cap → loaded, colors normally
    assert capped.rank("稀語") is None  # past cap → not loaded, same as band() returning None

    full = FreqDict.from_db(
        db, row
    )  # None cap → full ranking still available for a single-mode caller
    assert full.rank("稀語") == FREQ_BAND_TOP_X + 500


def test_anki_launch_is_fire_and_forget_off_the_critical_path(monkeypatch):
    """A down Anki must not block the dep build: it's launched fire-and-forget and the build returns
    the dict/mining objects immediately — no synchronous poll for AnkiConnect to come up."""
    monkeypatch.setattr(anki_mod, "anki_reachable", lambda *_a, **_k: False)  # Anki not up at build
    launched = []
    monkeypatch.setattr(anki_mod, "launch_anki", lambda: launched.append(True) or True)
    waited = []
    monkeypatch.setattr(
        anki_mod, "wait_until_anki_up", lambda *_a, **_k: waited.append(True) or True
    )
    monkeypatch.setattr(anki_mod, "Anki", lambda: "ANKI")

    _, anki, mine_conf, _ = reader_deps.build_reader_deps(
        {"mine": {"deck": "D", "model": "M"}}, color=False
    )
    assert launched == [True]  # Anki was kicked off...
    assert waited == []  # ...but the build never blocked polling for it
    assert anki == "ANKI" and mine_conf is not None


class _SeedFlagReader:
    """Minimal stand-in for the watcher's cross-thread hand-off (a real Reader is a heavy god-object)."""

    def __init__(self):
        self._pending_anki_seed = False


def test_anki_watch_flags_seed_when_already_up(monkeypatch):
    monkeypatch.setattr(anki_mod, "anki_reachable", lambda *_a, **_k: True)
    reader = _SeedFlagReader()
    reader_deps._anki_seed_watch(reader)
    assert reader._pending_anki_seed is True


def test_anki_watch_flags_seed_once_anki_comes_up(monkeypatch):
    monkeypatch.setattr(anki_mod, "anki_reachable", lambda *_a, **_k: False)
    monkeypatch.setattr(anki_mod, "wait_until_anki_up", lambda *_a, **_k: True)
    reader = _SeedFlagReader()
    reader_deps._anki_seed_watch(reader)
    assert reader._pending_anki_seed is True


def test_anki_watch_warns_and_skips_seed_on_timeout(monkeypatch, caplog):
    monkeypatch.setattr(anki_mod, "anki_reachable", lambda *_a, **_k: False)
    monkeypatch.setattr(anki_mod, "wait_until_anki_up", lambda *_a, **_k: False)
    reader = _SeedFlagReader()
    with caplog.at_level("WARNING"):
        reader_deps._anki_seed_watch(reader)
    assert reader._pending_anki_seed is False  # never came up → nothing to backfill
    assert "didn't come up" in caplog.text  # console warning fired


def test_known_falls_back_when_ankiconnect_raises(monkeypatch):
    import overlay.app.scoring as scoring_mod
    import overlay.app.wordlists as wl

    def boom(*_a, **_k):
        raise ConnectionError("down")

    # cache-first path: empty cache → miss → the blocking refresh_known_cache raises (Anki down)
    monkeypatch.setattr(wl, "refresh_known_cache", boom)
    monkeypatch.setattr(wl.KnownWords, "from_set", staticmethod(lambda _words: "empty-known"))
    monkeypatch.setattr(wl.JlptDict, "load", staticmethod(lambda _db: "JLPT"))
    monkeypatch.setattr(scoring_mod, "Scorer", lambda known, **_kw: {"known": known})
    scorer, _, _, _ = reader_deps.build_reader_deps({"known": {"Deck": ["Expression"]}}, color=True)
    assert scorer == {"known": "empty-known"}  # degraded, not crashed

"""attach/plugin mode builds the Reader's coloring/dict/mining collaborators from config alone.
Anki-dependent pieces must degrade to None (never raise) so a closed Anki can't block attaching."""

from __future__ import annotations

import pytest

from overlay.app import anki as anki_mod
from overlay.app import reader_deps


@pytest.fixture(autouse=True)
def _no_anki_launch(monkeypatch):
    """Never launch/poll real Anki from build_reader_deps in tests."""
    monkeypatch.setattr(anki_mod, "ensure_anki_running", lambda *a, **k: True)


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
    monkeypatch.setattr(anki_mod, "MineConfig", lambda deck, model: f"{deck}/{model}")
    _, anki, mine_conf, _ = reader_deps.build_reader_deps(
        {"mine": {"deck": "Saitenka::Mining", "model": "Lapis"}}, color=False
    )
    assert anki == "ANKI" and mine_conf == "Saitenka::Mining/Lapis"


def test_color_builds_scorer_even_without_known(monkeypatch):
    import overlay.app.scoring as scoring_mod
    import overlay.app.wordlists as wl

    monkeypatch.setattr(wl.KnownWords, "from_set", staticmethod(lambda words: f"known:{len(words)}"))
    monkeypatch.setattr(wl.JlptDict, "load", staticmethod(lambda: "JLPT"))
    monkeypatch.setattr(
        scoring_mod, "Scorer", lambda known, freq, jlpt: {"known": known, "jlpt": jlpt}
    )
    scorer, _, _, _ = reader_deps.build_reader_deps({}, color=True)
    assert scorer == {"known": "known:0", "jlpt": "JLPT"}


def test_known_falls_back_when_ankiconnect_raises(monkeypatch):
    import overlay.app.scoring as scoring_mod
    import overlay.app.wordlists as wl

    def boom(_cfg):
        raise ConnectionError("down")

    monkeypatch.setattr(wl.KnownWords, "from_ankiconnect", staticmethod(boom))
    monkeypatch.setattr(wl.KnownWords, "from_set", staticmethod(lambda words: "empty-known"))
    monkeypatch.setattr(wl.JlptDict, "load", staticmethod(lambda: "JLPT"))
    monkeypatch.setattr(scoring_mod, "Scorer", lambda known, freq, jlpt: {"known": known})
    scorer, _, _, _ = reader_deps.build_reader_deps(
        {"known": {"Deck": ["Expression"]}}, color=True
    )
    assert scorer == {"known": "empty-known"}  # degraded, not crashed

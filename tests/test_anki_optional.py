"""Contract: Anki is an OPTIONAL component. AnkiConnect is unreachable by default in the whole suite
(the conftest ``_anki_down`` fixture — Anki is usually closed in production), so the graceful-degradation
path is what every test exercises; any code that hard-requires Anki fails a test. This file names that
invariant explicitly and pins the seams a regression would slip through: the single reachability probe,
the compact 'expected-down' logging, and the console warning when Anki can't even be started."""

from __future__ import annotations

import logging

import saitenka.app.session.deps as reader_deps
from saitenka.app import anki as anki_mod
from saitenka.app.anki import MineConfig

# The conftest fixture patches the module attribute ``anki_reachable``; keep the REAL one so we can
# exercise its own logic (the SSOT fold onto the client) under a stubbed ``Anki._call``.
_REAL_REACHABLE = anki_mod.anki_reachable


def test_anki_reachable_is_false_when_the_one_client_cannot_connect(monkeypatch):
    def down(_self, *_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(anki_mod.Anki, "_call", down)
    assert _REAL_REACHABLE() is False  # the sole probe routes through the sole client (SSOT)


def test_anki_reachable_is_true_when_the_one_client_answers(monkeypatch):
    monkeypatch.setattr(anki_mod.Anki, "_call", lambda _self, *_a, **_k: 6)
    assert _REAL_REACHABLE() is True


def test_validate_mine_fields_skips_quietly_without_a_traceback_when_down(caplog):
    # An unreachable Anki must leave the field map untouched (validation skipped, mining NOT disabled)
    # and log ONE compact line — never a dumped traceback for an expected steady state.
    class _Down:
        def model_field_names(self, _model):
            raise anki_mod._AnkiRetryable("AnkiConnect unreachable")

    cfg = MineConfig(model="M", fields={"expression": "Expression"})
    with caplog.at_level(logging.DEBUG):
        reader_deps._validate_mine_fields(_Down(), cfg)
    assert cfg.fields == {"expression": "Expression"}  # map kept
    rec = next(r for r in caplog.records if "couldn't read" in r.getMessage())
    assert not rec.exc_info  # expected-down → no traceback attached


def test_validate_mine_fields_keeps_the_traceback_for_an_unexpected_fault(caplog):
    # Negative control: a NON-unreachable error is a real bug — it must still carry its traceback.
    class _Bug:
        def model_field_names(self, _model):
            raise ValueError("something genuinely wrong")

    cfg = MineConfig(model="M", fields={"expression": "Expression"})
    with caplog.at_level(logging.DEBUG):
        reader_deps._validate_mine_fields(_Bug(), cfg)
    rec = next(r for r in caplog.records if "couldn't read" in r.getMessage())
    assert rec.exc_info  # unexpected → traceback preserved for debugging


def test_validate_mine_fields_drops_word_audio_pack_when_field_missing_on_model(caplog, tmp_path):
    """#93: a configured word-audio pack whose target field doesn't exist on the note type must not
    silently write to nowhere — validation clears `word_audio_pack` so the miner skips it cleanly."""

    class _Model:
        def model_field_names(self, _model):
            return ["Expression"]  # no "WordAudio" field

    cfg = MineConfig(word_audio_pack=tmp_path, word_audio_field="WordAudio")
    with caplog.at_level(logging.WARNING):
        reader_deps._validate_mine_fields(_Model(), cfg)
    assert cfg.word_audio_pack is None
    assert "WordAudio" in caplog.text


def test_validate_mine_fields_keeps_word_audio_pack_when_field_present(tmp_path):
    class _Model:
        def model_field_names(self, _model):
            return ["Expression", "WordAudio"]

    cfg = MineConfig(word_audio_pack=tmp_path, word_audio_field="WordAudio")
    reader_deps._validate_mine_fields(_Model(), cfg)
    assert cfg.word_audio_pack == tmp_path


def test_run_warns_in_the_terminal_when_anki_cannot_be_started(monkeypatch):
    # launch_anki() returns False when Anki isn't found / won't launch → the user gets a distinct
    # console warning (not the softer 'launching…' note) and a log line.
    monkeypatch.setattr(anki_mod, "launch_anki", lambda *_a, **_k: False)
    notes: list[bool] = []
    reader_deps._maybe_start_anki(
        {"deck": "D"}, None, mine=True, on_unreachable=lambda launched: notes.append(launched)
    )
    assert notes == [False]  # callback told 'could not start', so run prints the warning variant


def test_run_reports_launching_when_anki_can_be_started(monkeypatch):
    monkeypatch.setattr(anki_mod, "launch_anki", lambda *_a, **_k: True)
    notes: list[bool] = []
    reader_deps._maybe_start_anki(
        {"deck": "D"}, None, mine=True, on_unreachable=lambda launched: notes.append(launched)
    )
    assert notes == [True]


def test_the_suite_default_is_anki_unavailable():
    # Documents the conftest contract: without the anki_up fixture the reachability gate is down.
    assert anki_mod.anki_reachable() is False

"""Make ``tests/util.py`` importable as ``util``, put ``src/`` on the path, and keep the consolidated
dictionary DB HERMETIC: tests must never write into the user's real ``data_dir()/dictionaries.sqlite``."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402  # must come after the sys.path setup above

import overlay.app.backlog as _backlog  # noqa: E402  # must come after the sys.path setup above
import overlay.app.dictdb as _dictdb  # noqa: E402  # must come after the sys.path setup above

# Opt-in CrossHair (symbolic-execution) backend for the Hypothesis property tests — `poe crosshair`.
# Registered ONLY when hypothesis-crosshair is installed (the pinned-3.13 .venv-cx), so default test
# runs are untouched. Select with `pytest --hypothesis-profile=crosshair`; a per-test @settings that
# omits `backend` inherits this profile's crosshair backend. See AGENTS.md "Fuzzing & symbolic checks".
try:
    import crosshair  # noqa: F401  # crosshair-tool; the "crosshair" hypothesis backend rides on it
    from hypothesis import settings as _hyp_settings

    _hyp_settings.register_profile("crosshair", backend="crosshair", deadline=None)
except ImportError:
    pass


@pytest.fixture(autouse=True)
def _hermetic_dict_db(tmp_path, monkeypatch):
    """Point the consolidated dictionary DB at a fresh per-test file so nothing touches the user's real
    ``data_dir()/dictionaries.sqlite``. Tests that build their own ``DictionaryDb.open(path)`` are
    unaffected; code that opens the default path (reader_deps, doctor, dicthelp) gets this tmp DB.
    ``test_compare`` opts back into the real DB by resetting the override."""
    monkeypatch.setattr(_dictdb, "_DB_PATH_OVERRIDE", tmp_path / "dictionaries.sqlite")
    monkeypatch.setattr(_backlog, "_DB_PATH_OVERRIDE", tmp_path / "backlog.sqlite")


@pytest.fixture(autouse=True)
def _anki_reachable(monkeypatch):
    """Default: AnkiConnect answers, so the ⊕ button shows when mining is configured (existing tests
    assume it) and _anki_ok() stays hermetic — no real localhost:8765 ping. Tests for the Anki-closed
    case patch ``overlay.app.anki.anki_reachable`` to return False."""
    monkeypatch.setattr("overlay.app.anki.anki_reachable", lambda *_a, **_k: True)


@pytest.fixture(autouse=True)
def _tts_present(monkeypatch):
    """Default: pretend a Japanese TTS voice exists so the 🔊 button is drawn — existing geometry tests
    assume it, and this keeps them hermetic (no real `say`/PowerShell subprocess). Tests for the
    hidden-button case patch ``overlay.app.controller.tts_available`` to False explicitly."""
    import overlay.app.controller as ctrl

    monkeypatch.setattr(ctrl, "tts_available", lambda: True)


@pytest.fixture(autouse=True)
def _isolate_keyring():
    """Never touch the real OS keyring in tests — force keyring's 'fail' backend so an un-mocked
    keychain_get/set can't read the developer's actual stored jimaku key from the login Keychain."""
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:  # keyring not importable → nothing to isolate
        yield
        return
    prev = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    try:
        yield
    finally:
        keyring.set_keyring(prev)

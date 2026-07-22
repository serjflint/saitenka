"""Make ``tests/util.py`` importable as ``util``, put ``src/`` on the path, and keep the dict
cache HERMETIC: tests must never write into the user's real ~/.cache/saitenka-overlay/dicts."""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

import overlay.app.dictionary as _dictionary  # noqa: E402

_dictionary._REAL_CACHE_DIR = _dictionary.CACHE_DIR  # test_compare opts back into the real one
_TEST_CACHE = Path(tempfile.mkdtemp(prefix="saitenka-test-dicts-"))
_dictionary.CACHE_DIR = _TEST_CACHE


@pytest.fixture(autouse=True)
def _anki_reachable(monkeypatch):
    """Default: AnkiConnect answers, so the ⊕ button shows when mining is configured (existing tests
    assume it) and _anki_ok() stays hermetic — no real localhost:8765 ping. Tests for the Anki-closed
    case patch ``overlay.app.anki.anki_reachable`` to return False."""
    monkeypatch.setattr("overlay.app.anki.anki_reachable", lambda *a, **k: True)


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
    except Exception:  # keyring not importable → nothing to isolate
        yield
        return
    prev = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    try:
        yield
    finally:
        keyring.set_keyring(prev)

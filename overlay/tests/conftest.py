"""Make ``tests/util.py`` importable as ``util``, put ``src/`` on the path, and keep the consolidated
dictionary DB HERMETIC: tests must never write into the user's real ``data_dir()/dictionaries.sqlite``."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import overlay.app.backlog as _backlog  # noqa: E402  # must come after the sys.path setup above
import overlay.app.dictdb as _dictdb  # noqa: E402  # must come after the sys.path setup above
import pytest  # noqa: E402  # must come after the sys.path setup above

# Opt-in CrossHair (symbolic-execution) backend for the Hypothesis property tests — `poe crosshair`.
# Registered ONLY when hypothesis-crosshair is installed (the pinned-3.13 `poe crosshair` env), so default test
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
def _hermetic_cache_dir(tmp_path, monkeypatch):
    """Point ``cache_dir()`` at a fresh per-test dir so the default-on render cache / mask atlas (#149,
    used-when-available) never open, read, or write the user's real ``~/…/Caches/saitenka`` — a test dir
    has no prebuilt ``render-cache.sqlite`` / ``mask-atlas.sqlite``, so both stay inert. Tests that want
    the caches build their own under ``tmp_path`` and inject/enable them explicitly."""
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(autouse=True)
def _hermetic_config(tmp_path, monkeypatch):
    """Point ``SAITENKA_CONFIG`` at a fresh per-test path (no file → pure defaults) so ``load_config()``
    never reads the developer's real ``overlay.toml``. Without this, a local knob silently changes
    behaviour under test and diverges from CI (which has no such file): a real ``[jimaku].keyring = false``
    flipped ``keyring_enabled()`` off, failing the jimaku-resolver keychain-path tests locally while they
    passed in CI. Tests that need specific config set ``SAITENKA_CONFIG`` themselves — their ``setenv``
    runs after this fixture and wins."""
    monkeypatch.setenv("SAITENKA_CONFIG", str(tmp_path / "overlay.toml"))


@pytest.fixture(autouse=True)
def _anki_down(monkeypatch):
    """Default: AnkiConnect is UNREACHABLE — the production-realistic state (Anki is usually closed).
    Making down the default means the graceful-degradation path is what the whole suite exercises, so
    any code that hard-requires Anki fails a test (Anki is an OPTIONAL component — see the
    ``test_anki_optional`` contract). Also neutralises ``launch_anki`` so a down probe never spawns a
    real Anki subprocess. Tests that need Anki reachable (the ⊕ button, live mining/coloring) request
    the ``anki_up`` fixture; ``test_anki_launch`` restores the real ``launch_anki`` it is testing."""
    monkeypatch.setattr("overlay.app.anki.anki_reachable", lambda *_a, **_k: False)
    monkeypatch.setattr("overlay.app.anki.launch_anki", lambda *_a, **_k: False)


@pytest.fixture
def anki_up(monkeypatch):
    """Opt in: AnkiConnect answers. The ⊕ mine button shows and the reachability gate is green, for
    tests that assert Anki-present behaviour rather than degradation."""
    monkeypatch.setattr("overlay.app.anki.anki_reachable", lambda *_a, **_k: True)


@pytest.fixture(autouse=True)
def _tts_present(monkeypatch):
    """Default: pretend a Japanese TTS voice exists so the 🔊 button is drawn — existing geometry tests
    assume it, and this keeps them hermetic (no real `say`/PowerShell subprocess). Tests for the
    hidden-button case patch ``overlay.app.controller.tts_available`` to False explicitly."""
    import overlay.app.controller as ctrl

    monkeypatch.setattr(ctrl, "tts_available", lambda: True)


@pytest.fixture(autouse=True)
def _isolate_keyring(tmp_path, monkeypatch):
    """Never touch the developer's real jimaku credentials."""
    from overlay.app import jimaku

    monkeypatch.setattr(jimaku, "key_file_path", lambda: tmp_path / "jimaku.key")
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

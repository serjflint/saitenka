"""Make ``tests/util.py`` importable as ``util``, put ``src/`` on the path, and keep the consolidated
dictionary DB HERMETIC: tests must never write into the user's real ``data_dir()/dictionaries.sqlite``."""

import functools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402  # must come after the sys.path setup above
import session_builder  # noqa: E402  # must come after the sys.path setup above

import saitenka.app.backlog as _backlog  # noqa: E402  # must come after the sys.path setup above
import saitenka.app.dictdb as _dictdb  # noqa: E402  # must come after the sys.path setup above
import saitenka.app.features.mining.mined_store as _mined_store  # noqa: E402

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
    monkeypatch.setattr(_mined_store, "_DB_PATH_OVERRIDE", tmp_path / "mined.sqlite")


@pytest.fixture(autouse=True)
def _hermetic_cache_dir(tmp_path, monkeypatch):
    """Point ``cache_dir()`` at a fresh per-test dir so the default-on render cache / mask atlas (#149,
    used-when-available) never open, read, or write the user's real ``~/…/Caches/saitenka`` — a test dir
    has no prebuilt ``render-cache.sqlite`` / ``mask-atlas.sqlite``, so both stay inert. Tests that want
    the caches build their own under ``tmp_path`` and inject/enable them explicitly."""
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(autouse=True)
def _reset_primary_font():
    """Reset the global font-chain lead after each test. A test that constructs a non-JP SessionController sets
    ``fonts.set_primary_font('NotoSans.ttf')``; under pytest-randomly a later font/render test would then
    see NotoSans lead. Restore the JP-universal default so the chain can't leak across tests."""
    from saitenka import fonts

    yield
    fonts.set_primary_font(None)


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
    monkeypatch.setattr("saitenka.app.anki.anki_reachable", lambda *_a, **_k: False)
    monkeypatch.setattr("saitenka.app.anki.launch_anki", lambda *_a, **_k: False)


@pytest.fixture
def anki_up(monkeypatch):
    """Opt in: AnkiConnect answers. The ⊕ mine button shows and the reachability gate is green, for
    tests that assert Anki-present behaviour rather than degradation."""
    monkeypatch.setattr("saitenka.app.anki.anki_reachable", lambda *_a, **_k: True)


@pytest.fixture(autouse=True)
def _tts_present(monkeypatch):
    """Default: pretend a Japanese TTS voice exists so the 🔊 button is drawn — existing geometry tests
    assume it, and this keeps them hermetic (no real `say`/PowerShell subprocess). Tests for the
    hidden-button case patches the turn's capability lookup explicitly."""
    from saitenka.app.session import builder

    monkeypatch.setattr(builder, "tts_available", lambda: True)


@pytest.fixture(autouse=True)
def _isolate_keyring(tmp_path, monkeypatch):
    """Never touch the developer's real jimaku credentials."""
    from saitenka.app import jimaku

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


@functools.cache
def _running_mpv_version() -> tuple[int, int] | None:
    """`(major, minor)` of the mpv on PATH, or None when there is none to ask."""
    from saitenka.mpvio.discover import find_mpv
    from saitenka.mpvio.launch import mpv_version_output, parse_mpv_version

    mpv = find_mpv(None)
    return parse_mpv_version(mpv_version_output(mpv)) if mpv else None


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip a live test whose mpv floor the running mpv does not meet.

    CI runs the live tier against several mpv versions on purpose — the declared minimum, so a
    regression breaking it cannot ship green against a later build, and current, so an upstream rename
    (`sub-border-size` → `sub-outline-size` reached a tag this way) is caught. The floors are not
    uniform: `sub-text/ass-full` arrived in 0.38, the native-geometry profile needs 0.40. Carrying the
    version on the marker keeps each test's real requirement next to the test, so no leg needs a marker
    expression and none of them can drift from each other.

    An absent mpv is left alone — the live tier's own module-level skips own that case.
    """
    marker = item.get_closest_marker("mpv_min")
    if marker is None:
        return
    # A tuple so a test gated by a floor the package declares can pass that constant instead of
    # restating its digits, which would go stale the next time the floor moves.
    floor = marker.args[0]
    required = floor if isinstance(floor, tuple) else tuple(int(p) for p in str(floor).split("."))
    running = _running_mpv_version()
    if running is not None and running < required:
        wanted = ".".join(str(part) for part in required)
        pytest.skip(f"needs mpv >= {wanted}, running {running[0]}.{running[1]}")


def pytest_sessionstart(session: pytest.Session) -> None:
    """Print the `pytest-randomly` seed even under `-q`, which hides the header that carries it.

    Every gate task runs `-q`, so a randomized run that fails once reports an order nothing records —
    and `--randomly-seed=<n>` is the only way to replay it. That cost a real investigation here: an
    intermittent failure survived eighteen reproduction attempts because the seed was never printed.
    Worker processes stay quiet; under `-n auto` the controller is the one with a terminal.
    """
    config = session.config
    if hasattr(config, "workerinput"):
        return
    seed = config.getoption("randomly_seed", default=None)
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if seed is None or reporter is None:  # `-p no:randomly`, or no terminal to write to
        return
    reporter.write_line(f"randomly-seed: {seed}  (replay with --randomly-seed={seed})")


@pytest.fixture
def make_session():
    """Build sessions that are closed when the test ends — the sanctioned form for new tests.

    A factory rather than a plain `session` fixture because call sites supply their own ipc /
    services / options, and several build more than one. A session owns worker threads, SQLite
    handles, timers and temp artifacts, and a live thread is a GC root: leaving one open keeps its
    whole graph reachable for the rest of the process (~14.6 MB against ~0.4 MB closed).

    Prefer this to calling `build_session` directly. The autouse sweep below still catches direct
    calls, but it is a net for the ~330 existing ones, not the ownership story.
    """
    built: list[session_builder.TestSession] = []

    def factory(ipc, **kwargs):
        session = session_builder.build_session(ipc, **kwargs)
        built.append(session)
        return session

    yield factory
    session_builder.drain_and_close(built)


@pytest.fixture(autouse=True)
def close_built_sessions():
    """Net under `make_session`: close whatever `build_session` handed out directly.

    The suite builds ~330 sessions and closes a minority explicitly, which is what made a worker's
    RSS climb monotonically through a file instead of returning between tests.
    `LiveSession.close()` is idempotent, so a test that closes its own — or used `make_session` —
    is unaffected.
    """
    yield
    session_builder.drain_and_close(session_builder.BUILT_SESSIONS)

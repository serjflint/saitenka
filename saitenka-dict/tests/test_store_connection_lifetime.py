"""`SqliteDictionaryStore` releases its connections when its owner dies.

The overlay's twin of this (`tests/test_store_connection_lifetime.py`) cannot cover it: this is a
separate distribution that must not import `saitenka`, so the check is written locally rather than
shared. The shape differs too — this store has no `close()`, and opens `file:…?mode=ro` lazily, so it
records **zero** connections at build. That makes `assert opened` a live guard here, where in the
overlay it is a formality.

Ordering matters, which is why tracking is started by an explicit call rather than a fixture:
`make_legacy_db` opens its own connection, and a fixture would install the patch before the test body
runs, so the fixture DB's connection would land in `opened` before the store exists — leaving the
`assert not opened` liveness guard satisfied by the wrong connection, and this test passing with its
lookup deleted.
"""

from __future__ import annotations

import gc
import sqlite3

from saitenka_dict import SqliteDictionaryStore
from test_sqlite_store import make_legacy_db


def _track(monkeypatch) -> list[sqlite3.Connection]:
    """Start recording connections here — not at fixture setup. See the module docstring."""
    seen: list[sqlite3.Connection] = []
    real = sqlite3.connect

    def tracking(*args, **kwargs):
        connection = real(*args, **kwargs)
        seen.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracking)
    return seen


def _is_closed(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def test_store_releases_its_connection_when_its_owner_dies(tmp_path, monkeypatch):
    path = tmp_path / "dictionary.sqlite"
    make_legacy_db(path)  # before tracking starts: its connection must not land in `opened`
    opened = _track(monkeypatch)

    store = SqliteDictionaryStore(path)
    assert not opened, "the store is expected to open lazily, on first use"
    store.exact_terms(("読む",), ())
    assert opened

    del store
    gc.collect()

    assert all(_is_closed(c) for c in opened)


def test_the_oracle_catches_a_store_that_never_registers(tmp_path, monkeypatch):
    """Negative control. `opened` holding a reference is what keeps the leak observable — otherwise
    refcounting would dealloc the connection on owner death and close it as a side effect."""
    opened = _track(monkeypatch)

    class UnregisteredStore:
        def __init__(self, path):
            self._con = sqlite3.connect(path)

    store = UnregisteredStore(tmp_path / "unregistered.sqlite")
    del store
    gc.collect()

    assert opened
    assert not all(_is_closed(c) for c in opened)

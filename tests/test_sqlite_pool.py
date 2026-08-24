"""A per-thread connection cache must release EVERY thread's connection, and must not need a caller.

Both halves were broken in the shipped stores: `close()` reached only the calling thread's connection,
and an owner nobody closed left its connections for the GC, which finalised them into a wall of
`ResourceWarning: unclosed database` in the gate log.
"""

from __future__ import annotations

import contextlib
import gc
import sqlite3
import threading

import pytest

from saitenka.sqlite_pool import ThreadLocalConnections, close_when_collected, open_wal


class Owner:
    def __init__(self, path):
        self.conns = ThreadLocalConnections(self, lambda: open_wal(path))


def _open_on_a_new_thread(pool: ThreadLocalConnections) -> sqlite3.Connection:
    opened: list[sqlite3.Connection] = []
    thread = threading.Thread(target=lambda: opened.append(pool.get()))
    thread.start()
    thread.join()
    return opened[0]


def _is_closed(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def test_each_thread_gets_its_own_connection(tmp_path):
    owner = Owner(tmp_path / "c.sqlite")
    assert _open_on_a_new_thread(owner.conns) is not owner.conns.get()


def test_close_reaches_a_connection_another_thread_opened(tmp_path):
    owner = Owner(tmp_path / "c.sqlite")
    worker_connection = _open_on_a_new_thread(owner.conns)
    owner.conns.close()
    assert _is_closed(worker_connection)


def test_close_leaves_the_pool_usable(tmp_path):
    owner = Owner(tmp_path / "c.sqlite")
    first = owner.conns.get()
    owner.conns.close()
    assert not _is_closed(owner.conns.get())
    assert _is_closed(first)


def test_dropping_the_owner_closes_every_connection(tmp_path):
    owner = Owner(tmp_path / "c.sqlite")
    connections = [owner.conns.get(), _open_on_a_new_thread(owner.conns)]
    del owner
    gc.collect()
    assert all(_is_closed(c) for c in connections)


def test_dropping_a_single_connection_store_closes_it(tmp_path):
    class Store:
        def __init__(self):
            self.con = sqlite3.connect(tmp_path / "s.sqlite")
            close_when_collected(self, self.con)

    store = Store()
    connection = store.con
    del store
    gc.collect()
    assert _is_closed(connection)


@pytest.mark.parametrize(("pragma", "expected"), [("journal_mode", "wal"), ("synchronous", 1)])
def test_open_wal_configures_the_connection(tmp_path, pragma, expected):
    """The caches share this opener; a plain `connect` would answer `delete` and `2`."""
    with contextlib.closing(open_wal(tmp_path / "c.sqlite")) as connection:
        assert connection.execute(f"PRAGMA {pragma}").fetchone()[0] == expected

"""SQLite connections that are released when their owner dies, not whenever the GC next runs.

Two defects live in the obvious spelling of a per-thread connection cache, and both were real here:

* ``threading.local`` can only reach the CALLING thread's connection, so a ``close()`` written against
  it leaves every prefetch worker's connection open for the process's lifetime.
* An owner nobody closes at all (a store built by a test, a cache dropped on a re-config) hands its
  connections to the interpreter, which finalises them on an arbitrary later ``gc.collect()``. Each one
  prints ``ResourceWarning: unclosed database`` attributed to whichever frame triggered the collection
  — 245 of them in one gate run, none naming a leak site.

Keeping a roster of the connections next to the thread-local fast path fixes both: ``close()`` can
reach them all, and ``weakref.finalize`` closes them for an owner that is simply dropped.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = ["ThreadLocalConnections", "close_when_collected", "open_wal"]


def open_wal(path: Path) -> sqlite3.Connection:
    """A rebuildable cache's connection: WAL for read-while-write, ``synchronous=NORMAL`` because
    losing the tail of a cache after a crash costs a re-render, not data."""
    connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def _close_all(connections: list[sqlite3.Connection]) -> None:
    while connections:
        with contextlib.suppress(sqlite3.Error):
            connections.pop().close()


class ThreadLocalConnections:
    """One connection per thread, plus the roster :meth:`close` and the owner's finalizer need.

    ``owner`` is held weakly. ``open_connection`` must NOT close over the owner: the pool is stored on
    it, so a bound method would make a cycle, and a cycle is collected by the GC rather than by
    refcount — the deferred release this exists to avoid.
    """

    def __init__(self, owner: object, open_connection: Callable[[], sqlite3.Connection]) -> None:
        self._open = open_connection
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        weakref.finalize(owner, _close_all, self._all)

    def get(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._open()
            self._local.connection = connection
            # No lock: a connection is opened once per thread, and list.append is atomic with the GIL
            # and under free-threading alike.
            self._all.append(connection)
        return connection

    def close(self) -> None:
        """Close every thread's connection. The next :meth:`get` opens a fresh one."""
        self._local.connection = None
        _close_all(self._all)


def close_when_collected(owner: object, connection: sqlite3.Connection) -> None:
    """Close ``connection`` when ``owner`` is collected, for a store that holds exactly one.

    Complements rather than replaces the store's own ``close()``: closing twice is a no-op, and this
    is what covers the caller who never gets there.
    """
    weakref.finalize(owner, connection.close)

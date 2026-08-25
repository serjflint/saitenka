"""Each real store is wired to the connection mechanism `tests/test_sqlite_pool.py` already proves.

That file owns the mechanism — `ThreadLocalConnections.close()` reaching every thread, and
`close_when_collected` firing on owner death. What it cannot say is whether any *shipped* store is
connected to it. Six were not (#f70ca017: 224 connections open at suite end), and today deleting
`close_when_collected(self, self._con)` from `backlog.py` passes the whole suite — the resulting
`ResourceWarning` comes from a finalizer, so it is unraisable and fails nothing.

So this is one assertion per store — wiring — not a second copy of the mechanism's tests.

**The liveness check differs per store, and the difference is measured, not assumed.** All six record
one connection at build, meaning two different things:

* `DictionaryDb` / `RenderCache` / `MaskAtlas` — build opens a *transient* schema-init connection,
  closed in its own `finally`. The registered one arrives lazily on first use, so the exercise must
  reach `_conn()` and `len(opened) > at_build` is what proves it did. A bare `assert opened` would be
  satisfied by the transient connection whether or not the store ever touches the pool.
* `BacklogStore` / `MinedCardStore` / `SessionStore` — build opens *the* registered connection, and no
  method opens another (measured: 1 → 1 across any exercise). Here `assert opened` is all there is; it
  cannot fail, and the closed-ness check below is the row's real oracle.

`opened` holding a reference is load-bearing: an unregistered connection whose owner dies would
otherwise be dealloc'd by refcounting and closed as a side effect, hiding the very leak this looks for.
Holding it keeps "close() was never called" observable.
"""

from __future__ import annotations

import gc
import sqlite3
from typing import TYPE_CHECKING

import pytest

from saitenka.app.backlog import BacklogStore
from saitenka.app.dictdb import DictionaryDb
from saitenka.app.features.mining.mined_store import MinedCardStore
from saitenka.app.render_cache import RenderCache
from saitenka.app.session_stats import SessionStore
from saitenka.mask_atlas import MaskAtlas

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

#: Build opens a throwaway schema-init connection; the registered one is lazy.
POOLED = "pooled"
#: Build opens the registered connection itself; nothing opens another.
EAGER = "eager"

STORES = [
    ("DictionaryDb", POOLED, lambda p: DictionaryDb.open(p), lambda s: s.stats()),
    ("RenderCache", POOLED, lambda p: RenderCache.open(p, max_bytes=1 << 20), lambda s: s.stats()),
    ("MaskAtlas", POOLED, lambda p: MaskAtlas.open(p), lambda s: s.count()),
    ("BacklogStore", EAGER, BacklogStore, lambda s: s.all_media()),
    ("MinedCardStore", EAGER, MinedCardStore, lambda s: s.by_note_id(1)),
    ("SessionStore", EAGER, SessionStore, lambda s: s.recent()),
]


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[sqlite3.Connection]:
    """Every connection opened while the fixture is active. Function-scoped and auto-restored, so the
    patch never outlives one test — nothing here for xdist or free-threading to race."""
    seen: list[sqlite3.Connection] = []
    real = sqlite3.connect

    def tracking(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = real(*args, **kwargs)  # type: ignore[arg-type]
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


@pytest.mark.parametrize(
    ("name", "kind", "build", "exercise"), STORES, ids=[store[0] for store in STORES]
)
def test_store_releases_every_connection_when_its_owner_dies(
    tmp_path: Path,
    opened: list[sqlite3.Connection],
    name: str,
    kind: str,
    build: Callable[[Path], object],
    exercise: Callable[[object], object],
) -> None:
    store = build(tmp_path / f"{name}.sqlite")
    at_build = len(opened)
    exercise(store)

    if kind == POOLED:
        assert len(opened) > at_build, "the exercise never reached the pooled connection"
    else:
        assert opened

    del store
    gc.collect()

    assert all(_is_closed(c) for c in opened)


def test_the_oracle_catches_a_store_that_never_registers(
    tmp_path: Path, opened: list[sqlite3.Connection]
) -> None:
    """Negative control. Without this, every row above could pass by never opening anything."""

    class UnregisteredStore:
        def __init__(self, path: Path) -> None:
            self._con = sqlite3.connect(path)

    store = UnregisteredStore(tmp_path / "unregistered.sqlite")
    del store
    gc.collect()

    assert opened
    assert not all(_is_closed(c) for c in opened)

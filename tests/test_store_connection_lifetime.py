"""Shipped stores must close live connections when their owner dies.

Holding connections past owner collection prevents refcounting from hiding missed registration.
Pool mechanics live in test_sqlite_pool.py.
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

STORES = [
    ("DictionaryDb", lambda p: DictionaryDb.open(p), lambda s: s.stats()),
    ("RenderCache", lambda p: RenderCache.open(p, max_bytes=1 << 20), lambda s: s.stats()),
    ("MaskAtlas", lambda p: MaskAtlas.open(p), lambda s: s.count()),
    ("BacklogStore", BacklogStore, lambda s: s.all_media()),
    ("MinedCardStore", MinedCardStore, lambda s: s.by_note_id(1)),
    ("SessionStore", SessionStore, lambda s: s.recent()),
]


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[sqlite3.Connection]:
    """Hold every opened connection past owner collection."""
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


@pytest.mark.parametrize(("name", "build", "exercise"), STORES, ids=[store[0] for store in STORES])
def test_store_releases_every_connection_when_its_owner_dies(
    tmp_path: Path,
    opened: list[sqlite3.Connection],
    name: str,
    build: Callable[[Path], object],
    exercise: Callable[[object], object],
) -> None:
    store = build(tmp_path / f"{name}.sqlite")
    exercise(store)

    assert any(not _is_closed(c) for c in opened), "exercise did not reach a live connection"

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
    for connection in opened:
        connection.close()

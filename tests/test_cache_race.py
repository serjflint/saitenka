"""Deterministic regression for concurrent decoded-entry eviction."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import override

import dicthelp
import pytest
from saitenka_dict import SqliteDictionaryStore, TermQuery, Translator
from saitenka_dict.store import TermRecord


class _PausedLru(OrderedDict[int, TermRecord]):
    def __init__(
        self,
        entries: OrderedDict[int, TermRecord],
        pause_key: int,
        at_move: threading.Event,
        eviction_attempted: threading.Event,
        eviction_finished: threading.Event,
        *,
        guarded: bool,
    ):
        super().__init__(entries)
        self._pause_key = pause_key
        self._guarded = guarded
        self._at_move = at_move
        self._eviction_attempted = eviction_attempted
        self._eviction_finished = eviction_finished

    @override
    def move_to_end(self, key: int, last: bool = True) -> None:
        if key == self._pause_key and threading.current_thread().name == "cache-hit":
            self._at_move.set()
            assert self._eviction_attempted.wait(2), "eviction did not attempt the cache lock"
            if not self._guarded:
                assert self._eviction_finished.wait(2), "unguarded eviction did not finish"
        super().move_to_end(key, last)


class _ObservedLock:
    def __init__(self, lock: threading.Lock | None, eviction_attempted: threading.Event):
        self._lock = lock
        self._eviction_attempted = eviction_attempted

    def __enter__(self) -> None:
        if threading.current_thread().name == "cache-eviction":
            self._eviction_attempted.set()
        if self._lock is not None:
            self._lock.acquire()

    def __exit__(self, *_exc_info: object) -> None:
        if self._lock is not None:
            self._lock.release()


def _store_with_paused_hit(tmp_path, monkeypatch, *, guarded: bool):
    db = dicthelp.db()
    row = db.import_zip(
        dicthelp.term_zip(
            tmp_path / "race.zip",
            "Race",
            [("古", "ふる", ["old"]), ("新", "しん", ["new"])],
        ),
        imported_at=dicthelp.AT,
    )
    store = SqliteDictionaryStore(db.path, entry_cache_max=1)
    translator = Translator(store)
    translator.lookup_terms(TermQuery("古"))
    original = store._entry_caches[row.id]
    at_move = threading.Event()
    eviction_attempted = threading.Event()
    eviction_finished = threading.Event()
    monkeypatch.setattr(
        store,
        "_entry_lock",
        _ObservedLock(store._entry_lock if guarded else None, eviction_attempted),
    )
    store._entry_caches[row.id] = _PausedLru(
        original,
        next(iter(original)),
        at_move,
        eviction_attempted,
        eviction_finished,
        guarded=guarded,
    )
    return store, translator, at_move, eviction_attempted, eviction_finished


def _force_eviction_race(translator, at_move, eviction_attempted, eviction_finished):
    errors: dict[str, BaseException] = {}

    def lookup(label: str, term: str) -> None:
        try:
            if label == "eviction":
                assert at_move.wait(2), "cached lookup did not reach move_to_end"
            translator.lookup_terms(TermQuery(term))
        except BaseException as error:  # noqa: BLE001 - thread failures must reach the test
            errors[label] = error
        finally:
            if label == "eviction":
                eviction_finished.set()

    hit = threading.Thread(target=lookup, args=("hit", "古"), name="cache-hit")
    eviction = threading.Thread(target=lookup, args=("eviction", "新"), name="cache-eviction")
    hit.start()
    eviction.start()
    try:
        hit.join(3)
        eviction.join(3)
    finally:
        at_move.set()
        eviction_attempted.set()
        eviction_finished.set()
        hit.join(1)
        eviction.join(1)
    assert not hit.is_alive() and not eviction.is_alive(), "forced cache race did not terminate"
    return errors


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_entry_cache_survives_a_concurrent_eviction(tmp_path, monkeypatch):
    store, translator, at_move, eviction_attempted, eviction_finished = _store_with_paused_hit(
        tmp_path, monkeypatch, guarded=True
    )

    assert _force_eviction_race(translator, at_move, eviction_attempted, eviction_finished) == {}
    assert store.decoded_entry_count() == 1


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_the_entry_lock_is_load_bearing(tmp_path, monkeypatch):
    _store, translator, at_move, eviction_attempted, eviction_finished = _store_with_paused_hit(
        tmp_path, monkeypatch, guarded=False
    )

    errors = _force_eviction_race(translator, at_move, eviction_attempted, eviction_finished)

    assert set(errors) == {"hit"}
    assert isinstance(errors.get("hit"), KeyError)

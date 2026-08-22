"""Deterministic concurrency regression for the entry-cache eviction race (the 942ca3c class).

`Dictionary._entry_cache` is a bare `OrderedDict` shared by the main thread and every prefetch worker under
the free-threaded build. Its check-then-act (`get(eid)` … `move_to_end(eid)`) races a concurrent
`popitem()` eviction: a worker can evict `eid` in the window between another thread's `get` and its
`move_to_end`, raising `KeyError` (and, worse, corrupting the link list). The fix guards the OrderedDict
touches with `self._entry_lock`.

Stress can't gate this: the window is sub-microsecond, so `pytest-run-parallel` / `PYTHON_GIL=0 pytest -n
auto` (process-parallel) hit it ~0/200 — which is why it escaped to production. We make it DETERMINISTIC
with `blanket`: its bytecode injector builds a copy of `_entry_from_row` with a rendezvous inserted at the
hit-path `move_to_end` (no edit to the production source), forcing the cached lookup to pause there while a
second thread evicts. blanket controls the *schedule*, so the logical TOCTOU reproduces regardless of the
GIL — the gate needs no special build.

Two tests, together self-certifying (the arm-2 "oracle-liveness" idea made permanent):
  - the regression: under the lock the evictor blocks on `_entry_lock`, the race is impossible → no raise;
  - the negative control: with the guard removed (a no-op lock ≈ pre-942ca3c) the SAME forced schedule
    DOES raise `KeyError` — proving the regression isn't vacuously green and documenting why the lock exists.

White-box on purpose: a race in a private cache has no public observation seam, so we drive
`_entry_from_row` directly with fabricated rows (also sidestepping a sqlite cross-thread confound). Opt-in
`grow` dependency group: `uv run --group grow pytest -q tests/test_cache_race.py`.
"""

from __future__ import annotations

import contextlib
import threading

import dicthelp
import pytest

from saitenka.app.dictionary import Dictionary

# blanket lives in the opt-in `grow` group; skip the whole module in the default env (collection-safe).
blanket = pytest.importorskip("blanket")

# Fabricated rows in the shape `_entry_from_row` decodes, which is the SELECT's column order:
# (id, term, reading, glossary, tags, seq). Widening that query is invisible here until this
# module runs, and it only runs with the opt-in `grow` group — #329 added `seq` and left these
# five-wide, so both tests raised IndexError before reaching what they gate.
ROW_OLD = (1, "古", "ふる", '["oldest — the eviction victim"]', "", 1000)
ROW_NEW = (2, "新", "しん", '["a second, distinct entry"]', "", 1001)


def _make_dict(tmp_path) -> Dictionary:
    d = dicthelp.load_dict(dicthelp.term_zip(tmp_path / "d.zip", "D", [("x", "x", ["x"])]))
    d._entry_cache_max = 1  # so inserting a 2nd distinct eid evicts the first (the race trigger)
    return d


def _force_eviction_race(d: Dictionary) -> dict[str, BaseException]:
    """Force: thread A pauses at the hit-path move_to_end while thread B inserts a new eid and evicts A's.
    Returns the captured exception (if any) from A. Assumes ``d`` is pre-warmed with ROW_OLD."""
    a_at_move = threading.Event()  # A has done get(1)=hit, is about to move_to_end(1)
    b_evicted = (
        threading.Event()
    )  # B has inserted 2 + evicted 1 (only reachable when A holds no lock)

    def rendezvous() -> None:
        a_at_move.set()
        b_evicted.wait(0.5)  # under the lock B is blocked → this times out and A proceeds safely

    # A copy of _entry_from_row with a pause injected at the HIT-path move_to_end (first of the two
    # occurrences, reached only by a cached lookup). Called directly — no patching of the production symbol.
    loc = blanket.Location.text(Dictionary._entry_from_row, "self._entry_cache.move_to_end(eid)")
    paused_entry_from_row = blanket.inject_call(rendezvous, loc)

    err: dict[str, BaseException] = {}

    def cached_lookup() -> None:  # A: get(1)=hit → rendezvous → move_to_end(1)
        try:
            paused_entry_from_row(d, ROW_OLD)
        except BaseException as e:  # noqa: BLE001 — capture the race outcome
            err["a"] = e

    def evicting_lookup() -> None:  # B: get(2)=miss → insert 2 → popitem evicts 1
        a_at_move.wait(5)
        d._entry_from_row(ROW_NEW)
        b_evicted.set()

    ta = threading.Thread(target=cached_lookup)
    tb = threading.Thread(target=evicting_lookup)
    ta.start()
    tb.start()
    ta.join(10)
    tb.join(10)
    return err


@pytest.mark.integration
@pytest.mark.timeout(15)
def test_entry_cache_survives_a_concurrent_eviction(tmp_path):
    d = _make_dict(tmp_path)
    d._entry_from_row(ROW_OLD)  # pre-warm → cache = {1}, the oldest
    err = _force_eviction_race(d)
    # The guarded cache serialises the touches, so the hit-path move_to_end never races the eviction.
    assert "a" not in err, f"entry-cache eviction raced the hit-path move_to_end: {err.get('a')!r}"


@pytest.mark.integration
@pytest.mark.timeout(15)
def test_the_entry_lock_is_load_bearing(tmp_path):
    # Negative control: remove the guard (a no-op lock ≈ pre-942ca3c) on this throwaway instance. The SAME
    # forced schedule must now raise — proving the regression above has teeth (it isn't vacuously green)
    # and that _entry_lock is what prevents the race.
    d = _make_dict(tmp_path)
    d._entry_lock = contextlib.nullcontext()
    d._entry_from_row(ROW_OLD)  # pre-warm
    err = _force_eviction_race(d)
    assert isinstance(err.get("a"), KeyError), (
        f"expected the UNGUARDED cache to raise KeyError under the forced eviction, got {err.get('a')!r} — "
        "the regression test may be vacuous / the injection point drifted"
    )

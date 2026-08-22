from __future__ import annotations

import ast
import copy
import importlib.util
import sys
from pathlib import Path

_CHECKER = Path(__file__).with_name("runtime_migration_check.py")


def _module():
    spec = importlib.util.spec_from_file_location("_runtime_migration_check", _CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_migration_manifest_matches_production() -> None:
    checker = _module()
    actual, _, _ = checker.scan()
    assert checker.check() == 0
    # Live rows, so the scanner is checked against production and not only against the synthetic
    # source below. Every kind is gone now — the driver, the writes, the reads, and finally the
    # host parameter — so the live assertion is that the census is empty. `_names_the_host` below
    # is what stops that being a scanner that quietly stopped looking.
    assert not actual


def test_the_terminal_set_claims_every_remaining_row() -> None:
    """WP5 is out: every live row is now a *named* terminal one, not merely a counted one.

    The interesting assertion is not the total — it is that the remainder is empty. A new row
    appearing outside the terminal set (a regression, or a kind nobody enumerated) would hold the
    total steady by displacing a converted row, and the count alone cannot see that. The total
    itself only ever falls, and falls by a deletion: the `driver-switch` and `transport-reads`
    groups were both taken out whole.
    """
    checker = _module()
    actual, _, _ = checker.scan()
    terminal = {row for group in checker._TERMINAL_DEBT.values() for row in group}
    assert len(terminal) == checker.TERMINAL_TOTAL == 0
    assert [item for item in actual if (item.kind, item.source) not in terminal] == []


def test_a_terminal_row_whose_symbol_moved_is_a_failure() -> None:
    """A renamed terminal symbol lowers the number WP5 compares against, without touching a count.

    Synthetic now that `_TERMINAL_DEBT` is empty. The guard is what a future row would be added
    behind, so it has to keep being exercised — a guard tested only through a live row stops being
    tested the moment the last row converts, which is exactly when it is about to be re-used.
    """
    checker = _module()
    actual, symbols, evidence = checker.scan()
    source = "src/saitenka/app/reader_factory.py::create_reader"
    checker._TERMINAL_DEBT = {"synthetic": frozenset({("reader-parameter", source)})}
    manifest = {
        "debt": [[item.kind, item.source] for item in sorted(actual)],
        **{group: [] for group in ("startup", "close", "entrypoints")},
    }
    problems = checker.failures(manifest, actual, symbols - {source}, evidence)
    assert problems["terminal_unresolved"] == [source]


def test_scanner_detects_each_debt_category() -> None:
    checker = _module()
    source = """
class Reader:
    # Not `run`: WP6 emptied `_TICK_METHODS`, so no name is a tick stage any more.
    def drive(self):
        self.ipc.command("get_property", "pause")
        self._overlay.show()
        self._queue.get_nowait()
        return self.manual_until

def feature(reader: Reader):
    return reader

def keyword_feature(*, reader: Reader):
    return reader

def untyped_feature(reader):
    return reader

def aliased_command(player):
    player.command("set_property", "pause", True)

def register(app):
    app.command(feature)
"""
    scanner = checker.Scanner("src/saitenka/app/planted.py")
    scanner.visit(ast.parse(source))
    kinds = {item.kind for item in scanner.debt}
    assert kinds == {
        "direct-mpv-command",
        "direct-mpv-read",
        "direct-overlay-mutation",
        "passive-result-drain",
        "polled-deadline",
        "reader-parameter",
    }
    assert (
        checker.Debt("reader-parameter", "src/saitenka/app/planted.py::keyword_feature")
        in scanner.debt
    )
    assert (
        checker.Debt("reader-parameter", "src/saitenka/app/planted.py::untyped_feature")
        in scanner.debt
    )
    assert (
        checker.Debt("direct-mpv-command", "src/saitenka/app/planted.py::aliased_command")
        in scanner.debt
    )
    assert (
        checker.Debt("direct-mpv-command", "src/saitenka/app/planted.py::register")
        not in scanner.debt
    )


def test_scanner_detects_a_monotonic_next_due_retry() -> None:
    checker = _module()
    scanner = checker.Scanner("src/saitenka/app/planted.py")
    scanner.visit(
        ast.parse(
            """
def request(self):
    now = time.monotonic()
    if now - self._started_at < self._delay:
        return
    self._next_due = time.monotonic() + 1
"""
        )
    )
    assert checker.Debt("polled-deadline", "src/saitenka/app/planted.py::request") in scanner.debt


def test_scanner_does_not_treat_a_latency_timestamp_as_a_deadline() -> None:
    checker = _module()
    scanner = checker.Scanner("src/saitenka/app/planted.py")
    scanner.visit(
        ast.parse(
            """
def record(self):
    self._submitted_at = time.monotonic()
    return time.monotonic() - self._submitted_at
"""
        )
    )
    assert (
        checker.Debt("polled-deadline", "src/saitenka/app/planted.py::record") not in scanner.debt
    )


def test_scanner_excludes_permitted_adapter_local_deadlines() -> None:
    checker = _module()
    actual, _, _ = checker.scan()
    assert (
        checker.Debt("polled-deadline", "src/saitenka/app/anki.py::wait_until_anki_up")
        not in actual
    )
    assert (
        checker.Debt("polled-deadline", "src/saitenka/app/otel_export.py::CTFSpanProcessor._flush")
        not in actual
    )


def test_lifecycle_evidence_binds_each_loop_and_terminal_order() -> None:
    checker = _module()

    def evidence(source: str) -> dict[str, set[str]]:
        scanner = checker.Scanner("src/saitenka/app/planted.py")
        scanner.visit(ast.parse(source))
        return scanner.evidence

    expected = "src/saitenka/app/planted.py::close"
    complete = evidence(
        """
def close(self, player):
    for worker in self.first:
        worker.join()
    for worker in self.second:
        worker.join()
    self.close()
    player.command("quit")
    player.close()
"""
    )
    assert checker._has_evidence(complete, expected, "loop:self.first=>call:worker.join")
    assert checker._has_evidence(complete, expected, "loop:self.second=>call:worker.join")
    assert checker._has_evidence(
        complete, expected, "order:call:self.close>call:player.command:quit"
    )
    assert checker._has_evidence(
        complete, expected, "order:call:player.command:quit>call:player.close"
    )

    missing_join = evidence(
        """
def close(self, player):
    for worker in self.first:
        worker.join()
    for worker in self.second:
        pass
"""
    )
    assert not checker._has_evidence(missing_join, expected, "loop:self.second=>call:worker.join")

    swapped = evidence(
        """
def close(self, player):
    self.close()
    player.close()
    player.command("quit")
"""
    )
    assert not checker._has_evidence(
        swapped, expected, "order:call:player.command:quit>call:player.close"
    )


def test_manifest_rejects_added_moved_and_unresolved_debt() -> None:
    checker = _module()
    expected = checker.Debt("direct-mpv-command", "old.py::f")
    added = checker.Debt("direct-mpv-command", "new.py::f")
    manifest = {
        "debt": [[expected.kind, expected.source]],
        "startup": [
            {
                "id": "duty",
                "source": "missing.py::f",
                "target": "owner",
                "work_package": "1",
                "replacement": "Event -> Effect",
                "test": "observable contract",
                "evidence": ["call:missing"],
            }
        ],
        "close": [],
        "entrypoints": [],
    }
    problems = checker.failures(manifest, {added}, set(), {})
    assert problems["missing"] == ["direct-mpv-command:old.py::f"]
    assert problems["added"] == ["direct-mpv-command:new.py::f"]
    assert problems["unresolved"] == ["missing.py::f"]
    assert problems["missing_evidence"] == ["duty@missing.py::f:call:missing"]


def test_manifest_rejects_deleted_added_and_moved_lifecycle_duties() -> None:
    checker = _module()
    actual, symbols, evidence = checker.scan()
    original = checker._load()

    deleted = copy.deepcopy(original)
    deleted["startup"].pop()
    assert "startup duty IDs differ" in " ".join(
        checker.failures(deleted, actual, symbols, evidence)["schema"]
    )

    added = copy.deepcopy(original)
    extra = copy.deepcopy(added["close"][0])
    extra["id"] = "invented-duty"
    added["close"].append(extra)
    assert "close duty IDs differ" in " ".join(
        checker.failures(added, actual, symbols, evidence)["schema"]
    )

    moved = copy.deepcopy(original)
    moved["entrypoints"][0]["source"] = "src/saitenka/app/controller.py::Reader.run"
    problems = checker.failures(moved, actual, symbols, evidence)
    # The key names the SITE as well as the duty: a duty can be sourced at several entrypoints,
    # and "one of them still does it by hand" is exactly what this census exists to report.
    assert any(
        item.startswith("run-owned-player@src/saitenka/app/controller.py::Reader.run:")
        for item in problems["missing_evidence"]
    )


def test_scanner_separates_a_deadline_field_from_a_call_that_ends_in_until() -> None:
    """`SessionRunner.run_until` is the shape WP5.5 mandates, and the `*_until` heuristic — written
    for deadline fields like `manual_until` — read its name as the thing it replaces."""
    checker = _module()
    scanner = checker.Scanner("src/saitenka/app/planted.py")
    scanner.visit(
        ast.parse(
            """
def waits(self, runner):
    return runner.run_until(lambda: True, deadline=None)

def polls(self):
    return self.manual_until
"""
        )
    )

    assert checker.Debt("polled-deadline", "src/saitenka/app/planted.py::waits") not in scanner.debt
    assert checker.Debt("polled-deadline", "src/saitenka/app/planted.py::polls") in scanner.debt


def test_scanner_exempts_the_presentation_adapters_but_not_their_callers() -> None:
    """`direct-overlay-mutation` means a *feature* reached past its layer. The surface adapters are
    that layer, so painting is their job — and the exemption is by symbol, so a feature that calls
    them is untouched by it."""
    checker = _module()
    actual, _, _ = checker.scan()

    assert (
        checker.Debt(
            "direct-overlay-mutation",
            "src/saitenka/app/interaction_surfaces.py::InteractionSurfaces.present_bgra",
        )
        not in actual
    )
    # The counter-example moved: `_flush_paused_nudge` was the standing one until its `ov.repaint()`
    # went behind `LifecycleSurfaces`, which took `direct-overlay-mutation` to zero. A planted
    # feature stands in, so the exemption is still proved NOT to cover a caller.
    scanner = checker.Scanner("src/saitenka/app/planted.py")
    scanner.visit(ast.parse("def feature(self):\n    self.ov.show(img)\n"))
    assert (
        checker.Debt("direct-overlay-mutation", "src/saitenka/app/planted.py::feature")
        in scanner.debt
    )
    assert not [d for d in actual if d.kind == "direct-overlay-mutation"]


def test_scanner_separates_a_direct_read_from_a_direct_write() -> None:
    """WP5's exit gate is phrased as "no direct *write* remains", and the one kind could not answer
    it: a `get_property` has no terminal outcome to correlate, so routing it through the egress
    gateway buys nothing and it is not what the gate is counting.

    A symbol that reads and then writes is a WRITE site. Classifying it by its first call, or by
    whether any call is a read, would hide `remove_external_sub_tracks` — which reads the track list
    and then removes tracks — behind the read kind.
    """
    checker = _module()
    scanner = checker.Scanner("src/saitenka/app/planted.py")
    scanner.visit(
        ast.parse(
            """
def only_reads(ipc):
    return ipc.command("get_property", "sub-text")

def only_writes(ipc):
    ipc.command("set_property", "sid", 2)

def reads_then_writes(ipc):
    for track in ipc.command("get_property", "track-list")["data"]:
        ipc.command("sub-remove", track["id"])

def dynamic_verb(ipc, *command):
    ipc.command(*command)
"""
        )
    )
    planted = "src/saitenka/app/planted.py"

    assert checker.Debt("direct-mpv-read", f"{planted}::only_reads") in scanner.debt
    assert checker.Debt("direct-mpv-command", f"{planted}::only_reads") not in scanner.debt

    assert checker.Debt("direct-mpv-command", f"{planted}::only_writes") in scanner.debt
    assert checker.Debt("direct-mpv-command", f"{planted}::reads_then_writes") in scanner.debt
    assert checker.Debt("direct-mpv-read", f"{planted}::reads_then_writes") not in scanner.debt

    # An unresolvable verb is a write until proven otherwise — the gate must not be relaxed by a
    # call site it cannot read.
    assert checker.Debt("direct-mpv-command", f"{planted}::dynamic_verb") in scanner.debt


def test_a_synchronous_by_contract_write_is_exempt_but_its_neighbours_are_not() -> None:
    """Four writes cannot go through the egress gateway: the caller needs the reply or the side
    effect before returning, or the reactor is stopping and could never drain it. Counting them as
    debt keeps WP5's exit gate permanently unreachable and hides the rows that ARE still work.

    The exemption is by symbol, so it cannot silently widen: a new direct write in the same module,
    or a second one added to an exempt function, is still counted.
    """
    checker = _module()
    actual, _, _ = checker.scan()

    for source in checker._SYNCHRONOUS_BY_CONTRACT:
        assert checker.Debt("direct-mpv-command", source) not in actual
        assert checker.Debt("direct-mpv-read", source) not in actual

    # Same module as an exempt symbol, still counted. Planted rather than pointed at a live row:
    # every module is clean now, so a live neighbour would keep vanishing and the non-widening
    # claim would quietly stop being asserted.
    scanner = checker.Scanner("src/saitenka/app/media.py")
    scanner.visit(
        ast.parse(
            """
def screenshot(ipc):
    ipc.command("screenshot-to-file", "shot.png")

def neighbour(ipc):
    ipc.command("get_property", "sub-start")
"""
        )
    )
    assert (
        checker.Debt("direct-mpv-command", "src/saitenka/app/media.py::screenshot")
        not in scanner.debt
    )
    assert checker.Debt("direct-mpv-read", "src/saitenka/app/media.py::neighbour") in scanner.debt


def test_no_write_to_mpv_bypasses_the_egress_gateway() -> None:
    """WP5's exit condition for writes, now at zero: every mpv write in `app/` is correlated.

    The last three — the renderer's activate/deactivate and `_apply_action`'s SHOW_MPV /
    RESTORE_VISIBILITY — waited on `MpvIPC.close` flushing its write queue, without which a
    correlated teardown restore was queued and then dropped. What stays permanently synchronous is
    enumerated in `_SYNCHRONOUS_BY_CONTRACT`, and is exempted there rather than counted here.
    """
    checker = _module()
    actual, _, _ = checker.scan()

    assert {d.source for d in actual if d.kind == "direct-mpv-command"} == set()


def test_a_converted_symbol_is_retired_without_a_hand_bless() -> None:
    """A row vanishing while its symbol remains IS the migration working. Failing on it made every
    conversion cost two extra gate runs — the run that reported it and the run after the bless.
    """
    checker = _module()
    manifest = {
        "debt": [["reader-parameter", "keeps.py::converted"]],
        "startup": [],
        "close": [],
        "entrypoints": [],
    }

    problems = checker.failures(manifest, set(), {"keeps.py::converted"}, {})

    assert problems.get("retired") == ["reader-parameter:keeps.py::converted"]
    assert "missing" not in problems  # not a failure


def test_a_row_whose_symbol_vanished_still_fails() -> None:
    """The hole the auto-retire must not open: renaming or moving a debt-carrying function out of
    the scanned tree would otherwise read as progress. The symbol is gone, so this is not a
    conversion — it is debt leaving the denominator without being fixed.
    """
    checker = _module()
    manifest = {
        "debt": [["reader-parameter", "moved.py::gone"]],
        "startup": [],
        "close": [],
        "entrypoints": [],
    }

    problems = checker.failures(manifest, set(), {"other.py::something"}, {})

    assert problems["missing"] == ["reader-parameter:moved.py::gone"]
    assert "retired" not in problems


def test_a_rename_fails_on_the_added_half_even_though_the_old_row_went() -> None:
    """A rename is one removal plus one addition. The removal half may look like a conversion, so
    the addition half is what has to bite — and does."""
    checker = _module()
    manifest = {
        "debt": [["reader-parameter", "m.py::old_name"]],
        "startup": [],
        "close": [],
        "entrypoints": [],
    }
    actual = {checker.Debt("reader-parameter", "m.py::new_name")}

    problems = checker.failures(manifest, actual, {"m.py::new_name"}, {})

    assert problems["added"] == ["reader-parameter:m.py::new_name"]


def test_growth_is_still_a_failure() -> None:
    """The property the ratchet exists for, unchanged: debt may shrink on its own, never grow."""
    checker = _module()
    manifest = {"debt": [], "startup": [], "close": [], "entrypoints": []}
    actual = {checker.Debt("direct-mpv-command", "m.py::new_write")}

    assert checker.failures(manifest, actual, {"m.py::new_write"}, {})["added"] == [
        "direct-mpv-command:m.py::new_write"
    ]


def test_a_duty_sourced_at_two_entrypoints_is_watched_at_both() -> None:
    """A duty performed at several sites is migrated when ALL of them move.

    `source` was a single string, so `transport` named only `run_impl` while `attach` did the
    identical `ipc.close()` — outside the census entirely. Converting `run` would have reported the
    duty migrated with attach still doing it by hand, which is the one thing the duty half of the
    exit gate exists to prevent.
    """
    checker = _module()
    duty = {
        "id": "duty",
        "source": ["a.py::f", "b.py::g"],
        "target": "t",
        "work_package": "6",
        "replacement": "r",
        "test": "t",
        "evidence": ["call:ipc.close"],
        "migrated": False,
    }
    manifest = {"debt": [], "startup": [], "close": [duty], "entrypoints": []}
    symbols = {"a.py::f", "b.py::g"}

    both = checker.failures(
        manifest, set(), symbols, {"a.py::f": {"call:ipc.close"}, "b.py::g": {"call:ipc.close"}}
    )
    assert both.get("missing_evidence", []) == []

    # The second site stops doing it: the duty is not silently satisfied by the first.
    one = checker.failures(manifest, set(), symbols, {"a.py::f": {"call:ipc.close"}})
    assert one.get("missing_evidence") == ["duty@b.py::g:call:ipc.close"]


def test_a_host_parameter_is_the_host_and_not_a_value_named_after_it() -> None:
    """The census is zero, so the scanner has to be shown still biting — and shown where it stopped.

    A substring test on the annotation counted `ReaderOptions` and `ReaderServices` as host
    parameters. Both were then filed under terminal composition debt, where being unconvertible is
    the point, so nothing re-examined them and the last two rows of the migration were a spelling.
    """
    checker = _module()
    bites = [
        "def by_name(reader): ...",
        "def annotated(host: Reader): ...",
        'def quoted(host: "Reader"): ...',
        "def optional(host: Reader | None): ...",
        "def qualified(host: controller.Reader): ...",
        "def keyword_only(*, host: Reader): ...",
    ]
    for source in bites:
        annotation = ast.parse(source).body[0].args
        arguments = [*annotation.args, *annotation.kwonlyargs]
        assert any(
            argument.arg == "reader" or checker._names_the_host(argument.annotation)
            for argument in arguments
        ), source

    passes = [
        "def config(options: ReaderOptions): ...",
        "def services(services: ReaderServices | None): ...",
        "def unrelated(x: int): ...",
        "def bare(x): ...",
    ]
    for source in passes:
        annotation = ast.parse(source).body[0].args
        arguments = [*annotation.args, *annotation.kwonlyargs]
        assert not any(
            argument.arg == "reader" or checker._names_the_host(argument.annotation)
            for argument in arguments
        ), source

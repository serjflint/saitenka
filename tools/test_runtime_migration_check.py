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
    # Two live rows, so the scanner is checked against production and not only against the
    # synthetic source below. They are anchors, not landmarks: as the migration converts them, point
    # these at whatever `poe runtime-status` still reports rather than weakening the assertion.
    # `tick-stage` is down to the driver itself — `poll_once` and `run`, which WP6 deletes
    # together. When it does, point these at whatever `poe runtime-status` still reports.
    assert checker.Debt("tick-stage", "src/saitenka/app/controller.py::Reader.poll_once") in actual
    assert checker.Debt("tick-stage", "src/saitenka/app/controller.py::Reader.run") in actual


def test_scanner_detects_each_debt_category() -> None:
    checker = _module()
    source = """
class Reader:
    def run(self):
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
    player.command("get_property", "pause")

def register(app):
    app.command(feature)
"""
    scanner = checker.Scanner("src/saitenka/app/planted.py")
    scanner.visit(ast.parse(source))
    kinds = {item.kind for item in scanner.debt}
    assert kinds == {
        "direct-mpv-command",
        "direct-overlay-mutation",
        "passive-result-drain",
        "polled-deadline",
        "reader-parameter",
        "tick-stage",
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
    assert problems["missing_evidence"] == ["duty:call:missing"]


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
    assert any(item.startswith("run-owned-player:") for item in problems["missing_evidence"])


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
    assert (
        checker.Debt(
            "direct-overlay-mutation", "src/saitenka/app/controller.py::Reader._flush_paused_nudge"
        )
        in actual
    )

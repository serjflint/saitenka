"""Lock the exact legacy runtime coupling until each vertical slice deletes it."""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from itertools import starmap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "runtime_migration_manifest.json"
APP = ROOT / "src" / "saitenka" / "app"

_OVERLAY_METHODS = {
    "hide",
    "hide_interactive",
    "repaint",
    "set_visible",
    "show",
    "show_bgra",
    "show_bgra_interactive",
}
_TICK_METHODS = {
    "Reader._apply_background_results",
    "Reader._expire_surfaces",
    "Reader._reconcile_subtitles",
    "Reader._refresh_surfaces",
    "Reader._update_interaction",
    "Reader.poll_once",
    "Reader.run",
}
_AUTONOMOUS_DRAINS = {
    "src/saitenka/app/otel_export.py::CTFSpanProcessor._flush",
    "src/saitenka/app/prefetch.py::_try_head_prefetch_item",
}
_AUTONOMOUS_DEADLINES = {
    "src/saitenka/app/anki.py::wait_until_anki_up",
    "src/saitenka/app/otel_export.py::CTFSpanProcessor._flush",
}
#: The presentation adapters. `direct-overlay-mutation` means "a *feature* reaches past its layer
#: and paints" — these two ARE the layer, and mutating the overlay is their whole job.
#: `LifecycleSurfaces` is absent only because it happens to stage through `prepare`, which is not in
#: `_OVERLAY_METHODS`; that is an accident of naming, not a different status. Anything added here
#: must be the sole owner of a presentation slot's transactions, not merely a frequent painter.
_PRESENTATION_ADAPTERS = {
    "src/saitenka/app/interaction_surfaces.py::InteractionSurfaces.present_bgra",
    "src/saitenka/app/interaction_surfaces.py::InteractionSurfaces.remove",
}
_NON_MPV_COMMAND_RECEIVERS = {"app", "profile_app"}
#: Writes that CANNOT go through the egress gateway, with the reason each is permanent. Not debt and
#: not deferral — a correlated command here would be wrong, so counting them keeps WP5's exit gate
#: permanently unreachable and hides the rows that are still work.
#:
#: The bar for adding one: the caller needs the reply or the side effect BEFORE it returns, or the
#: reactor is stopping and could never drain it. "It is awkward" is not on that list.
_SYNCHRONOUS_BY_CONTRACT = {
    # Runs from `close`: a queued command is never drained, and the forced section would outlive us
    # still holding the mouse away from a detached mpv.
    "src/saitenka/app/controller.py::Reader._release_mouse_capture",
    # The caller reads the file mpv writes, so this one genuinely must be awaited.
    "src/saitenka/app/media.py::screenshot",
    # Same: prints the capture's reply, and the file must exist when it returns.
    "src/saitenka/app/launch/run.py::_run_demo_actions",
    # `quit`, issued while the reactor is stopping.
    "src/saitenka/app/launch/run.py::run_impl",
}
#: mpv verbs that only READ. WP5's exit gate is phrased in terms of a direct *write* — a read has no
#: terminal outcome to correlate, so routing one through the egress gateway buys nothing. Splitting
#: the kind is what makes that gate answerable from the manifest instead of by eye.
_MPV_READ_VERBS = {"get_property"}
_DRIVER_SWITCH_SYMBOLS = {
    "src/saitenka/app/runtime/commands.py::LegacyPickerRepeatGuard",
}
_DUTY_IDS = {
    "startup": {
        "version-and-render-guard",
        "initial-render-space",
        "observers-and-replay",
        "bindings-and-input-sections",
        "mined-seed-and-capabilities",
        "session-history",
        "telemetry-and-startup-health",
        "runtime-ready",
        "session-loop",
    },
    "close": {
        "worker-and-capability-actors",
        "input-capture",
        "subtitle-and-ownership",
        "session-and-stores",
        "surfaces",
        "telemetry",
        "temporary-artifacts",
        "transport",
    },
    "entrypoints": {
        "run-owned-player",
        "attach-external-player",
        "demo-owned-player",
        "screenshot-owned-player",
    },
}


@dataclass(frozen=True, slots=True, order=True)
class Debt:
    kind: str
    source: str

    def encode(self) -> str:
        return f"{self.kind}:{self.source}"


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


class Scanner(ast.NodeVisitor):
    def __init__(self, relative: str) -> None:
        self.relative = relative
        self.stack: list[str] = []
        self._debt: set[Debt] = set()
        self.symbols: set[str] = set()
        self.evidence: dict[str, set[str]] = {}
        self.call_order: dict[str, list[str]] = {}
        self.monotonic_locals: list[set[str]] = []
        #: Attribute nodes that are a call's target. Keyed by identity because the AST has
        #: no parent links and `visit_Call` runs before the walk reaches its own `func`.
        self.called_attributes: set[int] = set()
        #: Per symbol, one bool per direct `.command(...)` call: True when the verb only reads.
        #: A symbol is a read row only if EVERY one of its calls is a read — a function that reads
        #: and then writes is a write site, and the gate must see it as one.
        self.mpv_calls: dict[str, list[bool]] = {}

    @property
    def debt(self) -> set[Debt]:
        """Discovered debt, with each symbol's direct mpv calls folded in as one row.

        Folded here rather than at each call site because the kind is a property of the SYMBOL, not
        of one call: a function that reads the track list and then removes tracks is a write site,
        and classifying per call would file it under both.
        """
        rows = set(self._debt)
        rows.update(
            Debt("direct-mpv-read" if all(reads) else "direct-mpv-command", source)
            for source, reads in self.mpv_calls.items()
        )
        return rows

    def _symbol(self) -> str:
        return ".".join(self.stack) or "<module>"

    def _source(self) -> str:
        return f"{self.relative}::{self._symbol()}"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.symbols.add(self._source())
        if self._source() in _DRIVER_SWITCH_SYMBOLS:
            self._debt.add(Debt("driver-switch", self._source()))
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.monotonic_locals.append(set())
        self.symbols.add(self._source())
        if ".".join(self.stack) in _TICK_METHODS:
            self._debt.add(Debt("tick-stage", self._source()))
        arguments = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            *([node.args.vararg] if node.args.vararg is not None else []),
            *([node.args.kwarg] if node.args.kwarg is not None else []),
        )
        annotations = [arg.annotation for arg in arguments]
        if any(argument.arg == "reader" for argument in arguments) or any(
            annotation is not None and "Reader" in ast.unparse(annotation)
            for annotation in annotations
        ):
            self._debt.add(Debt("reader-parameter", self._source()))
        self.generic_visit(node)
        self.monotonic_locals.pop()
        self.stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            # A called `*_until` is a method, not a stored deadline. `SessionRunner.run_until` is
            # the shape WP5.5 mandates, so the name heuristic below must not read it as the thing
            # it replaces.
            self.called_attributes.add(id(node.func))
        called = _dotted(node.func)
        if called:
            facts = self.evidence.setdefault(self._source(), set())
            facts.add(f"call:{called}")
            label = _call_label(node)
            facts.add(label)
            ordered = self.call_order.setdefault(self._source(), [])
            facts.update(f"order:{previous}>{label}" for previous in ordered)
            ordered.append(label)
        if isinstance(node.func, ast.Attribute):
            receiver = _dotted(node.func.value)
            if (
                node.func.attr == "command"
                and receiver not in _NON_MPV_COMMAND_RECEIVERS
                and self._source() not in _SYNCHRONOUS_BY_CONTRACT
            ):
                verb = node.args[0] if node.args else None
                read = isinstance(verb, ast.Constant) and verb.value in _MPV_READ_VERBS
                self.mpv_calls.setdefault(self._source(), []).append(read)
            if node.func.attr == "get_nowait" and self._source() not in _AUTONOMOUS_DRAINS:
                self._debt.add(Debt("passive-result-drain", self._source()))
            receiver_tail = receiver.rsplit(".", 1)[-1]
            if (
                node.func.attr in _OVERLAY_METHODS
                and (receiver_tail == "ov" or "overlay" in receiver_tail)
                and self._source() not in _PRESENTATION_ADAPTERS
            ):
                self._debt.add(Debt("direct-overlay-mutation", self._source()))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        dotted = _dotted(node)
        if dotted:
            self.evidence.setdefault(self._source(), set()).add(f"ref:{dotted}")
        if (
            node.attr.endswith("_until")
            and id(node) not in self.called_attributes
            and self._source() not in _AUTONOMOUS_DEADLINES
        ):
            self._debt.add(Debt("polled-deadline", self._source()))
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if self._source() in _AUTONOMOUS_DEADLINES:
            self.generic_visit(node)
            return
        operands = (node.left, *node.comparators)
        aliases = self.monotonic_locals[-1] if self.monotonic_locals else set()
        if any(
            _contains_monotonic(part)
            or any(isinstance(child, ast.Name) and child.id in aliases for child in ast.walk(part))
            for part in operands
        ) and any(
            isinstance(child, ast.Attribute) for part in operands for child in ast.walk(part)
        ):
            self._debt.add(Debt("polled-deadline", self._source()))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if self.monotonic_locals and _contains_monotonic(node.value):
            self.monotonic_locals[-1].update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        iterable = _dotted(node.iter)
        facts = self.evidence.setdefault(self._source(), set())
        for statement in node.body:
            for part in ast.walk(statement):
                if isinstance(part, ast.Call) and _dotted(part.func):
                    facts.add(f"loop:{iterable}=>{_call_label(part)}")
        self.generic_visit(node)


def _contains_monotonic(node: ast.AST) -> bool:
    return any(
        isinstance(part, ast.Call) and _dotted(part.func) == "time.monotonic"
        for part in ast.walk(node)
    )


def _call_label(node: ast.Call) -> str:
    called = _dotted(node.func)
    if (
        node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str | int | float | bool)
    ):
        return f"call:{called}:{node.args[0].value}"
    return f"call:{called}"


def scan() -> tuple[set[Debt], set[str], dict[str, set[str]]]:
    debt: set[Debt] = set()
    symbols: set[str] = set()
    evidence: dict[str, set[str]] = {}
    for path in sorted(APP.glob("**/*.py")):
        relative = path.relative_to(ROOT).as_posix()
        scanner = Scanner(relative)
        scanner.visit(ast.parse(path.read_text(encoding="utf-8"), filename=relative))
        debt.update(scanner.debt)
        symbols.update(scanner.symbols)
        for source, facts in scanner.evidence.items():
            evidence.setdefault(source, set()).update(facts)
    return debt, symbols, evidence


def _load() -> dict[str, object]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def bless() -> int:
    """Refresh only the mechanically discovered debt denominator."""
    manifest = _load()
    debt, _symbols, _evidence = scan()
    manifest["debt"] = [[item.kind, item.source] for item in sorted(debt)]
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"runtime-migration: blessed {len(debt)} debt symbols")
    return 0


def failures(
    manifest: dict[str, object],
    actual: set[Debt],
    symbols: set[str],
    evidence: dict[str, set[str]],
) -> dict[str, list[str]]:
    debt_rows = manifest.get("debt")
    if not isinstance(debt_rows, list):
        return {"schema": ["debt must be a list"]}
    expected = set(starmap(Debt, debt_rows))
    missing = sorted(item.encode() for item in expected - actual)
    added = sorted(item.encode() for item in actual - expected)
    duty_groups: list[list[dict[str, object]]] = []
    schema: list[str] = []
    ids: set[str] = set()
    missing_evidence: list[str] = []
    for group in ("startup", "close", "entrypoints"):
        duties = manifest.get(group)
        if not isinstance(duties, list):
            schema.append(f"{group} must be a list")
            continue
        duty_groups.append(duties)
        for duty in duties:
            if set(duty) != {
                "id",
                "source",
                "target",
                "work_package",
                "replacement",
                "test",
                "evidence",
            }:
                schema.append(f"{group} duty has invalid fields: {duty.get('id', '<missing>')}")
            duty_id = duty.get("id")
            if not isinstance(duty_id, str) or duty_id in ids:
                schema.append(f"duplicate or invalid duty id: {duty_id}")
            else:
                ids.add(duty_id)
            source = duty.get("source")
            facts = duty.get("evidence")
            if isinstance(source, str) and isinstance(facts, list):
                missing_evidence.extend(
                    f"{duty_id}:{fact}"
                    for fact in facts
                    if not isinstance(fact, str) or not _has_evidence(evidence, source, fact)
                )
            else:
                schema.append(f"{group} duty has invalid evidence: {duty_id}")
        group_ids = {duty["id"] for duty in duties if isinstance(duty.get("id"), str)}
        if group_ids != _DUTY_IDS[group]:
            schema.append(f"{group} duty IDs differ from the closed contract")
    unresolved: list[str] = []
    for duties in duty_groups:
        for duty in duties:
            source = duty.get("source")
            if isinstance(source, str) and source not in symbols:
                unresolved.append(source)
    unresolved.sort()
    result = {
        "missing": missing,
        "added": added,
        "unresolved": unresolved,
        "missing_evidence": sorted(missing_evidence),
        "schema": schema,
    }
    return {name: values for name, values in result.items() if values}


def _has_evidence(evidence: dict[str, set[str]], primary: str, item: str) -> bool:
    source, separator, fact = item.partition("|")
    if not separator:
        source, fact = primary, source
    return fact in evidence.get(source, set())


def check() -> int:
    manifest = _load()
    actual, symbols, evidence = scan()
    problems = failures(manifest, actual, symbols, evidence)
    if problems:
        print(json.dumps(problems, indent=2))
        return 1
    print(
        "runtime-migration: OK "
        f"({len(actual)} debt symbols; "
        f"{sum(len(group) for group in duty_groups(manifest))} duties)"
    )
    return 0


def duty_groups(manifest: dict[str, object]) -> list[list[object]]:
    return [
        group
        for name in ("startup", "close", "entrypoints")
        if isinstance((group := manifest.get(name)), list)
    ]


def show() -> int:
    debt, _symbols, _evidence = scan()
    print(json.dumps([[item.kind, item.source] for item in sorted(debt)], indent=2))
    return 0


def status() -> int:
    """Per-kind census against the blessed manifest — the migration's progress checklist.

    Hand-maintained counts in planning docs drift the moment a slice lands, and the slice plan calls a
    wrong denominator a gate failure. Read them from here instead of retyping them.
    """
    manifest = _load()
    actual, _symbols, _evidence = scan()
    rows = manifest.get("debt")
    blessed: set[tuple[str, str]] = (
        {(row[0], row[1]) for row in rows if isinstance(row, list)}
        if isinstance(rows, list)
        else set()
    )
    live = {(item.kind, item.source) for item in actual}
    kinds = sorted({kind for kind, _ in blessed | live})
    width = max(len(kind) for kind in kinds)
    for kind in kinds:
        was = sum(1 for k, _ in blessed if k == kind)
        now = sum(1 for k, _ in live if k == kind)
        drift = "" if was == now else f"  ({now - was:+d} unblessed)"
        print(f"{kind:<{width}}  {now:>4}{drift}")
    duties = sum(len(group) for group in duty_groups(manifest))
    print(f"{'':<{width}}  {'-' * 4}")
    print(f"{'total':<{width}}  {len(live):>4}   {duties} duties")
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    commands = {"bless": bless, "check": check, "show": show, "status": status}
    if command not in commands:
        print(f"unknown command: {command}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(commands[command]())

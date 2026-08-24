"""Ratchet the mass of `class SessionController` — the meter that fails when the host grows.

    uv run --extra full python tools/host_mass.py            # markdown
    uv run --extra full python tools/host_mass.py json
    uv run --extra full python tools/host_mass.py check      # non-zero if the host grew
    uv run --extra full python tools/host_mass.py bless "why"  # accept growth, with its reason

**Four gated numbers, not one.** `total` is the anti-laundering ratchet: moving a body into a module
function and leaving `return ops.f(self._a, self._b)` behind drops `substantive` while the host is
unchanged, and gating only the substantive split would report that as progress — then reward shaping
every new feature as one module function plus one thin delegator. `substantive` and
`substantive_lines` are the diagnostic half: they say whether the mass that moved was behaviour or
naming, which a total alone cannot. `init_lines` closes the last escape the member counts have:
wiring a collaborator in `__init__` adds lines without adding a member, so a feature can move off
the host and leave its construction behind with every other number still falling.

**Members are discovered live, never by parsing one file.** A static parse of `session_controller.py` cannot
follow a mixin base and cannot see `SessionController.foo = foo` executed at import time at all — and that is
exactly the escape the total exists to close. So `SessionController` is imported and its MRO walked, the
`reducer_purity` idiom, with each member's source resolved back through `inspect`.

**The classification ladder, in precedence order.** The rule "a delegator is a one-call body" is
ambiguous where the kinds overlap, and two faithful readings of it differ by ~12% on this tree, so
the order is fixed here rather than left to the implementation:

1. What `cluster_map.classify_host` resolves a name to, when that is not `method` — a `Delegated`
   descriptor, a property over one owner slice, an alias, a derived property, a config group, a
   field. **A property is never substantive, whatever its body length**: it is a port wearing a
   member's name, and its body is not where behaviour accumulates.
2. Otherwise, for a plain function whose body (docstring stripped) is a single `Return`/`Expr`
   wrapping a `Call`: `self_delegator` when the receiver is `self`, `delegator` when it is anything
   else.
3. Otherwise `substantive`.

This is a ratchet, not a goal: it refuses growth and auto-tightens on a real retirement. It names no
target and asserts nothing about zero.
"""

from __future__ import annotations

import ast
import functools
import inspect
import json
import sys
from pathlib import Path

import cluster_map

ROOT = Path(__file__).resolve().parents[1]
CENSUS = ROOT / "tests" / "fixtures" / "host_mass_census.json"

#: Numbers a growth in which fails the gate. The rest of the census is reported, not gated.
GATED = ("total", "substantive", "substantive_lines", "init_lines")

#: `type`'s own bookkeeping in `vars(cls)`. Not members anybody wrote.
_MACHINERY = frozenset(
    {
        "__annotations__",
        "__dict__",
        "__doc__",
        "__firstlineno__",
        "__module__",
        "__qualname__",
        "__slots__",
        "__static_attributes__",
        "__weakref__",
    }
)


@functools.cache
def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _session_controller() -> type:
    from saitenka.app.session_controller import SessionController

    return SessionController


def _members(host: type) -> dict[str, object]:
    """Every name the host answers to, its own and its bases', nearest definition winning."""
    found: dict[str, object] = {}
    for klass in reversed(host.__mro__):
        if klass is object:
            continue
        found.update({k: v for k, v in vars(klass).items() if k not in _MACHINERY})
    return found


def _node(member: object) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The `def` a live member came from, located the way `reducer_purity` locates a reducer."""
    target = member
    if isinstance(target, staticmethod | classmethod):
        target = target.__func__
    if not (inspect.isfunction(target) or inspect.ismethod(target)):
        return None
    try:
        path = Path(inspect.getsourcefile(target) or "")
        _source, start = inspect.getsourcelines(target)
    except (TypeError, OSError):
        return None
    for node in ast.walk(_tree(path)):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == target.__name__
            and abs(node.lineno - start) <= 2  # the decorator lines a source range includes
        ):
            return node
    return None


def _body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body


def _one_call(body: list[ast.stmt]) -> ast.Call | None:
    if len(body) != 1 or not isinstance(body[0], ast.Return | ast.Expr):
        return None
    value = body[0].value
    return value if isinstance(value, ast.Call) else None


def _axis(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    call = _one_call(_body(node))
    if call is None:
        return "substantive"
    called = call.func
    on_self = (
        isinstance(called, ast.Attribute)
        and isinstance(called.value, ast.Name)
        and called.value.id == "self"
    )
    return "self_delegator" if on_self else "delegator"


def _lines(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    body = _body(node)
    if not body or node.end_lineno is None:
        return 0
    return node.end_lineno - body[0].lineno + 1


def classify(host: type, resolved: dict[str, cluster_map.Member]) -> dict[str, int]:
    counts: dict[str, int] = {"total": 0, "substantive": 0, "substantive_lines": 0}
    for name, member in sorted(_members(host).items()):
        counts["total"] += 1
        known = resolved.get(name)
        if isinstance(member, property):
            kind = known.kind if known and known.kind != "method" else "property"
        elif known is not None and known.kind != "method":
            kind = known.kind
        elif (node := _node(member)) is None:
            kind = "unresolved"
        else:
            kind = _axis(node)
            if kind == "substantive":
                counts["substantive_lines"] += _lines(node)
        counts[kind] = counts.get(kind, 0) + 1
    counts["init_lines"] = _init_lines(host)
    return dict(sorted(counts.items()))


def _init_lines(host: type) -> int:
    """`__init__`'s length — capped, not judged.

    The composition root is the one member the member counts cannot see: wiring a collaborator adds
    lines without adding a member, so a feature that "moved off the host" can leave its construction
    behind and every gated number still falls. Whether 476 lines is composition or accumulation is a
    decision nobody has made; this holds it still until someone does.
    """
    node = _node(host.__init__)
    return 0 if node is None else _lines(node)


def census() -> dict[str, int]:
    return classify(_session_controller(), cluster_map.classify_host())


def _saved() -> tuple[dict[str, int], str]:
    """The committed census and the reason it was last blessed, if it ever was."""
    if not CENSUS.exists():
        return {}, ""
    state = json.loads(CENSUS.read_text(encoding="utf-8"))
    counts = state.get("counts", {})
    return {str(k): int(v) for k, v in counts.items()}, str(state.get("reason", ""))


def _write(counts: dict[str, int], reason: str) -> None:
    payload = {"counts": counts, "reason": reason}
    CENSUS.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def check() -> int:
    """Refuse growth on the gated numbers; auto-tighten when the host actually shrank.

    A bless is recorded in the fixture with its reason and read back here, so a ratchet that has
    become a rubber stamp says so on every run instead of only in a commit nobody re-reads.
    """
    actual = census()
    if not actual.get("total"):
        print("host-mass: resolved no members — the census is vacuous", file=sys.stderr)
        return 1
    if not CENSUS.exists():
        _write(actual, "")
        print(f"host-mass: seeded {CENSUS.relative_to(ROOT)}")
        return 0
    expected, blessed = _saved()
    grew = {
        name: (expected.get(name, 0), actual[name])
        for name in GATED
        if actual[name] > expected.get(name, 0)
    }
    if grew:
        for name, (was, now) in sorted(grew.items()):
            print(f"host-mass: {name} grew {was} -> {now}", file=sys.stderr)
        print(
            "The host gained mass. Put the behaviour behind its owner's adapter, or "
            'bless the growth with its reason: host_mass.py bless "why".',
            file=sys.stderr,
        )
        return 1
    if actual != expected:
        _write(actual, "")
        for name in GATED:
            if actual[name] < expected.get(name, 0):
                print(f"host-mass: {name} {expected[name]} -> {actual[name]}")
    if blessed:
        print(f"host-mass: last blessed because {blessed}")
    print("host-mass: OK " + ", ".join(f"{name} {actual[name]}" for name in GATED))
    return 0


def bless(reason: str) -> int:
    if not reason.strip():
        print('host-mass: bless needs a reason: bless "why the host grew"', file=sys.stderr)
        return 2
    was, _blessed = _saved()
    now = census()
    _write(now, reason.strip())
    for name in GATED:
        delta = now[name] - was.get(name, 0)
        if delta:
            print(f"host-mass: {name} {was.get(name, 0)} -> {now[name]} ({delta:+d})")
    return 0


def markdown(counts: dict[str, int]) -> str:
    gated = ", ".join(f"**{name}** {counts[name]}" for name in GATED)
    rows = [f"| `{name}` | {value} |" for name, value in counts.items() if name not in GATED]
    return "\n".join(
        [f"- **Host mass** {gated}.", "", "| kind | members |", "| --- | --- |", *rows]
    )


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "markdown"
    if command == "check":
        raise SystemExit(check())
    if command == "bless":
        raise SystemExit(bless(" ".join(sys.argv[2:])))
    if command == "json":
        print(json.dumps(census(), indent=2))  # this is a CLI
    else:
        print(markdown(census()))  # this is a CLI

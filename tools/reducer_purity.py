"""Census the impurity inside registered reducers — the "pure policy" invariant, measured.

    uv run python tools/reducer_purity.py            # markdown
    uv run python tools/reducer_purity.py json
    uv run python tools/reducer_purity.py check      # non-zero if an unargued impurity appears

A reducer decides a turn from `(state, event)`. Reading a clock or an injected port *inside* the
turn breaks that in a way tests rarely catch: the same state and the same event stop producing the
same result, so a replay diverges from the session it replays and no assertion says why.

**Discovered at runtime, not by name.** The reducers are whatever `install_session_reactor` puts in
the route table; a scan keyed on `reduce_*`/`*Reducer` would measure a convention instead, and miss
the first reducer registered under another name. The reactor is built against a fake transport, the
route table walked, and each registered callable resolved back to its source.

Impurity is two things, both of which make a turn depend on something outside `(state, event)`:

- a **clock or entropy** call (`time.monotonic`, `random.…`);
- a call on an **injected callable** — `self._clock()`, `self._allocate()` — where the attribute
  came from a constructor parameter. A reducer that calls what it was handed is asking the world a
  question mid-turn, whatever the thing is named.

The whole class is scanned, not just `__call__`: a turn that delegates to `self._show` is still the
turn, and the impurity moving one frame down is not the impurity going away.

**Two severities, because they are not the same defect.** A reading that reaches a *branch* makes the
same `(state, event)` decide differently on two runs — that is the invariant broken, and a replay
diverges with nothing to say why. A reading that only *stamps* an already-decided effect (its ID, its
deadline) changes bytes the reducer never inspects: the decision is still a function of the turn. So
`decides` is the gate and `stamps` is reported, and the split is what makes the number actionable —
undifferentiated, this reducer reads as six equal problems when two of them are the problem.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Modules whose every call is a clock or entropy read. Matched by *module*, not by a list of
#: dotted names: `time.monotonic_ns` and the next stdlib clock are the same defect, and enumerating
#: functions is how a rule silently stops covering one.
_AMBIENT_MODULES = frozenset({"time", "random", "secrets", "uuid"})

#: Non-module ambient readings, by the attribute they end in.
_AMBIENT_ATTRIBUTES = frozenset({"now", "utcnow", "today"})

#: Impurities kept on purpose, `"Reducer.attribute"` -> reason. A list rather than a rule, for the
#: reason the Driver residue's `_ARGUED` is one: a rule excuses the next reducer of the same shape.
_ARGUED: dict[str, str] = {}


@dataclass(frozen=True)
class Impurity:
    reducer: str
    module: str
    line: int
    call: str
    kind: str
    severity: str

    @property
    def key(self) -> str:
        return f"{self.reducer}.{self.call.removeprefix('self.')}"


def _registered() -> dict[str, object]:
    """Every feature reducer the session reactor actually registers, by `owner:feature`."""
    sys.path.insert(0, str(ROOT / "tests"))
    from util import FakeIPC, runtime_gateway  # a tool, not a library

    from saitenka.app.session_routes import install_session_reactor

    gateway = runtime_gateway(FakeIPC())
    try:
        install_session_reactor(gateway)
        session_reducer = gateway.session_reactor._reducer._reducer
        routes = next(v for v in vars(session_reducer).values() if isinstance(v, dict))
        found: dict[str, object] = {}
        for key, owner_slice in routes.items():
            features = next(
                (v for v in vars(owner_slice).values() if isinstance(v, dict)),
                {},
            )
            for feature, reducer in features.items():
                found[f"{getattr(key, 'owner', '?')}:{feature}"] = reducer
        return found
    finally:
        gateway.close()


def _injected(node: ast.ClassDef) -> set[str]:
    """`self.<attr>` assigned from an `__init__` parameter — the reducer's injected collaborators."""
    init = next(
        (c for c in node.body if isinstance(c, ast.FunctionDef) and c.name == "__init__"), None
    )
    if init is None:
        return set()
    parameters = {
        a.arg for a in (*init.args.posonlyargs, *init.args.args, *init.args.kwonlyargs)
    } - {"self"}
    found: set[str] = set()
    for child in ast.walk(init):
        if isinstance(child, ast.Assign) and isinstance(child.value, ast.Name):
            if child.value.id not in parameters:
                continue
            found.update(
                t.attr
                for t in child.targets
                if isinstance(t, ast.Attribute)
                and isinstance(t.value, ast.Name)
                and t.value.id == "self"
            )
    return found


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Local name -> ambient module it stands for.

    Needed because a dotted-name match is trivially evaded: `import time as _t` renames the module,
    and `from time import monotonic` removes it. A planted control proved the first one walked
    straight past the gate, which is what a rule matching zero things looks like from the outside.
    """
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _AMBIENT_MODULES:
                    found[alias.asname or alias.name] = root
        elif isinstance(node, ast.ImportFrom) and (node.module or "") in _AMBIENT_MODULES:
            for alias in node.names:
                found[alias.asname or alias.name] = node.module or ""
    return found


def _is_ambient(call: ast.Call, aliases: dict[str, str]) -> bool:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id in aliases  # `from time import monotonic` -> `monotonic()`
    if not isinstance(function, ast.Attribute):
        return False
    if function.attr in _AMBIENT_ATTRIBUTES:
        return True
    root = function.value
    while isinstance(root, ast.Attribute):
        root = root.value
    return isinstance(root, ast.Name) and root.id in aliases


def _definition(reducer: object) -> tuple[str, ast.AST, dict[str, str]] | None:
    target = reducer if inspect.isfunction(reducer) else type(reducer)
    try:
        path = Path(inspect.getsourcefile(target) or "")
        _source, start = inspect.getsourcelines(target)
    except (TypeError, OSError):
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = _aliases(tree)
    wanted = target.__name__
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == wanted
            and abs(node.lineno - start) <= 2  # the decorator lines a source range includes
        ):
            return path.relative_to(ROOT).as_posix(), node, aliases
    return None


def collect() -> list[Impurity]:
    found: list[Impurity] = []
    for name, reducer in sorted(_registered().items()):
        located = _definition(reducer)
        if located is None:
            continue
        module, node, aliases = located
        injected = _injected(node) if isinstance(node, ast.ClassDef) else set()
        for function in _functions(node):
            deciding = _deciding_calls(function)
            for child in ast.walk(function):
                if not isinstance(child, ast.Call):
                    continue
                ambient = _is_ambient(child, aliases)
                is_injected = (
                    isinstance(child.func, ast.Attribute)
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "self"
                    and child.func.attr in injected
                )
                if not ambient and not is_injected:
                    continue
                found.append(
                    Impurity(
                        reducer=name,
                        module=module,
                        line=child.lineno,
                        call=_dotted(child.func) if ambient else f"self.{child.func.attr}",
                        kind="ambient" if ambient else "injected",
                        severity="decides" if id(child) in deciding else "stamps",
                    )
                )
    return found


def _functions(node: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return [node]
    return [c for c in ast.walk(node) if isinstance(c, ast.FunctionDef | ast.AsyncFunctionDef)]


def _deciding_calls(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """Call nodes whose value reaches a branch, directly or through a local.

    A reading inside a `Compare`/`BoolOp`/`if` test decides. So does one assigned to a name that a
    branch later reads — `epoch = self._connection_epoch()` followed by `if epoch > …` is the same
    defect written over two lines, and missing it is how a severity split becomes decoration.
    """
    tests: list[ast.AST] = []
    for node in ast.walk(function):
        if isinstance(node, ast.If | ast.IfExp | ast.While):
            tests.append(node.test)
        elif isinstance(node, ast.Compare | ast.BoolOp):
            tests.append(node)
    branch_names = {
        child.id for test in tests for child in ast.walk(test) if isinstance(child, ast.Name)
    }
    deciding = {
        id(child) for test in tests for child in ast.walk(test) if isinstance(child, ast.Call)
    }
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and any(isinstance(t, ast.Name) and t.id in branch_names for t in node.targets)
        ):
            deciding.add(id(node.value))
    return deciding


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def build() -> dict:
    impurities = collect()
    live = [i for i in impurities if i.key not in _ARGUED]
    return {
        "reducers": len(_registered()),
        "decides": sum(1 for i in live if i.severity == "decides"),
        "stamps": sum(1 for i in live if i.severity == "stamps"),
        "argued": len(impurities) - len(live),
        "rows": [
            {
                "reducer": i.reducer,
                "module": i.module,
                "line": i.line,
                "call": i.call,
                "kind": i.kind,
                "severity": i.severity,
            }
            for i in sorted(live, key=lambda i: (i.severity != "decides", i.reducer, i.line))
        ],
    }


def markdown(state: dict) -> str:
    lines = [
        (
            f"- **Reducer purity** {state['reducers']} registered reducers; "
            f"**{state['decides']} readings reach a branch**, {state['stamps']} only stamp an "
            f"emitted effect, {state['argued']} argued."
        ),
        "",
        "| severity | reducer | site | call | kind |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| **{r['severity']}** | `{r['reducer']}` | `{r['module'].split('/')[-1]}:{r['line']}` "
        f"| `{r['call']}` | {r['kind']} |"
        for r in state["rows"]
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    state = build()
    mode = sys.argv[1] if len(sys.argv) > 1 else "markdown"
    if mode == "json":
        print(json.dumps(state, indent=2))  # this is a CLI
    elif mode == "check":
        if state["decides"]:
            print(markdown(state))  # this is a CLI
            print(  # this is a CLI
                f"\nreducer-purity: {state['decides']} reading(s) reach a branch, so the same "
                "(state, event) can decide differently on two runs. Put the reading on the event, "
                "or add the site to `_ARGUED` with its reason."
            )
            raise SystemExit(1)
        print(  # this is a CLI
            f"reducer-purity: OK ({state['reducers']} reducers, 0 deciding readings; "
            f"{state['stamps']} stamp-only, {state['argued']} argued)"
        )
    else:
        print(markdown(state))  # this is a CLI

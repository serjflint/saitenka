"""Census the host-taking functions by the arity each would have once the host parameter is gone.

The `reader-parameter` rows in the runtime manifest are not homogeneous, and counting them as if
they were is what wrecked two migration plans. This splits them by the only property that decides
whether a row can be converted mechanically: how many parameters it would end up with.

The number that matters is TRANSITIVE. A function that forwards the host must receive the union of
everything downstream, so a leaf with three reads can still be unconvertible because its caller's
caller needs sixty-eight. Measured locally, 42 functions breach ruff's `max-args`; measured
transitively, 116 do. Read the live numbers from `over`, never from this paragraph.

  uv run python tools/host_arity.py            # gate the census against the manifest
  uv run python tools/host_arity.py show        # the full classification, as JSON
  uv run python tools/host_arity.py over        # what breaches the ceiling, worst first
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "saitenka" / "app"
CENSUS = ROOT / "tests" / "fixtures" / "host_arity_census.json"

#: ruff's `PLR0913` setting. The gate exists to catch a conversion that passes lint today and
#: breaches it two waves later, when the last caller in the chain finally converts — so it must be
#: the same number, not a friendlier one.
MAX_ARGS = 8

#: Hold the host because the host is what they build or own. `HOST_COMPOSITION` in the runtime
#: manifest; repeated here because this tool answers a different question and must not report a
#: composition root as an unconvertible monster.
EXEMPT = frozenset(
    {
        "controller.py::Reader.__init__",
        "miner.py::Miner.__init__",
        "reader_deps.py::apply_deps",
        "reader_deps.py::load_deps_async",
        "reader_factory.py::create_reader",
        "session_runtime.py::SessionRuntime.__init__",
    }
)


@dataclass(slots=True)
class Function:
    module: str
    symbol: str
    line: int
    params: int
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    methods: set[str] = field(default_factory=set)
    forwards: set[str] = field(default_factory=set)
    dynamic: int = 0
    closures: int = 0

    @property
    def key(self) -> str:
        return f"{self.module}::{self.symbol}"

    @property
    def members(self) -> set[str]:
        return self.reads | self.writes | self.methods


class _Visitor(ast.NodeVisitor):
    """Collect every use of the host parameter, per function."""

    def __init__(self, module: str) -> None:
        self.module = module
        self.found: list[Function] = []
        #: Imported name -> defining module, so a forward to `show_tooltip` resolves to the one
        #: module that defines it. Basename matching alone conflated `subtitle_modes::configure`
        #: with three unrelated `configure`s and inflated the unconvertible tier by an artifact.
        self.imports: dict[str, str] = {}
        self._stack: list[str] = []
        #: The innermost enclosing host-taking function, and the local name its host is bound to.
        self._host: list[tuple[Function, str]] = []
        #: Depth of nested scopes since that function — a use below 0 is a closure capture.
        self._depth = 0

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.module.startswith("saitenka.app"):
            module = node.module.removeprefix("saitenka.app.").replace(".", "/") + ".py"
            for alias in node.names:
                self.imports[alias.asname or alias.name] = module
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._stack.append(node.name)
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        host = _host_argument(arguments)
        if host is None:
            self._depth += 1
            self.generic_visit(node)
            self._depth -= 1
        else:
            function = Function(
                module=self.module,
                symbol=".".join(self._stack),
                line=node.lineno,
                # `self` never becomes a parameter, and neither does the host being removed.
                params=len(arguments) - 1 - (1 if arguments[0].arg in {"self", "cls"} else 0),
            )
            self.found.append(function)
            self._host.append((function, host))
            depth, self._depth = self._depth, 0
            self.generic_visit(node)
            self._depth = depth
            self._host.pop()
        self._stack.pop()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._host and isinstance(node.value, ast.Name):
            function, host = self._host[-1]
            if node.value.id == host:
                if self._depth:
                    function.closures += 1
                # A method call is recorded by `visit_Call`, which reaches the attribute first.
                target = function.writes if isinstance(node.ctx, ast.Store) else function.reads
                target.add(node.attr)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._host:
            function, host = self._host[-1]
            called = node.func
            if (
                isinstance(called, ast.Attribute)
                and isinstance(called.value, ast.Name)
                and called.value.id == host
            ):
                function.methods.add(called.attr)
                function.reads.discard(called.attr)
                self.generic_visit(node)
                return
            if isinstance(called, ast.Name) and called.id == "getattr" and _passes(node, host):
                function.dynamic += 1
            elif _passes(node, host):
                function.forwards.add(self._target(called))
        self.generic_visit(node)

    def _target(self, called: ast.expr) -> str:
        """Where a forward lands, as `module::symbol` when that is knowable and `?::name` when not.

        A bare name is an import or a module-local def, both exact. `self.m` is exact within the
        enclosing class. Anything else is a call through a value, which no AST pass can resolve —
        those fall back to the basename and over-approximate.
        """
        if isinstance(called, ast.Name):
            return f"{self.imports.get(called.id, self.module)}::{called.id}"
        if isinstance(called, ast.Attribute):
            if isinstance(called.value, ast.Name) and called.value.id in {"self", "cls"}:
                enclosing = self._stack[0] if self._stack else ""
                return f"{self.module}::{enclosing}.{called.attr}"
            if isinstance(called.value, ast.Name) and called.value.id in self.imports:
                return f"{self.imports[called.value.id]}::{called.attr}"
            return f"?::{called.attr}"
        return "?::<dynamic>"


def _host_argument(arguments: list[ast.arg]) -> str | None:
    for argument in arguments:
        annotation = argument.annotation
        if argument.arg == "reader" or (
            annotation is not None and "Reader" in ast.unparse(annotation)
        ):
            return argument.arg
    return None


def _passes(node: ast.Call, host: str) -> bool:
    values = [*node.args, *(keyword.value for keyword in node.keywords)]
    return any(isinstance(value, ast.Name) and value.id == host for value in values)


def _candidates(target: str, keys: set[str], by_name: dict[str, set[str]]) -> set[str]:
    module, _, symbol = target.partition("::")
    if module != "?" and target in keys:
        return {target}
    # A module-scoped target that resolved to nothing is a callee that does not take the host at
    # all — not a miss to widen. Only the genuinely unknowable receiver falls back.
    return by_name.get(symbol, set()) if module == "?" else set()


def collect() -> list[Function]:
    found: list[Function] = []
    for path in sorted(APP.glob("**/*.py")):
        visitor = _Visitor(path.relative_to(APP).as_posix())
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        found.extend(visitor.found)
    return found


def resolve(functions: list[Function]) -> dict[str, set[str]]:
    """Transitive host members per function, by fixpoint over the forwarding graph.

    An unresolvable forward (`?::name`, a call through a value) matches every function with that
    basename. That over-approximates, which is the safe direction: it can only move a function OUT
    of the mechanical tier, never into it.
    """
    keys = {function.key for function in functions}
    by_name: dict[str, set[str]] = {}
    for function in functions:
        by_name.setdefault(function.symbol.rsplit(".", 1)[-1], set()).add(function.key)
    closure = {function.key: set(function.members) for function in functions}
    edges = {
        function.key: {
            callee for target in function.forwards for callee in _candidates(target, keys, by_name)
        }
        - {function.key}
        for function in functions
    }
    changed = True
    while changed:
        changed = False
        for key, callees in edges.items():
            grown = closure[key].union(*(closure[callee] for callee in callees)) if callees else ()
            if grown and grown != closure[key]:
                closure[key] = grown
                changed = True
    return closure


@dataclass(frozen=True, slots=True)
class Row:
    key: str
    line: int
    arity: int
    local_arity: int
    writes: tuple[str, ...]
    dynamic: int
    closures: int
    forwards: tuple[str, ...]


def classify(functions: list[Function]) -> dict[str, list[Row]]:
    """Split the corpus by what a conversion would actually cost.

    Tier B is not "harder Tier A". A write-back has no parameter to become, and `getattr` on the
    host cannot be enumerated statically — those are design changes, and batching them with the
    mechanical rows is what made both earlier plans wrong.
    """
    closure = resolve(functions)
    tiers: dict[str, list[Row]] = {"exempt": [], "tierA": [], "tierB": []}
    for function in functions:
        members = closure[function.key]
        row = Row(
            key=function.key,
            line=function.line,
            arity=function.params + len(members),
            local_arity=function.params + len(function.members),
            writes=tuple(sorted(function.writes)),
            dynamic=function.dynamic,
            closures=function.closures,
            forwards=tuple(sorted(function.forwards)),
        )
        if function.key in EXEMPT:
            tier = "exempt"
        elif row.arity > MAX_ARGS or function.writes or function.dynamic:
            tier = "tierB"
        else:
            tier = "tierA"
        tiers[tier].append(row)
    for rows in tiers.values():
        rows.sort(key=lambda row: (-row.arity, row.key))
    return tiers


def census() -> dict[str, int]:
    tiers = classify(collect())
    return {name: len(rows) for name, rows in sorted(tiers.items())}


def check() -> int:
    """Ratchet, in the direction the migration moves: shrinking is progress, growth is a failure.

    Auto-tightening rather than demanding a hand `bless` for every conversion — the rewrite lands in
    the diff either way, and a bless step per wave was pure ceremony on the sibling gate too.
    """
    if not CENSUS.exists():
        CENSUS.write_text(json.dumps(census(), indent=2) + "\n", encoding="utf-8")
        print(f"host-arity: seeded {CENSUS.relative_to(ROOT)}")
        return 0
    expected = json.loads(CENSUS.read_text(encoding="utf-8"))
    actual = census()
    grew = {name: (expected.get(name, 0), count) for name, count in actual.items()}
    grew = {name: pair for name, pair in grew.items() if pair[1] > pair[0]}
    if grew:
        for name, (was, now) in sorted(grew.items()):
            print(f"host-arity: {name} grew {was} -> {now}", file=sys.stderr)
        print(
            "A new host-taking function, or an existing one pushed over the ceiling by a caller.",
            file=sys.stderr,
        )
        return 1
    if actual != expected:
        CENSUS.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
        for name, count in sorted(actual.items()):
            if count < expected.get(name, 0):
                print(f"host-arity: {name} {expected[name]} -> {count}")
    print("host-arity: OK " + ", ".join(f"{name} {count}" for name, count in actual.items()))
    return 0


def show() -> int:
    tiers = {name: [asdict(row) for row in rows] for name, rows in classify(collect()).items()}
    print(json.dumps(tiers, indent=2))
    return 0


def over() -> int:
    """The ceiling breaches, worst first — the queue Tier B works through."""
    tiers = classify(collect())
    rows = [row for row in tiers["tierB"] if row.arity > MAX_ARGS]
    print(
        f"{len(rows)} of {sum(len(group) for group in tiers.values())} breach max-args={MAX_ARGS}"
    )
    for row in rows:
        print(f"  {row.arity:>3} (local {row.local_arity:>3})  {row.key}")
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    commands = {"check": check, "over": over, "show": show}
    if command not in commands:
        print(f"unknown command: {command}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(commands[command]())

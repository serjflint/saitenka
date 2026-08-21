"""Census the host-taking functions by the arity each would have once the host parameter is gone.

The `reader-parameter` rows in the runtime manifest are not homogeneous, and counting them as if
they were is what wrecked two migration plans. This splits them by the only property that decides
whether a row can be converted mechanically: how many parameters it would end up with.

The number that matters is TRANSITIVE. A function that forwards the host must receive the union of
everything downstream, so a leaf with three reads can still be unconvertible because its caller's
caller needs sixty-eight. It is also transitive through a shared *signature*: functions bound into one
`Callable[[Reader], ...]` field cannot diverge from each other. Read every number from `over`.

  uv run python tools/host_arity.py            # gate the census against the manifest
  uv run python tools/host_arity.py explain     # which dispatches resolved rather than widened
  uv run python tools/host_arity.py bless       # accept growth, for a deliberate design decision
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
        "reader_factory.py::create_reader",
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
    #: `(class, attribute, method)` per unresolvable call whose receiver is `self.<attribute>`.
    #: Resolved after every module is scanned, in `collect` — the declaring class may live anywhere.
    dispatch: set[tuple[str, str, str]] = field(default_factory=set)

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
        #: Callable-field name -> the functions bound into it. `SurfaceSpec(scroll=...)` and the
        #: field's own default are one signature, so its members convert together or not at all.
        self.bindings: dict[str, set[str]] = {}
        #: Module-level def/class names; see `visit_Module`.
        self.defined: set[str] = set()
        #: Local name -> the method it was fetched as. `suspend = getattr(r, "suspend_for_overlay")`
        #: dispatches to `suspend_for_overlay`, and the local's own name says nothing about that.
        self.aliases: dict[str, str] = {}
        self._stack: list[str] = []
        #: Class name -> its method names, plus which classes are Protocols. A receiver's declared
        #: type resolves against these: a concrete class exactly, a Protocol structurally.
        self.classes: dict[str, set[str]] = {}
        self.protocols: set[str] = set()
        #: `(class, attribute)` -> declared type, or None when any assignment defeats it. `None`
        #: wins: narrowing is the unsafe direction here, so one unannotated write forfeits the whole
        #: attribute rather than resolving it from the annotated writes alone.
        self.attribute_types: dict[tuple[str, str], str | None] = {}
        #: Parameter annotations of the enclosing function, so `self._x = renderer` can inherit one.
        self._params: dict[str, str] = {}
        #: The innermost enclosing host-taking function, and the local name its host is bound to.
        self._host: list[tuple[Function, str]] = []
        #: Depth of nested scopes since that function — a use below 0 is a closure capture.
        self._depth = 0

    def visit_Module(self, node: ast.Module) -> None:
        # Module-level defs up front: a bare-name call is only module-scoped if one of these (or an
        # import) defines it. Anything else is a LOCAL holding a callable, and treating that as a
        # module symbol silently dropped `deactivate = getattr(...); deactivate(reader)` — the
        # dispatch that hides `NativeVisibleRenderer` behind `SubtitleModeCoordinator`.
        self.defined = {
            statement.name
            for statement in node.body
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        }
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.module.startswith("saitenka.app"):
            package = node.module.removeprefix("saitenka.app").removeprefix(".").replace(".", "/")
            for alias in node.names:
                name = alias.asname or alias.name
                # `from saitenka.app import tooltip` binds a MODULE, and its members live in
                # `tooltip.py` — not in the package the import names.
                submodule = f"{package}/{alias.name}".lstrip("/") + ".py"
                self.imports[name] = submodule if (APP / submodule).exists() else f"{package}.py"
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes[node.name] = {
            child.name
            for child in node.body
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        if any("Protocol" in ast.unparse(base) for base in node.bases):
            self.protocols.add(node.name)
        for child in node.body:
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                self._declare(node.name, child.target.id, _annotation_name(child.annotation))
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def _declare(self, class_name: str, attribute: str, declared: str | None) -> None:
        key = (class_name, attribute)
        if declared is None or self.attribute_types.get(key, declared) != declared:
            self.attribute_types[key] = None  # unknown, or two types: refuse to pick
        else:
            self.attribute_types.setdefault(key, declared)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if self._stack and isinstance(target, ast.Attribute) and _is_self(target.value):
                # Only a bare annotated parameter carries a usable type. A call, a literal or an
                # unannotated name forfeits the attribute (`_declare` stores None).
                source = (
                    self._params.get(node.value.id) if isinstance(node.value, ast.Name) else None
                )
                self._declare(self._stack[0], target.attr, source)
        method = _fetched_method(node.value)
        if method is not None:
            self.aliases.update(
                {target.id: method for target in node.targets if isinstance(target, ast.Name)}
            )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # A callable field's default is the first member of its family: `scroll: ... = _no_scroll`.
        if isinstance(node.target, ast.Name) and node.value is not None:
            self._bind(node.target.id, node.value)
        # `self._renderer: Renderer` — the declared type a dispatch through it resolves against.
        if self._stack and isinstance(node.target, ast.Attribute) and _is_self(node.target.value):
            self._declare(self._stack[0], node.target.attr, _annotation_name(node.annotation))
        self.generic_visit(node)

    def _bind(self, name: str, value: ast.expr) -> None:
        """Record `name=<a function, not called>`, resolved the same way a forward target is."""
        if isinstance(value, ast.Name):
            self.bindings.setdefault(name, set()).add(
                f"{self.imports.get(value.id, self.module)}::{value.id}"
            )
        elif isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            module = self.imports.get(value.value.id)
            if module:
                self.bindings.setdefault(name, set()).add(f"{module}::{value.attr}")

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
        outer_params = self._params
        self._params = {
            argument.arg: name
            for argument in arguments
            if (name := _annotation_name(argument.annotation))
        }
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
        self._params = outer_params
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
        self._record_dispatch(node)
        for keyword in node.keywords:
            # `SurfaceSpec(scroll=sub_picker.scroll)` — a function passed, not called. Every
            # function bound into one field shares that field's signature.
            if keyword.arg and not isinstance(keyword.value, ast.Call):
                self._bind(keyword.arg, keyword.value)
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

    def _record_dispatch(self, node: ast.Call) -> None:
        """Note a call whose receiver is `self.<attribute>`, so `collect` can resolve it later.

        Two forms reach a renderer here and both land on `?::method` today:
        `getattr(self._renderer, "cue_changed", None)` (the alias form, including the `a or b`
        chain `_fetched_method` cannot see) and `self._renderer.clear(...)` (the direct form).
        """
        if not self._host or not self._stack:
            return
        function, _host = self._host[-1]
        called = node.func
        if isinstance(called, ast.Name) and called.id == "getattr" and len(node.args) > 1:
            receiver, name = node.args[0], node.args[1]
            if (
                isinstance(receiver, ast.Attribute)
                and _is_self(receiver.value)
                and isinstance(name, ast.Constant)
                and isinstance(name.value, str)
            ):
                function.dispatch.add((self._stack[0], receiver.attr, name.value))
        elif (
            isinstance(called, ast.Attribute)
            and isinstance(called.value, ast.Attribute)
            and _is_self(called.value.value)
        ):
            function.dispatch.add((self._stack[0], called.value.attr, called.attr))

    def _target(self, called: ast.expr) -> str:
        """Where a forward lands, as `module::symbol` when that is knowable and `?::name` when not.

        A bare name is exact only when an import or a module-level def actually binds it; a bare
        name that binds neither is a LOCAL holding a callable. `self.m` is exact within the
        enclosing class. Everything else is a call through a value, which no AST pass can resolve —
        those fall back to the basename and over-approximate.
        """
        if isinstance(called, ast.Name):
            if called.id in self.aliases:
                return f"?::{self.aliases[called.id]}"
            if called.id in self.imports:
                return f"{self.imports[called.id]}::{called.id}"
            return f"{self.module}::{called.id}" if called.id in self.defined else f"?::{called.id}"
        if isinstance(called, ast.Attribute):
            if isinstance(called.value, ast.Name) and called.value.id in {"self", "cls"}:
                enclosing = self._stack[0] if self._stack else ""
                return f"{self.module}::{enclosing}.{called.attr}"
            if isinstance(called.value, ast.Name) and called.value.id in self.imports:
                return f"{self.imports[called.value.id]}::{called.attr}"
            return f"?::{called.attr}"
        return "?::<dynamic>"


def _is_self(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id in {"self", "cls"}


def _annotation_name(node: ast.expr | None) -> str | None:
    """The bare class name in an annotation, dropping `| None` and any module qualification."""
    if node is None:
        return None
    text = ast.unparse(node).replace("'", "").replace('"', "").split("|")[0].strip()
    return text.rsplit(".", 1)[-1] or None


def _resolve_dispatch(
    functions: list[Function],
    classes: dict[str, set[str]],
    protocols: set[str],
    declaring: dict[str, str],
    attribute_types: dict[tuple[str, str], str | None],
    bindings: dict[str, set[str]],
) -> dict[str, dict[str, set[str]]]:
    """Replace `?::method` with the callees it can actually reach, where that is knowable.

    Two rules, and both must be SOUND rather than merely narrower — `resolve`'s over-approximation
    only ever moves a function out of the mechanical tier, so tightening it is the direction that
    can lie.

    * A receiver typed by its declaration. A concrete class resolves exactly; a Protocol resolves to
      the corpus classes that structurally satisfy it, which is what satisfying a Protocol means.
      An attribute with any unannotated write was already forfeited by `_declare`.
    * A callee naming a declared callable FIELD. `route_click` does
      `any(s.on_click(reader, x, y) for s in SURFACES)` — the receiver is a loop variable and
      unknowable, but every function bound into `SurfaceSpec.on_click` is already collected, and
      that set is exact. Matching by basename instead swept in `tooltip.py::on_click`, which is not
      a hook, and fusing the sidebar and tooltip subtrees is what produced one 50-member closure.
    """
    keys = {function.key for function in functions}
    record: dict[str, dict[str, set[str]]] = {}
    for function in functions:
        replacements: dict[str, set[str]] = {}
        for class_name, attribute, method in function.dispatch:
            declared = attribute_types.get((class_name, attribute))
            if declared is None:
                continue
            if declared in protocols:
                required = classes.get(declared, set())
                targets = {
                    f"{declaring[name]}::{name}.{method}"
                    for name, defined in classes.items()
                    if name != declared and method in defined and required <= defined
                }
            elif method in classes.get(declared, set()):
                targets = {f"{declaring[declared]}::{declared}.{method}"}
            else:
                targets = set()
            if targets & keys:
                replacements.setdefault(f"?::{method}", set()).update(targets & keys)
        for stale in {target for target in function.forwards if target.startswith("?::")}:
            bound = bindings.get(stale.removeprefix("?::"), set()) & keys
            if bound:
                replacements.setdefault(stale, set()).update(bound)
        for stale, fresh in replacements.items():
            if stale in function.forwards:
                function.forwards.discard(stale)
                function.forwards |= fresh
                record.setdefault(function.key, {})[stale] = fresh
    return record


def _host_argument(arguments: list[ast.arg]) -> str | None:
    for argument in arguments:
        annotation = argument.annotation
        if argument.arg == "reader" or (
            annotation is not None and "Reader" in ast.unparse(annotation)
        ):
            return argument.arg
    return None


def _fetched_method(value: ast.expr) -> str | None:
    """The method name in `getattr(obj, "name", …)`, which is what a local bound to it dispatches to."""
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "getattr"
        and len(value.args) >= 2
        and isinstance(name := value.args[1], ast.Constant)
        and isinstance(name.value, str)
    ):
        return name.value
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


def _families(
    functions: list[Function],
    by_name: dict[str, set[str]],
    bindings: dict[str, set[str]],
) -> list[set[str]]:
    """Groups that must convert to the same signature, from the two ways this tree shares one.

    A callable *field* is exact: everything assigned to `SurfaceSpec.scroll`, including the field's
    own default, is that one signature. A `?::name` dispatch is the inexact fallback for a receiver
    no AST pass can resolve, and it groups by basename.
    """
    keys = {function.key for function in functions}
    dispatched = {
        target.removeprefix("?::")
        for function in functions
        for target in function.forwards
        if target.startswith("?::")
    } - {"<dynamic>"}
    groups = [by_name.get(name, set()) for name in dispatched]
    groups.extend(targets & keys for targets in bindings.values())
    return [group for group in groups if len(group) > 1]


def collect() -> tuple[list[Function], dict[str, set[str]]]:
    found: list[Function] = []
    bindings: dict[str, set[str]] = {}
    classes: dict[str, set[str]] = {}
    protocols: set[str] = set()
    declaring: dict[str, str] = {}
    attribute_types: dict[tuple[str, str], str | None] = {}
    for path in sorted(APP.glob("**/*.py")):
        module = path.relative_to(APP).as_posix()
        visitor = _Visitor(module)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        found.extend(visitor.found)
        for name, targets in visitor.bindings.items():
            bindings.setdefault(name, set()).update(targets)
        for name, methods in visitor.classes.items():
            # A class name defined twice is ambiguous, and picking one silently would resolve a
            # dispatch to the wrong body. Poison it so it falls back to over-approximating.
            classes[name] = set() if name in classes else methods
            declaring.setdefault(name, module)
        protocols |= visitor.protocols
        for key, declared in visitor.attribute_types.items():
            attribute_types[key] = (
                None if attribute_types.get(key, declared) != declared else declared
            )
    collect.resolved = _resolve_dispatch(  # type: ignore[attr-defined]  # explain-only breadcrumb
        found, classes, protocols, declaring, attribute_types, bindings
    )
    return found, bindings


def resolve(functions: list[Function], bindings: dict[str, set[str]]) -> dict[str, set[str]]:
    """Transitive host members per function, by fixpoint over the forwarding graph.

    An unresolvable forward (`?::name`, a call through a value) matches every function with that
    basename. That over-approximates, which is the safe direction: it can only move a function OUT
    of the mechanical tier, never into it.

    Such a call is dispatch through a value, and every candidate it might land on therefore shares
    ONE signature — `SurfaceSpec` holds its hooks as `Callable[[Reader], bool]` fields, so
    `help_overlay.scroll` cannot take different parameters from `sub_picker.scroll`. So the family
    members are joined to each other, not merely to the caller. Without that, a two-read hook reads
    as trivially convertible while its sibling in the same field needs forty.
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
    for family in _families(functions, by_name, bindings):
        for member in family:
            edges[member] |= family - {member}
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


def classify(functions: list[Function], bindings: dict[str, set[str]]) -> dict[str, list[Row]]:
    """Split the corpus by what a conversion would actually cost.

    Tier B is not "harder Tier A". A write-back has no parameter to become, and `getattr` on the
    host cannot be enumerated statically — those are design changes, and batching them with the
    mechanical rows is what made both earlier plans wrong.
    """
    closure = resolve(functions, bindings)
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
    tiers = classify(*collect())
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
    # Gate on the ARCHITECTURAL tier and the total, never on the mechanical one. Tier A grows for
    # two opposite reasons — a new host-taking function (bad) and a Tier B function dropping under
    # the ceiling (the migration working) — and a gate that fails on both reports progress as a
    # regression. Tier B growing, or the corpus growing at all, is unambiguous.
    watched = {
        name: (expected.get(name, 0), actual[name])
        for name in ("tierB", "exempt")
        if name in actual
    }
    watched["total"] = (sum(expected.values()), sum(actual.values()))
    grew = {name: pair for name, pair in watched.items() if pair[1] > pair[0]}
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


def bless() -> int:
    """Accept the current census, growth included. For a deliberate design decision.

    `check` auto-tightens but refuses growth, because the common cause is an accidental new
    host-taking function. The other cause is a design call that trades rows for something worth more
    — a total Protocol replacing `getattr` probes adds mechanical Tier A rows and removes a class of
    silent no-op. The ratchet exists to make that choice explicit, not to forbid it, and hand-editing
    the census JSON is not "explicit", it is just undocumented.

    The commit message is where the reason goes; this only records the number.
    """
    was = json.loads(CENSUS.read_text(encoding="utf-8")) if CENSUS.exists() else {}
    now = census()
    CENSUS.write_text(json.dumps(now, indent=2) + "\n", encoding="utf-8")
    for name in sorted(now):
        delta = now[name] - was.get(name, 0)
        if delta:
            print(f"host-arity: {name} {was.get(name, 0)} -> {now[name]} ({delta:+d})")
    print(f"host-arity: blessed (total {sum(now.values())})")
    return 0


def show() -> int:
    tiers = {name: [asdict(row) for row in rows] for name, rows in classify(*collect()).items()}
    print(json.dumps(tiers, indent=2))
    return 0


def explain() -> int:
    """Why the census reads as it does: every dispatch the tool resolved rather than widened.

    The counts move for two very different reasons — the code converted, or this tool got better at
    reading it — and the census alone cannot tell them apart. This is the second one, itemised, so a
    number that shifts without a refactor is auditable instead of mysterious.
    """
    functions, bindings = collect()
    resolved = getattr(collect, "resolved", {})
    tiers = classify(functions, bindings)
    print(", ".join(f"{name} {len(rows)}" for name, rows in sorted(tiers.items())))
    widened = sum(
        1 for function in functions for target in function.forwards if target.startswith("?::")
    )
    print(f"{len(resolved)} call site(s) resolved; {widened} still over-approximating\n")
    for key, mapping in sorted(resolved.items()):
        for stale, fresh in sorted(mapping.items()):
            print(f"{key}\n    {stale}  ->  {', '.join(sorted(fresh))}")
    return 0


def over() -> int:
    """The ceiling breaches, worst first — the queue Tier B works through."""
    tiers = classify(*collect())
    rows = [row for row in tiers["tierB"] if row.arity > MAX_ARGS]
    print(
        f"{len(rows)} of {sum(len(group) for group in tiers.values())} breach max-args={MAX_ARGS}"
    )
    for row in rows:
        print(f"  {row.arity:>3} (local {row.local_arity:>3})  {row.key}")
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    commands = {
        "bless": bless,
        "check": check,
        "explain": explain,
        "over": over,
        "show": show,
    }
    if command not in commands:
        print(f"unknown command: {command}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(commands[command]())

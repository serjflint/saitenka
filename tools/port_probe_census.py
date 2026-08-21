"""Census the `getattr(port, "method", fallback)` probes in `src/`, split by whether the probe is live.

    uv run python tools/port_probe_census.py            # markdown
    uv run python tools/port_probe_census.py json
    uv run python tools/port_probe_census.py check      # non-zero if a dead probe appears

A probe on a port we define is never defensive here (AGENTS.md; `vibe/runtime-migration-plan.md`):
it cannot tell "this stand-in has no runtime" from "somebody renamed the method", and the second
reads as a silent feature-off. A renamed timer method disarms every lifecycle deadline in the
process, and nothing goes red, because the fallback branch is the one every caller then takes.

**The discriminator is the receiver's type, not the probed name.** A first cut matched any name some
class in `src/` happens to define and called 52 rows debt — `entry.reading`, `oid.name`, `f.lemma`
all matched, and every one of them is a real optional attribute. So this resolves the receiver to a
class and asks that class:

- **dead** — the class defines the attribute unconditionally, so the fallback is unreachable and the
  probe's only live effect is to make a rename silent. This is the defect class.
- **live** — the class exists and does not define it, so the probe is a real capability check.
- **unresolved** — the receiver has no annotation to resolve (an unannotated parameter). Reported,
  never counted as debt: guessing here is what produced the 52.

`hasattr` on the class rather than `__annotations__`, because a port's method can arrive from a
base, a `Protocol` default, or a descriptor, and any of those makes the fallback unreachable. And
`hasattr` alone is not enough either: it is False for an attribute `__init__` assigns, which is how
`ipc.connected_at` and `ipc._bytes_read` first read as live capability checks when every instance
has them. So the class is asked twice — once built, once for what its `__init__` always sets.
"""

from __future__ import annotations

import ast
import importlib
import json
import pkgutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

#: Receivers whose attribute is legitimately absent until first set, so a probe IS the read.
#: Matched on the receiver's tail because the spelling varies (`_tls`, `self._local`).
_LAZY_SLOT_RECEIVERS = frozenset({"_tls", "_local", "_sql_tls", "_jamdict_local"})

#: Dead probes kept on purpose, one reason each. A list rather than a rule, for the reason the
#: Driver residue's `_ARGUED` is one: a rule would also excuse the next probe of the same shape.
_ARGUED: dict[tuple[str, str], str] = {}


@dataclass(frozen=True)
class Probe:
    module: str
    line: int
    receiver: str
    name: str
    default: str
    owner: str | None
    verdict: str

    @property
    def site(self) -> tuple[str, str]:
        return (self.module.split("/")[-1], f"{self.receiver}.{self.name}")


def _annotation_name(annotation: ast.expr | None) -> str | None:
    """The bare class name an annotation resolves to, or None when it is not one class.

    `X | None` resolves to `X`: an optional port is still that port when it is there, and the
    probe's question is about the port, not about the None.
    """
    if annotation is None:
        return None
    if isinstance(annotation, ast.Name):
        # `Any` names no class, so resolving it would answer the probe's question with a shrug.
        return None if annotation.id in {"Any", "object"} else annotation.id
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value.split("|")[0].strip().split("[")[0].strip() or None
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        left = _annotation_name(annotation.left)
        return left if left != "None" else _annotation_name(annotation.right)
    return None


class _Types(ast.NodeVisitor):
    """Per-module: `self.<attr>` types inside each class, and each function's annotated locals."""

    def __init__(self) -> None:
        self.attributes: dict[str, dict[str, str]] = {}
        self.scopes: dict[tuple[str, ...], dict[str, str]] = {}
        self._class: list[str] = []
        self._function: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class.append(node.name)
        self.attributes.setdefault(node.name, {})
        for child in node.body:
            # `port: MpvIPC` at class level is as good a declaration as one in `__init__`.
            if (
                isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
                and (name := _annotation_name(child.annotation))
            ):
                self.attributes[node.name][child.target.id] = name
        self.generic_visit(node)
        self._class.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._function.append(node.name)
        params = {
            argument.arg: name
            for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            if (name := _annotation_name(argument.annotation))
        }
        self.scopes[*self._class, *self._function] = params
        if self._class:
            for child in ast.walk(node):
                # `self.ipc = ipc`, where `ipc` is an annotated parameter of this function.
                if not isinstance(child, ast.Assign) or not isinstance(child.value, ast.Name):
                    continue
                if (declared := params.get(child.value.id)) is None:
                    continue
                for target in child.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        self.attributes[self._class[-1]].setdefault(target.attr, declared)
        self.generic_visit(node)
        self._function.pop()


def _always_set() -> dict[str, set[str]]:
    """Per class, the `self.<attr>` its `__init__` sets at top level — so every instance has them.

    Top level only, on purpose: an assignment inside an `if`/`try` is conditional, and treating it
    as always-present would call a real capability check dead. Under-approximating is the safe
    direction here — it can only leave a probe uncounted, never invent one.
    """
    always: dict[str, set[str]] = {}
    for path in sorted(SRC.glob("**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            init = next(
                (
                    child
                    for child in node.body
                    if isinstance(child, ast.FunctionDef) and child.name == "__init__"
                ),
                None,
            )
            if init is None:
                continue
            names = always.setdefault(node.name, set())
            for statement in init.body:
                targets = (
                    [statement.target]
                    if isinstance(statement, ast.AnnAssign)
                    else statement.targets
                    if isinstance(statement, ast.Assign)
                    else []
                )
                names.update(
                    t.attr
                    for t in targets
                    if isinstance(t, ast.Attribute)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "self"
                )
    return always


def _classes() -> dict[str, type]:
    """Every class `saitenka` exports, by bare name. Imported, because a port's method can come from
    a base or a descriptor and only the built class knows."""
    import saitenka

    found: dict[str, type] = {}
    for info in pkgutil.walk_packages(saitenka.__path__, "saitenka."):
        try:
            module = importlib.import_module(info.name)
        except Exception:  # an optional-extra module is not a census failure
            continue
        for name, value in vars(module).items():
            if isinstance(value, type) and not name.startswith("__"):
                found.setdefault(name, value)
    return found


def _receiver_type(
    receiver: ast.expr, types: _Types, scope: tuple[str, ...], klass: str | None
) -> str | None:
    if isinstance(receiver, ast.Name):
        for depth in range(len(scope), 0, -1):
            if declared := types.scopes.get(scope[:depth], {}).get(receiver.id):
                return declared
        return None
    if (
        isinstance(receiver, ast.Attribute)
        and isinstance(receiver.value, ast.Name)
        and receiver.value.id == "self"
        and klass
    ):
        return types.attributes.get(klass, {}).get(receiver.attr)
    return None


def collect() -> list[Probe]:
    classes = _classes()
    always_set = _always_set()
    probes: list[Probe] = []
    for path in sorted(SRC.glob("**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        types = _Types()
        types.visit(tree)
        module = path.relative_to(ROOT).as_posix()
        for scope, klass, node in _calls(tree):
            if not (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(literal := node.args[1], ast.Constant)
                and isinstance(literal.value, str)
            ):
                continue
            receiver = node.args[0]
            tail = receiver.attr if isinstance(receiver, ast.Attribute) else ""
            if (isinstance(receiver, ast.Name) and receiver.id in _LAZY_SLOT_RECEIVERS) or (
                tail in _LAZY_SLOT_RECEIVERS
            ):
                continue
            owner = _receiver_type(receiver, types, scope, klass)
            resolved = classes.get(owner) if owner else None
            if resolved is None:
                verdict = "unresolved"
            elif hasattr(resolved, literal.value) or literal.value in always_set.get(owner, ()):
                verdict = "dead"
            else:
                verdict = "live"
            probes.append(
                Probe(
                    module=module,
                    line=node.lineno,
                    receiver=ast.unparse(receiver),
                    name=literal.value,
                    default=ast.unparse(node.args[2]) if len(node.args) > 2 else "<raises>",
                    owner=owner,
                    verdict=verdict,
                )
            )
    return probes


def _calls(tree: ast.AST):
    """Every call, with the scope path and enclosing class name it sits in."""

    def outer(node, scope, klass):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                yield from outer(child, (*scope, child.name), child.name)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                yield from outer(child, (*scope, child.name), klass)
            else:
                if isinstance(child, ast.Call):
                    yield scope, klass, child
                yield from outer(child, scope, klass)

    yield from outer(tree, (), None)


def build() -> dict:
    probes = collect()
    dead = [p for p in probes if p.verdict == "dead" and p.site not in _ARGUED]
    return {
        "total": len(probes),
        "dead": len(dead),
        "live": sum(1 for p in probes if p.verdict == "live"),
        "unresolved": sum(1 for p in probes if p.verdict == "unresolved"),
        "argued": sum(1 for p in probes if p.verdict == "dead" and p.site in _ARGUED),
        "rows": [
            {
                "module": p.module,
                "line": p.line,
                "receiver": p.receiver,
                "name": p.name,
                "default": p.default,
                "owner": p.owner,
            }
            for p in sorted(dead, key=lambda p: (p.module, p.line))
        ],
    }


def markdown(state: dict) -> str:
    lines = [
        (
            f"- **Port probes** {state['total']} total: **{state['dead']} dead** (the port always "
            f"has the attribute, so only a rename can take the fallback), {state['live']} live "
            f"capability checks, {state['unresolved']} unresolved receivers, "
            f"{state['argued']} argued."
        ),
        "",
        "| site | probe | default | port |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| `{r['module'].split('/')[-1]}:{r['line']}` | `{r['receiver']}.{r['name']}` "
        f"| `{r['default']}` | `{r['owner']}` |"
        for r in state["rows"]
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    state = build()
    mode = sys.argv[1] if len(sys.argv) > 1 else "markdown"
    if mode == "json":
        print(json.dumps(state, indent=2))  # this is a CLI
    elif mode == "check":
        if state["dead"]:
            print(markdown(state))  # this is a CLI
            print(  # this is a CLI
                f"\nport-probe: {state['dead']} dead probe(s). The port always has the attribute, "
                "so the fallback is unreachable and the probe only makes a rename silent. Call the "
                "port directly, or add the site to `_ARGUED` with its reason."
            )
            raise SystemExit(1)
        print(  # this is a CLI
            f"port-probe: OK (0 dead; {state['live']} live, "
            f"{state['unresolved']} unresolved, {state['argued']} argued)"
        )
    else:
        print(markdown(state))  # this is a CLI

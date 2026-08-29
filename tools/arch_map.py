"""Generate the architecture views that cannot be drawn once and left alone.

`ARCHITECTURE.md` owns the static picture — the module map, the render pipeline, the load-bearing
decisions — and it is prose because those change slowly and carry *why*. What it cannot own is the
part that is rebuilt on every import: which owner holds which feature, which events reach it, and
where a new feature plugs in. A typed copy of that is wrong within a week, which is the same reason
the migration plan carries no census.

So the rule this file follows is the repo's own: prose for the argument, a generator for the map.
Four views, one concern each, in the order a reader needs them:

    static      what may import what, and whether the cycles are real
    ownership   who holds state, and which events reach them
    command     what a keypress reaches
    seams       where a new feature registers, and what its adapter costs

The last one is the reason this exists. Both layers register; stateless capability values declare
the bounded owners and acts each policy may reach. Their width is review evidence generated here,
not a hand-maintained architectural claim.

    uv run poe arch-map              # markdown, all four views
    uv run poe arch-map -- --json    # the same data, for a diff or a check
"""

from __future__ import annotations

import argparse
import ast
import collections
import contextlib
import json
import pathlib
import subprocess
import sys
from typing import Any, cast

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "saitenka"

#: Import edges the graph reports that are not real coupling: an annotation-only import costs
#: nothing at runtime under `from __future__ import annotations`, and a cycle made only of them is
#: a naming artifact, not a design defect. Classifying instead of counting is the whole point —
#: `ruff analyze` cannot tell the two apart, and a raw cycle count here would read as an alarm.
_ANNOTATION_ONLY = "type-only"
_DEFERRED = "deferred"
_RUNTIME = "runtime"


# --- view 1: static -----------------------------------------------------------------------------


def _import_graph() -> dict[str, list[str]]:
    out = subprocess.run(
        ["uv", "run", "ruff", "analyze", "graph", str(SRC)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )
    return json.loads(out.stdout)


def _package(path: str) -> str:
    tail = path.rsplit("src/saitenka/", maxsplit=1)[-1].split("/")
    return tail[0] if len(tail) > 1 else "(root)"


def _cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan, iterative — the app package's largest component is 10 modules deep and recursion
    here would be a stack limit nobody expects from a documentation tool."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    onstack: set[str] = set()
    found: list[list[str]] = []
    counter = 0

    for root in graph:
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, child = work[-1]
            if child == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                onstack.add(node)
            successors = graph.get(node, [])
            if child < len(successors):
                work[-1] = (node, child + 1)
                nxt = successors[child]
                if nxt not in index:
                    work.append((nxt, 0))
                elif nxt in onstack:
                    low[node] = min(low[node], index[nxt])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    top = stack.pop()
                    onstack.discard(top)
                    component.append(top)
                    if top == node:
                        break
                if len(component) > 1:
                    found.append(sorted(component))
    return found


def _edge_kinds(module: pathlib.Path, peers: set[str]) -> dict[str, str]:
    """How `module` imports each peer: at runtime, only for types, or inside a function body."""
    tree = ast.parse(module.read_text(encoding="utf-8"))

    def named(node: ast.AST) -> set[str]:
        hit = set()
        for child in ast.walk(node):
            if isinstance(child, ast.ImportFrom) and child.module:
                # Both spellings reach the same module: `from saitenka.app.beta import X` names the
                # peer in the module tail, `from saitenka.app import beta` names it in the alias.
                # Reading only the tail silently drops every edge written the second way.
                hit |= {child.module.split(".")[-1]} & peers
                hit |= {alias.name for alias in child.names} & peers
            elif isinstance(child, ast.Import):
                hit |= {alias.name.split(".")[-1] for alias in child.names} & peers
        return hit

    type_only: set[str] = set()
    top: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.If) and ast.unparse(node.test).strip() == "TYPE_CHECKING":
            type_only |= named(node)
        elif not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            # `named` walks, so a `def` left in here drags its local imports up to module scope and
            # every deferred edge reads as runtime coupling. A `try:`/`if:` block is still module
            # scope and must stay.
            top |= named(node)
    deferred: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            deferred |= named(node)

    # Precedence, strongest last: a name imported at module scope is runtime coupling whether or
    # not some function also imports it locally. Writing this as `top - deferred` drops such a name
    # from every bucket, and an unclassified edge silently disappears from the map.
    kinds = dict.fromkeys(type_only, _ANNOTATION_ONLY)
    kinds.update(dict.fromkeys(deferred, _DEFERRED))
    kinds.update(dict.fromkeys(top, _RUNTIME))
    return kinds


def static_view() -> dict:
    graph = _import_graph()
    edges = collections.Counter()
    for source, targets in graph.items():
        for target in targets:
            if _package(source) != _package(target):
                edges[f"{_package(source)} -> {_package(target)}"] += 1

    classified = []
    for component in _cycles(graph):
        names = {pathlib.Path(p).stem for p in component}
        # A cycle is real only when *runtime* edges close it. Asking "does any runtime edge exist
        # in here" marks a ten-module annotation cycle as coupling because one member happens to
        # import a peer for real — the edge is there, the loop is not.
        runtime_only = {
            pathlib.Path(path).stem: sorted(
                peer
                for peer, kind in _edge_kinds(
                    pathlib.Path(path), names - {pathlib.Path(path).stem}
                ).items()
                if kind == _RUNTIME
            )
            for path in component
        }
        closed = _cycles(runtime_only)
        classified.append(
            {
                "members": [p.split("saitenka/")[-1] for p in component],
                "runtime_edges": {k: v for k, v in runtime_only.items() if v},
                "closed_by_runtime": [sorted(c) for c in closed],
                "kind": _RUNTIME if closed else _ANNOTATION_ONLY,
            }
        )
    return {
        "modules": len(graph),
        "edges": sum(len(v) for v in graph.values()),
        "package_edges": dict(edges.most_common()),
        "cycles": classified,
    }


# --- views 2 and 4: the live reactor ------------------------------------------------------------


@contextlib.contextmanager
def _session():
    """A wired session, the way `reducer_purity` gets one — the route table is only assembled by
    `install_session_reactor`, so there is no static form of it to read."""
    sys.path.insert(0, str(ROOT / "tests"))
    from util import FakeIPC, session_gateway  # a tool, not a library

    gateway = session_gateway(FakeIPC())
    try:
        yield gateway
    finally:
        gateway.close()


def ownership_view() -> dict:
    import dataclasses

    import saitenka.app.session.routes as session_routes
    from saitenka.runtime.state import SessionState

    with _session() as gateway:
        reactor = gateway.session_reactor
        routes = next(v for v in vars(reactor._reducer._reducer).values() if isinstance(v, dict))
        events = collections.defaultdict(list)
        for key in routes:
            events[key.owner.value].append(key.event_type.__name__)
        owners = [
            {
                "owner": field.name,
                "features": [name for name, _ in getattr(reactor.state, field.name).features],
                "events": sorted(events.get(field.name, [])),
            }
            for field in dataclasses.fields(SessionState)
        ]
    return {
        "owners": owners,
        "broadcast": [t.__name__ for t in session_routes.LIFETIME_EVENTS],
        "claimed_from_reader": sorted(t.__name__ for t in session_routes._CLAIMED),
    }


def _app_module(path: pathlib.Path) -> str:
    return f"app/{path.relative_to(SRC / 'app').as_posix()}"


def _policy_name(path: pathlib.Path) -> str:
    return path.parent.name if path.stem == "intents" else path.stem.removesuffix("_intents")


def _adapter_ports() -> list[dict]:
    """Purpose-specific capability values at stateless policy boundaries."""
    protocol_names: set[str] = set()
    for path in sorted((SRC / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        protocol_names.update(
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and any(ast.unparse(base).endswith("Protocol") for base in node.bases)
        )

    ports = []
    paths = {
        *sorted((SRC / "app").rglob("*adapter.py")),
        *sorted((SRC / "app").rglob("*endpoint.py")),
    }
    for path in sorted(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not node.name.endswith(
                ("Ports", "Read", "Apply", "Endpoint")
            ):
                continue
            fields = [n for n in node.body if isinstance(n, ast.AnnAssign)]
            field_rows = [
                f"{ast.unparse(field.target)}: {ast.unparse(field.annotation)}" for field in fields
            ]
            acts = sum(
                "Callable" in ast.unparse(field.annotation)
                or bool(
                    {
                        child.id
                        for child in ast.walk(field.annotation)
                        if isinstance(child, ast.Name)
                    }
                    & protocol_names
                )
                for field in fields
            )
            ports.append(
                {
                    "port": node.name,
                    "module": _app_module(path),
                    "members": len(fields),
                    "acts": acts,
                    "fields": field_rows,
                }
            )
    return sorted(ports, key=lambda p: -p["members"])


def _registered_policies() -> list[str]:
    """Which policies the command-type registry actually owns."""
    from saitenka.app.session.routes import stateless_features

    class _Adapter:
        def inputs(self) -> object:
            return object()

        def apply(self, effect: object, /) -> None:
            pass

    adapter = cast("Any", _Adapter())
    return sorted(
        binding.feature
        for binding in stateless_features(
            adapter,
            adapter,
            adapter,
            adapter,
            adapter,
            adapter,
            adapter,
        )
    )


def _host_residue() -> dict[str, list[str]]:
    """What is left on the host in the adapter's shape — duties with no policy behind them.

    Named by prefix, so it over-reports on purpose: `_apply_playback_delta` matches and is not a
    stateless feature's interpreter at all. A prefix names a shape, never a family — which is the
    error this whole migration kept making, so the report says so rather than implying a worklist.
    """
    from saitenka.app.session.controller import SessionController

    roles = collections.defaultdict(list)
    for name, value in vars(SessionController).items():
        if not callable(value) and not isinstance(value, property):
            continue
        if name.startswith("_run_") and name.endswith("_command"):
            roles["runs a command"].append(name)
        elif name.startswith("_apply_"):
            roles["applies something"].append(name)
        elif name.endswith("_inputs"):
            roles["gathers inputs"].append(name)
    return {role: sorted(names) for role, names in roles.items()}


def seams_view() -> dict:
    """Where a feature plugs in. Two layers, and only one of them has a seam."""
    policies = []
    for path in sorted((SRC / "app").rglob("*intents.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        entry = next(
            (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "reduce"), None
        )
        if entry is None:
            continue
        params = [
            f"{a.arg}: {ast.unparse(a.annotation)}" if a.annotation else a.arg
            for a in entry.args.args
        ]
        policies.append(
            {
                "feature": _policy_name(path),
                "module": _app_module(path),
                "signature": f"reduce({', '.join(params)})",
                # A reducer that threads state belongs in an owner slice; one that does not is a
                # policy over a snapshot, and the mailbox would only add a hop.
                "stateful": any("State" in p for p in params),
            }
        )

    with _session() as gateway:
        routes = next(
            v
            for v in vars(gateway.session_reactor._reducer._reducer).values()
            if isinstance(v, dict)
        )
        registered = sorted(
            {
                f"{key.owner.value}:{feature}"
                for key, owner_slice in routes.items()
                for feature in next(
                    (v for v in vars(owner_slice).values() if isinstance(v, dict)), {}
                )
            }
        )
        adapter = _host_residue()

    return {
        "stateful": {"registered": registered, "seam": "SliceReducer({name: reducer}) + RouteKey"},
        "stateless": {
            "policies": policies,
            "seam": "StatelessRouter keyed by command type",
            "registered": _registered_policies(),
            "ports": _adapter_ports(),
            "host_residue": adapter,
        },
    }


# --- view 3: commands ---------------------------------------------------------------------------


def command_view() -> dict:
    """What the closed stateless command table contributes to the script-message router."""
    from saitenka.app.session.routes import STATELESS_COMMANDS

    rows = [
        {
            "message": row.message,
            "target": f"{type(row.command).__name__}.{getattr(row.command, 'name', row.command)}",
            "kind": "policy",
        }
        for row in STATELESS_COMMANDS
    ]
    rows.append({"message": "**assembly", "target": "SessionAssembly", "kind": "registration"})
    return {"rows": rows}


# --- rendering ----------------------------------------------------------------------------------


def build() -> dict:
    return {
        "static": static_view(),
        "ownership": ownership_view(),
        "commands": command_view(),
        "seams": seams_view(),
    }


def markdown(state: dict) -> str:
    out: list[str] = [
        "# Architecture map — generated",
        "",
        "Four views, one concern each. `ARCHITECTURE.md` owns the static prose and the *why*; this",
        "file owns what is rebuilt on every import and would be stale in a week if typed.",
        "Regenerate with `uv run poe arch-map`.",
        "",
    ]

    static = state["static"]
    out += [
        "## 1. Static — what may import what",
        "",
        f"{static['modules']} modules, {static['edges']} import edges.",
        "",
        "| edge | count |",
        "| --- | ---: |",
    ]
    out += [f"| `{edge}` | {n} |" for edge, n in static["package_edges"].items() if n > 1]
    real = [c for c in static["cycles"] if c["kind"] == _RUNTIME]
    out += [
        "",
        (
            f"**Cycles: {len(static['cycles'])} reported, {len(real)} real.** The rest are"
            " annotation cycles — free under `from __future__ import annotations`, and counting"
            " them as coupling is the error this view exists to prevent."
        ),
        "",
    ]
    for cycle in static["cycles"]:
        out.append(f"- *{cycle['kind']}* — {', '.join(f'`{m}`' for m in cycle['members'])}")

    own = state["ownership"]
    out += [
        "",
        "## 2. Ownership — who holds state, and what reaches them",
        "",
        "| owner | events | features |",
        "| --- | ---: | --- |",
    ]
    out += [
        f"| `{o['owner']}` | {len(o['events'])} | {', '.join(f'`{f}`' for f in o['features'])} |"
        for o in own["owners"]
    ]
    out += [
        "",
        (
            f"Broadcast to every slice: {', '.join(f'`{e}`' for e in own['broadcast'])}."
            f" Withheld from the `SessionController`: {len(own['claimed_from_reader'])} payloads."
        ),
        "",
    ]

    cmd = state["commands"]
    out += [
        "## 3. Command — what a keypress reaches",
        "",
        (
            f"{len(cmd['rows'])} rows; {cmd['verbs']} resolve to a `SessionController` verb by name, the"
            " rest carry an intent to a policy."
        ),
        "",
        "| message | target |",
        "| --- | --- |",
    ]
    out += [f"| `{r['message']}` | `{r['target']}` |" for r in cmd["rows"]]

    seams = state["seams"]
    out += [
        "",
        "## 4. Seams — where a new feature plugs in",
        "",
        (
            f"**Stateful** — {len(seams['stateful']['registered'])} registered reducers. Seam:"
            f" `{seams['stateful']['seam']}`. A feature joins by naming itself."
        ),
        "",
        (
            "**Stateless** — a policy over a snapshot, no state threaded, so nothing to sequence"
            " and the mailbox would only add a hop. Seam:"
            f" `{seams['stateless']['seam']}`, the counterpart to the stateful table."
        ),
        "",
        "| module | signature | registered |",
        "| --- | --- | --- |",
    ]
    registered = set(seams["stateless"]["registered"])
    out += [
        f"| `{p['module']}` | `{p['signature']}` |"
        f" {'yes' if p['feature'] in registered else 'NO'} |"
        for p in seams["stateless"]["policies"]
    ]
    out += [
        "",
        (
            "Each adapter declares a purpose-specific capability value — never a `SessionController`"
            " parameter. Width is review evidence: owner references are state authority; callable"
            " or named-call Protocol fields are acts. It is not a numeric gate."
        ),
        "",
        "| capability | module | members | acts | declarations |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    out += [
        f"| `{p['port']}` | `{p['module']}` | {p['members']} | {p['acts']} "
        f"| {', '.join(f'`{field}`' for field in p['fields'])} |"
        for p in seams["stateless"]["ports"]
    ]
    out += [
        "",
        (
            "Left on the host in the same shape, by name prefix — a shape, not a family, so this"
            " over-reports and is a place to look rather than a worklist:"
        ),
        "",
    ]
    for role, names in sorted(seams["stateless"]["host_residue"].items()):
        out.append(f"- **{role}** ({len(names)}): {', '.join(f'`{n}`' for n in names)}")
    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the data instead of the prose")
    args = parser.parse_args()
    state = build()
    print(json.dumps(state, indent=2) if args.json else markdown(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

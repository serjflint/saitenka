"""What a module actually touches on the host — the map the design step needs, per cluster.

The other generators answer *how much*: `host_arity` ranks arity by the call graph. It treats a host
member as an opaque name, and that is the number the shape decision is made from — wrongly, because
members are not independent.

`subtitle_modes` reads sixteen. Seven of them (`jp_sid`, `en_sid`, `subtitle_language`,
`subtitle_slang`, `subtitle_tracks`, `_translation_secondary_sid`, `_last_announced_sid`) are
properties over `self._subtitle_tracks.current` — ONE fact wearing seven names. Sixteen says "too
big for a value"; ten says "a port, and here are its fields". Only the second is true, and reading
`session/controller.py` to find that out is the expensive step this replaces.

    uv run python tools/cluster_map.py <module.py> [...]   # the map, per module
    uv run python tools/cluster_map.py --json <module.py>  # the same, machine-readable
    uv run python tools/cluster_map.py --member tip_width  # one member: what it is + every site

`--member` is the step before a codemod. It answers in one call what was otherwise several `git
grep`s and a hand-written AST dump: what the name resolves to, what its body reads off the host
(none means it is a constant wearing a member's cost), which host-taking functions read it, and
every attribute site in every tree the gate checks — an attribute match, so a same-named parameter
or a mention in a comment is not one.

Classification is by construction, never by name: a `Delegated` descriptor, a property whose body
reduces to one slice read, a config group copied in `__init__`, a plain field, a method. Anything
this cannot resolve says `?` rather than guessing — an unresolved member is a prompt to look, not
a fact to design against.
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
from dataclasses import dataclass, field
from pathlib import Path

import host_arity

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "saitenka" / "app"
CONTROLLER = APP / "session" / "controller.py"


@dataclass(frozen=True, slots=True)
class Member:
    """One name on the SessionController, and what it resolves to underneath."""

    name: str
    kind: str
    #: The underlying fact — a store slice, a lifetime context field, a config field. Members that
    #: share a `fact` are one thing, however many names they wear.
    fact: str

    @property
    def label(self) -> str:
        return self.fact if self.fact != self.name else self.kind


def _self_chain(node: ast.expr) -> list[str] | None:
    """`self._subtitle_tracks.current.jp_sid` -> ['_subtitle_tracks', 'current', 'jp_sid']."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name) and node.id == "self":
        return list(reversed(parts))
    return None


def _returned(body: list[ast.stmt]) -> ast.expr | None:
    """The single returned expression of a one-liner property, or None if it does anything else."""
    statements = [s for s in body if not isinstance(s, ast.Expr | ast.Pass)]
    if len(statements) != 1 or not isinstance(statements[0], ast.Return):
        return None
    return statements[0].value


def _decorated(node: ast.FunctionDef, name: str) -> bool:
    return any(ast.unparse(d).endswith(name) for d in node.decorator_list)


def classify_host() -> dict[str, Member]:
    """Every name `controller.<x>` can resolve to, read once from `session/controller.py`."""
    tree = ast.parse(CONTROLLER.read_text(encoding="utf-8"), filename=str(CONTROLLER))
    controller = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SessionController"),
        None,
    )
    if controller is None:
        raise SystemExit("session/controller.py has no class SessionController")
    members: dict[str, Member] = {}

    for node in controller.body:
        # `_sub_index = Delegated[CueIndex | None]("episode", "sub_index")` — the flat-name
        # compatibility layer. Its `fact` is the context field, so two names onto one field show up
        # as one fact even when neither looks like the other.
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            target = node.targets[0]
            if (
                isinstance(target, ast.Name)
                and ast.unparse(call.func).startswith("Delegated")
                and len(call.args) >= 2
            ):
                where = ".".join(str(a.value) for a in call.args[:2] if isinstance(a, ast.Constant))
                members[target.id] = Member(target.id, "delegated", where)

        elif isinstance(node, ast.FunctionDef):
            if _decorated(node, "setter") or _decorated(node, "deleter"):
                continue
            if not _decorated(node, "property"):
                members[node.name] = Member(node.name, "method", node.name)
                continue
            returned = _returned(node.body)
            chain = _self_chain(returned) if returned is not None else None
            if chain and len(chain) >= 2 and chain[1] in {"current", "state"}:
                # `self._subtitle_tracks.current.jp_sid` — a read of one owner slice. The store is
                # the fact; the trailing field is which part of it.
                members[node.name] = Member(node.name, "slice", ".".join(chain[:1] + chain[2:]))
            elif chain and len(chain) == 1:
                members[node.name] = Member(node.name, "alias", chain[0])
            elif chain:
                members[node.name] = Member(node.name, "derived", ".".join(chain))
            else:
                members[node.name] = Member(node.name, "derived", node.name)

    for node in ast.walk(controller):
        # `self.keys = o.keys` in `__init__` — a config group, and `self.osd = ...` a plain field.
        # Annotated (`self._geometry_cue_hint: Cue | None = None`) counts the same; missing that
        # form left real fields showing as unresolved.
        # Only fill what no property/descriptor already claimed: those are the stronger answer.
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                chain = _self_chain(target)
                if not chain or len(chain) != 1 or chain[0] in members:
                    continue
                source = ast.unparse(node.value) if node.value is not None else ""
                kind, fact = "field", chain[0]
                if source.startswith(("o.", "options.")):
                    kind, fact = "config", source
                members[chain[0]] = Member(chain[0], kind, fact)

    return members


@dataclass(slots=True)
class ModuleMap:
    module: str
    functions: list[host_arity.Function] = field(default_factory=list)

    @property
    def members(self) -> set[str]:
        return set().union(*(f.members for f in self.functions)) if self.functions else set()

    @property
    def writes(self) -> set[str]:
        return set().union(*(f.writes for f in self.functions)) if self.functions else set()


def build(modules: list[str]) -> dict[str, object]:
    host = classify_host()
    functions, _bindings = host_arity.collect()
    by_module: dict[str, ModuleMap] = {}
    for function in functions:
        by_module.setdefault(function.module, ModuleMap(function.module)).functions.append(function)

    out: dict[str, object] = {}
    for module in modules:
        mapping = by_module.get(module)
        if mapping is None:
            out[module] = {"error": "no host-taking functions"}
            continue
        resolved = {
            name: host.get(name, Member(name, "?", name)) for name in sorted(mapping.members)
        }
        facts: dict[str, list[str]] = collections.defaultdict(list)
        for name, member in resolved.items():
            facts[f"{member.kind}:{member.fact}"].append(name)
        # Fields of one container are separate facts but a single OWNER, and that is what decides
        # whether a value can carry them: five `episode.*` fields travel as one episode, not five
        # parameters.
        containers: dict[str, set[str]] = collections.defaultdict(set)
        for name, member in resolved.items():
            head = member.fact.split(".")[0]
            if member.kind in {"delegated", "slice", "config"} and head != name:
                containers[head].add(name)
        out[module] = {
            "members": len(resolved),
            "facts": len(facts),
            "containers": {k: sorted(v) for k, v in sorted(containers.items())},
            "by_fact": {k: sorted(v) for k, v in sorted(facts.items())},
            "writes": sorted(mapping.writes),
            "functions": {
                function.symbol: {
                    "reads": sorted(function.reads),
                    "methods": sorted(function.methods),
                    "writes": sorted(function.writes),
                    # BOTH directions. An intra-module forward gates a conversion exactly as hard
                    # as a cross-module one — the host still has to reach the callee — so filtering
                    # them out reports pass-throughs as ready.
                    "forwards": sorted(function.forwards),
                    "forwards_out": sorted(
                        t for t in function.forwards if not t.startswith(module)
                    ),
                }
                for function in sorted(mapping.functions, key=lambda f: f.symbol)
            },
        }
    return out


#: Where a rename has to land. Every tree `poe types` checks, not just the package — a flat name
#: left in `examples/` fails the gate rather than the tests, which is how one got missed.
_SWEPT = ("src", "tests", "tools", "examples", "install", ".agents")


def _definition(name: str) -> tuple[str, list[str]]:
    """The member's source on the SessionController, and which other host members its body reads.

    The second is the question a collapse turns on: a property reading nothing is a constant on the
    host, one reading three is a value waiting to be named, and a method whose body is one call
    into a module is a round trip that costs its callers a member for nothing.
    """
    tree = ast.parse(CONTROLLER.read_text(encoding="utf-8"), filename=str(CONTROLLER))
    lines = CONTROLLER.read_text(encoding="utf-8").splitlines()
    for reader in tree.body:
        if not isinstance(reader, ast.ClassDef) or reader.name != "SessionController":
            continue
        for node in reader.body:
            if not isinstance(node, ast.FunctionDef) or node.name != name:
                continue
            reads = sorted(
                {
                    chain[0]
                    for inner in ast.walk(node)
                    if isinstance(inner, ast.Attribute)
                    and (chain := _self_chain(inner))
                    and chain[0] != name
                }
            )
            return "\n".join(lines[node.lineno - 1 : node.end_lineno]), reads
    return "", []


def sites(name: str) -> dict[str, list[str]]:
    """Every `<expr>.<name>` in the swept trees, as `path:line`. The codemod's worklist.

    An attribute match, not a text match: `tip_width` as a *parameter* of `render_preview` is not a
    site, and a comment mentioning it is not either. Both showed up in the greps this replaces.
    """
    found: dict[str, list[str]] = collections.defaultdict(list)
    for base in _SWEPT:
        for path in sorted((ROOT / base).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if name not in source:
                continue
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == name:
                    found[str(path.relative_to(ROOT))].append(str(node.lineno))
    return dict(found)


def member_report(names: list[str]) -> dict[str, object]:
    host = classify_host()
    functions, _bindings = host_arity.collect()
    out: dict[str, object] = {}
    for name in names:
        member = host.get(name, Member(name, "?", name))
        source, reads = _definition(name)
        out[name] = {
            "kind": member.kind,
            "fact": member.fact,
            "reads": reads,
            "readers": sorted(f.symbol for f in functions if name in f.members),
            "sites": sites(name),
            "source": source,
        }
    return out


def render_members(report: dict[str, object]) -> None:
    for name, data in report.items():
        assert isinstance(data, dict)
        total = sum(len(v) for v in data["sites"].values())  # type: ignore[union-attr]
        print(f"## {name} — {data['kind']} → {data['fact']}")
        print(f"  reads on the host: {', '.join(data['reads']) or 'none (a constant here)'}")  # type: ignore[arg-type]
        print(f"  {total} site(s) in {len(data['sites'])} file(s)")  # type: ignore[arg-type]
        for path, at in data["sites"].items():  # type: ignore[union-attr]
            print(f"    {path}:{','.join(at)}")
        if data["readers"]:
            print(f"  host-taking readers: {', '.join(data['readers'])}")  # type: ignore[arg-type]
        if data["source"]:
            print("\n" + str(data["source"]))
        print()


def render(mapping: dict[str, object]) -> None:
    for module, data in mapping.items():
        if not isinstance(data, dict) or "error" in data:
            print(f"## {module}: {data}")
            continue
        members, facts = data["members"], data["facts"]
        collapsed = " (collapses to " + str(facts) + ")" if facts != members else ""
        print(f"## {module} — {members} members{collapsed}")
        for owner, names in data["containers"].items():  # type: ignore[union-attr]
            if len(names) > 1:
                print(f"  {len(names)} of them are one owner: {owner} — {', '.join(names)}")
        print()
        for fact, names in data["by_fact"].items():  # type: ignore[union-attr]
            kind, _, detail = fact.partition(":")
            shown = ", ".join(names)
            print(f"  {kind:<10} {shown}" + (f"   → {detail}" if detail != shown else ""))
        if data["writes"]:
            print(f"\n  WRITES: {', '.join(data['writes'])}")  # type: ignore[arg-type]
        print("\n  per function (reads · methods · forwards out):")
        for symbol, use in data["functions"].items():  # type: ignore[union-attr]
            touched = sorted({*use["reads"], *use["methods"]})
            out = f"  {symbol:<32} {', '.join(touched) or '-'}"
            if use["forwards"]:
                out += f"\n  {'':<32} → {', '.join(use['forwards'])}"
            print(out)
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modules", nargs="*", help="module filenames, e.g. subtitle_modes.py")
    parser.add_argument(
        "--member",
        action="append",
        default=[],
        metavar="NAME",
        help="one host member: what it is, what its body reads, and every call site",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.member:
        report = member_report(args.member)
        print(json.dumps(report, indent=2)) if args.json else render_members(report)
        return 0

    modules = list(args.modules)
    if not modules:
        parser.error("name a module, or pass --member")

    mapping = build(sorted(dict.fromkeys(modules)))
    if args.json:
        print(json.dumps(mapping, indent=2))
    else:
        render(mapping)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

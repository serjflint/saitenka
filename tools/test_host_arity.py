from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

_TOOL = Path(__file__).with_name("host_arity.py")


def _module():
    spec = importlib.util.spec_from_file_location("_host_arity", _TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _collect(tool, module: str, source: str):
    visitor = tool._Visitor(module)
    visitor.visit(ast.parse(source))
    return {function.key: function for function in visitor.found}


def test_a_forwarder_inherits_the_arity_of_everything_downstream() -> None:
    """The whole point of the tool: a two-read leaf caller is not a two-parameter function.

    Counted locally this chain is 2/3/4 and every link converts. Counted the way a signature
    actually works, the top of the chain carries all nine and breaches the ceiling.
    """
    tool = _module()
    functions = _collect(
        tool,
        "chain.py",
        """
def top(reader):
    middle(reader)
    return reader.a, reader.b

def middle(reader):
    bottom(reader)
    return reader.c, reader.d, reader.e

def bottom(reader):
    return reader.f, reader.g, reader.h, reader.i
""",
    )
    closure = tool.resolve(list(functions.values()), {})
    assert len(functions["chain.py::top"].members) == 2
    assert len(closure["chain.py::top"]) == 9
    assert len(closure["chain.py::bottom"]) == 4


def test_a_forwarding_cycle_terminates_on_the_union() -> None:
    """`NativeVisibleRenderer` has a mutually-forwarding core; a naive walk never returns."""
    tool = _module()
    functions = _collect(
        tool,
        "cycle.py",
        """
class R:
    def one(self, reader):
        self.two(reader)
        return reader.a

    def two(self, reader):
        self.one(reader)
        return reader.b
""",
    )
    closure = tool.resolve(list(functions.values()), {})
    assert closure["cycle.py::R.one"] == closure["cycle.py::R.two"] == {"a", "b"}


def test_a_write_back_is_architectural_however_small_it_is() -> None:
    """One assignment to the host, and no parameter list can express the conversion."""
    tool = _module()
    functions = _collect(
        tool,
        "writer.py",
        """
def stash(reader):
    reader._store = 1

def peek(reader):
    return reader._store
""",
    )
    tiers = tool.classify(list(functions.values()), {})
    assert [row.key for row in tiers["tierB"]] == ["writer.py::stash"]
    assert [row.key for row in tiers["tierA"]] == ["writer.py::peek"]


def test_an_import_scopes_a_forward_to_the_one_module_that_defines_it() -> None:
    """Basename matching conflated same-named callees and inflated Tier B by an artifact."""
    tool = _module()
    caller = _collect(
        tool,
        "caller.py",
        """
from saitenka.app.real import configure

def entry(reader):
    configure(reader)
""",
    )
    real = _collect(tool, "real.py", "def configure(reader):\n    return reader.wanted\n")
    decoy = _collect(tool, "decoy.py", "def configure(reader):\n    return reader.unrelated\n")
    closure = tool.resolve([*caller.values(), *real.values(), *decoy.values()], {})
    assert closure["caller.py::entry"] == {"wanted"}


def test_a_call_through_a_value_over_approximates_rather_than_missing() -> None:
    """Protocol dispatch is unresolvable, so it must widen — never quietly report a small arity."""
    tool = _module()
    caller = _collect(tool, "caller.py", "def entry(reader):\n    reader.mode.activate(reader)\n")
    one = _collect(tool, "one.py", "def activate(reader):\n    return reader.first\n")
    two = _collect(tool, "two.py", "def activate(reader):\n    return reader.second\n")
    closure = tool.resolve([*caller.values(), *one.values(), *two.values()], {})
    assert closure["caller.py::entry"] == {"mode", "first", "second"}


def test_functions_bound_into_one_callable_field_share_its_arity() -> None:
    """`SurfaceSpec` holds its hooks as `Callable[[SessionController], bool]` fields.

    So `help_overlay.scroll` cannot take different parameters from `sub_picker.scroll`, however
    little it reads itself. Without this the small hook reads as trivially convertible while its
    sibling in the same field needs forty — and a codemod would convert one and break the other.
    """
    tool = _module()
    functions, bindings = {}, {}
    for module, source in (
        ("registry.py", "SPECS = (Spec(scroll=small.scroll), Spec(scroll=large.scroll))"),
        ("small.py", "def scroll(reader):\n    return reader.a\n"),
        ("large.py", "def scroll(reader):\n    return reader.b, reader.c, reader.d\n"),
    ):
        visitor = tool._Visitor(module)
        visitor.imports.update({"small": "small.py", "large": "large.py"})
        visitor.visit(ast.parse(source))
        functions.update({function.key: function for function in visitor.found})
        for name, targets in visitor.bindings.items():
            bindings.setdefault(name, set()).update(targets)
    assert bindings["scroll"] == {"small.py::scroll", "large.py::scroll"}
    closure = tool.resolve(list(functions.values()), bindings)
    assert closure["small.py::scroll"] == closure["large.py::scroll"] == {"a", "b", "c", "d"}


def test_a_callable_held_in_a_local_is_dispatch_not_a_module_symbol() -> None:
    """`SubtitleModeCoordinator` hides its renderer behind exactly this shape.

    Reading `deactivate(reader)` as a module-level call finds no such symbol and drops the forward
    silently — an under-approximation, which is the one direction this tool must never take. The
    coordinator then reported as trivially convertible while the renderer behind it needs forty.
    """
    tool = _module()
    caller = _collect(
        tool,
        "coordinator.py",
        """
def deactivate_all(reader):
    deactivate = getattr(reader.renderer, "deactivate", None)
    if deactivate is not None:
        deactivate(reader)
""",
    )
    assert caller["coordinator.py::deactivate_all"].forwards == {"?::deactivate"}
    renderer = _collect(tool, "big.py", "def deactivate(reader):\n    return reader.a, reader.b\n")
    closure = tool.resolve([*caller.values(), *renderer.values()], {})
    assert closure["coordinator.py::deactivate_all"] == {"renderer", "a", "b"}


def test_a_dispatch_alias_is_named_by_the_method_not_by_the_local() -> None:
    """`suspend = getattr(r, "suspend_for_overlay")` dispatches to `suspend_for_overlay`.

    Falling back to the local's own name looks like it widens while matching nothing at all, which
    is the same silent drop wearing a disguise — and it is the shape the real coordinator uses.
    """
    tool = _module()
    caller = _collect(
        tool,
        "coordinator.py",
        """
def suspend_all(reader):
    suspend = getattr(reader.renderer, "suspend_for_overlay", None)
    if suspend is not None:
        suspend(reader)
""",
    )
    assert caller["coordinator.py::suspend_all"].forwards == {"?::suspend_for_overlay"}
    renderer = _collect(
        tool, "big.py", "def suspend_for_overlay(reader):\n    return reader.a, reader.b\n"
    )
    closure = tool.resolve([*caller.values(), *renderer.values()], {})
    assert closure["coordinator.py::suspend_all"] == {"renderer", "a", "b"}


def _resolved(tool, sources: dict[str, str], bindings: dict[str, set[str]] | None = None):
    """Scan several modules, run the cross-module dispatch pass, and return the closure."""
    functions: list = []
    classes: dict[str, set[str]] = {}
    protocols: set[str] = set()
    declaring: dict[str, str] = {}
    attribute_types: dict[tuple[str, str], str | None] = {}
    for module, source in sources.items():
        visitor = tool._Visitor(module)
        visitor.visit(ast.parse(source))
        functions.extend(visitor.found)
        classes.update(visitor.classes)
        protocols |= visitor.protocols
        declaring.update(dict.fromkeys(visitor.classes, module))
        attribute_types.update(visitor.attribute_types)
    tool._resolve_dispatch(
        functions, classes, protocols, declaring, attribute_types, bindings or {}
    )
    return functions, tool.resolve(functions, bindings or {})


def test_a_protocol_typed_receiver_resolves_to_what_actually_implements_it() -> None:
    """`self._renderer.cue_changed(reader)` reaches only renderers that define `cue_changed`.

    Widening to every same-named function charged the legacy renderer with the native one's whole
    subtree — `SubtitleRenderer.deactivate` read as arity 38 while it touches one host member.
    """
    functions, closure = _resolved(
        tool := _module(),
        {
            "pipeline.py": """
class Renderer(Protocol):
    def draw(self, reader): ...

class Coordinator:
    def __init__(self, renderer: Renderer):
        self._renderer = renderer

    def changed(self, reader):
        self._renderer.cue_changed(reader)
""",
            "renderers.py": """
class Native:
    def draw(self, reader):
        return reader.a

    def cue_changed(self, reader):
        return reader.native_only

class Legacy:
    def draw(self, reader):
        return reader.b
""",
        },
    )
    assert tool  # the module under test, loaded once above
    forwards = next(f.forwards for f in functions if f.key == "pipeline.py::Coordinator.changed")
    assert forwards == {"renderers.py::Native.cue_changed"}
    assert "native_only" in closure["pipeline.py::Coordinator.changed"]


def test_an_unannotated_receiver_keeps_over_approximating() -> None:
    """Narrowing is the unsafe direction, so one unannotated write forfeits the whole attribute."""
    functions, _closure = _resolved(
        _module(),
        {
            "pipeline.py": """
class Coordinator:
    def __init__(self, renderer):
        self._renderer = renderer

    def changed(self, reader):
        self._renderer.cue_changed(reader)
""",
            "renderers.py": "class Native:\n    def cue_changed(self, reader):\n        return reader.a\n",
        },
    )
    forwards = next(f.forwards for f in functions if f.key == "pipeline.py::Coordinator.changed")
    assert forwards == {"?::cue_changed"}


def test_a_callable_field_resolves_to_its_bound_set_not_every_same_name() -> None:
    """`any(s.on_click(reader, x, y) for s in SURFACES)` reaches the bound hooks, and only those.

    A plain function that merely shares the name is not a hook, and sweeping it in fused two
    unrelated subtrees into one closure.
    """
    functions, closure = _resolved(
        _module(),
        {
            "surfaces.py": "def route_click(reader, x, y):\n    return any(s.on_click(reader, x, y) for s in SURFACES)\n",
            "hook.py": "def on_click(reader, x, y):\n    return reader.bound\n",
            "unrelated.py": "def on_click(reader, x, y):\n    return reader.not_a_hook\n",
        },
        bindings={"on_click": {"hook.py::on_click"}},
    )
    forwards = next(f.forwards for f in functions if f.key == "surfaces.py::route_click")
    assert forwards == {"hook.py::on_click"}
    assert closure["surfaces.py::route_click"] == {"bound"}


def test_the_census_matches_production() -> None:
    tool = _module()
    assert tool.check() == 0
    tiers = tool.classify(*tool.collect())
    # The exempt tier is the runtime manifest's `host-composition` set, and the two tools disagreeing
    # about it would make WP5's exit unanswerable from either.
    assert {row.key for row in tiers["exempt"]} == set(tool.EXEMPT)


def test_the_mechanical_tier_may_grow_when_the_architectural_one_shrinks() -> None:
    """Tier A growing is ambiguous, so the gate must not fail on it.

    It grows for two opposite reasons: a new host-taking function appeared (a regression), or a
    Tier B function dropped under the ceiling because its callees converted (the migration working).
    A gate that fails on both reports progress as a regression — which is exactly what it did, five
    functions into the tooltip decomposition.
    """
    tool = _module()
    census = json.loads(tool.CENSUS.read_text(encoding="utf-8"))
    moved = dict(census)
    moved["tierA"] += 5
    moved["tierB"] -= 5  # same total: five rows crossed the ceiling

    tool.CENSUS.write_text(json.dumps(census, indent=2) + "\n", encoding="utf-8")
    try:
        assert tool.check() == 0  # the real tree against the real fixture stays green
    finally:
        tool.CENSUS.write_text(json.dumps(census, indent=2) + "\n", encoding="utf-8")

    # And the direction that must still fail: the corpus itself growing.
    assert sum(moved.values()) == sum(census.values())

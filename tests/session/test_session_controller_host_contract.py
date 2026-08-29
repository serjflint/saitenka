import ast
from pathlib import Path

from session_controller_host_contract import session_controller_parameters

ROOT = Path(__file__).resolve().parents[2]


def test_new_feature_functions_cannot_accept_session_controller() -> None:
    assert session_controller_parameters(ROOT) == set()


def _turn_graph_collaborators(
    path: Path = ROOT / "src/saitenka/app/session/controller.py",
) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    controller = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SessionController"
    )
    collaborators: set[str] = set()
    for method in controller.body:
        if not isinstance(method, ast.FunctionDef) or method.name == "_build_entry_runtime":
            continue
        for node in ast.walk(method):
            if not isinstance(node, ast.Attribute):
                continue
            graph_alias = isinstance(node.value, ast.Name) and node.value.id == "graph"
            controller_graph = (
                isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "self"
                and node.value.attr == "_graph"
            )
            if graph_alias or controller_graph:
                collaborators.add(node.attr)
    return collaborators


def test_the_live_turn_does_not_reach_new_feature_authority() -> None:
    """New feature policy belongs behind its owner or an explicit session conjunction."""
    assert _turn_graph_collaborators() <= {
        "annotation",
        "commands",
        "connection",
        "cue",
        "episode_watch",
        "interaction",
        "ipc",
        "lifecycle",
        "overlay",
        "playback",
        "presentation",
    }


def test_the_live_turn_guard_detects_direct_feature_authority(tmp_path: Path) -> None:
    controller = tmp_path / "controller.py"
    controller.write_text(
        "class SessionController:\n"
        "    def perform(self):\n"
        "        graph = self._graph\n"
        "        graph.mining.mine()\n",
        encoding="utf-8",
    )

    assert _turn_graph_collaborators(controller) == {"mining"}


def test_session_controller_aliases_and_reader_names_are_detected(tmp_path: Path) -> None:
    app = tmp_path / "src/saitenka/app/nested"
    app.mkdir(parents=True)
    (app / "new_feature.py").write_text(
        "from somewhere import SessionController as Host\n"
        "import somewhere as session_controller\n"
        "from typing import TYPE_CHECKING, TypeAlias\n"
        "ControllerMaybe: TypeAlias = Host | None\n"
        "type ControllerHost = ControllerMaybe\n"
        "ControllerUnion = Host | None\n"
        "if TYPE_CHECKING:\n"
        "    QualifiedHost = session_controller.SessionController\n"
        '    ForwardHost: TypeAlias = "SessionController"\n'
        "def mutate(host: Host | None) -> None:\n    pass\n"
        "def mutate_again(*reader) -> None:\n    pass\n"
        "def mutate_alias(host: ControllerHost) -> None:\n    pass\n"
        "def mutate_union(host: ControllerUnion) -> None:\n    pass\n"
        "def mutate_qualified(host: QualifiedHost) -> None:\n    pass\n"
        "def mutate_forward(host: ForwardHost) -> None:\n    pass\n",
        encoding="utf-8",
    )
    assert session_controller_parameters(tmp_path) == {
        "saitenka.app.nested.new_feature:mutate",
        "saitenka.app.nested.new_feature:mutate_again",
        "saitenka.app.nested.new_feature:mutate_alias",
        "saitenka.app.nested.new_feature:mutate_union",
        "saitenka.app.nested.new_feature:mutate_qualified",
        "saitenka.app.nested.new_feature:mutate_forward",
    }


def _public_methods(path: Path, name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name)
    return {
        m.name for m in cls.body if isinstance(m, ast.FunctionDef) and not m.name.startswith("_")
    }


#: Production surface the shared fake deliberately does not model. Connection lifecycle only —
#: nothing here is probed with a `getattr(..., None)` fallback, so a fake lacking it cannot silently
#: divert production down a different branch.
_UNMODELLED = {"close", "connect", "disconnected", "reconnect_once", "reconnects_left"}


def test_the_shared_fake_offers_every_runtime_port_production_does() -> None:
    """The divergence this catches is one-sided and invisible: production probes each runtime port
    with `getattr(ipc, name, None)` and falls back when it is absent. Production always has them, so
    that fallback is dead code there — but a fake missing one silently drives the whole suite down a
    path production never takes. `register_runtime_observers` was exactly that: its absence sent
    `register_observer_set` down a branch that issues the same commands but never registers them
    with the gateway, so reconnect replay went unexercised.
    """
    production = _public_methods(ROOT / "src/saitenka/mpvio/ipc.py", "MpvIPC")
    fake = _public_methods(ROOT / "tests/util.py", "FakeIPC")

    assert (production - fake) <= _UNMODELLED


#: Doubles that legitimately do NOT inherit the shared fake, each with the reason it cannot.
_STANDALONE_IPC_DOUBLES = {
    # The shared fake is gateway-wired; these test the gateway itself, so inheriting it would mean
    # testing a gateway through a gateway.
    ("test_mpv_gateway.py", "FakeIPC"),
    ("test_loading.py", "FakeIPC"),
}


def _ipc_doubles() -> list[tuple[str, str, bool]]:
    found = []
    transport_methods = {
        "command_async",
        "install_runtime_ingress",
        "probe",
        "query",
        "receive_session",
    }
    for path in sorted((ROOT / "tests").rglob("*.py")):
        if path.name == "util.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken test file fails elsewhere
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = {m.name for m in node.body if isinstance(m, ast.FunctionDef)}
            if "command" not in methods:
                continue
            if "ipc" not in node.name.lower() and methods.isdisjoint(transport_methods):
                continue
            inherits = any("FakeIPC" in ast.unparse(base) for base in node.bases)
            found.append((path.name, node.name, inherits))
    return found


def test_ipc_doubles_inherit_the_shared_fake() -> None:
    """A hand-rolled double implements whichever ports its author happened to need, and production
    falls back on the rest — so each one runs the suite down a different set of branches production
    never takes. Seven of these were found taking `submit_runtime_mpv`'s absent-port path while
    production always has it.

    Inheriting is the fix, not documenting: the shared fake is checked against production's surface
    by the test above, so inheritance is what transitively keeps a double honest.
    """
    handrolled = {
        (module, name) for module, name, inherits in _ipc_doubles() if not inherits
    } - _STANDALONE_IPC_DOUBLES

    assert handrolled == set()

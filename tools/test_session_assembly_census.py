from __future__ import annotations

import session_assembly_census as census


def test_census_classifies_each_assembly_family(tmp_path):
    source = tmp_path / "src" / "saitenka" / "app"
    source.mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("abc123\n", encoding="utf-8")
    (source / "sample.py").write_text(
        """
from saitenka.app.session_controller import SessionController
BINDINGS = ()
SURFACES = ()
_RESOURCE_OF = {}
_PERFORMER_OF = {}
SessionController(ipc)
CommandSpec('x', owner)
HelpAdapter(owner)
SliceReducer({})
SurfaceSpec('help')
ipc.register_session_resource('help', resource)
Performing(run)
ipc.observe_property('time-pos', sink)
class SessionController:
    def __init__(self):
        self.help = object()
""",
        encoding="utf-8",
    )

    report = census.build(tmp_path)

    assert report["source"] == "abc123"
    assert report["counts"] == {
        "direct_construction": 1,
        "input_and_commands": 2,
        "stateless_policy": 1,
        "stateful_policy": 1,
        "surfaces": 2,
        "lifecycle": 2,
        "operation_performers": 2,
        "physical_observations": 1,
        "owner_absorption": 1,
    }


def test_census_does_not_classify_unrelated_calls(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "plain.py").write_text(
        "class SessionController: pass\nSessionController(config)\nWidget(config)\n",
        encoding="utf-8",
    )

    report = census.build(tmp_path)

    assert all(count == 0 for count in report["counts"].values())

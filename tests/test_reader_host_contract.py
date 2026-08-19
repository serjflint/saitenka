import json
from pathlib import Path

from reader_host_contract import enforce_reader_host_contract, unexpected_reader_parameters

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "tests/fixtures/reader_host_allowlist.json"


def test_new_feature_functions_cannot_accept_the_reader_host_object() -> None:
    """The gate. Growth fails; a decrease tightens the baseline in place, because a conversion is
    this contract working and making it cost a hand-bless was pure ceremony."""
    assert enforce_reader_host_contract(ROOT, ALLOWLIST) == set()


def test_reader_host_allowlist_rejects_a_new_module(tmp_path: Path) -> None:
    app = tmp_path / "src/saitenka/app/nested"
    app.mkdir(parents=True)
    (app / "new_feature.py").write_text(
        "from somewhere import Reader as Host\n"
        "import somewhere as controller\n"
        "from typing import TYPE_CHECKING, TypeAlias\n"
        "ReaderMaybe: TypeAlias = Host | None\n"
        "type ReaderHost = ReaderMaybe\n"
        "ReaderUnion = Host | None\n"
        "if TYPE_CHECKING:\n"
        "    QualifiedHost = controller.Reader\n"
        '    ForwardHost: TypeAlias = "Reader"\n'
        "def mutate(host: Host | None) -> None:\n    pass\n"
        "def mutate_again(*reader) -> None:\n    pass\n"
        "def mutate_alias(host: ReaderHost) -> None:\n    pass\n"
        "def mutate_union(host: ReaderUnion) -> None:\n    pass\n"
        "def mutate_qualified(host: QualifiedHost) -> None:\n    pass\n"
        "def mutate_forward(host: ForwardHost) -> None:\n    pass\n",
        encoding="utf-8",
    )
    empty_allowlist = tmp_path / "allowlist.json"
    empty_allowlist.write_text("{}", encoding="utf-8")

    assert unexpected_reader_parameters(tmp_path, empty_allowlist) == {
        "saitenka.app.nested.new_feature: current=6 baseline=0"
    }


def test_reader_host_allowlist_has_no_slack_after_a_removal(tmp_path: Path) -> None:
    """A baseline left above the real count is headroom a regression could slip back into
    unnoticed. The gate no longer FAILS on a decrease — it removes the slack instead, which is what
    that property actually required."""
    app = tmp_path / "src/saitenka/app"
    app.mkdir(parents=True)
    (app / "feature.py").write_text(
        'def mutate(host: "controller.Reader | None") -> None:\n    pass\n', encoding="utf-8"
    )
    stale_allowlist = tmp_path / "allowlist.json"
    stale_allowlist.write_text('{"saitenka.app.feature": 2}', encoding="utf-8")

    assert unexpected_reader_parameters(tmp_path, stale_allowlist) == {
        "saitenka.app.feature: current=1 baseline=2"
    }

    assert enforce_reader_host_contract(tmp_path, stale_allowlist) == set()
    assert json.loads(stale_allowlist.read_text()) == {"saitenka.app.feature": 1}
    assert unexpected_reader_parameters(tmp_path, stale_allowlist) == set()  # no slack left


def test_a_module_that_grew_fails_and_the_baseline_is_left_alone(tmp_path: Path) -> None:
    """The property the whole file exists for. The baseline must NOT be rewritten on a failure, or
    a second run would accept the growth it just rejected."""
    app = tmp_path / "src/saitenka/app"
    app.mkdir(parents=True)
    (app / "feature.py").write_text(
        'def a(host: "controller.Reader") -> None:\n    pass\n'
        'def b(host: "controller.Reader") -> None:\n    pass\n',
        encoding="utf-8",
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text('{"saitenka.app.feature": 1}', encoding="utf-8")

    assert enforce_reader_host_contract(tmp_path, allowlist) == {
        "saitenka.app.feature: current=2 baseline=1"
    }
    assert json.loads(allowlist.read_text()) == {"saitenka.app.feature": 1}  # untouched
    assert enforce_reader_host_contract(tmp_path, allowlist)  # …so it still fails on a re-run


def test_a_module_converted_to_zero_leaves_the_allowlist(tmp_path: Path) -> None:
    """A fully converted module should stop being listed at all, so the file shrinks with the
    migration rather than accumulating zero entries."""
    app = tmp_path / "src/saitenka/app"
    app.mkdir(parents=True)
    (app / "feature.py").write_text("def pure(x: int) -> int:\n    return x\n", encoding="utf-8")
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text('{"saitenka.app.feature": 3}', encoding="utf-8")

    assert enforce_reader_host_contract(tmp_path, allowlist) == set()
    assert json.loads(allowlist.read_text()) == {}


def test_a_function_moved_between_modules_fails_on_the_destination(tmp_path: Path) -> None:
    """A move is a decrease in one module and an increase in another. The decrease half must not
    hide the increase half — that is how coupling relocates instead of leaving."""
    app = tmp_path / "src/saitenka/app"
    app.mkdir(parents=True)
    (app / "source.py").write_text("def pure(x: int) -> int:\n    return x\n", encoding="utf-8")
    (app / "destination.py").write_text(
        'def moved(host: "controller.Reader") -> None:\n    pass\n', encoding="utf-8"
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text('{"saitenka.app.source": 1}', encoding="utf-8")

    assert enforce_reader_host_contract(tmp_path, allowlist) == {
        "saitenka.app.destination: current=1 baseline=0"
    }

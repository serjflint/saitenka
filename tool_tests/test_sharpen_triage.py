from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import sharpen_ledger as sl
import sharpen_triage as st


class _LatestLedger:
    def __init__(self, record: dict):
        self.record = record

    def latest(self, _module: str) -> dict:
        return self.record


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "src/saitenka/app/session").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/saitenka/app/config.py").write_text("class Config: pass\n", encoding="utf-8")
    (tmp_path / "src/saitenka/app/session/controller.py").write_text(
        "class SessionController:\n    _state = 1\n", encoding="utf-8"
    )
    (tmp_path / "tests/test_shared.py").write_text(
        "from saitenka.app.config import Config\n"
        "from saitenka.app.session.controller import SessionController\n\n"
        "def make_controller():\n    return SessionController()\n\n"
        "def test_controller_state():\n    assert make_controller()._state == 1\n",
        encoding="utf-8",
    )
    return tmp_path


def test_actionable_hit_is_attributed_to_its_test_function_not_the_whole_file(
    monkeypatch, tmp_path
):
    root = _repo(tmp_path)
    hit = {
        "file": "tests/test_shared.py",
        "ruleId": "test-compound-private-assert",
        "range": {"start": {"line": 7}},
        "metaVariables": {"multi": {"secondary": [{"text": "_state"}]}},
    }
    monkeypatch.setattr(st, "run_json", lambda *_args, **_kwargs: [hit])

    result = st.conformance_by_module(root, sl.map_tests_to_modules(root))

    assert result["app/session/controller.py"] == (1, 1, 0)
    assert result["app/config.py"] == (0, 0, 0)


def test_sleep_polling_is_counted_but_not_actionable(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    hit = {
        "file": "tests/test_shared.py",
        "ruleId": "test-sleep-polling",
        "range": {"start": {"line": 7}},
        "metaVariables": {"multi": {"secondary": []}},
    }
    monkeypatch.setattr(st, "run_json", lambda *_args, **_kwargs: [hit])

    result = st.conformance_by_module(root, sl.map_tests_to_modules(root))

    assert result["app/session/controller.py"] == (1, 0, 0)


def test_private_seam_metric_excludes_advisory_and_actionable_hits(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    hits = [
        {
            "file": "tests/test_shared.py",
            "ruleId": rule,
            "range": {"start": {"line": 7}},
            "metaVariables": {"multi": {"secondary": [{"text": "_state"}]}},
        }
        for rule in (
            "test-assert-private-attr",
            "test-sleep-polling",
            "test-compound-private-assert",
        )
    ]
    monkeypatch.setattr(st, "run_json", lambda *_args, **_kwargs: hits)

    result = st.conformance_by_module(root, sl.map_tests_to_modules(root))

    assert result["app/session/controller.py"] == (3, 1, 1)


def _campaign(root: Path, module: str, outcomes: list[str | None]) -> None:
    cache = root / ".mutation-cache"
    cache.mkdir()
    db = cache / f"{sl.SRC}/{module}".replace("/", "_")
    db = db.with_suffix(".sqlite")
    con = sqlite3.connect(db)
    con.execute("create table mutation_specs (job_id integer primary key)")
    con.execute("create table work_results (job_id integer, test_outcome text)")
    for job_id, outcome in enumerate(outcomes, 1):
        con.execute("insert into mutation_specs values (?)", (job_id,))
        if outcome is not None:
            con.execute("insert into work_results values (?, ?)", (job_id, outcome))
    con.commit()
    con.close()


def test_campaign_readiness_distinguishes_missing_partial_and_complete(tmp_path):
    module = "app/x.py"
    assert st.campaign_readiness(tmp_path, module) == (False, 0)

    _campaign(tmp_path, module, ["KILLED", None])
    assert st.campaign_readiness(tmp_path, module) == (False, 0)


def test_complete_campaign_reports_survivors(tmp_path):
    module = "app/x.py"
    _campaign(tmp_path, module, ["KILLED", "SURVIVED"])
    assert st.campaign_readiness(tmp_path, module) == (True, 1)


def test_candidate_readiness_requires_grounded_work():
    assert st.candidate_ready(1, False, 0)
    assert st.candidate_ready(0, True, 1)
    assert not st.candidate_ready(0, False, 0)
    assert not st.candidate_ready(0, True, 0)


def test_survival_reads_new_efficacy_detail_and_preserves_unknown_after():
    ledger = _LatestLedger(
        {
            "axes": {
                "efficacy": {
                    "status": "pass",
                    "detail": {"before": 0.4, "after": None},
                }
            }
        }
    )
    assert st.survival_from_ledger(ledger, "app/x.py") == 0.4


def test_github_queries_run_from_the_repository_root(monkeypatch, tmp_path):
    calls = []

    def fake_run_json(command, cwd, _expected):
        calls.append((command, cwd))
        return []

    monkeypatch.setattr(st, "run_json", fake_run_json)

    assert st.open_pr_paths(tmp_path) == set()
    assert calls == [(["gh", "pr", "list", "--state", "open", "--json", "files"], tmp_path)]


def test_main_anchors_the_default_ledger_at_the_repository_root(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(sys, "argv", ["sharpen_triage.py", "--no-network"])
    monkeypatch.setattr(st, "repository_root", lambda _path: tmp_path)

    def fake_rank(root, ledger_path, *, check_network):
        captured.update(root=root, ledger_path=ledger_path, check_network=check_network)
        return []

    monkeypatch.setattr(st, "rank", fake_rank)

    st.main()

    assert captured == {
        "root": tmp_path,
        "ledger_path": (tmp_path / ".ledger.sharpen.jsonl").resolve(),
        "check_network": False,
    }


def test_unsharpenable_candidate_is_excluded(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    ledger_path = root / ".ledger.sharpen.jsonl"
    source_hash = sl.source_sha(root, "app/config.py", ["tests/test_shared.py"])
    ledger_path.write_text(
        '{"type":"manifest","toolset_version":1}\n'
        f'{{"module":"app/config.py","source_sha":"{source_hash}",'
        '"toolset_version":1,"state":"unsharpenable"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        st,
        "conformance_by_module",
        lambda *_args: {"app/config.py": (1, 1, 0)},
    )
    monkeypatch.setattr(st, "open_pr_paths", lambda _root: set())
    monkeypatch.setattr(st, "churn_and_age", lambda *_args: (0, None))
    monkeypatch.setattr(st, "campaign_readiness", lambda *_args: (False, 0))

    candidates = st.rank(root, ledger_path, check_network=False)

    candidate = next(item for item in candidates if item.module == "app/config.py")
    assert candidate.excluded == "unsharpenable & unchanged"

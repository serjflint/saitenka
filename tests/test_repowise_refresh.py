"""Planted +/- controls for the repowise-refresh hook (.agents/hooks/repowise-refresh.py).

The hook decides — from the indexed commit, HEAD, an opt-in flag, server reachability, and a
per-HEAD sentinel — whether to stay silent, remind, or launch a background refresh. Pin every branch so
the "safe by default, auto only when asked and the server is up" contract can't silently rot.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HOOK = Path(__file__).resolve().parents[1] / ".agents" / "hooks" / "repowise-refresh.py"

_INDEXED = "8eb7b8212e3e4e0e92239038812958ccad4af1b4"
_HEAD = "88747f6830a2d444cb80f1a89264731de2e4ad3f"


def _mod():
    spec = importlib.util.spec_from_file_location("_repowise_refresh", _HOOK)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def _state_dir(tmp_path: Path, indexed: str | None = _INDEXED) -> Path:
    d = tmp_path / ".repowise"
    d.mkdir()
    if indexed is not None:
        (d / "state.json").write_text(json.dumps({"last_sync_commit": indexed}))
    return d


def test_no_state_file_is_noop(tmp_path: Path) -> None:
    d = tmp_path / ".repowise"
    d.mkdir()
    action, _ = _mod().plan_refresh(
        repowise_dir=d, head=_HEAD, autoupdate=True, mlx_up=True, seen=None
    )
    assert action == "noop"


def test_fresh_index_is_noop(tmp_path: Path) -> None:
    d = _state_dir(tmp_path)
    action, _ = _mod().plan_refresh(
        repowise_dir=d, head=_INDEXED, autoupdate=True, mlx_up=True, seen=None
    )
    assert action == "noop"


def test_already_seen_head_is_noop(tmp_path: Path) -> None:
    d = _state_dir(tmp_path)
    action, _ = _mod().plan_refresh(
        repowise_dir=d, head=_HEAD, autoupdate=True, mlx_up=True, seen=_HEAD
    )
    assert action == "noop"


def test_stale_without_autoupdate_reminds(tmp_path: Path) -> None:
    d = _state_dir(tmp_path)
    action, msg = _mod().plan_refresh(
        repowise_dir=d, head=_HEAD, autoupdate=False, mlx_up=True, seen=None
    )
    assert action == "remind"
    assert "repowise-doc-update" in msg


def test_stale_autoupdate_but_server_down_reminds(tmp_path: Path) -> None:
    d = _state_dir(tmp_path)
    action, _ = _mod().plan_refresh(
        repowise_dir=d, head=_HEAD, autoupdate=True, mlx_up=False, seen=None
    )
    assert action == "remind"


def test_stale_autoupdate_and_server_up_updates(tmp_path: Path) -> None:
    d = _state_dir(tmp_path)
    action, msg = _mod().plan_refresh(
        repowise_dir=d, head=_HEAD, autoupdate=True, mlx_up=True, seen=None
    )
    assert action == "update"
    assert "background" in msg

"""Non-destructive reinstall (preserve extras) + destructive uninstall (remove saitenka's data, never
mpv/ffmpeg). Path lookups + plugin removal are behind seams monkeypatched to tmp dirs — hermetic."""

from __future__ import annotations

import saitenka.app.lifecycle as lc


def test_detect_extras_reads_importable_markers(monkeypatch):
    present = {"saitenka_deinflect", "jamdict"}  # telemetry (opentelemetry) absent
    monkeypatch.setattr(
        lc.importlib.util, "find_spec", lambda m: object() if m in present else None
    )
    assert lc.detect_extras() == ["deinflect", "jmdict"]  # sorted, telemetry dropped


def test_reinstall_command_pypi_and_github_forms():
    assert lc.reinstall_command(["telemetry", "deinflect"])[-1] == "saitenka[deinflect,telemetry]"
    assert lc.reinstall_command([])[-1] == "saitenka"  # bare when nothing installed
    gh = lc.reinstall_command(["deinflect"], source="github", ref="v0.5.0")[-1]
    assert gh == "saitenka[deinflect] @ git+https://github.com/serjflint/saitenka.git@v0.5.0"
    assert (
        "@v" not in lc.reinstall_command([], source="github")[-1]
    )  # no ref → default branch (latest)


def test_reinstall_attempts_ordering_by_source_and_ref():
    def specs(plan):
        return [c[-1] for c in plan]

    auto = lc.reinstall_attempts(["deinflect"], github_ref="v0.5.0")  # PyPI first, then GitHub@tag
    assert len(auto) == 2 and "git+" not in specs(auto)[0]
    assert "@v0.5.0" in specs(auto)[1]  # github attempt targets the release tag
    assert "subdirectory=" not in specs(auto)[1]  # installable project is the repository root
    assert specs(lc.reinstall_attempts([], source="pypi")) == ["saitenka"]  # PyPI only
    assert len(lc.reinstall_attempts([], source="github")) == 1  # forced GitHub only


def test_update_command_is_uv_tool_upgrade():
    # upgrade (not install --reinstall) preserves the recorded extras/constraints automatically.
    assert lc.update_command() == ["uv", "tool", "upgrade", "saitenka"]


def test_resolve_uv_prefers_which_falls_back_to_bare_name(monkeypatch):
    monkeypatch.setattr(lc.shutil, "which", lambda _n: "/opt/uv/bin/uv")
    assert lc.resolve_uv() == "/opt/uv/bin/uv"
    monkeypatch.setattr(lc.shutil, "which", lambda _n: None)  # not on PATH → bare name
    assert lc.resolve_uv() == "uv"


def test_handoff_script_waits_on_pid_chains_attempts_and_self_deletes():
    attempts = [["uv", "tool", "upgrade", "saitenka"], ["uv", "tool", "install", "saitenka[x] @ y"]]
    script = lc.handoff_script(attempts, pid=4321)
    assert 'tasklist /fi "PID eq 4321"' in script and "goto wait" in script  # waits for us to exit
    # each attempt is fully quoted (the github spec has spaces) and they fall back with ||
    assert '"uv" "tool" "upgrade" "saitenka"' in script
    assert '"uv" "tool" "install" "saitenka[x] @ y"' in script
    assert " || " in script
    assert 'del "%~f0"' in script  # cleans up the temp script
    assert script.endswith("\r\n") and "\r\n" in script  # CRLF for cmd.exe


def test_latest_release_tag_parses_api_or_returns_none(monkeypatch):
    import io
    import json
    import urllib.request

    class _Resp(io.BytesIO):  # a urlopen() result usable as a context manager
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: _Resp(json.dumps({"tag_name": "v0.5.0"}).encode()),
    )
    assert lc.latest_release_tag() == "v0.5.0"

    def _boom(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert lc.latest_release_tag() is None  # offline → None (caller falls back to main)


def _fake_dirs(monkeypatch, root):
    """Point every saitenka data dir at a distinct child of ``root`` and create them."""
    dirs = {}
    for name in ("config", "data", "cache", "crash"):
        d = root / name
        d.mkdir()
        dirs[name] = d
    import saitenka.app.crashlog as cl
    from saitenka.app import paths

    monkeypatch.setattr(paths, "config_dir", lambda: dirs["config"])
    monkeypatch.setattr(paths, "data_dir", lambda: dirs["data"])
    monkeypatch.setattr(paths, "cache_dir", lambda: dirs["cache"])
    monkeypatch.setattr(cl, "crash_dir", lambda: dirs["crash"])
    return dirs


def test_uninstall_removes_all_saitenka_dirs_but_not_mpv(tmp_path, monkeypatch):
    dirs = _fake_dirs(monkeypatch, tmp_path)
    plugin_calls = []
    monkeypatch.setattr("saitenka.app.plugin.uninstall_plugin", lambda: plugin_calls.append(1))
    removed = lc.uninstall(lambda _p: True)
    assert set(removed) == set(dirs.values())
    assert all(not d.exists() for d in dirs.values())  # data gone
    assert plugin_calls == [1]  # plugin removed (mpv untouched — no mpv path is ever referenced)


def test_uninstall_declined_removes_nothing(tmp_path, monkeypatch):
    dirs = _fake_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr("saitenka.app.plugin.uninstall_plugin", lambda: None)
    removed = lc.uninstall(lambda _p: False)  # user says no
    assert removed == []
    assert all(d.exists() for d in dirs.values())  # nothing deleted


def test_uninstall_keep_dicts_preserves_the_data_dir(tmp_path, monkeypatch):
    dirs = _fake_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr("saitenka.app.plugin.uninstall_plugin", lambda: None)
    removed = lc.uninstall(lambda _p: True, keep_dicts=True)
    assert dirs["data"] not in removed and dirs["data"].exists()  # dict DB kept
    assert not dirs["config"].exists() and not dirs["cache"].exists()  # the rest gone

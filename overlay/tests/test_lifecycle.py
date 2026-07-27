"""Non-destructive reinstall (preserve extras) + destructive uninstall (remove saitenka's data, never
mpv/ffmpeg). Path lookups + plugin removal are behind seams monkeypatched to tmp dirs — hermetic."""

from __future__ import annotations

import overlay.app.lifecycle as lc


def test_detect_extras_reads_importable_markers(monkeypatch):
    present = {"saitenka_deinflect", "jamdict"}  # telemetry (opentelemetry) absent
    monkeypatch.setattr(
        lc.importlib.util, "find_spec", lambda m: object() if m in present else None
    )
    assert lc.detect_extras() == ["deinflect", "jmdict"]  # sorted, telemetry dropped


def test_reinstall_command_pypi_and_github_forms():
    assert (
        lc.reinstall_command(["telemetry", "deinflect"])[-1]
        == "saitenka-overlay[deinflect,telemetry]"
    )
    assert lc.reinstall_command([])[-1] == "saitenka-overlay"  # bare when nothing installed
    gh = lc.reinstall_command(["deinflect"], source="github", ref="v0.5.0")[-1]
    assert (
        gh
        == "saitenka-overlay[deinflect] @ git+https://github.com/serjflint/saitenka.git@v0.5.0#subdirectory=overlay"
    )
    assert (
        "@v" not in lc.reinstall_command([], source="github")[-1]
    )  # no ref → default branch (latest)


def test_reinstall_attempts_ordering_by_source_and_ref():
    def specs(plan):
        return [c[-1] for c in plan]

    auto = lc.reinstall_attempts(["deinflect"], github_ref="v0.5.0")  # PyPI first, then GitHub@tag
    assert len(auto) == 2 and "git+" not in specs(auto)[0]
    assert (
        "@v0.5.0#subdirectory=overlay" in specs(auto)[1]
    )  # github attempt targets the release tag
    assert specs(lc.reinstall_attempts([], source="pypi")) == ["saitenka-overlay"]  # PyPI only
    assert len(lc.reinstall_attempts([], source="github")) == 1  # forced GitHub only


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
    import overlay.app.crashlog as cl
    from overlay.app import paths

    monkeypatch.setattr(paths, "config_dir", lambda: dirs["config"])
    monkeypatch.setattr(paths, "data_dir", lambda: dirs["data"])
    monkeypatch.setattr(paths, "cache_dir", lambda: dirs["cache"])
    monkeypatch.setattr(cl, "crash_dir", lambda: dirs["crash"])
    return dirs


def test_uninstall_removes_all_saitenka_dirs_but_not_mpv(tmp_path, monkeypatch):
    dirs = _fake_dirs(monkeypatch, tmp_path)
    plugin_calls = []
    monkeypatch.setattr("overlay.app.plugin.uninstall_plugin", lambda: plugin_calls.append(1))
    removed = lc.uninstall(lambda _p: True)
    assert set(removed) == set(dirs.values())
    assert all(not d.exists() for d in dirs.values())  # data gone
    assert plugin_calls == [1]  # plugin removed (mpv untouched — no mpv path is ever referenced)


def test_uninstall_declined_removes_nothing(tmp_path, monkeypatch):
    dirs = _fake_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr("overlay.app.plugin.uninstall_plugin", lambda: None)
    removed = lc.uninstall(lambda _p: False)  # user says no
    assert removed == []
    assert all(d.exists() for d in dirs.values())  # nothing deleted


def test_uninstall_keep_dicts_preserves_the_data_dir(tmp_path, monkeypatch):
    dirs = _fake_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr("overlay.app.plugin.uninstall_plugin", lambda: None)
    removed = lc.uninstall(lambda _p: True, keep_dicts=True)
    assert dirs["data"] not in removed and dirs["data"].exists()  # dict DB kept
    assert not dirs["config"].exists() and not dirs["cache"].exists()  # the rest gone

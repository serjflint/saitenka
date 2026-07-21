"""Stage 17b: the `saitenka-overlay setup` wizard — installer logic in Python, not shell.

Fully unit-tested with MOCKED package managers and fake home dirs; the shell stubs only bootstrap uv
and hand off to this. Non-destructive: inventory-first, confirm-first, ``--yes``/``--dry-run``,
resumable (re-run skips satisfied steps). No real installs, no network.
"""

from __future__ import annotations


from overlay.app import setup_wizard as sw


# --- package-manager selection ---------------------------------------------------------------


def test_macos_uses_brew(monkeypatch):
    monkeypatch.setattr(sw.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        sw.shutil, "which", lambda n: "/opt/homebrew/bin/brew" if n == "brew" else None
    )
    plan = sw.install_plan(["mpv", "ffmpeg"])
    assert plan.manager == "brew"
    assert plan.commands and all(c[:2] == ["brew", "install"] for c in plan.commands)


def test_windows_prefers_winget_then_choco_then_scoop(monkeypatch):
    monkeypatch.setattr(sw.platform, "system", lambda: "Windows")
    present = {"scoop"}
    monkeypatch.setattr(sw.shutil, "which", lambda n: "x" if n in present else None)
    assert sw.install_plan(["mpv"]).manager == "scoop"
    present.add("choco")
    assert sw.install_plan(["mpv"]).manager == "choco"
    present.add("winget")
    assert sw.install_plan(["mpv"]).manager == "winget"


def test_linux_prints_hints_no_autoinstall(monkeypatch):
    monkeypatch.setattr(sw.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sw.shutil, "which", lambda n: None)
    plan = sw.install_plan(["mpv", "ffmpeg"])
    assert plan.manager is None  # Linux: never auto-install
    assert plan.commands == []
    assert plan.hint and "mpv" in plan.hint


def test_no_manager_available(monkeypatch):
    monkeypatch.setattr(sw.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sw.shutil, "which", lambda n: None)
    plan = sw.install_plan(["mpv"])
    assert plan.manager is None
    assert plan.hint  # tells the user how to get brew


# --- inventory -------------------------------------------------------------------------------


def test_inventory_reports_missing(monkeypatch):
    monkeypatch.setattr(sw.shutil, "which", lambda n: "/bin/mpv" if n == "mpv" else None)
    inv = sw.inventory()
    assert inv["mpv"] is True
    assert inv["ffmpeg"] is False


def test_missing_tools_filters_present(monkeypatch):
    monkeypatch.setattr(sw.shutil, "which", lambda n: "/bin/uv" if n in ("uv", "mpv") else None)
    assert sw.missing_tools(["mpv", "ffmpeg", "uv"]) == ["ffmpeg"]


# --- step running (mocked subprocess boundary) -----------------------------------------------


def test_run_install_dry_run_executes_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(sw, "_run_cmd", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(sw.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sw.shutil, "which", lambda n: "/bin/brew" if n == "brew" else None)
    n = sw.do_install(["mpv", "ffmpeg"], dry_run=True, confirm=lambda _p: True)
    assert calls == []  # dry-run runs nothing
    assert n == 2  # but reports what it WOULD install


def test_run_install_declined_executes_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(sw, "_run_cmd", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(sw.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sw.shutil, "which", lambda n: "/bin/brew" if n == "brew" else None)
    n = sw.do_install(["mpv"], dry_run=False, confirm=lambda _p: False)
    assert calls == []
    assert n == 0


def test_run_install_confirmed_invokes_manager(monkeypatch):
    calls = []
    monkeypatch.setattr(sw, "_run_cmd", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(sw.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sw.shutil, "which", lambda n: "/bin/brew" if n == "brew" else None)
    sw.do_install(["mpv"], dry_run=False, confirm=lambda _p: True)
    assert calls == [["brew", "install", "mpv"]]


def test_full_wizard_resumable_skips_satisfied(monkeypatch, tmp_path):
    """Everything already present → the wizard installs nothing and still reaches doctor/init."""
    monkeypatch.setattr(sw.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sw.shutil, "which", lambda n: f"/bin/{n}")  # all tools present
    installs = []
    monkeypatch.setattr(sw, "_run_cmd", lambda cmd: installs.append(cmd))
    ran = {"doctor": False, "init": False}
    monkeypatch.setattr(sw, "_run_doctor", lambda: ran.__setitem__("doctor", True))
    monkeypatch.setattr(sw, "_run_init", lambda confirm: ran.__setitem__("init", True))
    monkeypatch.setattr(sw, "_offer_anki", lambda confirm: None)
    monkeypatch.setattr(sw, "_offer_import", lambda confirm: None)
    monkeypatch.setattr(sw, "_offer_plugin", lambda confirm: None)
    rc = sw.run_setup(yes=True, dry_run=False)
    assert rc == 0
    assert installs == []  # nothing to install
    assert ran["doctor"] and ran["init"]


def test_anki_config_fragment():
    """The wizard's Anki choices → config: [known] deck→field (coloring), [mine] merged over existing."""
    from overlay.app.setup_wizard import anki_config_fragment as f

    frag = f("Known", "Entry", "My::Mine", "Lapis", existing_mine={"key": "Ctrl+m"})
    assert frag == {
        "known": {"Known": ["Entry"]},
        "mine": {"key": "Ctrl+m", "deck": "My::Mine", "model": "Lapis"},  # existing key preserved
    }
    assert f("", "", "D", "M") == {"mine": {"deck": "D", "model": "M"}}  # no deck → no [known]
    assert f("K", "", "D", "M")["known"] == {"K": ["Expression"]}  # blank field → default

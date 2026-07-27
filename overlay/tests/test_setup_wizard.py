"""Stage 17b: the `saitenka-overlay setup` wizard — installer logic in Python, not shell.

Fully unit-tested with MOCKED package managers and fake home dirs; the shell stubs only bootstrap uv
and hand off to this. Non-destructive: inventory-first, confirm-first, ``--yes``/``--dry-run``,
resumable (re-run skips satisfied steps). No real installs, no network.
"""

from __future__ import annotations

from types import SimpleNamespace

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
    monkeypatch.setattr(sw.shutil, "which", lambda _n: None)
    plan = sw.install_plan(["mpv", "ffmpeg"])
    assert plan.manager is None  # Linux: never auto-install
    assert plan.commands == []
    assert plan.hint and "mpv" in plan.hint


def test_no_manager_available(monkeypatch):
    monkeypatch.setattr(sw.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sw.shutil, "which", lambda _n: None)
    plan = sw.install_plan(["mpv"])
    assert plan.manager is None
    assert plan.hint  # tells the user how to get brew


# --- inventory -------------------------------------------------------------------------------


def test_inventory_reports_missing(monkeypatch):
    monkeypatch.setattr(sw, "_present", lambda t: t == "mpv")
    inv = sw.inventory()
    assert inv["mpv"] is True
    assert inv["ffmpeg"] is False


def test_missing_tools_filters_present(monkeypatch):
    monkeypatch.setattr(sw, "_present", lambda t: t in ("uv", "mpv"))
    assert sw.missing_tools(["mpv", "ffmpeg", "uv"]) == ["ffmpeg"]


def test_present_mpv_routes_through_find_mpv(monkeypatch):
    """mpv presence uses find_mpv (knows C:\\mpv / mpv.net / registry), not bare PATH — the fix for the
    winget-crash loop when mpv is installed off-PATH."""
    from overlay.mpvio import discover

    monkeypatch.setattr(sw.shutil, "which", lambda _n: None)  # nothing on PATH
    monkeypatch.setattr(discover, "find_mpv", lambda _config_path=None: "C:\\mpv\\mpv.exe")
    assert sw._present("mpv") is True
    monkeypatch.setattr(discover, "find_mpv", lambda _config_path=None: None)
    assert sw._present("mpv") is False


# --- step running (mocked subprocess boundary) -----------------------------------------------


def test_run_install_dry_run_executes_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(sw, "_present", lambda _t: False)  # force both tools "missing"
    monkeypatch.setattr(sw, "_run_cmd", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(sw.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sw.shutil, "which", lambda n: "/bin/brew" if n == "brew" else None)
    n = sw.do_install(["mpv", "ffmpeg"], dry_run=True, confirm=lambda _p: True)
    assert calls == []  # dry-run runs nothing
    assert n == 2  # but reports what it WOULD install


def test_run_install_declined_executes_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(sw, "_present", lambda _t: False)
    monkeypatch.setattr(sw, "_run_cmd", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(sw.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sw.shutil, "which", lambda n: "/bin/brew" if n == "brew" else None)
    n = sw.do_install(["mpv"], dry_run=False, confirm=lambda _p: False)
    assert calls == []
    assert n == 0


def test_run_install_confirmed_invokes_manager(monkeypatch):
    calls = []
    monkeypatch.setattr(sw, "_present", lambda _t: False)
    monkeypatch.setattr(sw, "_run_cmd", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(sw.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sw.shutil, "which", lambda n: "/bin/brew" if n == "brew" else None)
    sw.do_install(["mpv"], dry_run=False, confirm=lambda _p: True)
    assert calls == [["brew", "install", "mpv"]]


def test_do_install_continues_and_survives_a_failed_manager(monkeypatch):
    """A package manager exiting non-zero (winget already-installed / declined) must NOT crash setup —
    the other tools still install and the count reflects only the successes."""
    monkeypatch.setattr(sw, "_present", lambda _t: False)
    monkeypatch.setattr(sw.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sw.shutil, "which", lambda n: "/bin/brew" if n == "brew" else None)

    def _run(cmd):
        if "mpv" in cmd:
            raise sw.subprocess.CalledProcessError(1, cmd)  # mpv install fails
        # ffmpeg succeeds

    monkeypatch.setattr(sw, "_run_cmd", _run)
    n = sw.do_install(["mpv", "ffmpeg"], dry_run=False, confirm=lambda _p: True)
    assert n == 1  # ffmpeg installed; the mpv failure was swallowed, not raised


def test_full_wizard_resumable_skips_satisfied(monkeypatch):
    """Everything already present → the wizard installs nothing and still reaches doctor/init."""
    monkeypatch.setattr(sw.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sw, "_present", lambda _t: True)  # all tools present
    installs = []
    monkeypatch.setattr(sw, "_run_cmd", lambda cmd: installs.append(cmd))
    ran = {"doctor": False, "init": False}

    def _fake_doctor():  # the wizard branches on the returned report's exit_code
        ran["doctor"] = True
        return SimpleNamespace(exit_code=0)

    monkeypatch.setattr(sw, "_run_doctor", _fake_doctor)
    monkeypatch.setattr(
        sw,
        "_run_init",
        lambda _confirm: ran.__setitem__("init", True),  # noqa: FBT003  # dict.__setitem__'s own signature
    )
    monkeypatch.setattr(sw, "_offer_anki", lambda _confirm: None)
    monkeypatch.setattr(sw, "_offer_import", lambda _confirm: None)
    monkeypatch.setattr(sw, "_offer_plugin", lambda _confirm: None)
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


def test_resolve_mpv_input_accepts_exe_strips_quotes_and_scans_dir(tmp_path):
    exe = tmp_path / ("mpv.exe" if sw.sys.platform == "win32" else "mpv")
    exe.write_text("")
    exe.chmod(0o755)
    assert sw._resolve_mpv_input(str(exe)) == str(exe)  # direct path
    assert sw._resolve_mpv_input(f'"{exe}"') == str(exe)  # Windows "Copy as path" quotes stripped
    assert sw._resolve_mpv_input(str(tmp_path)) == str(exe)  # a DIR → finds the binary inside
    assert sw._resolve_mpv_input("") is None
    assert sw._resolve_mpv_input(str(tmp_path / "nope")) is None  # non-existent → None


def test_rank_decks_biggest_first():
    from overlay.app.setup_wizard import rank_decks

    assert rank_decks(["A", "B", "C"], {"A": 10, "B": 500, "C": 0}) == ["B", "A", "C"]
    assert rank_decks(["z", "a"], {}) == ["a", "z"]  # unknown sizes → alphabetical


def test_default_known_deck_prefers_saitenka_known_then_largest():
    from overlay.app.setup_wizard import default_known_deck as d

    # Saitenka::Known wins even when empty (it's the config convention)
    assert d(["Big", "Saitenka::Known"], {"Big": 999, "Saitenka::Known": 0}) == "Saitenka::Known"
    # any ::Known leaf is next
    assert d(["Big", "JP::Known"], {"Big": 999, "JP::Known": 3}) == "JP::Known"
    # else the largest non-empty, non-Default deck
    assert d(["Default", "Vocab", "Small"], {"Default": 5, "Vocab": 800, "Small": 10}) == "Vocab"
    # nothing qualifies → '' so the caller offers skip
    assert d(["Default"], {"Default": 5}) == ""
    assert d(["Empty"], {"Empty": 0}) == ""

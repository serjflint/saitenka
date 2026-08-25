"""The premises about mpv that every other tier can only assert.

Two shipped defects were the same shape: a belief about mpv, written into a comment, checked
against nothing. A gate row demanded `sub-ass-vsfilter-aspect-compat is None`, which no mpv that
still had the option could satisfy — vacuous on 0.41, a total refusal below it, and invisible
because everyone testing ran 0.41. A keybind section registered as `"default"` on the belief that
`keybind` gave default priority; it gives 16 and is not weak, while a `"default"` section is weak at
15, so every shortcut silently lost to the user's `input.conf`. The unit test written for the fix
asserted the string `"force"` against the string `"force"`.

Nothing below asserts what we believe. It asks mpv.

Opt-in like the rest of the live tier: `SAITENKA_LIVE=1`, or `uv run poe smoke-live`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from live_harness import live_reader as _live_reader

from saitenka.mpvio.launch import NATIVE_GEOMETRY_MPV_MIN

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAITENKA_LIVE"),
    reason="live real-mpv test — set SAITENKA_LIVE=1; run `uv run poe smoke-live`",
)


def _bare_mpv(*args: str):
    """A headless mpv carrying `args`, and a `get(name)` that asks it for a property.

    `--no-config` on purpose: the question is what mpv does with OUR launch profile, and a
    developer's own `mpv.conf` would answer a different one.
    """
    from saitenka.mpvio.discover import find_mpv
    from saitenka.mpvio.ipc import MpvIPC, default_ipc_path

    mpv = find_mpv(None)
    if not mpv:
        pytest.skip("mpv not found")
    tmp = Path(tempfile.mkdtemp(prefix="saitenka-premise-"))
    sock = default_ipc_path(tmp.name)
    proc = subprocess.Popen(
        [
            mpv,
            "--no-config",
            "--idle",
            "--vo=null",
            "--ao=null",
            f"--input-ipc-server={sock}",
            *args,
        ]
    )
    try:
        ipc = MpvIPC(sock).connect(timeout=15)
    except Exception:
        proc.kill()
        raise
    return proc, ipc


@pytest.mark.live
@pytest.mark.mpv_min(NATIVE_GEOMETRY_MPV_MIN)
@pytest.mark.timeout(30)
def test_mpv_reports_every_option_the_geometry_gate_reads() -> None:
    """An option the gate names and mpv does not have reads `None`, which no row should accept and
    a removed one always will. `sub-ass-vsfilter-aspect-compat` sat in the list after 0.41 deleted
    it, so the row it fed passed vacuously for everyone who could run it at all."""
    from saitenka.app.native_subtitles import GATE_OPTIONS

    proc, ipc = _bare_mpv()
    try:
        missing = [
            name
            for name in GATE_OPTIONS
            if ipc.command("get_property", f"options/{name}").get("error") != "success"
        ]
    finally:
        ipc.command("quit")
        ipc.close()
        proc.terminate()

    assert missing == []


@pytest.mark.live
@pytest.mark.mpv_min(NATIVE_GEOMETRY_MPV_MIN)
@pytest.mark.timeout(30)
def test_the_launch_profile_lands_on_values_the_gate_accepts() -> None:
    """`saitenka run` and the geometry gate are written by different hands against the same mpv. A
    profile that sets an option to a value the gate refuses takes native geometry away for the whole
    session, and the only symptom is the legacy renderer."""
    from saitenka.app.native_subtitles import GATE_OPTIONS, _unsupported_render_inputs
    from saitenka.mpvio.launch import MpvLaunchOptions, build_mpv_argv

    launched = build_mpv_argv(
        "mpv",
        "/dev/null",
        "/dev/null",
        "/dev/null",
        MpvLaunchOptions(slang="ja", start="0", native_visible=True),
    )
    # Only the subtitle flags: the rest names a socket, a log and a file this mpv has no use for.
    profile = [arg for arg in launched if arg.startswith(("--sub", "--blend", "--osd"))]

    proc, ipc = _bare_mpv(*profile)
    try:
        settings = {
            name: ipc.command("get_property", f"options/{name}").get("data")
            for name in GATE_OPTIONS
        }
    finally:
        ipc.command("quit")
        ipc.close()
        proc.terminate()

    assert _unsupported_render_inputs(settings) == ()


@pytest.mark.live
@pytest.mark.timeout(30)
def test_every_shortcut_we_register_outranks_the_user_s_own_binding(tmp_path: Path) -> None:
    """The keybind regression, asked of mpv's own table — against a user's `input.conf` that binds
    every key we do, because that is the collision the regression was.

    Compared rather than pinned. mpv computes priority as the section's index in the active-section
    stack plus the number of active sections (`mp_input_get_bindings`), so the absolute number counts
    how many sections mpv happens to have up — 31 on 0.41, 19 on 0.37, and it moves again whenever a
    release adds a script. Only the ordering is ours to claim.
    """
    from saitenka.app.bindings import GLOBAL_SECTION, active_bindings
    from saitenka.app.config import KeyOptions

    keys = [b.key for b in active_bindings(KeyOptions(), "global") if b.spec.message]
    config_dir = tmp_path / "mpv-config"
    config_dir.mkdir()
    (config_dir / "input.conf").write_text(
        "".join(f'{key} show-text "user-{index}"\n' for index, key in enumerate(keys)),
        encoding="utf-8",
    )

    with _live_reader(config_dir=config_dir) as (_tmp, reader, ipc):
        for _ in range(20):
            reader.pump()
            bindings = ipc.command("get_property", "input-bindings").get("data") or []
            ours = [b for b in bindings if b.get("section") == GLOBAL_SECTION]
            if ours:
                break
            time.sleep(0.1)

        assert ours, f"no binding registered under {GLOBAL_SECTION}"
        weak = [b["key"] for b in ours if b.get("is_weak")]
        assert weak == [], f"{json.dumps(weak)} would lose to a user's input.conf"

        # Paired on the key as MPV reports it, on both sides: it normalises what it is given
        # (`Shift+c` comes back as `C`), so pairing on our own spelling compares nothing.
        # Matched on the marker rather than on `show-text`: mpv's OWN default bindings for F8/F9 are
        # show-text too, and they are not what a user typed.
        theirs = {
            b["key"]: b["priority"] for b in bindings if 'show-text "user-' in str(b.get("cmd", ""))
        }
        ours_by_key = {b["key"]: b["priority"] for b in ours}
        # The user's entries must still be *present*: "force" overrides a binding, where a per-key
        # `keybind` would overwrite it and leave the user no way to see what happened to theirs.
        assert set(theirs) == set(ours_by_key), (
            f"mpv kept no user binding for {sorted(set(ours_by_key) - set(theirs))}"
        )

        lost = sorted(key for key, priority in ours_by_key.items() if priority <= theirs[key])
        assert lost == [], f"{json.dumps(lost)} would lose to a user's input.conf"


@pytest.mark.live
@pytest.mark.timeout(30)
def test_the_ranking_oracle_catches_a_section_that_does_not_outrank(tmp_path: Path) -> None:
    """Negative control for the test above. A section defined `default` rather than `force` is what
    shipped the F1 regression; if the comparison cannot see that, it is measuring nothing."""
    from saitenka.app.bindings import GLOBAL_SECTION

    config_dir = tmp_path / "mpv-config"
    config_dir.mkdir()
    (config_dir / "input.conf").write_text('F1 show-text "user-f1"\n', encoding="utf-8")

    section = f"{GLOBAL_SECTION}-weak-control"
    proc, ipc = _bare_mpv(f"--config-dir={config_dir}")
    try:
        ipc.command("define-section", section, 'F1 show-text "ours"\n', "default")
        ipc.command("enable-section", section)
        bindings = ipc.command("get_property", "input-bindings").get("data") or []
    finally:
        ipc.command("quit")
        ipc.close()
        proc.terminate()

    ours = next(b for b in bindings if b.get("section") == section)
    theirs = next(
        b
        for b in bindings
        if b.get("section") == "default" and str(b.get("cmd", "")).startswith("show-text")
    )
    assert ours["priority"] <= theirs["priority"] or ours.get("is_weak")


NATIVE_SID, GENERATED_SID = 1, 2


def _two_track_mpv(tmp_path: Path):
    """A paused clip carrying two external ASS tracks, and a drain that waits for quiet.

    The Gate B probe's shape, reduced to what the premises below need: `sid` 1 is the track mpv
    loads with the file, `sid` 2 the one added after.
    """
    if not shutil.which("ffmpeg"):
        # `_bare_mpv` skips on a missing mpv; a missing ffmpeg has to do the same, or one absent
        # binary in this file skips and the other raises FileNotFoundError as a test ERROR.
        pytest.skip("ffmpeg not found")
    fixtures = Path(__file__).parent / "fixtures" / "mpv_source_envelope"
    clip = tmp_path / "clip.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=320x180:d=8",
            "-c:v",
            "ffv1",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )
    proc, ipc = _bare_mpv(
        "--keep-open=yes",
        "--pause",
        "--sub-auto=no",
        f"--sub-file={fixtures / 'external.ass'}",
        str(clip),
    )
    for index, name in enumerate(("sid", "sub-text"), start=1):
        ipc.command("observe_property", index, name)
    ipc.command("sub-add", str(fixtures / "generated.ass"), "auto", "generated", "jpn")
    ipc.command("set_property", "time-pos", 1.0)
    return proc, ipc


def _drain_until_quiet(ipc, *, quiet: float = 0.4, limit: float = 5.0) -> list[dict]:
    collected: list[dict] = []
    deadline = time.monotonic() + limit
    last = time.monotonic()
    while time.monotonic() < deadline and time.monotonic() - last < quiet:
        batch = ipc.drain_events()
        if batch:
            collected.extend(batch)
            last = time.monotonic()
        time.sleep(0.01)
    return collected


def _changes(events: list, name: str) -> list:
    return [
        event.get("data")
        for event in events
        if event.get("event") == "property-change" and event.get("name") == name
    ]


@pytest.mark.live
@pytest.mark.timeout(30)
def test_mpv_emits_sub_text_on_a_paused_track_switch(tmp_path: Path) -> None:
    """`tools/test_mpv_source_transition.py`'s `_FakeMpv` refreshes `sub-text` on every switch. The
    plan that produced it assumed the opposite — that a paused mpv has no redraw and so no new
    `sub-text` — and built a whole causal story on it before anyone asked the binary."""
    proc, ipc = _two_track_mpv(tmp_path)
    try:
        _drain_until_quiet(ipc)
        assert ipc.command("get_property", "pause").get("data") is True
        ipc.command("set_property", "sid", GENERATED_SID)
        events = _drain_until_quiet(ipc)
    finally:
        ipc.command("quit")
        ipc.close()
        proc.terminate()

    # The last value, not the whole list: how many notifications mpv raises is timing (see the note
    # below), and the premise here is the `sub-text` refresh, not the count.
    assert _changes(events, "sid")[-1] == GENERATED_SID
    assert any("生成した字幕" in (text or "") for text in _changes(events, "sub-text"))


# Not pinned here, on purpose: how many `sid` notifications mpv raises for N writes is timing, not
# semantics. It raises them from the playloop rather than the command handler, so writes that reach
# one iteration collapse into a single event carrying the final value. Measured on 0.41 in a settled
# loop: 60/60 collapse through `command_async`, 18/60 through the blocking `command` — and the 60/60
# stops holding right after startup, when ticks are frequent. Every rate here is environment-
# dependent, and an assertion on one is a flake generator. Consumers must not require a
# notification per write; `tools/mpv_source_transition.py` serializes instead.


@pytest.mark.live
@pytest.mark.timeout(30)
def test_a_deselected_sid_reads_back_as_false(tmp_path: Path) -> None:
    """A confirm written against the literal `"no"` would never fire, turning a probe's 3 s flake
    into a deterministic 3 s red. `tools/mpv_source_transition.py` already accepts the three-way
    `{None, "no", False}`; this says which one mpv actually sends."""
    proc, ipc = _two_track_mpv(tmp_path)
    try:
        _drain_until_quiet(ipc)
        ipc.command("set_property", "sid", "no")
        events = _drain_until_quiet(ipc)
        queried = ipc.command("get_property", "sid").get("data")
    finally:
        ipc.command("quit")
        ipc.close()
        proc.terminate()

    assert queried is False
    assert _changes(events, "sid") == [False]

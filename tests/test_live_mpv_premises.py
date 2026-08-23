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
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from live_harness import live_reader as _live_reader

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
def test_every_shortcut_we_register_outranks_the_user_s_own_binding() -> None:
    """The keybind regression, asked of mpv's own table. A section registered weak sits at priority
    15, below an `input.conf` entry at 16, and the shortcut silently stops working — which is
    exactly what happened to F1. Only mpv knows these numbers."""
    from saitenka.app.bindings import GLOBAL_SECTION

    with _live_reader() as (_tmp, reader, ipc):
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
        assert {b.get("priority") for b in ours} == {31}

"""Shared real-mpv setup for the live tier — a real mpv window + subtitle + a SessionController wired to it.

Extracted from ``test_live_mpv.py`` so the L3 smoke tests AND the live-mpv jank harness
(``examples/jank_live.py``, #32) drive one identical setup. Needs a real display + mpv binary; every
caller is opt-in (``SAITENKA_LIVE`` / the ``live`` marker).
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from session_builder import build_session

from saitenka.app.session.factory import SessionServices

DEMO_LINE = "門前の小僧習わぬ経を読む"


class MiniDS:
    """A trivial dict so a tooltip renders — the live tier is about the input/render path, not content."""

    # Empty collections so the render-cache signature path (dict_set_signature) works when a cache file
    # is present on disk (the cold-paint path); a fresh CI runner has none, but a dev machine does.
    dicts = ()
    freqs = ()
    pitches = ()

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # match DictionarySet
        from saitenka.panel import Definition, Entry

        return Entry(
            headword=[tok.surface],
            reading=getattr(tok, "reading", "") or tok.surface,
            defs=[Definition("D", ["to read"])],
        )

    def has_term(self, *_forms):
        return False  # no multi-token phrase merge — the input path, not phrase stacking


def make_clip_and_sub(tmp: Path) -> tuple[Path, Path]:
    clip = tmp / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=1280x720:d=8",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )
    srt = tmp / "line.srt"
    srt.write_text(f"1\n00:00:00,000 --> 00:00:08,000\n{DEMO_LINE}\n", encoding="utf-8")
    return clip, srt


@contextmanager
def live_reader(*, paused: bool = True, dict_set=None, config_dir: Path | None = None):
    """A live mpv window with the demo cue loaded and a :class:`SessionController` observing it. ``paused=False``
    lets playback run so mpv's VO advances frames — required for the jank harness to see real
    ``frame-drop-count`` / ``vo-delayed-frame-count`` movement (the smoke tests keep it paused).

    ``dict_set`` is taken at construction, before the cue is driven, because a swap afterwards does not
    reach the cue's already-resolved entries: `replace_dictionary_set` is the async-arrival installer,
    and only `switch_to` pairs it with the invalidation that clears them.

    ``config_dir`` replaces the default ``--no-config`` with a real mpv config directory, for the one
    question that cannot be asked without a user's own ``input.conf`` present. Everything else wants
    ``--no-config``: a developer's own ``mpv.conf`` would answer a different question."""
    from saitenka.app.session.routes import install_session_runtime
    from saitenka.mpvio.discover import find_mpv
    from saitenka.mpvio.ipc import MpvIPC, default_ipc_path

    mpv = find_mpv(None)
    if not mpv:
        pytest.skip("mpv not found")

    tmp = Path(tempfile.mkdtemp(prefix="saitenka-live-"))
    clip, srt = make_clip_and_sub(tmp)
    sock = default_ipc_path(tmp.name)
    proc = subprocess.Popen(
        [
            mpv,
            f"--input-ipc-server={sock}",
            "--force-window=yes",
            "--keep-open=yes",
            "--sub-visibility=no",
            "--osd-level=1",
            "--pause" if paused else "--loop-file=inf",
            f"--config-dir={config_dir}" if config_dir else "--no-config",
            f"--sub-file={srt}",
            str(clip),
        ]
    )
    reader = ipc = gateway = None
    try:
        ipc = MpvIPC(sock).connect(timeout=15)
        # Before the SessionController, exactly as `run`/`attach` do it: without a runtime ingress the
        # transport routes no replies, so even the OSD-dimensions seed comes back None and nothing
        # downstream draws. No breadcrumb — this harness screenshots.
        gateway = install_session_runtime(ipc, startup_hint=False)
        reader = build_session(
            ipc,
            services=SessionServices(dictionaries=dict_set if dict_set is not None else MiniDS()),
        )
        reader.turn.refresh_osd()
        reader.turn.playback_observation.start_session()
        reader.turn.command_runtime.install_input()
        reader.turn.subtitle_navigation.load_index(srt)

        for _ in range(100):  # wait for the subtitle cue → tokens + per-word boxes
            reader.pump()
            if (
                reader.turn.subtitle_presentation.cue.current.tokens
                and reader.turn.subtitle_presentation.cue.current.boxes
            ):
                break
            time.sleep(0.1)
        assert (
            reader.turn.subtitle_presentation.cue.current.tokens
            and reader.turn.subtitle_presentation.cue.current.boxes
        ), "subtitle never loaded into the reader"
        yield tmp, reader, ipc
    finally:
        try:
            if reader is not None:
                reader.close()
            if gateway is not None:
                gateway.close()
            if ipc is not None:
                ipc.command("quit")
                ipc.close()
        except Exception:  # noqa: BLE001  # best-effort teardown - preserve the caller's assertion
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def poll_until(reader, predicate, message: str) -> None:
    for _ in range(60):
        reader.pump()
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail(message)

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

    def rareness_rank(self, _token):  # protocol shape
        """No frequency dictionaries, so no blended rank and no pill."""
        return


#: Touching boundaries, so the successor arrives in the frame the predecessor ends — the shape
#: every cue-boundary defect lives in, and one a single cue spanning the clip cannot reach. The
#: neighbours are lines whose tokens the tokenizer skips, so they cannot explain away a missing box.
BOUNDARY_CUES: tuple[tuple[float, float, str], ...] = (
    (0.5, 2.5, "♬～"),
    (2.5, 5.0, "犬…　かな？"),
    (5.0, 7.5, "♬～"),
)


_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_timestamp(seconds: float) -> str:
    return f"{int(seconds) // 3600}:{int(seconds) // 60 % 60:02d}:{seconds % 60:05.2f}"


def write_ass(path: Path, cues: tuple[tuple[float, float, str], ...]) -> Path:
    """An AUTHORED .ass track. The native geometry reads the document mpv is drawing, and a
    converted SubRip has none to read — `subtitle-source-conversion-unreproduced`, which is what the
    harness got for as long as it only ever wrote SubRip."""
    body = "".join(
        f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},Default,,0,0,0,,{text}\n"
        for start, end, text in cues
    )
    path.write_text(_ASS_HEADER + body, encoding="utf-8")
    return path


def _srt_timestamp(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 3600:02d}:{whole // 60 % 60:02d}:{whole % 60:02d},{round(seconds % 1 * 1000):03d}"


def make_clip_and_sub(
    tmp: Path, cues: tuple[tuple[float, float, str], ...] | None = None
) -> tuple[Path, Path]:
    """A navy clip and a subtitle file. ``cues`` overrides the single demo line with real timings."""
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
    if cues is None:
        srt.write_text(f"1\n00:00:00,000 --> 00:00:08,000\n{DEMO_LINE}\n", encoding="utf-8")
        return clip, srt
    srt.write_text(
        "\n".join(
            f"{index}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n"
            for index, (start, end, text) in enumerate(cues, start=1)
        ),
        encoding="utf-8",
    )
    return clip, srt


@contextmanager
def live_reader(
    *,
    paused: bool = True,
    dict_set=None,
    scorer=None,
    native_visible: bool = False,
    config_dir: Path | None = None,
    cues: tuple[tuple[float, float, str], ...] | None = None,
):
    """A live mpv window with the demo cue loaded and a :class:`SessionController` observing it. ``paused=False``
    lets playback run so mpv's VO advances frames — required for the jank harness to see real
    ``frame-drop-count`` / ``vo-delayed-frame-count`` movement (the smoke tests keep it paused).

    ``dict_set`` is taken at construction, before the cue is driven, because a swap afterwards does not
    reach the cue's already-resolved entries: `replace_dictionary_set` is the async-arrival installer,
    and only `switch_to` pairs it with the invalidation that clears them.

    ``native_visible`` picks the renderer. It defaults OFF because that is `ReaderOptions`' default
    and every existing caller was written against it — which is also the catch: the whole live tier
    has therefore been exercising the LEGACY renderer, while a configured session runs the native
    one. A caller asking about geometry, color or hit boxes wants ``True``.

    ``scorer`` is what makes words *colored* rather than merely boxed: with none, every token's style
    is `None` and the color ladder has nothing to assign, so a caller demonstrating color has to
    supply one.

    ``config_dir`` replaces the default ``--no-config`` with a real mpv config directory, for the one
    question that cannot be asked without a user's own ``input.conf`` present. Everything else wants
    ``--no-config``: a developer's own ``mpv.conf`` would answer a different question."""
    from saitenka.app.config import ReaderOptions, SubtitleGeometryOptions
    from saitenka.app.session.routes import install_session_runtime
    from saitenka.mpvio.discover import find_mpv
    from saitenka.mpvio.ipc import MpvIPC, default_ipc_path

    mpv = find_mpv(None)
    if not mpv:
        pytest.skip("mpv not found")

    tmp = Path(tempfile.mkdtemp(prefix="saitenka-live-"))
    clip, srt = make_clip_and_sub(tmp, cues)
    if native_visible:
        # An authored track, because the native path measures the document mpv draws: a converted
        # SubRip leaves it with no document at all (`subtitle-source-conversion-unreproduced`).
        srt = write_ass(tmp / "line.ass", cues or ((0.0, 8.0, DEMO_LINE),))
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
            options=ReaderOptions(
                subtitle_geometry=SubtitleGeometryOptions(
                    native_visible=native_visible, native_formats="all"
                )
            ),
            services=SessionServices(
                dictionaries=dict_set if dict_set is not None else MiniDS(),
                scorer=scorer,
            ),
        )
        reader.start()
        reader.graph.subtitle_navigation.load_index(srt)
        if native_visible and reader.graph.subtitle_presentation.native is not None:
            from saitenka.app.embedded_subs import resolve_track_fonts

            # Through the production resolver, as `test_native_subtitles` does: a track load is
            # where the font set is read, and skipping it measures an environment no session has.
            resolve_track_fonts(ipc, ipc.query, reader.graph.subtitle_presentation.native)
            reader.graph.subtitle_presentation.native.set_source(srt)
        if cues is not None:
            # A multi-cue file starts before its first cue, so the wait below would time out on an
            # empty overlay rather than on a real failure.
            ipc.command("seek", str(cues[0][0] + 0.1), "absolute")

        for _ in range(100):  # wait for the subtitle cue → tokens + per-word boxes
            reader.pump()
            if (
                reader.graph.subtitle_presentation.cue.current.tokens
                and reader.graph.subtitle_presentation.cue.current.boxes
            ):
                break
            time.sleep(0.1)
        assert (
            reader.graph.subtitle_presentation.cue.current.tokens
            and reader.graph.subtitle_presentation.cue.current.boxes
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

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
import util
from dirty_equals import IsPartialDict
from driver import Driver
from saitenka_subtitles import (
    MAX_ASS_SOURCE_BYTES,
    Cue,
    CueIndex,
    GeometryRequest,
    GeometrySnapshot,
    Rect,
    TokenGeometry,
)
from saitenka_tokenize.japanese import Token
from saitenka_tokenize.languages import MAIN_LANG
from saitenka_wordstate import Scorer
from saitenka_wordstate.known import KnownWords
from session_builder import TestSession, build_session
from util import record_spans

from saitenka.app import native_subtitles, subtitle_fonts, subtitle_render
from saitenka.app.config import ReaderOptions, SubtitleGeometryOptions
from saitenka.app.embedded_subs import resolve_track_fonts
from saitenka.app.features.tooltip.nested_popup import kanji_current
from saitenka.app.native_subtitles import AssFullCapability
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.scoring import Coloring
from saitenka.app.session.factory import (
    SessionInfrastructure,
    SessionServices,
)
from saitenka.app.subtitle_intents import SeekCue
from saitenka.app.subtitle_ownership import PixelOwner
from saitenka.app.subtitle_render import NativeVisibleRenderer, SubtitleRenderer
from saitenka.app.subtitle_selection import SubtitleStartup, SubtitleTracks
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome

if TYPE_CHECKING:
    from pathlib import Path

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫を見る
""".encode()

ASS_TWO = (
    ASS.decode()
    .replace(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫を見る\n",
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫を見る\n"
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,犬も見る\n",
    )
    .encode()
)


class FakeIPC(util.FakeIPC):
    """Defers job and command delivery so a test controls when each completes, and injects mpv
    errors. Those overrides are deliberate; inheriting is what supplies the ports it does NOT
    specialise — a double defining only what its author needed is how production ends up on a
    fallback branch it never takes in a real session."""

    def __init__(self, *, annotation_jobs: bool = False) -> None:
        super().__init__()
        self.annotation_jobs = annotation_jobs
        self.props |= {
            "sid": 2,
            "sub-text/ass-full": "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0000,0000,0000,,猫を見る",
            "sub-start": 1.0,
            "sub-end": 3.0,
            "time-pos": 1.25,
            "sub-delay": 0.0,
            "sub-visibility": False,
            "pause": True,
            "osd-dimensions": {"w": 1280, "h": 720},
            "video-out-params": {"dw": 1280, "dh": 720, "w": 1280, "h": 720, "par": 1.0},
            "options/sub-ass-override": "no",
            "options/sub-ass-scale-with-window": False,
            "options/sub-scale": 1.0,
            "options/sub-pos": 100.0,
            "options/sub-use-margins": True,
            "options/sub-ass-force-margins": False,
            "options/sub-ass-video-aspect-override": 0.0,
            "options/sub-ass-use-video-data": "all",
            "options/sub-ass-style-overrides": [],
            "options/sub-scale-with-window": True,
            "options/sub-scale-by-window": True,
            "options/blend-subtitles": False,
            "options/sub-font-provider": "auto",
            "options/embeddedfonts": False,
            "options/sub-fonts-dir": "",
            "options/sub-font": "sans-serif",
        }
        self.set_property_error: str | None = None
        self.set_property_exception: Exception | None = None
        self.overlay_add_error: str | None = None
        self.osd_bounds: dict | None = None
        self.get_property_error: str | None = None
        self.correlate_commands = False
        #: Identity substrings the gateway will not admit — its real answer when it is at capacity.
        self.refused_identities: tuple[str, ...] = ()
        self.submitted: list[tuple] = []
        self.job_lanes: dict[str, object] = {}
        self.pending_jobs: list[tuple] = []

    def register_runtime_job_lane(self, name, policy, handler) -> bool:  # noqa: ARG002
        # Geometry only. Every lane in the session probes for this port at composition, so accepting
        # any name would move annotation, tooltip raster and prefetch onto lanes too — five
        # subsystems this file does not exercise, changing when their results land.
        if name != "subtitle-geometry" and not (self.annotation_jobs and name == "cue-annotation"):
            return False
        self.job_lanes[name] = handler
        return True

    def submit_runtime_job(self, *, owner, identity, lane, request, on_finished) -> bool:
        """Run one lane job inline, then QUEUE its terminal for the next drain.

        The work runs inline so results are deterministic without threads. The terminal must not
        be, though: the broker publishes it to the mailbox, so it reaches the host on a later drain,
        never re-entering the call that submitted it. Delivering it inline collapses the window in
        which a cue is scheduled but not yet published — a real state the host spends time in, and
        one several tests exist to pin.
        """
        from saitenka.runtime import EffectError

        handler = self.job_lanes.get(lane)
        if handler is None:
            return False
        outcome, error = EffectOutcome.SUCCEEDED, None
        result = None
        try:
            result = handler(request, threading.Event())
        except Exception:  # noqa: BLE001  # the broker turns a handler failure into an outcome
            outcome, error = EffectOutcome.FAILED, EffectError.INTERNAL
        completion = EffectFinished(
            EffectId(0), owner, identity, outcome, result=result, error=error
        )
        if lane == "cue-annotation":
            on_finished(completion)
        else:
            self.pending_jobs.append((on_finished, completion))
        return True

    def deliver_runtime_jobs(self) -> int:
        """Drain queued lane terminals, including any a terminal itself produces."""
        delivered = 0
        while self.pending_jobs:
            on_finished, completion = self.pending_jobs.pop(0)
            on_finished(completion)
            delivered += 1
        return delivered

    def close_runtime_job_lane(self, name, timeout=2.0) -> bool:  # noqa: ARG002
        return self.job_lanes.pop(name, None) is not None

    def submit_runtime_mpv(self, *, identity, command, on_finished, **_kwargs) -> bool:
        """Admit a correlated command, completing it inline unless the test wants to place the
        terminal itself.

        Inline is the default so a test that does not care about ownership timing reads exactly as
        it did when the command was synchronous. `correlate_commands = True` queues instead, which
        is the only way to observe the mid-flight window or place a late result.
        """
        if any(refused in str(identity) for refused in self.refused_identities):
            return False
        self.submitted.append((identity, command, on_finished))
        if not self.correlate_commands:
            self.deliver_runtime_mpv()
        return True

    def deliver_runtime_mpv(self, *, match=None, outcome=None, result=None) -> bool:
        """Complete the oldest outstanding correlated command, or the oldest matching `match`.

        `match` names the property or overlay a test means (checked against every element of the
        command), because more than one subsystem correlates its writes and a positional "the next
        one" would silently retarget whenever another starts.

        Delivery runs it through `command`, not around it: a fake whose correlated path skips its
        own mpv-state simulation reports a stale readback, which presents as a production ownership
        regression rather than as the fake-only fault it is.
        """
        from saitenka.runtime import EffectError, EffectFinished, EffectId, Owner

        index = next(
            (
                i
                for i, (_identity, command, _cb) in enumerate(self.submitted)
                if match is None or match in command
            ),
            None,
        )
        if index is None:
            return False
        identity, command, on_finished = self.submitted.pop(index)
        outcome = outcome or EffectOutcome.SUCCEEDED
        error = None
        if outcome is EffectOutcome.SUCCEEDED:
            try:
                reply = self.command(*command)
            except Exception:  # noqa: BLE001  # the gateway reports a dead pipe, it never raises
                outcome, error = EffectOutcome.FAILED, EffectError.DISCONNECTED
            else:
                if result is None and isinstance(reply, dict):
                    result = reply.get("data")
                if isinstance(reply, dict) and reply.get("error") not in {None, "success"}:
                    # Same mapping the real gateway applies (`MpvGateway._reply`); a fake that
                    # collapses every reply error to one code hides which failure a caller saw.
                    outcome = EffectOutcome.FAILED
                    error = {
                        "disconnected": EffectError.DISCONNECTED,
                        "timeout": EffectError.TIMEOUT,
                        "overloaded": EffectError.OVERLOADED,
                    }.get(reply.get("error"), EffectError.INVALID_RESULT)
        on_finished(
            EffectFinished(
                EffectId(0), Owner.SUBTITLE, identity, outcome, result=result, error=error
            )
        )
        return True

    def schedule_runtime_timer(self, *, timer, identity, due_at, on_finished, **_kwargs) -> bool:
        self.timers[timer] = (identity, due_at, on_finished)
        return True

    def cancel_runtime_timer(self, timer) -> bool:
        return self.timers.pop(timer, None) is not None

    def fire_runtime_timer(self, timer) -> bool:
        from saitenka.runtime import EffectFinished, EffectId, Owner

        entry = self.timers.pop(timer, None)
        if entry is None:
            return False
        identity, _due_at, on_finished = entry
        on_finished(EffectFinished(EffectId(0), Owner.SUBTITLE, identity, EffectOutcome.SUCCEEDED))
        return True

    def command(self, *args):
        self.commands.append(args)
        if args and args[0] == "get_property":
            if self.get_property_error is not None:
                return {"error": self.get_property_error}
            return {"error": "success", "data": self.props.get(args[1])}
        if args and args[0] == "set_property" and self.set_property_exception is not None:
            raise self.set_property_exception
        if args and args[0] == "set_property" and self.set_property_error is not None:
            return {"error": self.set_property_error}
        if args[:2] == ("set_property", "sub-visibility"):
            self.props["sub-visibility"] = args[2]
        if args and args[0] == "overlay-add" and self.overlay_add_error is not None:
            return {"error": self.overlay_add_error}
        if args[:1] == ("osd-overlay",) and args[-1] is True:
            # `compute_bounds`: mpv lays the payload out through its OSD libass and answers with the
            # box. No fake can lay text out, so the box is the test's to state — `None` until one
            # does, which is the same "not a box" every caller must already survive.
            return {"error": "success", "data": self.osd_bounds}
        return {"error": "success", "data": None}

    def close(self) -> None:
        pass


class FakeBackend:
    def __init__(self) -> None:
        self.requests: list[GeometryRequest] = []
        self.closed = False
        self.error: Exception | None = None
        self.token_index_offset = 0
        #: `None` echoes the request's palette, which is what the real backend does
        #: (`libass_backend._token_geometry` copies the key straight through). A fake that answered
        #: a constant instead is how the palette shipped reading zero for every cue with the whole
        #: suite green: nothing downstream of the request was ever driven by the request.
        self.font_name: str | None = None
        self.font_size: float | None = None

    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        tokens = tuple(
            TokenGeometry(
                entry.event_id,
                entry.token_index + self.token_index_offset,
                Rect(100 + entry.token_index * 60, 600, 50, 40),
                (),
                entry.font_name if self.font_name is None else self.font_name,
                entry.font_size if self.font_size is None else self.font_size,
                # Solid coverage over the whole rect when the request asked for it — the real
                # backend keeps the render's own anti-aliased mask, and the shape of the bytes is
                # what the raster device consumes.
                bytes([255]) * (50 * 40) if request.keep_coverage else b"",
            )
            for entry in request.palette
        )
        return GeometrySnapshot(
            request.generation,
            request.track_id,
            request.frame_id,
            request.timestamp_ms,
            request.variant,
            tokens,
        )

    def close(self) -> None:
        self.closed = True


class _SingleTokenizer:
    name = "single"

    def tokenize(self, line, *, strip_furigana=True, merge=True):  # noqa: ARG002
        return [Token(surface=line, lemma=line, reading="", pos="名詞", start=0, end=len(line))]

    def is_content(self, _token):
        return True

    def is_skippable(self, token):
        return not token.surface.strip()

    def query_token(self, _query):
        return None

    def inflected_in(self, tokens, index):
        return tokens[index].surface

    def phrase_terms(self, _tokens, _index, _has_term):
        return None

    def merge_dict_compounds(self, tokens, _terms_exist):
        return tokens


class _MismatchedTokenizer(_SingleTokenizer):
    name = "mismatched"

    def tokenize(self, line, *, strip_furigana=True, merge=True):  # noqa: ARG002
        return [Token(surface="different", lemma=line, reading="", pos="名詞", start=0, end=1)]


class _WhitespaceTokenizer(_SingleTokenizer):
    name = "whitespace"

    def tokenize(self, line, *, strip_furigana=True, merge=True):  # noqa: ARG002
        return [
            Token(surface=char, lemma=char, reading="", pos="名詞", start=index, end=index + 1)
            for index, char in enumerate(line)
        ]


class _AllSkippableTokenizer(_SingleTokenizer):
    name = "all-skippable"

    def is_skippable(self, _token):
        return True


class _ExistsDS:
    def terms_exist(self, _forms):
        return set()

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002
        from saitenka.panel import Definition, Entry

        return Entry(headword=tok.surface, defs=[Definition("test", ["entry"])])

    def decoded_entry_count(self):
        return 0

    def rareness_rank(self, _token):  # protocol shape
        """No frequency dictionaries, so no blended rank and no pill."""
        return


def reader(
    tmp_path: Path,
    *,
    correlated_surfaces: bool = False,
    native_visible: bool = True,
    scorer=None,
    annotation_jobs: bool = False,
    annotations_ready: bool = True,
) -> tuple[TestSession, FakeIPC, FakeBackend]:
    source = tmp_path / "episode.ass"
    source.write_bytes(ASS)
    ipc = FakeIPC(annotation_jobs=annotation_jobs)
    backend = FakeBackend()
    options = ReaderOptions(
        subtitle_geometry=SubtitleGeometryOptions(native_visible=native_visible),
        prefetch=False,
    )
    # Overlay egress is a composition decision, so the surface path is only correlated when a test
    # asks for it; without it the overlay writes run inline, as they do with no gateway.
    result = build_session(
        ipc,
        runtime_submit=ipc.submit_runtime_mpv if correlated_surfaces else None,
        services=SessionServices(
            scorer=scorer,
            dictionaries=_ExistsDS() if annotations_ready else None,
        ),
        infrastructure=SessionInfrastructure(
            geometry=backend,
        ),
        options=options,
    )
    # Native geometry exists exactly when the mode does — the legacy renderer lays its own boxes out
    # and has no provider to schedule against.
    assert (result.graph.subtitle_presentation.native is not None) == native_visible
    if result.graph.subtitle_presentation.native is not None:
        # Through the production resolver, not a hand-built environment: a track load is where the
        # font set is read, and a harness that skipped it would leave every test measuring against
        # an environment the runtime never produces.
        resolve_track_fonts(ipc, ipc.query, result.graph.subtitle_presentation.native)
        result.graph.subtitle_presentation.native.set_source(source)
    return result, ipc, backend


def settle_jobs(result: TestSession, ipc: FakeIPC) -> None:
    """Let the geometry lane finish and deliver its terminals.

    Two steps because they are two facts: the work completing, and the host being told. The broker
    publishes a completion to the mailbox, so the host learns on a later drain — which is why a cue
    can be scheduled and not yet published, and why this is not folded into `wait_idle`. A legacy
    session has no lane at all, so there is nothing to settle.
    """
    if result.graph.subtitle_presentation.native is None:
        return
    assert result.graph.subtitle_presentation.native.worker.wait_idle()
    ipc.deliver_runtime_jobs()


def toasts(ipc: FakeIPC) -> list[tuple]:
    """Every notification drawn on the toast surface."""
    return [
        command
        for command in ipc.commands
        if command[0] == "overlay-add" and command[1] == OverlayId.TOAST
    ]


def painted_overlays(ipc: FakeIPC) -> list[tuple]:
    """Every `overlay-add` that draws a cue, excluding the notification surface.

    A toast is an `overlay-add` too, and a geometry refusal raises one, so a bare scan for the
    command cannot tell "no boxes were painted" from "the user was told why they were not".
    """
    return [
        command
        for command in ipc.commands
        if command[0] == "overlay-add" and command[1] != OverlayId.TOAST
    ]


def visible_pixel_changes(ipc: FakeIPC) -> list[tuple[object, object]]:
    """Every moment the command trace changed what the user can actually see.

    Neither a raw trace nor a final-state fold answers "did the picture move": the trace fails on a
    repaint that re-sends the payload already on screen, and the fold cannot see a flicker — a
    change and a change back land on the same final state. So the sequence of *effective* writes is
    the observable, and an unchanged suffix is the claim.
    """
    state: dict[object, object] = {}
    changes: list[tuple[object, object]] = []
    for command in ipc.commands:
        if command[:2] == ("set_property", "sub-visibility"):
            key, value = "sub-visibility", command[2]
        elif command and command[0] == "osd-overlay":
            key, value = ("osd-overlay", command[1]), command[2:]
        elif command and command[0] in {"overlay-add", "overlay-remove"}:
            key, value = ("overlay", command[1]), command
        else:
            continue
        if state.get(key, object()) != value:
            state[key] = value
            changes.append((key, value))
    return changes


def settle_geometry(result: TestSession, ipc: FakeIPC) -> None:
    """Advance past the batch boundary the way the next drain would.

    Geometry-input changes arm one zero-delay deadline rather than refreshing per observation, so
    a test that changes an input has to let that deadline come due before asserting on the request
    the backend received.
    """
    # Production order: the deadline armed during one drain comes due as an envelope at the head of
    # the next, and cue reconciliation settles at that drain's end. Firing after settling would let
    # reconciliation retire the deadline before it is ever delivered.
    ipc.fire_runtime_timer("subtitle:geometry-refresh")
    result.graph.cue.settle()


def test_native_visible_mode_never_adds_or_selects_generated_track(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)

    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it

    assert backend.requests
    assert [box.index for box in result.graph.subtitle_presentation.cue.current.boxes] == list(
        range(len(result.graph.subtitle_presentation.cue.current.tokens))
    )
    result.graph.tooltip.select(0)
    result.graph.subtitle_presentation.draw()
    focus = [
        command for command in ipc.commands if command[:3] == ("osd-overlay", 1001, "ass-events")
    ]
    assert len(focus) == 1
    assert focus[0][4:6] == (1280, 720)
    assert ("set_property", "sub-visibility", True) in ipc.commands
    assert not any(command and command[0] in {"sub-add", "sub-remove"} for command in ipc.commands)
    assert not any(
        command[:2] == ("set_property", "sid") for command in ipc.commands if len(command) >= 2
    )
    result.close()
    assert backend.closed


def test_visible_cue_cache_miss_keeps_native_pixels_until_geometry_is_ready(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)

    result.graph.cue.set_subtitle("猫を見る")

    assert result.graph.subtitle_presentation.native is not None
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-geometry-cache-miss"
    )
    assert result.graph.subtitle_presentation.native.status.owner == "native"
    assert ("set_property", "sub-visibility", True) in ipc.commands
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    assert result.graph.subtitle_presentation.cue.current.boxes == []
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    assert result.graph.subtitle_presentation.cue.current.boxes
    result.close()


@pytest.mark.parametrize("data", [None, ""])
def test_successful_empty_ass_full_reply_proves_mpv_capability(
    tmp_path: Path, data: object
) -> None:
    result, _ipc, _backend = reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None

    result.graph.subtitle_presentation.native.observe_ass_full_reply(
        {"error": "success", "data": data}
    )

    assert (
        result.graph.subtitle_presentation.native.ass_full_capability == AssFullCapability.SUPPORTED
    )
    result.close()


def test_temporarily_unavailable_ass_full_reply_remains_retryable(tmp_path: Path) -> None:
    result, _ipc, _backend = reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None

    result.graph.subtitle_presentation.native.observe_ass_full_reply(
        {"error": "property unavailable"}
    )

    assert (
        result.graph.subtitle_presentation.native.ass_full_capability == AssFullCapability.UNKNOWN
    )
    result.close()


def test_missing_ass_full_property_disables_only_native_geometry(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.observe_ass_full_reply(
        {"error": "property not found"}
    )

    result.graph.cue.set_subtitle("猫を見る")

    assert (
        result.graph.subtitle_presentation.native.ass_full_capability
        == AssFullCapability.UNSUPPORTED
    )
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-ass-full-unsupported"
    )
    assert result.graph.subtitle_presentation.native.status.owner == "native"
    assert backend.requests == []
    assert not painted_overlays(ipc)
    result.close()


def _visibility_asserts(ipc) -> int:
    return sum(1 for command in ipc.commands if command == ("set_property", "sub-visibility", True))


def test_native_visibility_is_reasserted_after_track_reconfigure(tmp_path: Path) -> None:
    """A new track means the established flag is about pixels from the old one, so it is re-proved.

    The trigger is the selection moving, not the call: `activate` is idempotent by contract, and a
    caller cannot know whether the ground moved under it.
    """
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    ipc.commands.clear()

    ipc.props["sid"] = 5
    result.graph.subtitle_presentation.pipeline.activate(
        result.graph.subtitle_presentation.target(), draw=result.graph.subtitle_presentation.draw
    )
    ipc.props["sid"] = 6
    result.graph.subtitle_presentation.pipeline.activate(
        result.graph.subtitle_presentation.target(), draw=result.graph.subtitle_presentation.draw
    )

    assert _visibility_asserts(ipc) == 2
    result.close()


def test_reconfiguring_the_same_track_does_not_reassert(tmp_path: Path) -> None:
    """The negative control, and the reason the renderer decides rather than the caller: a repeat
    with nothing changed must not spend an mpv round-trip proving what it already proved."""
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    ipc.props["sid"] = 5
    result.graph.subtitle_presentation.pipeline.activate(
        result.graph.subtitle_presentation.target(), draw=result.graph.subtitle_presentation.draw
    )
    ipc.commands.clear()

    result.graph.subtitle_presentation.pipeline.activate(
        result.graph.subtitle_presentation.target(), draw=result.graph.subtitle_presentation.draw
    )

    assert _visibility_asserts(ipc) == 0
    result.close()


def test_same_session_reconnect_reasserts_native_and_preserves_restore_baseline(
    tmp_path: Path,
) -> None:
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    renderer = result.graph.subtitle_presentation.pipeline.renderer
    assert isinstance(renderer, NativeVisibleRenderer)
    ipc.props["sub-visibility"] = False
    ipc.commands.clear()

    renderer.connection_replaced(result.graph.subtitle_presentation.target())

    assert renderer.ownership_state.owner.value == "native"
    assert renderer.ownership_state.context.connection_epoch == 1
    assert ("set_property", "sub-visibility", True) in ipc.commands
    ipc.commands.clear()
    result.close()
    assert ("set_property", "sub-visibility", False) in ipc.commands


def test_missing_source_keeps_native_pixels_without_hits(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(tmp_path / "missing.ass")

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    result.graph.subtitle_presentation.native.apply(result.graph.cue.geometry_observation())

    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-source-unavailable"
    )
    assert result.graph.subtitle_presentation.native.status.owner == "native"
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    result.graph.subtitle_presentation.draw()
    assert result.graph.subtitle_presentation.cue.current.boxes == []
    result.close()


def test_oversized_source_hands_the_pixels_to_legacy_without_provider_work(tmp_path: Path) -> None:
    """Too large is a property of the file, so it selects the renderer — but it must still not cost
    a provider round trip, which is what this asserts beyond the owner."""
    result, _ipc, backend = reader(tmp_path)
    source = tmp_path / "oversized.ass"
    source.write_bytes(b"x" * (MAX_ASS_SOURCE_BYTES + 1))
    assert result.graph.subtitle_presentation.native is not None

    result.graph.subtitle_presentation.native.set_source(source)
    result.graph.cue.set_subtitle("猫を見る")

    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-source-too-large"
    )
    assert backend.requests == []
    assert result.graph.subtitle_presentation.native.status.owner == "legacy"
    result.close()


def test_geometry_availability_never_changes_the_pixel_owner(tmp_path: Path) -> None:
    """WP4.3's gate, asserted on the owner itself rather than on the absence of a write.

    Whether hit boxes exist is an interaction fact; who owns the pixels is a visibility fact. The
    pure FSM keeps them apart, but that is not evidence the system does — the SessionController-side degrade
    path clears `boxes` and drives the FSM in the same call, which is exactly where the two would
    get welded together.
    """
    result, ipc, backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    renderer = result.graph.subtitle_presentation.pipeline.renderer
    assert isinstance(renderer, NativeVisibleRenderer)
    assert renderer.ownership_state.owner is PixelOwner.NATIVE
    assert result.graph.subtitle_presentation.cue.current.boxes

    backend.error = RuntimeError("font provider unavailable")
    result.graph.subtitle_presentation.pipeline.invalidate()
    result.graph.subtitle_presentation.native.worker.invalidate_cache()
    assert result.graph.subtitle_presentation.native.schedule(
        result.graph.cue.geometry_observation()
    )
    settle_jobs(result, ipc)
    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )

    assert renderer.ownership_state.owner is PixelOwner.NATIVE  # unproved boxes go, pixels stay
    assert not renderer.ownership_state.geometry_ready
    assert result.graph.subtitle_presentation.cue.current.boxes == []

    backend.error = None
    result.graph.subtitle_presentation.pipeline.invalidate()
    result.graph.subtitle_presentation.native.worker.invalidate_cache()
    assert result.graph.subtitle_presentation.native.schedule(
        result.graph.cue.geometry_observation()
    )
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    assert renderer.ownership_state.owner is PixelOwner.NATIVE  # recovery does not re-own either
    assert result.graph.subtitle_presentation.cue.current.boxes
    result.close()


@pytest.mark.parametrize("native_visible", [True, False], ids=["native", "legacy"])
def test_navigation_lands_on_the_target_cue_under_either_renderer(
    tmp_path: Path, *, native_visible: bool
) -> None:
    """WP4.5's one navigation interaction contract, replayed under both renderers.

    Which renderer owns the pixels is a visibility decision; where a navigation key lands the user
    is not. The two paths diverge everywhere below that — native publishes a hit map from a
    provider, legacy lays the boxes out itself — so a navigation regression in one is invisible to
    every test written against the other.
    """
    result, ipc, _backend = reader(tmp_path, native_visible=native_visible)
    result.graph.track_commands.navigation.current.sub_index = CueIndex(
        [Cue(1.0, 3.0, "猫を見る"), Cue(4.0, 6.0, "犬も見る")]
    )
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    result.graph.tooltip.select(0)

    assert result.graph.subtitle_navigation.seek(SeekCue(1, result.graph.cue.revision))

    assert (
        result.graph.playback.cue.text == "犬も見る"
    )  # the target cue, drawn from the index without waiting
    assert (
        result.graph.tooltip.observation().selected == -1
    )  # …with the previous cue's interaction state gone
    assert [token.surface for token in result.graph.subtitle_presentation.cue.current.tokens] == [
        "犬",
        "も",
        "見る",
    ]
    assert ("sub-seek", "1") in [command[:2] for command in ipc.commands if len(command) >= 2]
    result.close()


def test_every_geometry_cache_miss_reports_a_bounded_text_free_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP4.4's observability obligation, as two claims rather than one.

    A miss with no reason is the silent give-up this gate exists to forbid, so each one names why —
    and the four here are four different whys, not one repeated. A hit needs no reason and carries
    none.

    The second claim is the reason's vocabulary. A cache key is derived from cue text and a source
    path, which makes this the one diagnostic on the path with a route to that content, so each
    reason has to come from the closed set rather than describe what was actually looked up.
    """
    from saitenka.app.subtitle_geometry_diagnostics import GeometryCacheReason

    spans = record_spans(monkeypatch)
    result, ipc, _backend = reader(tmp_path)
    text = "猫を見る"
    assert result.graph.subtitle_presentation.native is not None

    result.graph.cue.set_subtitle(text)  # the artifact was installed moments ago
    settle_jobs(result, ipc)
    result.graph.subtitle_presentation.native.schedule(
        result.graph.cue.geometry_observation()
    )  # same epoch, key never cached
    settle_jobs(result, ipc)
    result.graph.subtitle_presentation.native.invalidate(live=True)  # a render input moved
    result.graph.subtitle_presentation.native.schedule(result.graph.cue.geometry_observation())
    settle_jobs(result, ipc)
    second = tmp_path / "other.ass"
    second.write_bytes(ASS)
    result.graph.subtitle_presentation.native.set_source(second)  # a different artifact entirely
    result.graph.subtitle_presentation.native.schedule(result.graph.cue.geometry_observation())
    settle_jobs(result, ipc)

    misses = [span["attrs"] for span in spans if span["name"] == "subtitle_geometry_cache"]
    assert {attrs["outcome"] for attrs in misses} == {"miss"}
    assert [attrs["reason"] for attrs in misses] == [
        GeometryCacheReason.SOURCE_CHANGED,
        GeometryCacheReason.FIRST_SEEN,
        GeometryCacheReason.RENDER_INPUT_CHANGED,
        GeometryCacheReason.SOURCE_CHANGED,
    ]
    exported = {str(value) for attrs in misses for value in attrs.values()}
    assert not any(text in value or str(tmp_path) in value for value in exported)
    result.close()


def test_a_late_valid_hit_map_restores_interaction_without_changing_visible_pixels(
    tmp_path: Path,
) -> None:
    """WP4.4's positive case, which quarantining stale results does not cover.

    Retiring a late result is only half the contract: a result that is late but still *valid* for
    the cue on screen has to be taken. The failure mode it guards is the mirror of the quarantine —
    a restore that repaints, so the user sees the cue flicker to prove a hit map they never see.
    """
    result, ipc, backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert result.graph.subtitle_presentation.cue.current.boxes

    # A provider failure retires the hit boxes for the cue that is still displayed.
    backend.error = RuntimeError("font provider unavailable")
    result.graph.subtitle_presentation.pipeline.invalidate()
    result.graph.subtitle_presentation.native.worker.invalidate_cache()
    assert result.graph.subtitle_presentation.native.schedule(
        result.graph.cue.geometry_observation()
    )
    settle_jobs(result, ipc)
    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )
    assert result.graph.subtitle_presentation.cue.current.boxes == []
    degraded = visible_pixel_changes(ipc)

    # The retry lands while that same cue is still on screen: interaction comes back…
    backend.error = None
    result.graph.subtitle_presentation.pipeline.invalidate()
    result.graph.subtitle_presentation.native.worker.invalidate_cache()
    assert result.graph.subtitle_presentation.native.schedule(
        result.graph.cue.geometry_observation()
    )
    settle_jobs(result, ipc)

    assert result.graph.subtitle_presentation.native.status.geometry_ready
    assert [box.index for box in result.graph.subtitle_presentation.cue.current.boxes] == list(
        range(len(result.graph.subtitle_presentation.cue.current.tokens))
    )
    assert visible_pixel_changes(ipc) == degraded  # …and nothing the user can see moved
    result.close()


def test_provider_failure_preserves_hover_pause_while_boxes_are_removed(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    Driver(result).move_to_word(0)
    ipc.commands.clear()
    backend.error = RuntimeError("font provider unavailable")
    result.graph.subtitle_presentation.pipeline.invalidate()
    result.graph.subtitle_presentation.native.worker.invalidate_cache()

    assert result.graph.subtitle_presentation.native.schedule(
        result.graph.cue.geometry_observation()
    )
    settle_jobs(result, ipc)
    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )

    assert ("set_property", "pause", False) not in ipc.commands
    assert result.graph.tooltip.observation().selected == 0
    assert result.graph.subtitle_presentation.cue.current.boxes == []
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    result.graph.tooltip.release_pause_claim()
    result.close()


def test_cache_miss_preserves_hover_pause_while_boxes_are_removed(tmp_path: Path) -> None:
    """The other half of WP4.3's "removes unproved hit boxes only".

    A miss and a provider failure reach the same degrade, but from opposite sides: the failure
    arrives with a verdict, the miss only says "not yet". The miss is the one that runs on every
    ordinary cue, and it is the one that degrades *before* the work is even queued — so if the
    removal were ever wider than the boxes, this is the path the user would feel it on.
    """
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert result.graph.subtitle_presentation.native.status.geometry_ready
    Driver(result).move_to_word(0)
    ipc.commands.clear()
    result.graph.subtitle_presentation.pipeline.invalidate()
    result.graph.subtitle_presentation.native.worker.invalidate_cache()

    assert result.graph.subtitle_presentation.native.schedule(
        result.graph.cue.geometry_observation()
    )  # degrades on the miss, before the lane runs

    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-geometry-cache-miss"
    )
    assert result.graph.subtitle_presentation.cue.current.boxes == []
    assert result.graph.tooltip.observation().selected == 0
    assert ("set_property", "pause", False) not in ipc.commands
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    result.graph.tooltip.release_pause_claim()
    result.close()


def test_failed_provider_keeps_native_pixels_while_recovery_is_pending(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    backend.error = RuntimeError("font provider unavailable")
    result.graph.subtitle_presentation.pipeline.invalidate()
    result.graph.subtitle_presentation.native.worker.invalidate_cache()
    assert result.graph.subtitle_presentation.native.schedule(
        result.graph.cue.geometry_observation()
    )
    settle_jobs(result, ipc)
    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )
    ipc.commands.clear()
    backend.error = None
    ipc.props["sub-start"] = None
    ipc.props["sub-end"] = None

    result.graph.subtitle_presentation.native.refresh(result.graph.cue.geometry_observation())

    assert result.graph.subtitle_presentation.native.status.owner == "native"
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    result.close()


def test_completed_provider_failure_survives_refresh_before_apply(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    backend.error = RuntimeError("font provider unavailable")
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    ipc.props["sub-start"] = None
    ipc.props["sub-end"] = None
    ipc.commands.clear()

    result.graph.subtitle_presentation.native.refresh(result.graph.cue.geometry_observation())

    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "geometry-provider-failed"
    )
    assert result.graph.subtitle_presentation.native.status.owner == "native"
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )
    result.close()


def test_annotation_free_cue_is_a_valid_noninteractive_recovery(
    tmp_path: Path,
) -> None:
    result, ipc, backend = reader(tmp_path)
    backend.error = RuntimeError("font provider unavailable")
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )
    original_tokenizer = result.graph.profile.profile.tokenizer
    result.graph.profile.profile.use_tokenizer(_AllSkippableTokenizer())
    ipc.commands.clear()

    result.graph.cue.set_subtitle("猫を見る")

    assert result.graph.subtitle_presentation.native.status.owner == "native"
    assert result.graph.subtitle_presentation.native.status.fallback_reason is None
    assert result.graph.subtitle_presentation.native.status.geometry_ready is False
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    result.graph.profile.profile.use_tokenizer(original_tokenizer)
    ipc.commands.clear()
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )
    assert result.graph.subtitle_presentation.native.status.owner == "native"
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    result.close()


def test_empty_cue_does_not_claim_recovery_from_provider_failure(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    backend.error = RuntimeError("font provider unavailable")
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )
    ipc.commands.clear()

    result.graph.cue.set_subtitle("")
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )

    assert result.graph.subtitle_presentation.native.status.owner == "native"
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "geometry-provider-failed"
    )
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    result.close()


def test_blank_interval_does_not_repeat_provider_failure_diagnostic(tmp_path: Path, caplog) -> None:
    result, ipc, backend = reader(tmp_path)
    backend.error = RuntimeError("font provider unavailable")
    assert result.graph.subtitle_presentation.native is not None
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="saitenka.app.native_subtitles"):
        result.graph.cue.set_subtitle("猫を見る")
        settle_jobs(result, ipc)
        assert not result.graph.subtitle_presentation.native.apply(
            result.graph.cue.geometry_observation()
        )
        result.graph.cue.set_subtitle("")
        result.graph.cue.set_subtitle("猫を見る")
        settle_jobs(result, ipc)
        assert not result.graph.subtitle_presentation.native.apply(
            result.graph.cue.geometry_observation()
        )

    assert [record.getMessage() for record in caplog.records] == [
        ("native subtitle interaction unavailable: geometry-provider-failed detail=provider-error")
    ]
    result.close()


def test_a_non_ass_source_is_drawn_by_legacy_and_stays_scannable(tmp_path: Path) -> None:
    """The point of handing an unusable source to legacy: an .srt keeps its hit boxes.

    It used to keep mpv's pixels and produce none, which left the episode unscannable for its whole
    run — the boxes here are the whole reason the renderer switches."""
    result, _ipc, backend = reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None

    result.graph.subtitle_presentation.native.set_source(tmp_path / "episode.srt", live=True)
    result.graph.cue.set_subtitle("猫を見る")

    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-source-not-authored-ass"
    )
    assert backend.requests == []  # no provider work for a source it can never accept
    assert result.graph.subtitle_presentation.native.status.owner == "legacy"
    result.graph.subtitle_presentation.draw()
    assert result.graph.subtitle_presentation.cue.current.boxes != []
    result.close()


def test_unsupported_transition_is_not_counted_as_provider_failure(tmp_path: Path) -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from saitenka import otel_metrics

    result, _ipc, _backend = reader(tmp_path)
    metric_reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[metric_reader])
    otel_metrics.register(metric_reader, provider.get_meter("test"))
    try:
        assert result.graph.subtitle_presentation.native is not None
        source = tmp_path / "episode.srt"
        result.graph.subtitle_presentation.native.set_source(source, live=True)
        result.graph.subtitle_presentation.native.set_source(source, live=True)

        failures = otel_metrics.snapshot().get("saitenka.subtitle_geometry.failures")
        assert failures is None or failures["value"] == 0
    finally:
        result.close()
        otel_metrics.unregister()
        provider.shutdown()


def test_catastrophic_pixel_fallback_records_one_bounded_metric(tmp_path: Path) -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from saitenka import otel_metrics

    result, ipc, _backend = reader(tmp_path)
    metric_reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[metric_reader])
    otel_metrics.register(metric_reader, provider.get_meter("test"))
    try:
        ipc.set_property_error = "rejected"
        result.graph.cue.set_subtitle("猫を見る")
        result.graph.subtitle_presentation.pipeline.activate(
            result.graph.subtitle_presentation.target(),
            draw=result.graph.subtitle_presentation.draw,
        )

        renderer = result.graph.subtitle_presentation.pipeline.renderer
        assert isinstance(renderer, NativeVisibleRenderer)
        assert renderer.ownership_state.owner.value == "legacy"
        assert (
            otel_metrics.snapshot()["saitenka.subtitle_pixels.catastrophic_fallbacks"]["value"] == 1
        )
        ipc.set_property_error = None
        renderer.suspend_for_overlay(result.graph.subtitle_presentation.target())
        renderer.resume_after_overlay(result.graph.subtitle_presentation.target())

        assert renderer.ownership_state.owner.value == "legacy"
        assert (
            otel_metrics.snapshot()["saitenka.subtitle_pixels.catastrophic_fallbacks"]["value"] == 1
        )
    finally:
        result.close()
        otel_metrics.unregister()
        provider.shutdown()


def test_a_source_geometry_can_never_use_hands_the_pixels_to_legacy(tmp_path: Path) -> None:
    """An .srt otherwise leaves mpv drawing with no hit boxes for the whole episode — unscannable,
    and unrecoverable without changing tracks. The reason is a property of the source, so this
    selects a renderer once per track rather than reacting to a geometry outcome."""
    result, _ipc, _backend = reader(tmp_path)
    renderer = NativeVisibleRenderer()
    result.graph.subtitle_presentation.pipeline.renderer = renderer
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(tmp_path / "episode.srt", live=True)
    result.graph.playback.install_seed({"sub-text": "猫を見る"})

    result.graph.subtitle_presentation.pipeline.draw_current(
        result.graph.subtitle_presentation.target()
    )

    assert renderer.ownership_state.context.mode.value == "legacy-overlay"
    result.close()


def test_the_track_load_reset_does_not_hand_the_pixels_to_legacy(tmp_path: Path) -> None:
    """`set_source(None)` is the reset every track load runs before the real source arrives, so
    treating it as unsupported would flap the renderer twice per episode. The negative control for
    the test above: `subtitle-source-unavailable` is deliberately not an unsupported-source reason.
    """
    result, _ipc, _backend = reader(tmp_path)
    renderer = NativeVisibleRenderer()
    result.graph.subtitle_presentation.pipeline.renderer = renderer
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(None, live=True)
    result.graph.playback.install_seed({"sub-text": "猫を見る"})

    result.graph.subtitle_presentation.pipeline.draw_current(
        result.graph.subtitle_presentation.target()
    )

    assert renderer.ownership_state.context.mode.value == "native-visible"
    result.close()


def test_switching_from_an_srt_to_an_ass_returns_the_pixels_to_native(tmp_path: Path) -> None:
    """The switch is per selection and reversible: an unusable source parks the pixels with legacy,
    and selecting an authored `.ass` takes them back rather than latching the fallback."""
    result, ipc, backend = reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(tmp_path / "episode.srt", live=True)
    result.graph.cue.set_subtitle("猫を見る")
    result.graph.subtitle_presentation.draw()
    assert result.graph.subtitle_presentation.native.status.owner == "legacy"

    source = tmp_path / "episode.ass"
    source.write_bytes(ASS)
    result.graph.subtitle_presentation.native.set_source(source, live=True)
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    assert backend.requests
    assert ("set_property", "sub-visibility", True) in ipc.commands
    assert result.graph.subtitle_presentation.native.status.fallback_reason is None
    result.close()


def test_sub_delay_during_gap_preserves_ready_lookahead_for_next_cue(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    source = tmp_path / "episode.ass"
    source.write_bytes(ASS_TWO)
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)
    result.graph.track_commands.navigation.current.sub_index = CueIndex(
        (Cue(1.0, 3.0, "猫を見る"), Cue(4.0, 6.0, "犬も見る"))
    )

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    assert len(backend.requests) == 2
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    result.graph.cue.set_subtitle("")
    result.graph.playback.install_seed(
        ipc.props | {"sub-text": "", "sub-start": None, "sub-end": None}
    )

    result.graph.playback.observe_event({"name": "sub-delay", "data": -6.0})
    result.graph.cue.settle()

    assert len(backend.requests) == 2
    ipc.commands.clear()
    ipc.props.update(
        {
            "sub-text/ass-full": "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0000,0000,0000,,犬も見る",
            "sub-start": 4.0,
            "sub-end": 6.0,
            "time-pos": 4.2,
        }
    )
    result.graph.playback.install_seed(ipc.props)

    result.graph.cue.set_subtitle("犬も見る")

    assert ("set_property", "sub-visibility", False) not in ipc.commands
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    assert len(backend.requests) == 2
    stats = result.graph.subtitle_presentation.native.worker.stats
    assert (stats.ready_before_presented, stats.presented) == (1, 2)
    result.close()


def test_prefetched_hit_restores_native_pixels_after_provider_failure(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    source = tmp_path / "episode.ass"
    source.write_bytes(ASS_TWO)
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)
    result.graph.track_commands.navigation.current.sub_index = CueIndex(
        (Cue(1.0, 3.0, "猫を見る"), Cue(4.0, 6.0, "犬も見る"))
    )

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    backend.error = RuntimeError("font provider unavailable")
    ipc.props["osd-dimensions"] = {"w": 1279, "h": 720}
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )
    assert result.graph.subtitle_presentation.native.status.owner == "native"
    backend.error = None
    ipc.props.update(
        {
            "osd-dimensions": {"w": 1280, "h": 720},
            "sub-text/ass-full": (
                "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0000,0000,0000,,犬も見る"
            ),
            "sub-start": 4.0,
            "sub-end": 6.0,
            "time-pos": 4.2,
        }
    )
    ipc.commands.clear()

    result.graph.cue.set_subtitle("犬も見る")

    assert result.graph.subtitle_presentation.native.status.owner == "native"
    assert result.graph.subtitle_presentation.native.status.fallback_reason is None
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    result.close()


def test_lookahead_caches_start_and_end_transitions_for_overlapping_events(
    tmp_path: Path,
) -> None:
    result, ipc, backend = reader(tmp_path)
    source = tmp_path / "episode.ass"
    source.write_bytes(
        ASS.replace(
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫を見る\n".encode(),
            (
                "Dialogue: 0,0:00:01.00,0:00:05.00,Default,,0,0,0,,猫を見る\n"
                "Dialogue: 1,0:00:03.00,0:00:07.00,Default,,0,0,0,,犬も見る\n"
            ).encode(),
        )
    )
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)
    result.graph.track_commands.navigation.current.sub_index = CueIndex(
        (Cue(1.0, 5.0, "猫を見る"), Cue(3.0, 7.0, "犬も見る"))
    )
    first = "Dialogue: 0,0:00:01.00,0:00:05.00,Default,,0000,0000,0000,,猫を見る"
    second = "Dialogue: 1,0:00:03.00,0:00:07.00,Default,,0000,0000,0000,,犬も見る"
    ipc.props.update({"sub-text/ass-full": first, "sub-end": 5.0})
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    assert len(backend.requests) == 3
    ipc.commands.clear()

    ipc.props.update(
        {
            "sub-text/ass-full": f"{first}\n{second}",
            "sub-start": 1.0,
            "sub-end": 5.0,
            "time-pos": 3.2,
        }
    )
    result.graph.cue.set_subtitle("猫を見る\n犬も見る")
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    ipc.props.update(
        {
            "sub-text/ass-full": second,
            "sub-start": 3.0,
            "sub-end": 7.0,
            "time-pos": 5.2,
        }
    )
    result.graph.cue.set_subtitle("犬も見る")

    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    assert len(backend.requests) == 3
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    result.close()


def test_invalid_lookahead_is_only_a_cache_miss_for_valid_current_frame(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None
    result.graph.track_commands.navigation.current.sub_index = CueIndex(
        (Cue(1.0, 3.0, "猫を見る"), Cue(8.0, 9.0, "不存在"))
    )

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    assert len(backend.requests) == 1
    assert result.graph.subtitle_presentation.native.status.fallback_reason is None
    result.close()


def test_native_lookahead_uses_the_single_annotation_coordinator(
    tmp_path: Path,
) -> None:
    result, ipc, backend = reader(tmp_path, annotation_jobs=True)
    source = tmp_path / "episode.ass"
    source.write_bytes(
        ASS.replace(
            b"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,\xe7\x8c\xab\xe3\x82\x92\xe8\xa6\x8b\xe3\x82\x8b\n",
            b"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,\xe7\x8c\xab\xe3\x82\x92\xe8\xa6\x8b\xe3\x82\x8b\n"
            b"Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,\xe7\x8a\xac\n",
        )
    )
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)
    result.graph.track_commands.navigation.current.sub_index = CueIndex(
        (Cue(1.0, 3.0, "猫を見る"), Cue(4.0, 6.0, "犬"))
    )

    result.graph.profile_integration.enable_async_annotation()
    result.graph.profile_integration.dependencies_changed()

    result.graph.cue.set_subtitle("猫を見る")
    result.graph.interaction.settle()
    settle_jobs(result, ipc)

    assert any(request.timestamp_ms == 4_001 for request in backend.requests)
    result.close()


@pytest.mark.parametrize(
    ("time_pos", "sub_delay", "expected_timestamp_ms"),
    [
        (11.25, 10.0, 1_250),
        (1.25, -6.0, 7_250),
        (None, 10.0, 1_000),
        (None, -6.0, 1_000),
    ],
)
def test_geometry_request_uses_delay_adjusted_subtitle_timestamp(
    tmp_path: Path,
    time_pos: float | None,
    sub_delay: float,
    expected_timestamp_ms: int,
) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["time-pos"] = time_pos
    ipc.props["sub-delay"] = sub_delay

    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)

    assert backend.requests[0].timestamp_ms == expected_timestamp_ms
    result.close()


def test_sub_delay_property_event_records_the_derived_subtitle_clock(
    tmp_path: Path, monkeypatch
) -> None:
    from saitenka import otel_metrics

    captured: list[dict[str, object]] = []

    class RecordingSpan:
        def set(self, key: str, value: object) -> None:
            captured[-1][key] = value

    @contextmanager
    def record_span(name: str, **attributes: str):
        if name == "subtitle_geometry_clock":
            captured.append(dict(attributes))
        yield RecordingSpan()

    monkeypatch.setattr(otel_metrics, "traced", record_span)
    result, ipc, _backend = reader(tmp_path)
    result.graph.playback.install_seed(ipc.props | {"time-pos": 11.25})

    result.graph.playback.observe_event({"name": "sub-delay", "data": 10.0})

    assert captured == [
        {
            "outcome": "ready",
            "video_time_ms": 11_250,
            "sub_delay_ms": 10_000,
            "subtitle_time_ms": 1_250,
        }
    ]
    result.close()


def test_sub_delay_event_reports_unavailable_clock_without_timing_sources(
    tmp_path: Path, monkeypatch
) -> None:
    from saitenka import otel_metrics

    captured: list[dict[str, object]] = []

    class RecordingSpan:
        def set(self, key: str, value: object) -> None:
            captured[-1][key] = value

    @contextmanager
    def record_span(name: str, **attributes: str):
        if name == "subtitle_geometry_clock":
            captured.append(dict(attributes))
        yield RecordingSpan()

    monkeypatch.setattr(otel_metrics, "traced", record_span)
    result, ipc, _backend = reader(tmp_path)
    result.graph.playback.install_seed(ipc.props | {"time-pos": None, "sub-start": None})

    result.graph.playback.observe_event({"name": "sub-delay", "data": 10.0})

    assert captured == [{"outcome": "invalid"}]
    result.close()


def test_instant_navigation_uses_target_cue_timing_not_stale_mpv_properties(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    source = tmp_path / "episode.ass"
    source.write_bytes(ASS_TWO)
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)
    result.graph.track_commands.navigation.current.geometry_cue_hint = Cue(4.0, 6.0, "犬も見る")

    result.graph.cue.set_subtitle("犬も見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)

    assert backend.requests[0].frame_id.active_event_ids[0].start_ms == 4_000
    result.close()


def test_source_clear_is_a_generation_boundary_and_keeps_native_pixels(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    old_generation = result.graph.subtitle_presentation.pipeline.generation
    ipc.commands.clear()

    result.graph.subtitle_presentation.native.set_source(None, live=True)

    assert result.graph.subtitle_presentation.pipeline.generation == old_generation + 1
    assert result.graph.subtitle_presentation.pipeline.current is None
    assert ("osd-overlay", 1001, "none", "") in ipc.commands
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    assert result.graph.subtitle_presentation.cue.current.boxes == []
    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )
    result.close()


def test_tokenizer_change_rebuilds_geometry_after_new_tokens_land(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    assert len(result.graph.subtitle_presentation.cue.current.boxes) > 1

    result.graph.profile.profile.use_tokenizer(_SingleTokenizer())
    result.graph.profile_integration.retokenize_current_cue()

    assert result.graph.subtitle_presentation.cue.current.boxes == []
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    assert (
        len(result.graph.subtitle_presentation.cue.current.boxes)
        == len(backend.requests[-1].palette)
        == 1
    )
    result.close()


def test_mismatched_token_annotation_fails_closed(tmp_path: Path) -> None:
    result, _ipc, backend = reader(tmp_path)
    result.graph.profile.profile.use_tokenizer(_MismatchedTokenizer())

    result.graph.cue.set_subtitle("猫を見る")

    assert backend.requests == []
    assert result.graph.subtitle_presentation.cue.current.boxes == []
    assert result.graph.subtitle_presentation.native is not None
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-token-annotation-invalid"
    )
    result.close()


def test_unpaintable_full_width_space_is_not_required_from_libass(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    source = tmp_path / "episode.ass"
    source.write_bytes(ASS.replace("猫を見る".encode(), "猫　犬".encode()))
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)
    ipc.props["sub-text/ass-full"] = (
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0000,0000,0000,,猫　犬"
    )
    result.graph.profile.profile.use_tokenizer(_WhitespaceTokenizer())

    result.graph.cue.set_subtitle("猫　犬")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)

    assert [entry.token_index for entry in backend.requests[0].palette] == [0, 2]
    assert result.graph.subtitle_presentation.native.status.eligible_tokens == 2
    assert result.graph.subtitle_presentation.native.status.skipped_tokens == 1
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    assert [box.index for box in result.graph.subtitle_presentation.cue.current.boxes] == [0, 2]
    result.close()


def test_sparse_native_boxes_anchor_tooltip_by_token_identity(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path, annotations_ready=False)
    source = tmp_path / "episode.ass"
    source.write_bytes(ASS.replace("猫を見る".encode(), "猫　犬".encode()))
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)
    ipc.props["sub-text/ass-full"] = (
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0000,0000,0000,,猫　犬"
    )
    result.graph.profile.profile.use_tokenizer(_WhitespaceTokenizer())
    result.graph.cue.set_subtitle("猫　犬")
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    ipc.commands.clear()

    Driver(result).move_to_word(2)

    assert result.graph.tooltip.observation().selected == 2
    assert result.graph.tooltip.surface_state().view.state is not None
    focus = [
        command for command in ipc.commands if command[:3] == ("osd-overlay", 1001, "ass-events")
    ]
    assert len(focus) == 1
    assert focus[0][3] != ""
    result.close()


def test_missing_native_anchor_rearms_hover_and_preserves_kanji_cycle(tmp_path: Path) -> None:
    result, _ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫")
    result.graph.tooltip.select(0)
    result.graph.profile.profile.replace_dictionary_set(object())

    result.graph.tooltip.show_tooltip(0)

    assert result.graph.tooltip.observation().selected == -1
    assert result.graph.tooltip.surface_state().view.state is None
    result.graph.tooltip.select(0)
    kanji_current(
        result.graph.tooltip.tip_ports,
        result.graph.tooltip.panel_ports,
        result.graph.tooltip.hover_inputs,
    )
    assert result.graph.tooltip.observation().kanji_index == 0
    result.close()


def test_simultaneous_ass_events_publish_event_aware_hit_geometry(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    source = tmp_path / "episode.ass"
    source.write_bytes(
        ASS.replace(
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫を見る\n".encode(),
            (
                "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫\n"
                "Dialogue: 1,0:00:01.50,0:00:02.50,Default,sign,12,34,56,,犬\n"
            ).encode(),
        )
    )
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)
    ipc.props["sub-text/ass-full"] = (
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0000,0000,0000,,猫\n"
        "Dialogue: 1,0:00:01.50,0:00:02.50,Default,sign,0012,0034,0056,,犬"
    )

    result.graph.cue.set_subtitle("猫\n犬")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)

    assert [event.source_order for event in backend.requests[0].frame_id.active_event_ids] == [0, 1]
    assert {entry.event_id.source_order for entry in backend.requests[0].palette} == {0, 1}
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    assert len(result.graph.subtitle_presentation.cue.current.boxes) == len(
        backend.requests[0].palette
    )
    result.close()


def test_unexpected_geometry_error_keeps_native_pixels(tmp_path: Path, monkeypatch) -> None:
    result, ipc, backend = reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None

    def fail_render_inputs(_observed_property, _osd):
        raise RuntimeError("unexpected profile failure")

    monkeypatch.setattr(
        result.graph.subtitle_presentation.native, "_render_inputs", fail_render_inputs
    )
    result.graph.cue.set_subtitle("猫を見る")
    result.graph.subtitle_presentation.draw()

    assert backend.requests == []
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "geometry-provider-failed"
    )
    assert result.graph.subtitle_presentation.native.status.geometry_ready is False
    assert result.graph.subtitle_presentation.cue.current.boxes == []
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    result.close()


def test_provider_error_is_consumed_once_and_cleared_by_source_switch(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    backend.error = RuntimeError("boom")
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)

    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )
    clears = ipc.commands.count(("osd-overlay", 1001, "none", ""))
    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )
    assert ipc.commands.count(("osd-overlay", 1001, "none", "")) == clears

    result.graph.subtitle_presentation.native.set_source(None, live=True)
    assert result.graph.subtitle_presentation.pipeline.last_error is None
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-source-unavailable"
    )
    result.close()


def test_repeated_provider_failure_emits_one_transition_diagnostic(tmp_path: Path, caplog) -> None:
    result, ipc, backend = reader(tmp_path)
    backend.error = RuntimeError("font provider unavailable")
    assert result.graph.subtitle_presentation.native is not None
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="saitenka.app.native_subtitles"):
        result.graph.cue.set_subtitle("猫を見る")
        settle_jobs(result, ipc)
        result.graph.subtitle_presentation.native.apply(result.graph.cue.geometry_observation())
        result.graph.subtitle_presentation.pipeline.invalidate()
        result.graph.subtitle_presentation.native.worker.invalidate_cache()
        assert result.graph.subtitle_presentation.native.schedule(
            result.graph.cue.geometry_observation()
        )
        settle_jobs(result, ipc)
        result.graph.subtitle_presentation.native.apply(result.graph.cue.geometry_observation())

    assert [record.getMessage() for record in caplog.records] == [
        ("native subtitle interaction unavailable: geometry-provider-failed detail=provider-error")
    ]
    result.close()


def test_invalid_result_identity_removes_visible_native_focus(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    result.graph.tooltip.select(0)
    result.graph.subtitle_presentation.draw()
    focus_index = len(ipc.commands)

    backend.token_index_offset = len(result.graph.subtitle_presentation.cue.current.tokens)
    result.graph.subtitle_presentation.pipeline.invalidate()
    result.graph.subtitle_presentation.native.worker.invalidate_cache()
    assert result.graph.subtitle_presentation.native.schedule(
        result.graph.cue.geometry_observation()
    )
    settle_jobs(result, ipc)

    assert not result.graph.subtitle_presentation.native.apply(
        result.graph.cue.geometry_observation()
    )
    assert result.graph.subtitle_presentation.cue.current.boxes == []
    assert ("osd-overlay", 1001, "none", "") in ipc.commands[focus_index:]
    result.close()


def test_empty_cue_removes_visible_native_focus(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    result.graph.tooltip.select(0)
    result.graph.subtitle_presentation.draw()

    result.graph.cue.set_subtitle("")

    assert result.graph.subtitle_presentation.cue.current.boxes == []
    assert ("osd-overlay", 1001, "none", "") in ipc.commands
    result.close()


def test_close_removes_visible_native_focus(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    result.graph.tooltip.select(0)
    result.graph.subtitle_presentation.draw()

    result.close()

    assert ("osd-overlay", 1001, "none", "") in ipc.commands


def test_a_geometry_schedule_that_never_starts_names_the_missing_input(tmp_path: Path) -> None:
    """The four preconditions in _resolve_schedule_inputs used to return a bare None, so a schedule
    that never ran left no trace in logs or telemetry — which is why the repeated-text gap below took
    an hour to locate. Not a degrade: pixels and ownership are untouched, only the silence was wrong."""
    result, _ipc, _backend = reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None
    result.graph.cue.set_subtitle("猫を見る")
    result.graph.subtitle_presentation.cue.replace_tokenized(
        tokens=[]
    )  # annotation has not landed yet: inputs are not assembled

    traced: list[tuple[str, int]] = []
    result.graph.subtitle_presentation.native._trace_unscheduled = lambda reason, revision: (
        traced.append(  # type: ignore[method-assign]
            (reason, revision)
        )
    )
    assert (
        result.graph.subtitle_presentation.native.schedule(result.graph.cue.geometry_observation())
        is False
    )

    # The revision is what lets a dropped schedule be matched to the observation that armed it.
    assert traced == [("no-tokens", result.graph.cue.revision)]
    result.close()


def _correlated_reader(tmp_path: Path):
    """A reader whose ownership assertion goes through the gateway, so the test places the terminal.

    The renderer's `use_native` answers "not yet" until the readback lands, which is the whole
    behavioural difference from the synchronous trio every other test in this file exercises.
    """
    result, ipc, backend = reader(tmp_path)
    ipc.correlate_commands = True
    return result, ipc, backend


def test_an_undecided_assertion_defers_publishing_instead_of_degrading(tmp_path: Path) -> None:
    result, _ipc, _backend = _correlated_reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None

    result.graph.cue.set_subtitle("猫を見る")

    # Mid-flight: no answer yet. Degrading here is the regression this asserts against — it would
    # mark geometry rejected for the lack of a reply and never publish hit boxes.
    assert result.graph.subtitle_presentation.native_ownership_undecided()
    assert (
        result.graph.subtitle_presentation.native.fallback_reason != "mpv-sub-visibility-rejected"
    )
    assert result.graph.subtitle_presentation.cue.current.boxes == []
    result.close()


def test_a_geometry_result_that_lands_while_ownership_is_undecided_is_not_lost(
    tmp_path: Path,
) -> None:
    """The geometry worker can finish before the ownership terminal does.

    `_apply` consumes the snapshot before it learns ownership is undecided, so deferring alone
    would drop that result on the floor and no later drain would rebuild it — the confirmation has
    to re-drive the refresh. This is the case that makes the re-drive load-bearing rather than
    defensive.
    """
    result, ipc, _backend = _correlated_reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None
    result.graph.cue.set_subtitle("猫を見る")

    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it is False  # deferred: no answer to publish against
    # Deferring is not degrading — recording a rejection here is the regression.
    assert (
        result.graph.subtitle_presentation.native.fallback_reason != "mpv-sub-visibility-rejected"
    )

    ipc.props["sub-visibility"] = True  # mpv now reports what the write asked for
    assert ipc.deliver_runtime_mpv(match="sub-visibility")  # the set_property terminal
    assert ipc.deliver_runtime_mpv(match="sub-visibility")  # the get_property readback
    assert not result.graph.subtitle_presentation.native_ownership_undecided()

    assert result.graph.subtitle_presentation.native.status.geometry_ready
    assert result.graph.subtitle_presentation.native.fallback_reason is None
    assert [box.index for box in result.graph.subtitle_presentation.cue.current.boxes] == list(
        range(len(result.graph.subtitle_presentation.cue.current.tokens))
    )
    result.close()


def test_a_false_readback_hands_pixels_to_legacy_rather_than_native(tmp_path: Path) -> None:
    result, ipc, _backend = _correlated_reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")

    # mpv accepted the write and still reports FALSE — the case the readback exists for, and the
    # reason the write's own outcome cannot stand in for it.
    assert ipc.deliver_runtime_mpv(match="sub-visibility")  # the set_property terminal
    ipc.props["sub-visibility"] = False  # ...and mpv did not honour it
    assert ipc.deliver_runtime_mpv(match="sub-visibility")  # the get_property readback

    assert (
        result.graph.subtitle_presentation.pipeline.renderer.ownership_state.owner
        != PixelOwner.NATIVE
    )
    assert not result.graph.subtitle_presentation.native_ownership_undecided()
    result.close()


def test_a_rejected_assertion_write_never_claims_native_pixels(tmp_path: Path) -> None:

    result, ipc, _backend = _correlated_reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")

    assert ipc.deliver_runtime_mpv(match="sub-visibility", outcome=EffectOutcome.REJECTED)

    # A refused write still reads back — mpv's actual state decides ownership, not our write's
    # outcome, and the readback is what can still prove legacy.
    assert [
        command for _identity, command, _cb in ipc.submitted if "sub-visibility" in command
    ] == [("get_property", "sub-visibility")]
    assert ipc.deliver_runtime_mpv()
    assert (
        result.graph.subtitle_presentation.pipeline.renderer.ownership_state.owner
        != PixelOwner.NATIVE
    )
    result.close()


def test_only_one_assertion_is_in_flight_across_a_reassert(tmp_path: Path) -> None:
    result, ipc, _backend = _correlated_reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    visibility = [c for _i, c, _cb in ipc.submitted if "sub-visibility" in c]
    assert len(visibility) == 1

    # Through the fact, not a verb: an overlay release is the production trigger for a
    # re-verification with the selection unchanged.
    result.graph.subtitle_presentation.pipeline.renderer.resume_after_overlay(
        result.graph.subtitle_presentation.target()
    )

    # A second assertion would orphan the first's effect id and leave a terminal nobody retires.
    assert [c for _i, c, _cb in ipc.submitted if "sub-visibility" in c] == visibility
    result.close()


def test_repeated_text_event_retires_interaction_before_timing_refresh(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    source = tmp_path / "episode.ass"
    source.write_bytes(
        ASS.replace(
            b"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,\xe7\x8c\xab\xe3\x82\x92\xe8\xa6\x8b\xe3\x82\x8b\n",
            b"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,\xe7\x8c\xab\xe3\x82\x92\xe8\xa6\x8b\xe3\x82\x8b\n"
            b"Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,\xe7\x8c\xab\xe3\x82\x92\xe8\xa6\x8b\xe3\x82\x8b\n",
        )
    )
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)

    # Drive the first cue as an observation, not a set_subtitle call: reconciliation reads the cue
    # from the projection now, so a cue the projection never saw settles as empty and the next
    # observation retires a cue this test never installed there.
    result.graph.playback.install_seed(ipc.props)
    ipc.props["sub-text"] = "猫を見る"
    result.graph.playback.observe_event({"name": "sub-text", "data": "猫を見る"})
    result.graph.cue.settle()
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it

    ipc.props.update(
        {
            "sub-text": "猫を見る",
            "sub-text/ass-full": "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0000,0000,0000,,猫を見る",
            "sub-start": 4.0,
            "sub-end": 6.0,
            "time-pos": 4.2,
        }
    )
    # mpv pushes the new authored row as its own observation; the old tick stage used to pull it
    # through the ass-full probe instead, which let this test skip it.
    result.graph.playback.observe_event(
        {"name": "sub-text/ass-full", "data": ipc.props["sub-text/ass-full"]}
    )
    result.graph.playback.observe_event({"name": "sub-start", "data": 4.0})
    result.graph.playback.observe_event({"name": "sub-end", "data": 6.0})
    assert result.graph.subtitle_presentation.cue.current.boxes == []

    settle_geometry(result, ipc)

    assert result.graph.subtitle_presentation.cue.current.boxes == []
    settle_jobs(result, ipc)

    assert backend.requests[-1].frame_id.active_event_ids[0].start_ms == 4_000
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    result.close()


def test_split_timing_property_batch_keeps_published_native_interaction(
    tmp_path: Path,
) -> None:
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    original_boxes = list(result.graph.subtitle_presentation.cue.current.boxes)
    result.graph.playback.install_seed(ipc.props | {"sub-text": result.graph.playback.cue.text})
    ipc.commands.clear()

    result.graph.playback.observe_event({"name": "sub-start", "data": None})
    result.graph.playback.observe_event({"name": "sub-end", "data": None})
    settle_geometry(result, ipc)

    assert result.graph.subtitle_presentation.cue.current.boxes == original_boxes
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-observation-pending"
    )

    result.graph.playback.observe_event({"name": "sub-start", "data": 1.0})
    result.graph.playback.observe_event({"name": "sub-end", "data": 3.0})
    settle_geometry(result, ipc)

    assert result.graph.subtitle_presentation.cue.current.boxes == original_boxes
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    assert result.graph.subtitle_presentation.native.status.fallback_reason is None
    result.close()


def test_incomplete_observation_with_changed_frame_clears_only_interaction(
    tmp_path: Path,
) -> None:
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    native_boxes = list(result.graph.subtitle_presentation.cue.current.boxes)
    result.graph.playback.install_seed(ipc.props | {"sub-text": result.graph.playback.cue.text})
    ipc.commands.clear()

    result.graph.playback.observe_event(
        {
            "name": "sub-text/ass-full",
            "data": ("Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0000,0000,0000,,猫を見る"),
        }
    )
    result.graph.playback.observe_event({"name": "sub-start", "data": None})
    result.graph.playback.observe_event({"name": "sub-end", "data": None})
    settle_geometry(result, ipc)

    assert native_boxes
    assert result.graph.subtitle_presentation.cue.current.boxes == []
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-observation-pending"
    )
    result.close()


def test_custom_mpv_subtitle_settings_report_mismatched_inputs(tmp_path: Path, caplog) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["options/sub-scale"] = 1.2
    ipc.props["options/sub-pos"] = 50.0
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="saitenka.app.native_subtitles"):
        result.graph.cue.set_subtitle("猫を見る")

    assert backend.requests == []
    assert result.graph.subtitle_presentation.cue.current.boxes == []
    assert result.graph.subtitle_presentation.native is not None
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-render-input-unsupported"
    )
    assert [record.getMessage() for record in caplog.records] == [
        (
            "native subtitle interaction unavailable: "
            "subtitle-render-input-unsupported "
            "detail=sub-scale=1.2, sub-pos=50.0"
        )
    ]
    result.close()


def test_pending_timing_does_not_escape_an_unsupported_render_profile(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["options/sub-scale"] = 1.2
    ipc.props["sub-start"] = None
    ipc.props["sub-end"] = None

    result.graph.cue.set_subtitle("猫を見る")

    assert backend.requests == []
    assert result.graph.subtitle_presentation.native is not None
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-render-input-unsupported"
    )
    assert result.graph.subtitle_presentation.native.status.owner == "native"
    assert ("set_property", "sub-visibility", False) not in ipc.commands
    assert not painted_overlays(ipc)
    result.close()


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("options/sub-font-provider", "fontconfig"),
        ("options/embeddedfonts", True),
        ("options/sub-fonts-dir", "/fonts"),
        ("options/sub-font", "Symbola"),
    ],
)
def test_a_font_setting_changed_under_a_resolved_track_fails_closed(
    tmp_path: Path, option: str, value: object
) -> None:
    """mpv rebuilds its subtitle decoder for any of these, so a change between track loads means its
    faces and ours have diverged. Measuring on anyway is the silent failure: every box comes out the
    wrong width and nothing anywhere says so."""
    result, ipc, backend = reader(tmp_path)
    ipc.props[option] = value

    result.graph.cue.set_subtitle("猫を見る")

    assert backend.requests == []
    assert result.graph.subtitle_presentation.cue.current.boxes == []
    assert result.graph.subtitle_presentation.native is not None
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-font-environment-stale"
    )
    result.close()


SRT_ROWS = "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫を見る"


def converted_reader(tmp_path: Path, *, codec: str = "subrip"):
    """A session whose selected track is one mpv converted from SubRip.

    The codec is stated because it is what decides the branch: the `.srt` on disk is our own
    extraction, and for `mov_text`/`webvtt` it is a *different* conversion from the one mpv is
    drawing — see `set_track_codec`.
    """
    result, ipc, backend = reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.formats = native_subtitles.NativeFormats.ALL
    result.graph.subtitle_presentation.native.set_track_codec(codec)
    ipc.props["sub-text/ass-full"] = SRT_ROWS
    source = tmp_path / "episode.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:03,000\n猫を見る\n", encoding="utf-8")
    result.graph.subtitle_presentation.native.set_source(source, live=True)
    return result, ipc, backend


def test_the_user_s_subtitle_style_reaches_the_document_the_boxes_are_measured_against(
    tmp_path: Path,
) -> None:
    """The end of the plumbing, not the reader in isolation: a `--sub-margin-y` mpv reports has to
    come out the other side as the `MarginV` libass is handed. It did not — `document()` was called
    with no style at all, so every converted cue on every machine was laid out against mpv's
    defaults, and a user who had moved the subtitles saw a second copy of the line offset from
    theirs."""
    result, ipc, backend = converted_reader(tmp_path)
    ipc.props["options/sub-margin-y"] = 120
    ipc.props["options/sub-bold"] = True

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    style = next(
        line
        for line in backend.requests[-1].ass.decode().splitlines()
        if line.startswith("Style: Default")
    ).split(",")
    assert style[21] == str(round(round(120 * 288 / 720) * 1.0))  # MarginV, at this font scale
    assert style[7] == "1"  # Bold


def test_a_converted_track_says_so_in_the_span_that_diagnoses_it(tmp_path: Path, monkeypatch):
    """`source_class` read `source_path`, so every converted track called itself `external-ass` —
    the one label that rules out the branch it was actually on. A span whose job is to say which
    document the boxes came from must not name a different one."""
    spans = _decision_spans(monkeypatch)
    result, ipc, _backend = converted_reader(tmp_path)

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert {span["source_class"] for span in spans if "source_class" in span} == {"converted"}


def test_a_converted_track_is_measured_against_the_document_mpv_rebuilt(tmp_path: Path) -> None:
    """mpv never renders a SubRip file — libavcodec converts it and mpv renders that. The boxes have
    to be measured against the conversion, so the document is rebuilt around the rows mpv reports
    rather than read off disk."""
    result, ipc, backend = converted_reader(tmp_path)

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert result.graph.subtitle_presentation.native is not None
    assert (
        result.graph.subtitle_presentation.native.source_kind
        is native_subtitles.SourceKind.CONVERTED
    )
    assert backend.requests
    document = backend.requests[-1].ass.decode()
    assert "PlayResY: 288" in document  # libavcodec's, not the .srt's (it has none)
    assert "YCbCr Matrix: None" in document
    # mpv's own row, carrying the per-token color keys the hit map is read back from — the events
    # are its conversion, so only the header around them was reproduced.
    assert (
        document.rstrip().splitlines()[-1].startswith("Dialogue: 0,0:00:01.00,0:00:03.00,Default")
    )
    assert "猫" in document and "見る" in document


def test_a_converted_track_carries_the_features_and_scale_mpv_sets(tmp_path: Path) -> None:
    """`configure_ass` turns on three track features and a non-unit font scale for a converted
    track. Leaving either off measures a layout mpv is not drawing."""
    result, ipc, backend = converted_reader(tmp_path)

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    state = backend.requests[-1].renderer_state
    assert dict(state.features) == {1: True, 2: True, 3: True}
    assert state.font_scale > 0


def test_an_srt_stays_with_the_legacy_renderer_unless_the_config_asks(tmp_path: Path) -> None:
    """The default envelope is the tested one. A track the native path has not been asked to take
    still selects the renderer that can draw its hit boxes."""
    result, _ipc, _backend = reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None
    source = tmp_path / "episode.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:03,000\n猫を見る\n", encoding="utf-8")

    result.graph.subtitle_presentation.native.set_source(source, live=True)

    assert result.graph.subtitle_presentation.native.source_unsupported is True
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-source-not-authored-ass"
    )
    result.close()


TWO_CUE_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\n猫を見る\n\n2\n00:00:04,000 --> 00:00:06,000\n犬を見る\n"
)


def converted_episode(tmp_path: Path, srt: str):
    """A converted session whose `.srt` holds more than the cue on screen, so lookahead has a
    target — `converted_reader`'s single cue has no next boundary to read ahead to."""
    result, ipc, backend = converted_reader(tmp_path)
    source = tmp_path / "episode.srt"
    source.write_text(srt, encoding="utf-8")
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source, live=True)
    result.graph.subtitle_navigation.load_index(str(source))
    return result, ipc, backend


def test_a_converted_track_reads_ahead_by_predicting_the_events_mpv_will_report(
    tmp_path: Path,
) -> None:
    """A converted track's events exist only as the rows mpv reports for the cue on screen, so every
    cue used to be a cache miss. They are now predicted from the `.srt` — libavcodec's conversion,
    done here — which is what gives the track a lookahead window at all."""
    result, ipc, backend = converted_episode(tmp_path, TWO_CUE_SRT)

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert result.graph.subtitle_presentation.native is not None
    assert result.graph.subtitle_presentation.native.worker.stats.prefetched >= 1, (
        "nothing was read ahead"
    )
    prefetched = backend.requests[-1].ass.decode()
    # The cue's own timings, not its text: by the time it reaches the backend every token carries a
    # reserved color key, so the words are no longer contiguous in the document.
    assert "0:00:04.00,0:00:06.00" in prefetched, "the next cue was not the one prefetched"
    assert result.graph.subtitle_presentation.native.status.fallback_reason is None
    result.close()


def test_a_predicted_cue_is_served_from_the_cache_when_mpv_reports_it(tmp_path: Path) -> None:
    """The payoff: mpv's row for the next cue lands on the key the prefetch was filed under, so the
    cue is published without a render. The row here is the shape a live mpv produces —
    `tests/test_subrip_conversion.py` owns that half against a recorded oracle; this owns the
    wiring, that a matching row is actually served from the cache."""
    result, ipc, backend = converted_episode(tmp_path, TWO_CUE_SRT)
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    rendered = len(backend.requests)

    ipc.props["sub-text/ass-full"] = "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,犬を見る"
    ipc.props.update({"sub-start": 4.0, "sub-end": 6.0})
    result.graph.cue.set_subtitle("犬を見る")
    settle_jobs(result, ipc)

    assert result.graph.subtitle_presentation.native is not None
    assert result.graph.subtitle_presentation.native.worker.stats.cache_hits >= 1
    assert len(backend.requests) == rendered, "the predicted cue was rendered a second time"
    assert result.graph.subtitle_presentation.cue.current.boxes
    result.close()


def test_a_miss_names_which_part_of_the_key_the_prediction_got_wrong(
    tmp_path: Path, monkeypatch
) -> None:
    """A live session reported two lookups, both misses, and there was no way to tell a lookahead
    that predicted a *nearly* right cue from one that never ran — both say `first-seen`. The
    divergence names the component, which is the difference between a one-field bug and an
    unwired one."""
    from saitenka import otel_metrics

    spans: list[dict[str, object]] = []

    class _Span:
        def __init__(self, values: dict[str, object]) -> None:
            self.values = values

        def set(self, key: str, value: object) -> None:
            self.values[key] = value

    @contextmanager
    def record(name: str, **attributes: str):
        values: dict[str, object] = dict(attributes)
        if name == "subtitle_geometry_cache":
            spans.append(values)
        yield _Span(values)

    result, ipc, _backend = converted_episode(tmp_path, TWO_CUE_SRT)
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    monkeypatch.setattr(otel_metrics, "traced", record)
    ipc.props["sub-text/ass-full"] = (
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,{\\b1}犬を見る"
    )
    ipc.props.update({"sub-start": 4.0, "sub-end": 6.0})
    result.graph.cue.set_subtitle("犬を見る")
    settle_jobs(result, ipc)

    missed = [span for span in spans if span.get("outcome") == "miss"]
    assert missed, "the mispredicted cue did not report a miss"
    # The rows differ and nothing else does: same file, same frame, same profile.
    assert missed[0]["key_divergence"] == "rows"
    result.close()


def test_a_mispredicted_cue_costs_a_render_and_not_a_wrong_box(tmp_path: Path) -> None:
    """The safety argument, exercised rather than asserted. The cache key carries the event rows, so
    a prediction that disagrees with mpv simply never matches — the cue is rebuilt from mpv's own
    row, which is exactly what a converted track did before any of this existed."""
    result, ipc, backend = converted_episode(tmp_path, TWO_CUE_SRT)
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    rendered = len(backend.requests)

    # mpv reports something the prediction could not have produced — a styled row for the same cue.
    ipc.props["sub-text/ass-full"] = (
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,{\\b1}犬を見る"
    )
    ipc.props.update({"sub-start": 4.0, "sub-end": 6.0})
    result.graph.cue.set_subtitle("犬を見る")
    settle_jobs(result, ipc)

    assert len(backend.requests) > rendered, "a mismatched prediction was used anyway"
    assert "{\\b1}" in backend.requests[-1].ass.decode(), "the rebuild did not use mpv's own row"
    assert result.graph.subtitle_presentation.cue.current.boxes
    result.close()


def test_a_cue_srtdec_would_mangle_is_simply_not_read_ahead(tmp_path: Path) -> None:
    """`srtdec` parses ` b ` in `a < b > c` as a bold tag. The converter declines rather than guess,
    and declining has to be quiet: it costs the lookahead for that cue, which is what a converted
    track had for every cue."""
    result, ipc, _backend = converted_episode(
        tmp_path,
        "1\n00:00:01,000 --> 00:00:03,000\n猫を見る\n\n2\n00:00:04,000 --> 00:00:06,000\na < b > c\n",
    )

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert result.graph.subtitle_presentation.native is not None
    assert result.graph.subtitle_presentation.native.worker.stats.prefetched == 0
    assert result.graph.subtitle_presentation.native.status.fallback_reason is None
    result.close()


@pytest.mark.parametrize("codec", ["mov_text", "webvtt", "microdvd", ""])
def test_a_conversion_we_have_not_reproduced_is_refused_by_name(tmp_path: Path, codec: str) -> None:
    """`native_formats = "all"` is not "any text track". Every codec here is converted to ASS by a
    DIFFERENT libavcodec decoder than SubRip's, writing its own header and styles — and our own
    extraction transcodes all of them to `.srt`, so the artifact on disk claims to be something it
    is not. Measuring one against SubRip's header is a wrong box, not a degraded one.

    The empty codec is the unknown case, and it is refused for the same reason: the stop rule is to
    narrow the mode rather than guess which decoder mpv is running.
    """
    result, _ipc, _backend = converted_reader(tmp_path, codec=codec)

    assert result.graph.subtitle_presentation.native is not None
    assert result.graph.subtitle_presentation.native.source_kind is native_subtitles.SourceKind.NONE
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-source-conversion-unreproduced"
    )
    # A named refusal, and one that selects the renderer that CAN draw the boxes — not a track left
    # with mpv's pixels and nothing to click.
    assert result.graph.subtitle_presentation.native.source_unsupported is True
    result.close()


def test_the_sdh_filter_is_refused_because_it_rewrites_the_event(tmp_path: Path) -> None:
    """`--sub-filter-sdh` rewrites an event's text before libass ever sees it, so mpv is drawing
    something the file does not contain and our match back to the source is against a document that
    no longer describes the screen."""
    result, ipc, _backend = reader(tmp_path)
    ipc.props["options/sub-filter-sdh"] = True

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert result.graph.subtitle_presentation.native is not None
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-render-input-unsupported"
    )
    assert not result.graph.subtitle_presentation.cue.current.boxes
    result.close()


def test_a_regex_filter_only_drops_a_cue_so_it_is_not_refused(tmp_path: Path) -> None:
    """The negative control for the refusal above, and the reason it names one option rather than
    the whole filter chain: `--sub-filter-regex` can only drop a whole packet, never rewrite one, so
    the events that do arrive are still the file's own."""
    result, ipc, _backend = reader(tmp_path)
    ipc.props["options/sub-filter-regex"] = ["advert"]

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert result.graph.subtitle_presentation.cue.current.boxes
    result.close()


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("all", native_subtitles.NativeFormats.ALL),
        ("authored-ass", native_subtitles.NativeFormats.AUTHORED_ASS),
        ("nonsense", native_subtitles.NativeFormats.AUTHORED_ASS),
    ],
)
def test_an_unknown_format_setting_narrows_rather_than_widens(
    configured: str, expected: native_subtitles.NativeFormats
) -> None:
    assert native_subtitles.native_formats(configured) is expected


def overlay_payloads(ipc) -> list[str]:
    return [
        str(command[3])
        for command in ipc.commands
        if command[0] == "osd-overlay" and len(command) > 3 and command[2] == "ass-events"
    ]


def attachment_supplying(ipc, *families: str) -> subtitle_fonts.FontEnvironment:
    """A font environment whose container attachment advertises exactly `families`.

    The names are stated rather than parsed out of real font bytes: what is under test here is which
    palette entries stand down given a set, and `tests/test_font_names.py` owns the other half —
    that a real attachment's set is read correctly from its name table.
    """
    return subtitle_fonts.FontEnvironment(
        subtitle_fonts.FontSetup(extract_fonts=True),
        (("Embedded.otf", b"font"),),
        subtitle_fonts.option_snapshot(
            {name: ipc.query(f"options/{name}") for name in subtitle_fonts.FONT_OPTIONS}
        ),
        frozenset(families),
    )


def test_a_face_only_the_subtitle_renderer_has_stands_the_overprint_down(tmp_path: Path) -> None:
    """The case the drift probe measured at -29px. mpv's OSD library can never receive a container
    attachment, so an overprint sent through `osd-overlay` would be laid out in a substitute face —
    right words, wrong glyph shapes. The boxes are still right, so the cue stays interactive; only
    the color stands down, and the demotion is counted rather than silent."""
    result, ipc, backend = reader(tmp_path)
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_fonts(attachment_supplying(ipc, "arial"))

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert backend.requests
    assert {entry.font_name for entry in backend.requests[-1].palette} == {""}
    assert result.graph.subtitle_presentation.cue.current.boxes  # the hit boxes still land
    result.close()


def presented_overpaints(ipc) -> list[tuple[int, int, int, int]]:
    """Every `overlay-add` on the raster device's slot, as (x, y, width, height).

    mpv's argument order is `<id> <x> <y> <file> <offset> <fmt> <w> <h> <stride>`.
    """
    return [
        (int(command[2]), int(command[3]), int(command[7]), int(command[8]))
        for command in ipc.commands
        if command[0] == "overlay-add" and command[1] == OverlayId.OVERPAINT
    ]


def test_a_face_the_osd_library_cannot_load_is_colored_as_a_raster_instead(tmp_path: Path) -> None:
    """The point of the second device: a signs-and-songs release keeps its color.

    The text device has to stand down there — mpv's OSD library can never load a container
    attachment — and until this device existed that meant no color at all on exactly the tracks
    whose typesetting this mode is for. The raster needs no face: it tints the pixels the
    measurement already drew, from the font set mpv's SUBTITLE renderer holds.
    """
    result, ipc, _backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_fonts(attachment_supplying(ipc, "arial"))

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert not [payload for payload in overlay_payloads(ipc) if "\\fn" in payload], (
        "the text device drew a face the OSD library cannot load"
    )
    painted = presented_overpaints(ipc)
    assert painted, "no raster reached mpv"
    # Cropped to the union of the three 50x40 boxes the fake laid out at x=100, 160, 220 — not to
    # the frame, which would be most of a megabyte of transparent pixels per cue.
    assert painted[-1] == (100, 600, 170, 40)
    assert result.graph.subtitle_presentation.cue.current.boxes
    result.close()


def test_the_handoff_to_legacy_takes_the_interaction_pixels_down(tmp_path: Path) -> None:
    """The raster is a tint over mpv's own glyphs. Once the handoff hides those, it is floating over
    a render that never laid it out — and nothing repaints it, because `draw` routes to the legacy
    renderer from then on. So it stays on the last cue's words for the rest of a gapless episode.

    The focus rect had the same hole: arriving at LEGACY emitted no interaction clear at all.
    """
    result, ipc, _backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )
    renderer = result.graph.subtitle_presentation.pipeline.renderer
    assert isinstance(renderer, NativeVisibleRenderer)
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_fonts(attachment_supplying(ipc, "arial"))
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    assert presented_overpaints(ipc), "the raster never reached mpv, so the teardown proves nothing"
    ipc.commands.clear()

    # Coming back from an overlay re-verifies ownership. The write does not land and the readback
    # says FALSE, which is the proof that hands the pixels to the legacy renderer.
    ipc.set_property_exception = OSError("pipe closed")
    ipc.props["sub-visibility"] = False
    renderer.resume_after_overlay(result.graph.subtitle_presentation.target())

    assert renderer.ownership_state.owner is PixelOwner.LEGACY
    assert ("overlay-remove", OverlayId.OVERPAINT) in ipc.commands
    result.close()


def palette_for(*, play_res_y: str, frame_height: int, font_scale: float):
    """The overprint palette for a one-token cue in a document declaring `play_res_y`."""
    from saitenka_subtitles import SubtitleTrackId, TokenAnnotation
    from saitenka_subtitles.ass_geometry import prepare_ass_hit_map_frame

    from saitenka.app.native_subtitles import _palette_in_frame_units

    row = "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫"
    source = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\n"
        f"{play_res_y}\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
        "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding\nStyle: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        "0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1\n\n[Events]\nFormat: Layer, Start, End, Style, "
        f"Name, MarginL, MarginR, MarginV, Effect, Text\n{row}\n"
    )
    track = SubtitleTrackId("palette-units")
    prepared = prepare_ass_hit_map_frame(
        source.encode(), track, active_rows=row, text="猫", tokens=[TokenAnnotation(0, 0, 1)]
    )
    return _palette_in_frame_units(
        prepared, frame_height, font_scale, unreachable=subtitle_fonts.OsdReach(frozenset())
    )


@pytest.mark.parametrize(
    ("frame_height", "font_scale", "expected"),
    [
        (720, 1.0, 48.0),  # identity: the case the wire-level test already pins
        (1080, 1.0, 72.0),  # a 720p script in a 1080p frame
        (720, 1.25, 60.0),  # `ass_set_font_scale`, which a converted track drives off 1.0
        (1080, 1.25, 90.0),  # both at once, because they multiply rather than compose
    ],
)
def test_the_overprint_font_size_is_restated_in_the_frames_pixels(
    frame_height: int, font_scale: float, expected: float
) -> None:
    """libass scales a style's `Fontsize` by `frame_height / PlayResY` and then by the font scale.
    Skip that and the overprint draws the token at its script-unit size over glyphs laid out at the
    frame's — a uniform error that is invisible at 720p, which is the only shape ever measured."""
    palette = palette_for(play_res_y="PlayResY: 720", frame_height=frame_height, font_scale=1.0)
    scaled = palette_for(
        play_res_y="PlayResY: 720", frame_height=frame_height, font_scale=font_scale
    )

    assert palette[0].font_size == pytest.approx(expected / font_scale)
    assert scaled[0].font_size == pytest.approx(expected)


def test_a_document_with_no_playresy_keeps_its_boxes_and_loses_its_overprint() -> None:
    """There is no script-unit-to-pixel ratio without it, so a size would be a guess. Zero is the
    overprint's "do not draw this token" — the hit boxes are unaffected, only the color stands
    down to a device that needs no size."""
    palette = palette_for(play_res_y="", frame_height=720, font_scale=1.0)

    assert [(entry.font_name, entry.font_size) for entry in palette] == [("", 0.0)]


def test_every_gate_option_is_observed_and_counted_a_render_space_input() -> None:
    """Three lists that have to agree, and nothing made them.

    An option missing from `OBSERVED_PROPERTIES` is not merely slower: `observed_property` falls through
    to a blocking `get_property`, and `_render_inputs` runs twice per cue, so each omission is two
    more round trips per cue on the interaction loop. Missing from `RENDER_SPACE_PROPERTIES` is the
    correctness half — a mid-episode change to it never invalidates the geometry it just moved, so
    the boxes stay where the old value put them until the track reloads.

    Four options landed on this branch reading the gate but neither list, which is why this exists.
    """
    from saitenka.app.native_subtitles import GATE_OPTIONS
    from saitenka.app.session.playback_observation import OBSERVED_PROPERTIES
    from saitenka.runtime.playback import RENDER_SPACE_PROPERTIES

    qualified = {f"options/{name}" for name in GATE_OPTIONS}

    assert qualified <= set(OBSERVED_PROPERTIES)
    assert qualified <= RENDER_SPACE_PROPERTIES


def calibration_calls(ipc) -> list[tuple]:
    """Every hidden `compute_bounds` the layout check asked mpv for."""
    return [
        command
        for command in ipc.commands
        if command[0] == "osd-overlay" and command[1] == subtitle_render.NATIVE_CALIBRATION_ID
    ]


def test_a_playing_session_still_measures_once_for_the_track(tmp_path: Path) -> None:
    """Paused is the usual moment, and the default config supplies it at the first hover. But
    `pause_on_tooltip` is a setting, and without this a session that never pauses would never
    measure at all — the inference about which faces mpv's OSD renderer can load would go
    unchecked for the whole episode. The track load is where that one check is affordable."""
    result, ipc, _backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )
    ipc.props["pause"] = False

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert len(calibration_calls(ipc)) == 1
    result.close()


def test_a_playing_session_does_not_measure_again_after_that(tmp_path: Path) -> None:
    """The other half, and the reason the check is gated at all: `compute_bounds` makes mpv do a
    full render on its core thread. One per track load is a stall nobody sees; one per cue is a
    stutter through the whole episode."""
    result, ipc, _backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )
    ipc.props["pause"] = False
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    assert len(calibration_calls(ipc)) == 1

    for _ in range(3):
        result.graph.cue.set_subtitle("猫を見る")
        settle_jobs(result, ipc)
        result.graph.subtitle_presentation.draw()

    assert len(calibration_calls(ipc)) == 1
    result.close()


def test_the_layout_check_measures_once_per_face_set_while_paused(tmp_path: Path) -> None:
    """Paused, it asks mpv where its OSD renderer actually put the overprint — the one direct check
    of the only claim the text device makes. Once per face set, not once per cue: the answer cannot
    change while the faces and the surface do not."""
    result, ipc, _backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )
    ipc.props["pause"] = True

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    first = len(calibration_calls(ipc))
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert first == 1
    assert len(calibration_calls(ipc)) == 1
    payload = calibration_calls(ipc)[0]
    # `hidden` and `compute_bounds` both set: mpv answers with the box and draws nothing.
    assert payload[-2:] == (True, True)
    assert r"\fnArial" in str(payload[3])
    result.close()


def test_a_refused_layout_check_is_asked_again_rather_than_written_off(tmp_path: Path) -> None:
    """ "Measured once per face set" has to mean measured, not asked. The gateway refuses admission
    when it is at capacity, and marking the face set before the ask lands retires it for the whole
    session — so a real substituted-face drift on that set is never measured, and the text device
    keeps drawing words in the wrong place with every meter reading green."""
    result, ipc, _backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )
    ipc.props["pause"] = True
    ipc.refused_identities = ("subtitle:calibrate:",)

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    assert not calibration_calls(ipc)

    ipc.refused_identities = ()
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert len(calibration_calls(ipc)) == 1
    result.close()


def osd_box(*, right: float) -> dict[str, float]:
    """What mpv reports for the fake's three 50x40 boxes at x=100/160/220, `right` px wider.

    Inflated by the hairline border on every edge, because `mp_ass_get_bb` unions the outline images
    — so a reply that did NOT carry it would understate the drift by two pixels.
    """
    return {"x0": 99.0, "y0": 599.0, "x1": 271.0 + right, "y1": 641.0}


def test_a_measured_drift_stands_the_text_device_down_on_the_cue_already_showing(
    tmp_path: Path,
) -> None:
    """The late verdict. Which families the OSD renderer can reach is inferred when the geometry is
    built, and the measurement can contradict that inference long after — by which time the request
    is gone. Applying it where the drawing happens is what lets the answer reach the cue on screen
    instead of some later one."""
    result, ipc, _backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )
    ipc.props["pause"] = True
    ipc.osd_bounds = osd_box(right=29.0)

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert r"\fnArial" in overlay_payloads(ipc)[0], (
        "nothing was drawn as text, so nothing to demote"
    )
    assert result.graph.subtitle_presentation.native is not None
    assert result.graph.subtitle_presentation.native._measured_unsafe == frozenset({"arial"}), (
        "no verdict was recorded"
    )

    # The verdict has to reschedule, not just redraw: it changes what the measurement must CONTAIN.
    # Redrawing the old snapshot cannot produce coverage masks, and nothing re-observes a cue that
    # has not changed — so without this the family stays uncolored for as long as it is shown.
    settle_geometry(result, ipc)
    settle_jobs(result, ipc)
    result.graph.subtitle_presentation.draw()

    assert presented_overpaints(ipc), "the color did not step down to the raster after the verdict"
    result.close()


def test_an_agreeing_measurement_leaves_the_text_device_alone(tmp_path: Path) -> None:
    """The negative control. Demoting on agreement would strip the color from every correctly
    typeset track — and the whole point of measuring is that the inference can be wrong either way."""
    result, ipc, _backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )
    ipc.props["pause"] = True
    ipc.osd_bounds = osd_box(right=0.0)

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert r"\fnArial" in overlay_payloads(ipc)[-1]
    result.close()


def test_a_drifting_family_gets_its_masks_kept_so_the_raster_can_take_it(tmp_path: Path) -> None:
    """The consequence for the other side. A late verdict lands on device 3's rule because the cue
    was built without masks; telling the geometry side is what makes the NEXT build keep them, so
    those tokens rise to the raster instead of staying on the bottom rung."""
    result, ipc, backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )
    ipc.props["pause"] = True
    ipc.osd_bounds = osd_box(right=29.0)

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    assert backend.requests[-1].keep_coverage is False, "the first cue had no verdict yet"
    # The same cue again, and it is re-rendered rather than served from cache — the verdict
    # invalidates, which is the half that makes the demotion reach the pixels.
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert backend.requests[-1].keep_coverage is True
    assert {entry.font_name for entry in backend.requests[-1].palette} == {""}
    result.close()


def test_a_cue_the_text_device_can_draw_publishes_no_raster(tmp_path: Path) -> None:
    """The negative control, and the ladder's rule: a device is used only when the one above it
    cannot draw. Two colors over one cue would double every glyph's alpha."""
    result, ipc, _backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert [payload for payload in overlay_payloads(ipc) if "\\fn" in payload]
    assert not presented_overpaints(ipc)
    result.close()


def test_only_the_tokens_in_the_embedded_family_lose_their_color(tmp_path: Path) -> None:
    """The reason the stand-down is keyed on families and not on the presence of an attachment: a
    release whose dialogue is a system font and whose signs are attachment-only should lose the
    color on its signs, not on the whole episode."""
    result, ipc, backend = reader(tmp_path)
    source = tmp_path / "episode.ass"
    source.write_bytes(
        ASS.replace(
            b"Style: Default,Arial,",
            b"Style: Sign,Embedded Signs,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,"
            b"100,100,0,0,1,2,1,2,10,10,30,1\nStyle: Default,Arial,",
        ).replace(
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫を見る\n".encode(),
            (
                "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫\n"
                "Dialogue: 1,0:00:01.00,0:00:03.00,Sign,,0,0,0,,犬\n"
            ).encode(),
        )
    )
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)
    result.graph.subtitle_presentation.native.set_fonts(attachment_supplying(ipc, "embedded signs"))
    ipc.props["sub-text/ass-full"] = (
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0000,0000,0000,,猫\n"
        "Dialogue: 1,0:00:01.00,0:00:03.00,Sign,,0000,0000,0000,,犬"
    )

    result.graph.cue.set_subtitle("猫\n犬")
    settle_jobs(result, ipc)

    by_event = {
        entry.event_id.source_order: entry.font_name for entry in backend.requests[-1].palette
    }
    assert by_event == {0: "Arial", 1: ""}
    result.close()


def test_a_document_that_embeds_its_own_fonts_stands_those_families_down(tmp_path: Path) -> None:
    """The fourth font source: an `[Fonts]` section inside the `.ass` reaches mpv's subtitle renderer
    through `ass_set_extract_fonts` and never its OSD one, exactly like a container attachment. It
    arrives with the source rather than with the font environment, so the two halves have to be
    combined on read or one of them is silently dropped.

    Driven through `set_source` with a real encoded font, because the families are only known by
    decoding the section — setting the derived set directly would test the combination and skip the
    decode that has to happen for it to hold any names at all."""
    result, ipc, backend = reader(tmp_path)
    source = tmp_path / "embedded.ass"
    source.write_bytes(
        ASS.decode().replace("[V4+ Styles]", util.ass_fonts_section("Arial") + "[V4+ Styles]").encode()
    )  # fmt: skip
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)
    result.graph.subtitle_presentation.native.set_fonts(attachment_supplying(ipc))

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert {entry.font_name for entry in backend.requests[-1].palette} == {""}
    result.close()


def test_a_document_that_embeds_a_family_nobody_uses_costs_no_color(tmp_path: Path) -> None:
    """The negative control for the test above: the section is decoded either way, so a green run
    there proves the demotion only if a section naming an unused family leaves the color alone."""
    result, ipc, backend = reader(tmp_path)
    source = tmp_path / "embedded.ass"
    source.write_bytes(
        ASS.decode()
        .replace("[V4+ Styles]", util.ass_fonts_section("Embedded Signs") + "[V4+ Styles]")
        .encode()
    )
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source)
    result.graph.subtitle_presentation.native.set_fonts(attachment_supplying(ipc))

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert {entry.font_name for entry in backend.requests[-1].palette} == {"Arial"}
    result.close()


def test_the_overprint_reaches_mpv_in_the_measured_face_and_size(tmp_path: Path) -> None:
    """The feature end to end, on the wire: a cue mpv is drawing gets a per-token `osd-overlay`
    payload naming the face and the frame-pixel size the measurement resolved.

    The one test the whole path had no positive for — which is why a palette carrying zero for every
    cue, and so an overprint that never drew anywhere, passed the suite.
    """
    result, ipc, _backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    drawn = [payload for payload in overlay_payloads(ipc) if "\\fn" in payload]
    assert drawn, "no overprint reached mpv"
    # The style's 48 script units at this document's PlayResY 720, into a 720-tall frame: unchanged.
    assert r"{\an7\pos(100,600)\fnArial\fs48" in drawn[-1]
    result.close()


def test_an_unmeasured_face_leaves_the_cue_uncolored_rather_than_guessed(tmp_path: Path) -> None:
    """A token whose face the measurement did not resolve is not drawn at a guess: the wrong glyph
    shape over the right word is worse than no color, because nothing shows it is wrong."""
    result, ipc, backend = reader(tmp_path)
    backend.font_name, backend.font_size = "", 0.0

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert (
        result.graph.subtitle_presentation.cue.current.boxes
    )  # the hit boxes still land, so the cue stays interactive
    assert not [payload for payload in overlay_payloads(ipc) if "\\fn" in payload]
    result.close()


def test_the_legacy_renderer_can_be_selected_and_given_back(tmp_path: Path) -> None:
    """The comparison target has to be selectable. Until now the only route to the legacy renderer
    was catastrophic recovery, and a target you cannot choose is not one."""
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.pipeline.renderer.ownership_state.owner
        is PixelOwner.NATIVE
    )

    assert result.graph.subtitle_presentation.toggle_renderer() is True
    assert (
        result.graph.subtitle_presentation.pipeline.renderer.ownership_state.owner
        is PixelOwner.LEGACY
    )

    assert result.graph.subtitle_presentation.toggle_renderer() is False
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.pipeline.renderer.ownership_state.owner
        is PixelOwner.NATIVE
    )
    result.close()


def test_a_forced_legacy_switch_is_told_apart_from_a_failure(tmp_path: Path) -> None:
    """Both end with the legacy renderer drawing, and a report has to say which happened: one is a
    user comparing the engines, the other is the native path giving up."""
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    result.graph.subtitle_presentation.toggle_renderer()

    assert result.graph.subtitle_presentation.pipeline.legacy_forced is True
    assert result.graph.subtitle_presentation.native is not None
    # Not a geometry failure: nothing refused a frame, so no fallback reason is latched.
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        != "mpv-sub-visibility-rejected"
    )
    result.close()


def test_resolving_the_track_again_clears_a_stale_font_environment(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["options/embeddedfonts"] = True
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-font-environment-stale"
    )

    resolve_track_fonts(ipc, ipc.query, result.graph.subtitle_presentation.native)
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    assert backend.requests
    assert result.graph.subtitle_presentation.native.status.fallback_reason is None
    result.close()


def test_mpv_empty_style_override_normalization_is_supported(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["options/sub-ass-style-overrides"] = [""]

    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)

    assert backend.requests
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    result.close()


def test_retina_letterbox_geometry_uses_mpv_frame_margins(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["osd-dimensions"] = {
        "w": 3024,
        "h": 1898,
        "mt": 98,
        "mb": 99,
        "ml": 0,
        "mr": 0,
    }
    ipc.props["video-out-params"] = {
        "dw": 1920,
        "dh": 1080,
        "w": 1920,
        "h": 1080,
        "par": 1.0,
    }
    # Through the path production uses: the host adopts a new OSD surface in `refresh_osd`, and the
    # layout follows the host rather than re-reading the property.
    assert result.graph.presentation.refresh_osd()

    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)

    request = backend.requests[-1]
    assert request.frame_size == (3024, 1898)
    assert request.storage_size == (1920, 1080)
    assert request.margins == (98, 99, 0, 0)
    assert request.use_margins is False
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    result.close()


def test_authored_ass_force_margins_is_forwarded(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["options/sub-ass-force-margins"] = True

    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)

    assert backend.requests[-1].use_margins is True
    result.close()


def test_authored_ass_margin_policy_change_refreshes_geometry(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it

    before = len(backend.requests)
    assert backend.requests[-1].use_margins is False  # the input the change is about to flip

    ipc.props["options/sub-ass-force-margins"] = True
    result.graph.playback.observe_event({"name": "options/sub-ass-force-margins", "data": True})
    ipc.fire_runtime_timer("subtitle:geometry-refresh")
    settle_jobs(result, ipc)

    # Assert the NEXT request carries the changed input, not that some request ever did: the
    # observation key covers the render profile, so a changed input must produce a new request.
    assert len(backend.requests) > before
    assert backend.requests[-1].use_margins is True
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    result.close()


def test_a_batch_of_geometry_input_changes_arms_one_deadline(tmp_path: Path) -> None:
    """A resize publishes several inputs together. Each one used to set a dirty flag drained by
    the tick; they now share one deadline, so the batch costs one refresh instead of one per
    property."""
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    ipc.timers.clear()

    result.graph.playback.observe_event({"name": "options/sub-pos", "data": 95.0})
    first = ipc.timers["subtitle:geometry-refresh"]
    result.graph.playback.observe_event({"name": "options/sub-scale", "data": 1.5})
    result.graph.playback.observe_event({"name": "options/sub-use-margins", "data": True})

    assert ipc.timers["subtitle:geometry-refresh"] is first
    assert list(ipc.timers) == ["subtitle:geometry-refresh"]

    assert ipc.fire_runtime_timer("subtitle:geometry-refresh")
    assert "subtitle:geometry-refresh" not in ipc.timers
    result.close()


def test_a_track_change_cancels_a_pending_geometry_refresh(tmp_path: Path) -> None:
    """The tick drain refreshed only while the pipeline generation held. The deadline keeps that
    guard: a refresh armed for the old track is cancelled rather than left to fire against the
    replacement."""
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it

    ipc.props["options/sub-pos"] = 95.0
    result.graph.playback.observe_event({"name": "options/sub-pos", "data": 95.0})
    assert "subtitle:geometry-refresh" in ipc.timers

    result.graph.playback.observe_event({"name": "sid", "data": 3})

    assert "subtitle:geometry-refresh" not in ipc.timers
    result.close()


def _select_external(ipc: FakeIPC, sid: int, path: Path) -> None:
    ipc.props["track-list"] = [
        {
            "id": sid,
            "type": "sub",
            "lang": "jpn",
            "external": True,
            "external-filename": str(path),
            "selected": True,
            "main-selection": 0,
        }
    ]
    ipc.props["sid"] = sid


def test_a_selection_the_session_made_itself_keeps_its_geometry_source(tmp_path: Path) -> None:
    """mpv echoes `sid` after the selecting call has already rebuilt the index, and the echo resets
    the geometry source unconditionally while the rebuild that would restore it runs only for a
    track the session does not already know. The track keeps its cues and loses its geometry: text
    on screen, nothing scannable, no overpaint, for the rest of the episode."""
    result, ipc, _backend = reader(tmp_path)
    native = result.graph.subtitle_presentation.native
    assert native is not None
    source = tmp_path / "episode.ass"
    _select_external(ipc, 3, source)
    # Declared before the echo, as the selecting call declares it — that is what makes sid 3
    # "known", and so the coordinate this regression lives at.
    result.graph.cue.configure_subtitle_mode(SubtitleStartup(SubtitleTracks(3, None), MAIN_LANG))
    result.graph.cue.rebuild_sub_index()
    assert native.source_path == source

    result.graph.playback.observe_event({"name": "sid", "data": 3})

    assert native.source_path == source
    result.close()


def test_an_unsupported_render_profile_tells_the_user_which_options_did_it(tmp_path: Path) -> None:
    """A refusal costs the whole episode's interaction, and the log is not a surface anyone reads
    live. The option names are the actionable half: the reason alone sends a user to the source."""
    result, ipc, _backend = reader(tmp_path)
    ipc.props["options/sub-scale"] = 1.4
    assert not toasts(ipc)

    result.graph.cue.set_subtitle("猫を見る")

    assert toasts(ipc)
    assert not painted_overlays(ipc)  # the cue itself stays unmeasured
    result.close()


def test_a_transient_provider_failure_does_not_interrupt_the_user(tmp_path: Path) -> None:
    """FAILED is provider-level and routinely recovers on the next cue — a geometry query that
    lands while `sub-delay` is moving finds no active event and fails once. Announcing it spends
    the user's attention on something already fixed, and a notice that cries wolf is ignored when
    a real refusal arrives."""
    result, ipc, backend = reader(tmp_path)
    native = result.graph.subtitle_presentation.native
    assert native is not None
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    backend.error = RuntimeError("missing libass token colors: [0]")
    result.graph.subtitle_presentation.pipeline.invalidate()
    native.worker.invalidate_cache()

    assert native.schedule(result.graph.cue.geometry_observation())
    settle_jobs(result, ipc)
    assert not native.apply(result.graph.cue.geometry_observation())

    assert native.status.fallback_reason == "geometry-provider-failed"
    assert not toasts(ipc)
    result.close()


def test_osd_and_video_pixel_aspects_are_composed(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["osd-dimensions"]["par"] = 1.25
    ipc.props["video-out-params"]["par"] = 1.2

    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)

    assert backend.requests[-1].pixel_aspect == pytest.approx(1.5)
    result.close()


def test_ass_video_aspect_override_falls_back_with_observed_value(tmp_path: Path, caplog) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["options/sub-ass-video-aspect-override"] = 1.85
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="saitenka.app.native_subtitles"):
        result.graph.cue.set_subtitle("猫を見る")

    assert backend.requests == []
    assert result.graph.subtitle_presentation.native is not None
    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-render-input-unsupported"
    )
    assert "sub-ass-video-aspect-override=1.85" in caplog.records[-1].getMessage()
    result.close()


def test_non_utf8_ass_has_stable_fallback_reason(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    source = tmp_path / "legacy.ass"
    source.write_bytes(ASS + b"\xff")
    assert result.graph.subtitle_presentation.native is not None
    result.graph.subtitle_presentation.native.set_source(source, live=True)

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    result.graph.subtitle_presentation.native.apply(result.graph.cue.geometry_observation())

    assert (
        result.graph.subtitle_presentation.native.status.fallback_reason
        == "subtitle-source-encoding-unsupported"
    )
    result.close()


def test_native_visibility_retries_without_repeating_diagnostic(tmp_path: Path, caplog) -> None:
    result, ipc, _backend = reader(tmp_path)
    now = [0.0]
    renderer = NativeVisibleRenderer(clock=lambda: now[0])
    result.graph.subtitle_presentation.pipeline.renderer = renderer
    ipc.set_property_error = "disconnected"
    ipc.get_property_error = "disconnected"
    result.graph.playback.install_seed({"sub-text": "猫を見る"})
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="saitenka.app.subtitle_render"):
        renderer.cue_changed(result.graph.subtitle_presentation.target(), nonempty=True)
        assert renderer.ownership_state.owner.value == "unknown"
        result.graph.subtitle_presentation.pipeline.draw_current(
            result.graph.subtitle_presentation.target()
        )
        # The retry is a named deadline now: nothing happens until it is due.
        assert ipc.timers["subtitle:ownership-retry"][1] == pytest.approx(0.05)
        assert ipc.commands.count(("set_property", "sub-visibility", True)) == 1
        assert ipc.fire_runtime_timer("subtitle:ownership-retry")

    assert ipc.commands.count(("set_property", "sub-visibility", True)) == 2
    assert ipc.commands.count(("set_property", "sub-visibility", False)) == 0
    assert not any(command[0] == "overlay-add" for command in ipc.commands)
    assert [record.getMessage() for record in caplog.records] == [
        "mpv rejected subtitle visibility assertion: disconnected"
    ]
    result.close()


@pytest.mark.parametrize("trigger", ["empty-cue", "reconnect", "mode-change"])
def test_an_ownership_trigger_asserts_visibility_at_most_once(tmp_path: Path, trigger: str) -> None:
    """WP4.3's "exactly one ownership result", driven at all four triggers rather than the
    activation path alone.

    "Exactly one" overstates it, and the empty cue is why: it settles ownership without asking mpv
    anything, because nothing about the pixels changed. The contract that holds across all of them
    is *at most* one assertion per trigger and one owner at the end — two writes to
    `sub-visibility` from one trigger are what orphan an assertion and strand the pixels.
    """
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    renderer = result.graph.subtitle_presentation.pipeline.renderer
    assert isinstance(renderer, NativeVisibleRenderer)
    ipc.commands.clear()

    if trigger == "empty-cue":
        renderer.cue_changed(result.graph.subtitle_presentation.target(), nonempty=False)
    elif trigger == "reconnect":
        renderer.connection_replaced(result.graph.subtitle_presentation.target())
    else:
        result.graph.subtitle_presentation.pipeline.activate(
            result.graph.subtitle_presentation.target(),
            draw=result.graph.subtitle_presentation.draw,
        )

    assert ipc.commands.count(("set_property", "sub-visibility", True)) <= 1
    assert ipc.commands.count(("set_property", "sub-visibility", False)) == 0
    assert renderer.ownership_state.owner is PixelOwner.NATIVE
    assert not renderer.assertion_in_flight  # nothing left orphaned behind the result
    result.close()


def test_rejected_native_visibility_reassertion_restores_legacy_renderer(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    ipc.command(
        "set_property",
        "sub-visibility",
        False,  # noqa: FBT003  # raw mpv IPC wire value
    )
    ipc.commands.clear()
    ipc.set_property_error = "disconnected"

    ipc.props["sid"] = 5  # a track reconfigure: the production trigger for a re-assertion
    result.graph.subtitle_presentation.pipeline.activate(
        result.graph.subtitle_presentation.target(), draw=result.graph.subtitle_presentation.draw
    )

    assert ("set_property", "sub-visibility", True) in ipc.commands
    assert any(command[0] == "overlay-add" for command in ipc.commands)
    assert result.graph.subtitle_presentation.cue.current.boxes
    box = result.graph.subtitle_presentation.cue.current.boxes[0]
    assert (
        result.graph.tooltip.hit(
            result.graph.subtitle_presentation.cue.current.origin[0] + box.x + 1,
            result.graph.subtitle_presentation.cue.current.origin[1] + box.y + 1,
        )
        == 0
    )
    result.close()


def _establish_native(result: TestSession, ipc: FakeIPC, sid: int) -> NativeVisibleRenderer:
    """Own the pixels for `sid`, the way a session that has been playing a track already does."""
    renderer = result.graph.subtitle_presentation.pipeline.renderer
    assert isinstance(renderer, NativeVisibleRenderer)
    ipc.props["sid"] = sid
    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)
    result.graph.subtitle_presentation.pipeline.activate(
        result.graph.subtitle_presentation.target(), draw=result.graph.subtitle_presentation.draw
    )
    assert renderer.ownership_state.native_pixels_established
    return renderer


def test_a_reconfigure_reasserts_before_mpv_echoes_the_selected_track(tmp_path: Path) -> None:
    """A live profile cycle must not leave the pixels owned on behalf of the track it replaced.

    `configure` runs a few statements after `select_initial` writes `sid` fire-and-forget, so mpv has
    not echoed the property yet and a renderer that reads it back still sees the OUTGOING track. It
    would conclude the selection had not moved and — with `native_pixels_established` still true from
    the old track — do nothing at all. So the selection is declared by the caller that wrote it.
    """
    result, ipc, _backend = reader(tmp_path)
    renderer = _establish_native(result, ipc, sid=1)
    before = renderer.ownership_state.context.ownership_epoch

    startup = SubtitleStartup(SubtitleTracks(jp_sid=5, en_sid=None), MAIN_LANG)
    result.graph.cue.configure_subtitle_mode(startup)

    assert ipc.props["sid"] == 1  # the echo has not landed: reading it back would see the old track
    assert renderer.ownership_state.context.ownership_epoch > before
    assert "5" in (renderer.ownership_state.context.selection or "")
    result.close()


def test_reconfiguring_the_same_track_spends_nothing(tmp_path: Path) -> None:
    """The negative control for the above: declaring the track already selected is not a change.

    Without this, "declare the selection" could be satisfied by bumping the epoch unconditionally,
    which re-proves ownership on every reconfigure and costs a round-trip per profile cycle.
    """
    result, ipc, _backend = reader(tmp_path)
    renderer = _establish_native(result, ipc, sid=5)
    before = renderer.ownership_state.context.ownership_epoch

    result.graph.cue.configure_subtitle_mode(
        SubtitleStartup(SubtitleTracks(jp_sid=5, en_sid=None), MAIN_LANG)
    )

    assert renderer.ownership_state.context.ownership_epoch == before
    result.close()


def test_native_visibility_exception_with_false_readback_commits_legacy(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    renderer = result.graph.subtitle_presentation.pipeline.renderer
    assert isinstance(renderer, NativeVisibleRenderer)
    ipc.set_property_exception = OSError("pipe closed")

    result.graph.cue.set_subtitle("猫を見る")
    result.graph.subtitle_presentation.draw()

    assert result.graph.subtitle_presentation.cue.current.boxes
    assert any(command[0] == "overlay-add" for command in ipc.commands)
    assert renderer.ownership_state.owner.value == "legacy"
    result.close()


def test_an_overtaken_subtitle_surface_never_acknowledges_over_the_current_one(
    tmp_path: Path,
) -> None:
    """The revision fence, which is why the surface is a transaction and not a bare write.

    Two cues can have overlay-adds in flight at once. If the older one's acknowledgement were
    accepted it would mark the slot present at a revision the newer cue has already replaced, and
    the stale bitmap would be the one the runtime believes is on screen.
    """
    from saitenka.app.overlay_ids import OverlayId
    from saitenka.runtime.surfaces import SurfaceStatus

    result, ipc, _backend = reader(tmp_path, correlated_surfaces=True)
    renderer = result.graph.subtitle_presentation.pipeline.renderer
    assert isinstance(renderer, NativeVisibleRenderer)
    result.graph.cue.set_subtitle("猫を見る")
    ipc.correlate_commands = True

    def stage():
        return renderer._fallback.draw(
            result.graph.cue.draw_request(),
            result.graph.lifecycle_surfaces,
            result.graph.ipc,
        )

    older, newer = stage(), stage()
    assert older is not None and newer is not None  # an open surface always mints a transaction
    older, newer = older.transaction, newer.transaction
    assert older is not None and newer is not None
    assert newer.revision > older.revision

    assert ipc.deliver_runtime_mpv(match="overlay-add")  # the older commit, now overtaken
    snapshot = result.graph.lifecycle_surfaces.snapshot(OverlayId.SUB)
    assert snapshot is not None
    assert snapshot.status is SurfaceStatus.PENDING
    assert snapshot.acknowledged_revision != older.revision

    assert ipc.deliver_runtime_mpv(match="overlay-add")  # the newer commit
    snapshot = result.graph.lifecycle_surfaces.snapshot(OverlayId.SUB)
    assert snapshot is not None
    assert snapshot.status is SurfaceStatus.PRESENT
    assert snapshot.acknowledged_revision == newer.revision
    result.close()


def test_legacy_ownership_commits_only_after_the_surface_commit_lands(tmp_path: Path) -> None:
    """The ordering rule this slice exists for.

    Taking legacy ownership before our own pixels are acknowledged is what lets the hide run
    against an unproved surface, leaving the frame with no subtitle at all — absent pixels, which
    WP4.3 forbids. So the overlay commit gates the ownership transition, not the other way round.
    Before this slice `_reply_accepted(draw(...))` was `True` the moment `draw` returned.
    """
    result, ipc, _backend = reader(tmp_path, correlated_surfaces=True)
    renderer = result.graph.subtitle_presentation.pipeline.renderer
    assert isinstance(renderer, NativeVisibleRenderer)
    result.graph.cue.set_subtitle("猫を見る")
    ipc.commands.clear()

    ipc.correlate_commands = True
    renderer.resume_after_overlay(result.graph.subtitle_presentation.target())
    assert ipc.deliver_runtime_mpv(match="sub-visibility")  # the visibility write
    ipc.props["sub-visibility"] = False  # mpv refuses to keep its subtitles visible
    assert ipc.deliver_runtime_mpv(match="sub-visibility")  # readback FALSE: hand to legacy

    pending = [command for _identity, command, _cb in ipc.submitted]
    assert any(command[0] == "overlay-add" for command in pending)
    assert renderer.ownership_state.owner != PixelOwner.LEGACY  # the commit is still outstanding

    assert ipc.deliver_runtime_mpv(match="overlay-add")  # the overlay commit lands

    assert renderer.ownership_state.owner == PixelOwner.LEGACY
    result.close()


def test_rejected_legacy_stage_does_not_commit_or_hide_native_pixels(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    renderer = result.graph.subtitle_presentation.pipeline.renderer
    assert isinstance(renderer, NativeVisibleRenderer)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it
    ipc.command(
        "set_property",
        "sub-visibility",
        False,  # noqa: FBT003  # raw mpv IPC wire value
    )
    ipc.commands.clear()
    ipc.set_property_error = "disconnected"
    ipc.overlay_add_error = "unsupported format"

    ipc.props["sid"] = 5  # a track reconfigure: the production trigger for a re-assertion
    result.graph.subtitle_presentation.pipeline.activate(
        result.graph.subtitle_presentation.target(), draw=result.graph.subtitle_presentation.draw
    )

    assert renderer.ownership_state.owner.value == "unknown"
    assert renderer.ownership_state.retry_effect_id is not None
    assert ipc.commands.count(("set_property", "sub-visibility", False)) == 0
    result.close()


def test_rejected_legacy_rehandoff_keeps_mpv_visible_and_retries(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    renderer = result.graph.subtitle_presentation.pipeline.renderer
    assert isinstance(renderer, NativeVisibleRenderer)
    ipc.set_property_exception = OSError("pipe closed")
    result.graph.cue.set_subtitle("猫を見る")
    result.graph.subtitle_presentation.draw()
    assert renderer.ownership_state.owner.value == "legacy"
    ipc.set_property_exception = None
    renderer.suspend_for_overlay(result.graph.subtitle_presentation.target())
    ipc.commands.clear()
    ipc.overlay_add_error = "unsupported format"

    renderer.resume_after_overlay(result.graph.subtitle_presentation.target())

    assert renderer.ownership_state.owner.value == "unknown"
    assert renderer.ownership_state.retry_effect_id is not None
    assert ipc.commands.count(("set_property", "sub-visibility", False)) == 0
    result.close()


def test_native_visibility_rejection_with_true_readback_keeps_native_owner(
    tmp_path: Path, monkeypatch
) -> None:
    from saitenka import otel_metrics

    spans: list[tuple[str, dict[str, object]]] = []

    class RecordingSpan:
        def __init__(self, attributes: dict[str, object]) -> None:
            self.attributes = attributes

        def set(self, key: str, value: object) -> None:
            self.attributes[key] = value

    @contextmanager
    def record_span(name: str, **attributes: str):
        values: dict[str, object] = dict(attributes)
        if name == "subtitle_geometry_decision":
            spans.append((name, values))
        yield RecordingSpan(values)

    monkeypatch.setattr(otel_metrics, "traced", record_span)
    result, ipc, _backend = reader(tmp_path)
    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    ipc.set_property_error = "disconnected"

    assert (
        result.graph.subtitle_presentation.native.status.geometry_ready
    )  # the lane terminal published it

    failures = [
        attributes
        for name, attributes in spans
        if name == "subtitle_geometry_decision"
        and attributes.get("reason") == "mpv-sub-visibility-rejected"
    ]
    assert failures == []
    renderer = result.graph.subtitle_presentation.pipeline.renderer
    assert isinstance(renderer, NativeVisibleRenderer)
    assert renderer.ownership_state.owner.value == "native"
    result.close()


def test_runtime_telemetry_reports_geometry_worker_health(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    backend.error = RuntimeError("font provider unavailable")

    result.graph.cue.set_subtitle("猫を見る")
    assert result.graph.subtitle_presentation.native is not None
    settle_jobs(result, ipc)
    result.graph.subtitle_presentation.native.apply(result.graph.cue.geometry_observation())

    assert result.graph.diagnostics.gauges() == IsPartialDict(
        **{
            "subtitle_geometry.submitted": 1.0,
            "subtitle_geometry.completed": 0.0,
            "subtitle_geometry.failures": 1.0,
            "subtitle_geometry.presented": 1.0,
        }
    )
    result.close()


def test_the_subtitle_clock_is_the_video_time_less_the_delay():
    from saitenka.app.native_subtitles import _subtitle_clock

    video_time, sub_delay, subtitle_time, timestamp_ms = _subtitle_clock(12.5, 0.5, None)

    assert (video_time, sub_delay, subtitle_time) == (12.5, 0.5, 12.0)
    assert timestamp_ms == 12_000  # the ms key the geometry cache is keyed on


def test_a_missing_delay_reads_as_no_offset():
    """mpv answers None for `sub-delay` before a track resolves; that is zero, not unavailable."""
    from saitenka.app.native_subtitles import _subtitle_clock

    assert _subtitle_clock(4.0, None, None) == (4.0, 0.0, 4.0, 4_000)


def test_a_missing_time_pos_falls_back_to_the_cue_start():
    """`sub-start` is the cue's own time, so the video time is reconstructed by re-adding the delay."""
    from saitenka.app.native_subtitles import _subtitle_clock

    assert _subtitle_clock(None, 0.25, 8.0) == (8.25, 0.25, 8.0, 8_000)


def test_a_clock_with_neither_a_position_nor_a_cue_start_is_unavailable():
    from saitenka.app.native_subtitles import _subtitle_clock

    with pytest.raises(ValueError, match="unavailable"):
        _subtitle_clock(None, 0.0, None)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_clock_is_rejected_rather_than_cached(bad: float):
    """A NaN timestamp would key a cache entry nothing can ever match again."""
    from saitenka.app.native_subtitles import _subtitle_clock

    with pytest.raises(ValueError, match="finite"):
        _subtitle_clock(bad, 0.0, None)
    with pytest.raises(ValueError, match="finite"):
        _subtitle_clock(1.0, bad, None)


def test_a_delay_that_runs_the_clock_before_the_file_starts_is_rejected():
    """A large positive `sub-delay` near the start puts the subtitle clock behind 0:00, which no cue
    can occupy — better a raised timing error than a negative cache key."""
    from saitenka.app.native_subtitles import _subtitle_clock

    with pytest.raises(ValueError, match="non-negative"):
        _subtitle_clock(1.0, 5.0, None)

    assert _subtitle_clock(5.0, 5.0, None)[2] == 0.0  # exactly zero is fine


def _snapshot(pairs, active=None):
    """A geometry snapshot carrying exactly `pairs` of (event_id, token_index)."""
    from saitenka_subtitles.document import (
        SubtitleEventId,
        SubtitleFrameId,
        SubtitleTrackId,
    )

    track = SubtitleTrackId("track")
    listed = pairs if active is None else active
    numbers = {p[0] for p in pairs} | {p[0] for p in listed}
    events = {n: SubtitleEventId(track, n * 1000, n * 1000 + 900, 0, n) for n in numbers}
    frame = SubtitleFrameId(track, tuple(events[n] for n in sorted({p[0] for p in listed})))
    return GeometrySnapshot(
        1,
        track,
        frame,
        0,
        "full",
        tuple(
            TokenGeometry(events[event], index, Rect(index * 60, 600, 50, 40))
            for event, index in pairs
        ),
    )


def _valid(snapshot, token_count: int) -> bool:
    from saitenka.app.native_subtitles import NativeSubtitleGeometry

    return NativeSubtitleGeometry._snapshot_identities_are_valid(snapshot, token_count)


def test_geometry_matching_the_tokenized_cue_is_installable():
    assert _valid(_snapshot([(0, 0), (0, 1), (1, 2)]), token_count=3)


def test_a_live_cue_with_no_paintable_tokens_is_installable():
    """A cue that is on screen but tokenized to nothing paintable (punctuation only) is a real
    state, not a malformed snapshot — every guard vacuously holds. It cannot be expressed as a frame
    with no events: `SubtitleFrameId` rejects that, so an on-screen cue always lists one."""
    assert _valid(_snapshot([], active=[(0, 0)]), token_count=0)


def test_a_repeated_token_index_is_rejected():
    """Two boxes for one token: whichever lands second wins the hit test, so a click on that word
    can resolve to geometry the other box drew."""
    assert not _valid(_snapshot([(0, 0), (1, 0)]), token_count=2)


def test_a_repeated_event_and_token_pair_is_rejected():
    assert not _valid(_snapshot([(0, 0), (0, 0)]), token_count=2)


def test_geometry_for_an_event_the_frame_no_longer_lists_is_rejected():
    """The overlapping-cue case: the snapshot carries boxes for a cue that has since ended, and
    painting them puts hit regions over words that are no longer on screen."""
    stale = _snapshot([(0, 0), (1, 1)], active=[(0, 0)])

    assert not _valid(stale, token_count=2)


@pytest.mark.parametrize("index", [-1, 2, 99])
def test_a_token_index_outside_the_cue_is_rejected(index: int):
    """The snapshot and the tokenizer disagree about how many tokens this cue has — the exact shape
    a mid-flight cue change produces, and the one that would index past `reader.tokens`."""
    assert not _valid(_snapshot([(0, index)]), token_count=2)


def test_the_bound_is_the_token_count_not_the_box_count():
    """Two boxes against a one-token cue is out of range, even though the indices are unique and the
    events are live — the count that matters is the cue's, not the snapshot's."""
    assert _valid(_snapshot([(0, 0), (0, 1)]), token_count=2)
    assert not _valid(_snapshot([(0, 0), (0, 1)]), token_count=1)


def _visibility(reply):
    """What the ownership FSM concludes from one `sub-visibility` read."""

    class _Ipc(util.FakeIPC):
        def command(self, *_args):
            if isinstance(reply, Exception):
                raise reply
            return reply

    return NativeVisibleRenderer()._read_visibility(_Ipc())


def test_mpv_reporting_its_subtitles_hidden_is_proof_native_owns_the_pixels():
    from saitenka.app.subtitle_ownership import Visibility

    assert _visibility({"error": "success", "data": False}) is Visibility.FALSE
    assert _visibility({"error": "success", "data": True}) is Visibility.TRUE


@pytest.mark.parametrize(
    "reply",
    [
        OSError("socket gone"),
        RuntimeError("gateway down"),
        {"error": "property unavailable"},
        # The case the error check exists for: mpv reports a failure AND still carries a data
        # field. Reading the payload past the error would take that `False` as legacy proof.
        {"error": "property unavailable", "data": False},
        "not a dict",
        None,
    ],
)
def test_an_unreadable_boundary_is_unknown_and_never_legacy_proof(reply: object):
    """The asymmetry the whole handoff rests on. FALSE means mpv confirmed it stopped drawing, so
    legacy may take the pixels; anything we could not read must not be mistaken for that, or a dead
    socket hands ownership away and the frame ends up with no subtitle at all.
    """
    from saitenka.app.subtitle_ownership import Visibility

    assert _visibility(reply) is Visibility.UNKNOWN


def _box(index, x, y, w=20, h=30):
    from saitenka.app.subtitles import WordBox

    return WordBox(index, x, y, w, h)


def test_the_highlight_spans_a_whole_multi_token_term():
    """コンサート over the over-split コン. The tooltip shows the whole term, so highlighting only the
    hovered morpheme would underline half of what the user is reading."""
    from saitenka.app.subtitle_render import FOCUS_PAD, focus_rect

    boxes = [_box(0, 0, 100), _box(1, 20, 100), _box(2, 40, 100)]

    assert focus_rect(boxes, 0, (0, 2)) == (0, 100, 40 + 2 * FOCUS_PAD, 30 + 2 * FOCUS_PAD)


def test_the_highlight_covers_only_the_hovered_word_without_a_span():
    from saitenka.app.subtitle_render import FOCUS_PAD, focus_rect

    boxes = [_box(0, 0, 100), _box(1, 20, 100)]

    assert focus_rect(boxes, 1, None) == (20, 100, 20 + 2 * FOCUS_PAD, 30 + 2 * FOCUS_PAD)


def test_a_span_over_two_lines_covers_both():
    """A term wrapped across the cue's two lines: the union is the bounding box, so the highlight
    is one rectangle covering the gap rather than two disjoint pieces."""
    from saitenka.app.subtitle_render import focus_rect

    rect = focus_rect([_box(0, 200, 100), _box(1, 0, 140)], 0, (0, 2))

    assert rect is not None
    _left, top, _w, height = rect
    assert top == 100
    assert height >= 70  # spans down to the second line's bottom edge


def test_nothing_is_hovered_means_nothing_is_highlighted():
    from saitenka.app.subtitle_render import focus_rect

    assert focus_rect([_box(0, 0, 100)], -1, None) is None


def test_a_span_whose_boxes_are_gone_highlights_nothing():
    """The cue was re-rendered under the hover, so the retained indices no longer address anything.
    An empty union would be a zero-size rect at the origin — a highlight in the corner of the video.
    """
    from saitenka.app.subtitle_render import focus_rect

    assert focus_rect([_box(0, 0, 100)], 0, (5, 9)) is None


def test_the_subtitle_sits_centred_above_the_bottom_margin():
    from saitenka.app.subtitle_render import place_subtitle

    assert place_subtitle((400, 80), (1920, 1080), 60) == ((1920 - 400) // 2, 1080 - 80 - 60)


def test_the_placement_tracks_the_video_size_not_a_fixed_resolution():
    from saitenka.app.subtitle_render import place_subtitle

    hd = place_subtitle((400, 80), (1920, 1080), 60)
    retina = place_subtitle((400, 80), (3024, 1898), 60)

    assert retina[0] > hd[0] and retina[1] > hd[1]


_SUPPORTED_SETTINGS = {
    "sub-ass-override": "no",
    "sub-ass-scale-with-window": False,
    "sub-scale": 1.0,
    "sub-pos": 100.0,
    "sub-use-margins": True,
    "sub-ass-force-margins": False,
    "sub-ass-video-aspect-override": None,
    "sub-ass-use-video-data": "all",
    "sub-ass-style-overrides": None,
    "sub-scale-with-window": True,
    "sub-scale-by-window": True,
    "blend-subtitles": False,
    "sub-filter-sdh": False,
    "video-crop": "",
    "video-rotate": 0,
    "sub-font-provider": "auto",
    "embeddedfonts": False,
    "sub-fonts-dir": None,
    "sub-shaper": "complex",
    "sub-ass-justify": False,
    "sub-line-spacing": 0.0,
    "sub-hinting": "none",
    "sub-scale-signs": False,
}
_OSD = {"w": 1920, "h": 1080, "mt": 0, "mb": 0, "ml": 0, "mr": 0, "par": 1.0}
_VIDEO = {"w": 1920, "h": 1080, "par": 1.0}


def _inputs(*, osd=None, video=None, frame_size=None, authored=True, **settings):
    from saitenka.app.native_subtitles import render_inputs_of

    return render_inputs_of(
        {**_OSD, **(osd or {})},
        {**_VIDEO, **(video or {})},
        {**_SUPPORTED_SETTINGS, **settings},
        frame_size=frame_size or (1920, 1080),
        authored=authored,
    )


def test_no_gate_row_is_satisfied_only_by_an_option_mpv_does_not_have() -> None:
    """`prop("options/<name>")` returns mpv's typed value; only a *removed* option reads `None`.

    So a row that accepts `None` and nothing else refuses every track on every mpv that still has
    the option, and passes vacuously on the builds that dropped it — which is invisible when
    everyone testing runs a recent one. `sub-ass-vsfilter-aspect-compat` sat here for exactly that
    reason: mpv defaults it to `yes`, so `True is None` refused native geometry outright on
    mpv < 0.41, and 0.41 removed the option and made the row read green.
    """
    from saitenka.app.native_subtitles import _unsupported_render_inputs

    plausible = (0, 0.0, 1.0, 100.0, "", (), False, True, "no", "yes", "all", "auto")

    for name, value in _SUPPORTED_SETTINGS.items():
        if value is not None:
            continue
        accepted = [
            candidate
            for candidate in plausible
            if name
            not in _unsupported_render_inputs(
                {**_SUPPORTED_SETTINGS, name: candidate}, authored=True
            )
        ]
        assert accepted, f"{name} is accepted only when mpv does not report it"


def test_a_default_mpv_render_configuration_supports_native_geometry():
    result = _inputs()

    assert result.frame_size == (1920, 1080)
    assert result.storage_size == (1920, 1080)
    assert result.pixel_aspect == 1.0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("sub-scale", 1.5),  # scales the text away from the geometry we computed
        ("sub-pos", 50.0),  # moves it up the frame
        ("sub-use-margins", False),
        ("sub-ass-override", "force"),
        ("sub-ass-scale-with-window", True),
        ("sub-ass-use-video-data", "aspect-only"),
        ("sub-ass-style-overrides", ["Default.FontSize=60"]),
        # `=video` alone, and not for the arithmetic: it lays the cue out on `texture_w/h` AFTER the
        # user's shader hooks, which a `--glsl-shader` can resize and no property reports.
        ("blend-subtitles", "video"),
    ],
)
def test_a_setting_that_moves_or_restyles_the_text_disqualifies_geometry(name: str, value: object):
    """Every one of these has the same consequence: our boxes would be computed against a frame mpv
    is not drawing into, so hit regions land beside the words rather than on them. The error names
    the setting because a user who set it needs to know which one to undo."""
    with pytest.raises(ValueError, match=name):
        _inputs(**{name: value})


def _scaled(**settings):
    from saitenka.app.native_subtitles import _scaled_renderer_state

    return _scaled_renderer_state(_inputs(**{"sub-ass-override": "scale", **settings}).scale)


def test_override_scale_is_reproduced_rather_than_refused() -> None:
    """`scale` only sets renderer state — `ass_set_font_scale`, `ass_set_line_position`,
    `ass_set_line_spacing`, `ass_set_hinting` (`sd_ass.c:552-558,601-603`) — all of which the
    measuring renderer sets too. `force` is the one that substitutes mpv's own style into every
    event, which would make all sixteen `converted.STYLE_OPTIONS` authored-track layout inputs."""
    state = _scaled(**{"sub-scale": 0.7, "sub-pos": 95.0})

    assert state.font_scale == 0.7
    assert state.line_position == 5.0  # mpv hands libass the complement (`sd_ass.c:553`)


def test_override_no_leaves_the_renderer_at_libass_defaults() -> None:
    """The branch `configure_ass` takes under `no` assigns none of them, so reproducing a zero here
    would be claiming a value mpv never read — and would move every box on a track that was
    previously measured correctly."""
    from saitenka_subtitles import RendererState

    from saitenka.app.native_subtitles import _scaled_renderer_state

    assert _scaled_renderer_state(_inputs().scale) == RendererState()


@pytest.mark.parametrize(("name", "value"), [("sub-scale", 1.5), ("sub-pos", 50.0)])
def test_a_moving_setting_is_still_refused_when_the_override_is_off(name: str, value: object):
    """Widening `scale` must not widen `no`. mpv reads neither option on that branch, so a track
    carrying one is unchanged — but the row has to keep firing, because the value would otherwise
    reach a renderer that does apply it."""
    with pytest.raises(ValueError, match=name):
        _inputs(**{name: value})


@pytest.mark.parametrize(("name", "value"), [("sub-scale", 1.5), ("sub-pos", 90.0)])
def test_a_converted_track_refuses_the_moving_settings_even_under_scale(name: str, value: object):
    """`converted` is the FIRST disjunct of both branch conditions (`sd_ass.c:544,553`), so a
    converted track applies `sub-scale` and `100 - sub-pos` whatever the override says — while
    `_renderer_state` reproduces neither there. Widening on the override alone would accept a
    letterboxed SubRip track mpv draws at another size and height: boxes silently misplaced rather
    than a refusal, with every meter green."""
    with pytest.raises(ValueError, match=name):
        _inputs(authored=False, **{"sub-ass-override": "scale", name: value})


def test_justify_is_refused_under_scale_because_it_is_not_reproduced() -> None:
    """`--sub-ass-justify` reaches libass through `(converted || override) && ass_justify`
    (`sd_ass.c:589-591`), so `no` never lets it through on an authored track and `scale` does. It
    decides where every line of a wrapped cue starts, which is a box position."""
    with pytest.raises(ValueError, match="sub-ass-justify"):
        _inputs(**{"sub-ass-override": "scale", "sub-ass-justify": True})


@pytest.mark.parametrize(("scale_signs", "selective"), [(False, True), (True, False)])
def test_scale_signs_inverts_into_the_selective_font_scale_bit(
    *, scale_signs: bool, selective: bool
) -> None:
    """`ASS_OVERRIDE_BIT_SELECTIVE_FONT_SCALE` CONFINES the scale to dialogue, so mpv sets it
    exactly when the user did NOT ask for signs to be scaled (`sd_ass.c:577`). Read straight
    through, every positioned sign in the episode is measured at the dialogue scale."""
    assert _scaled(**{"sub-scale-signs": scale_signs}).selective_font_scale is selective


LETTERBOX = {"w": 1920, "h": 1080, "mt": 140, "mb": 140, "ml": 0, "mr": 0, "par": 1.0}


def test_blending_lays_the_cue_out_on_the_video_rectangle_not_the_window() -> None:
    """`--blend-subtitles=yes` draws the subtitle into the video texture before scaling, on an
    `mp_osd_res` mpv rebuilds from the src/dst rects (`video.c:3249-3263`): the video's on-screen
    rectangle, every margin zero, `display_par` 1. Laying out on the window instead would put the
    cue in the letterbox — the whole 280px of it — and every box beside its word."""
    result = _inputs(osd=LETTERBOX, frame_size=(1920, 1080), **{"blend-subtitles": "yes"})

    assert result.frame_size == (1920, 800)
    assert result.margins == (0, 0, 0, 0)
    assert result.box_origin == (0, 140)


def test_without_blending_the_letterbox_stays_part_of_the_frame() -> None:
    """The negative control. `--sub-use-margins=yes` is in the profile precisely so mpv may put the
    cue in the letterbox, so the unblended frame is the whole window and the origin is nothing."""
    result = _inputs(osd=LETTERBOX, frame_size=(1920, 1080))

    assert result.frame_size == (1920, 1080)
    assert result.margins == (140, 140, 0, 0)
    assert result.box_origin == (0, 0)


def test_the_blend_surface_drops_the_screens_own_aspect() -> None:
    """`display_par` is 1.0 on the blend rect, so only the video's own pixel aspect survives —
    keeping the screen's would stretch every box by it."""
    result = _inputs(
        osd={**LETTERBOX, "par": 2.0},
        video={"w": 1920, "h": 1080, "par": 1.5},
        frame_size=(1920, 1080),
        **{"blend-subtitles": "yes"},
    )

    assert result.pixel_aspect == 1.5


@pytest.mark.parametrize(("name", "value"), [("video-crop", "1280x720"), ("video-rotate", 90)])
def test_a_crop_or_rotation_is_refused_only_while_blending(name: str, value: object) -> None:
    """`_blend_space` derives the video rectangle from the premise that the src rect is the whole
    image. A crop breaks that outright and a rotation re-orients it (`aspect.c:156-163`). Neither
    changes anything the unblended path reads, so refusing them there would cost tracks for nothing.
    """
    with pytest.raises(ValueError, match=name):
        _inputs(**{name: value, "blend-subtitles": "yes"})

    assert _inputs(**{name: value}).frame_size == (1920, 1080)


@pytest.mark.parametrize(
    ("scale_with_window", "scale_by_window"),
    [(True, True), (False, True), (True, False), (False, False)],
)
def test_the_sub_scale_switches_are_reproduced_not_refused(
    *, scale_with_window: bool, scale_by_window: bool
) -> None:
    """mpv reads these two only on the forced-override branch a CONVERTED track takes, and
    `converted.font_scale` reproduces both — so they are inputs to the measurement. On the authored
    branch mpv reads `sub-ass-scale-with-window` instead and never reads `sub-scale-by-window` at
    all, which is why refusing a track for them would give up an episode over nothing."""
    result = _inputs(
        **{"sub-scale-with-window": scale_with_window, "sub-scale-by-window": scale_by_window}
    )

    assert result.scale_with_window is scale_with_window
    assert result.scale_by_window is scale_by_window


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("embeddedfonts", True),
        ("sub-fonts-dir", "/fonts"),
        ("sub-font-provider", "fontconfig"),
        ("sub-font", "Symbola"),
    ],
)
def test_a_font_setting_is_an_input_to_the_measuring_renderer_not_a_refusal(
    name: str, value: object
):
    """These four decide which faces libass loads, and `subtitle_fonts.resolve` reproduces each of
    them. Refusing a track for them — as the render-input gate used to — gave up the whole episode's
    interaction over a setting we can simply mirror."""
    result = _inputs(**{name: value})

    assert (name, repr(value)) in result.font_options


def test_the_osd_surface_wins_over_the_reported_video_size():
    """Subtitles are composited onto the OSD surface, so that is the frame the boxes belong to —
    a letterboxed video reports a different size and would offset every box by the bars."""
    result = _inputs(osd={"w": 1920, "h": 1080}, video={"w": 1280, "h": 720})

    assert result.frame_size == (1920, 1080)
    assert result.storage_size == (1280, 720)


def test_an_unreported_osd_size_never_moves_the_frame():
    """`osd-dimensions` reads 0×0 until mpv has rendered a frame, and reads a stale size across a
    resize. Neither can move the layout: the surface the host is drawing onto is the frame, and it
    is passed in rather than re-read here."""
    result = _inputs(osd={"w": None, "h": None}, frame_size=(1280, 720))

    assert result.frame_size == (1280, 720)


def test_margins_that_swallow_the_frame_are_rejected():
    """Margins at or beyond the frame leave no area to lay text into; the geometry that came back
    would be for a zero- or negative-sized region."""
    with pytest.raises(ValueError, match="osd-margins"):
        _inputs(osd={"mt": 600, "mb": 600})
    with pytest.raises(ValueError, match="osd-margins"):
        _inputs(osd={"ml": -1})


@pytest.mark.parametrize("par", [-1.0, float("nan"), float("inf")])
def test_a_meaningless_pixel_aspect_is_rejected(par: float):
    with pytest.raises(ValueError, match="pixel-aspect"):
        _inputs(video={"par": par})


def test_an_unreported_pixel_aspect_means_square_pixels():
    """mpv reports 0 for a par it does not know, and square is the right assumption — so the
    `<= 0` rejection above can only ever fire for a NEGATIVE value, never for zero."""
    assert _inputs(video={"par": 0}).pixel_aspect == 1.0
    assert _inputs(osd={"par": 0}, video={"par": 2.0}).pixel_aspect == 2.0


def test_the_profile_changes_when_any_setting_does():
    """The profile keys the geometry cache. Two different render configurations sharing a key would
    serve one's boxes for the other's frame."""
    assert _inputs().profile != _inputs(**{"sub-ass-force-margins": True}).profile


def test_the_legacy_renderer_restores_the_visibility_it_found_at_close(tmp_path: Path) -> None:
    """The teardown write that kept `deactivate` synchronous — correlated now, and it still lands.

    `activate` records what mpv held before hiding its subtitles and close puts it back. That is the
    one thing a user notices about a session that ended, and nothing asserted it: the native
    renderer's own restore was covered, the fallback's was not.
    """
    result, ipc, _backend = reader(tmp_path)
    ipc.props["sub-visibility"] = True
    renderer = SubtitleRenderer()
    result.graph.subtitle_presentation.pipeline.renderer = renderer

    assert renderer.activate(result.graph.subtitle_presentation.target()) is True
    assert ("set_property", "sub-visibility", False) in ipc.commands
    ipc.commands.clear()

    result.close()

    assert ("set_property", "sub-visibility", True) in ipc.commands


def _decision_spans(monkeypatch) -> list[dict[str, object]]:
    """Record every `subtitle_geometry_decision` span's attributes."""
    from saitenka import otel_metrics

    spans: list[dict[str, object]] = []

    class _Span:
        def __init__(self, attributes: dict[str, object]) -> None:
            self.attributes = attributes

        def set(self, key: str, value: object) -> None:
            self.attributes[key] = value

    @contextmanager
    def record(name: str, **attributes: str):
        values: dict[str, object] = dict(attributes)
        if name == "subtitle_geometry_decision":
            spans.append(values)
        yield _Span(values)

    monkeypatch.setattr(otel_metrics, "traced", record)
    return spans


@pytest.mark.parametrize("surface", [(1280, 720), (3574, 2074)])
def test_boxes_are_laid_out_in_the_surface_they_are_drawn_onto(
    tmp_path, monkeypatch, surface: tuple[int, int]
) -> None:
    """One value, not two reads of one property.

    The layout used to come from a live `osd-dimensions` read and the drawing from the host's
    latched surface. Whenever those disagreed the output was not degraded but silently wrong: every
    box carried the same scale-and-offset error for the whole episode, and nothing compared them.
    The second surface here is one `osd-dimensions` never reports, so a layout that went back to
    reading the property directly would not follow it."""
    spans = _decision_spans(monkeypatch)
    result, ipc, _backend = reader(tmp_path)
    result.graph.screen.osd = surface

    result.graph.cue.set_subtitle("猫を見る")
    settle_jobs(result, ipc)

    framed = [s for s in spans if "frame_width" in s]
    assert framed, "no decision reached the frame branch"
    assert {(s["frame_width"], s["frame_height"]) for s in framed} == {surface}
    result.close()

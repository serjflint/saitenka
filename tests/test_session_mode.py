"""The ``session: mode=…`` log line each entry point emits before the reader loop.

A bundled ``report`` needs to know which entry point produced a trace — run/attach/plugin behave
differently (async dep load, other mpv scripts sharing input). Nothing consumes the line
programmatically, so its only contract is *that it fires*; before this, no test referenced it, so a
silent removal/rename was invisible. Run-mode is smoke-testable at its seam here; attach mode connects
to a live mpv (``cli.attach`` is ``# pragma: no cover``) and is left to the live tier."""

from __future__ import annotations

import logging
import threading

import pytest

from saitenka.app.launch import run as cli_run
from saitenka.app.session.runtime import (
    SessionActs,
    SessionEntry,
    SessionFacts,
    SessionRuntime,
)
from saitenka.app.tokenize import Token


def _facts(**given) -> SessionFacts:
    """A `SessionFacts` for a stand-in: what it supplies, and a raiser for everything else.

    Total by construction, like the value it stands in for. A member this drive never uses raises
    rather than answering `None`, so a change that starts reading it fails here instead of silently
    observing nothing.
    """
    return SessionFacts(**{**dict.fromkeys(SessionFacts.__slots__, _unused), **given})


def _acts(**given) -> SessionActs:
    """The acts half of `_facts`, same contract."""
    return SessionActs(**{**dict.fromkeys(SessionActs.__slots__, _unused), **given})


def _unused(*_a, **_k):
    raise AssertionError("this drive was not supposed to reach that member")


class _FakeReader:
    """Just enough SessionController for the non-demo ``run`` branch: ``run()`` stands in for the blocking loop."""

    def __init__(self) -> None:
        self.ran = False
        self.ipc = None

    def run(self) -> None:
        self.ran = True

    @property
    def session_entry(self):
        """The shape `SessionController.session_entry` builds — the seam the entry point is driven through."""
        return SessionEntry(runtime=SessionRuntime(_facts(), _acts(), self.ipc), run=self.run)


def test_run_session_logs_mode_run_before_the_loop(caplog):
    reader = _FakeReader()
    with caplog.at_level(logging.INFO, logger="saitenka.app.launch.run"):
        cli_run._execute_reader_session(
            reader.session_entry,
            demo=cli_run.DemoSpec(),  # no demo_word / screenshot → the real interactive branch
            video=None,
            translate_key="t",
        )
    assert reader.ran  # the branch under test is the one that actually enters the loop
    assert "session: mode=run" in caplog.text


@pytest.mark.timeout(5)
def test_demo_waits_for_annotation_before_hovering_or_capturing(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class SessionController:
        osd = (1280, 720)
        ipc = None

        def __init__(self) -> None:
            self.tokens = []
            self.hovered = None

        def run(self) -> None:  # pragma: no cover — the demo branch never enters the loop
            raise AssertionError("a demo must not fall through to the interactive loop")

        @property
        def session_entry(self):
            return SessionEntry(
                runtime=SessionRuntime(
                    _facts(
                        refresh_osd=self.refresh_osd,
                        prop=self.observed_property,
                        get=self._get,
                        tokens=lambda: self.tokens,
                        is_content_token=lambda _t: True,
                        painted=lambda: True,
                    ),
                    _acts(
                        drive_annotation_once=lambda _t: None,
                        prepare_subtitle=self.prepare_subtitle_blocking,
                        prepare_hover=self.prepare_hover_blocking,
                        mark_ready=self._mark_interactive_ready,
                    ),
                    self.ipc,
                ),
                run=self.run,
            )

        def refresh_osd(self) -> None:
            pass

        def _get(self, _name):
            return "猫"

        def observed_property(self, _name):
            return {"w": 1280, "h": 720}  # mpv has published its geometry

        def prepare_subtitle_blocking(self, _text: str) -> None:
            started.set()
            assert release.wait(2)
            self.tokens = [Token("猫", "猫", "猫", "名詞", 0, 1)]

        def prepare_hover_blocking(self, index: int) -> None:
            self.hovered = index

        def _mark_interactive_ready(self) -> None:
            assert self.tokens and self.hovered == 0
            self.ready = True

    reader = SessionController()
    monkeypatch.setattr(cli_run.time, "sleep", lambda _seconds: None)
    thread = threading.Thread(
        target=cli_run._execute_reader_session,
        args=(reader.session_entry, cli_run.DemoSpec(demo_word="猫")),
        kwargs={"video": None, "translate_key": "t"},
    )
    thread.start()
    assert started.wait(1)
    assert reader.tokens == [] and reader.hovered is None

    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert reader.hovered == 0
    assert reader.ready is True


class _GeometryReader:
    """A reader whose window geometry arrives after N turns of the session loop."""

    osd = (1280, 720)

    def __init__(self, *, turns_until_geometry: int) -> None:
        self.remaining = turns_until_geometry
        self.turns: list[float | None] = []
        self.refreshed = 0

    def _drive_annotation_once(self, timeout: float | None) -> None:
        self.turns.append(timeout)
        self.remaining -= 1

    def refresh_osd(self) -> None:
        self.refreshed += 1

    def observed_property(self, _name):
        return {"w": 1920, "h": 1080} if self.remaining <= 0 else {}


def _geometry_facts(reader) -> SessionFacts:
    return _facts(refresh_osd=reader.refresh_osd, prop=reader.observed_property)


def _geometry_acts(reader) -> SessionActs:
    return _acts(drive_annotation_once=reader._drive_annotation_once)


def test_demo_waits_for_readiness_and_uses_the_owned_terminal_sequence():
    """A demo drives the session until mpv publishes its geometry rather than napping for it.

    `SessionController.osd` falls back to 720p, so a demo that composed before the real geometry landed would
    have produced a correct-looking panel sized for a window that does not exist — the failure a
    fixed sleep cannot rule out and a bounded wait on the fact can.
    """
    from saitenka.app.session.runtime import SessionRuntime

    reader = _GeometryReader(turns_until_geometry=3)
    runtime = SessionRuntime(_geometry_facts(reader), _geometry_acts(reader), ipc=None)

    assert runtime.await_render_space(timeout=5.0) is True
    assert len(reader.turns) == 3, "it drove the session per wake, rather than sleeping through it"
    assert all(t is not None and t <= 5.0 for t in reader.turns), "every wait carries the deadline"


def test_a_demo_that_never_gets_geometry_gives_up_on_its_deadline():
    """The negative control: without it the assertion above passes on a predicate stuck at True."""
    from saitenka.app.session.runtime import SessionRuntime

    clock = iter([0.0, 0.0, 1.0, 9.0])
    reader = _GeometryReader(turns_until_geometry=10**6)
    runtime = SessionRuntime(
        _geometry_facts(reader), _geometry_acts(reader), ipc=None, clock=lambda: next(clock)
    )

    assert runtime.await_render_space(timeout=5.0) is False


def test_screenshot_captures_after_readiness_and_uses_the_owned_terminal_sequence():
    """The capture follows the paint wait. A shot taken while a slot is still PENDING photographs
    whatever was on screen before it, which is a silently wrong screenshot rather than a failure."""
    order: list[str] = []

    class Runtime:
        def scroll_tooltip(self) -> None: ...
        def enable_translation(self) -> None: ...
        def mine(self, *, bulk: bool) -> None: ...

        def await_paint(self, *, timeout: float) -> bool:
            order.append(f"await_paint({timeout})")
            return True

        def capture(self, path: str) -> object:
            order.append(f"capture({path})")
            return "ok"

    cli_run._run_demo_actions(Runtime(), cli_run.DemoSpec(screenshot="/tmp/shot.png"))

    assert order == [f"await_paint({cli_run._PAINT_SETTLE_SECONDS})", "capture(/tmp/shot.png)"]

"""WP5.5: the demo/screenshot cue search is bounded by a deadline, not a retry count."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import util

from saitenka.app.launch.run import DEMO_LINE, _demo_cue_text
from saitenka.app.session_runtime import (
    SessionActs,
    SessionFacts,
    SessionRuntime,
    choose_demo_token,
)


class FakeIPC(util.FakeIPC):
    """Counts cue hops. Inherits the shared fake so the runtime egress port is present — the hop is
    a correlated write now, and a double without the port would take a branch production never does.
    """

    def __init__(self) -> None:
        super().__init__()
        self.seeks = 0

    def command(self, *args):
        if args and args[0] == "sub-seek":
            self.seeks += 1
        return super().command(*args)


def ports_for(texts: list[str], ipc: FakeIPC, clock: list[float]):
    """The two drive values whose `sub-text` follows `texts`, one value per hop.

    A stand-in for the values rather than for a `Reader`: what a cue search reads is six facts and
    one act, and standing in for those is what makes the double's surface the drive's surface.

    Each hop advances the injected clock by the time it was granted, so a search that never finds a
    cue exhausts its deadline without the test spending it — the determinism rule, and the
    difference between a 20-second suite and an instant one.
    """

    def get(name: str):
        return texts[min(ipc.seeks, len(texts) - 1)] if name == "sub-text" else None

    def drive(timeout: float | None) -> None:
        clock[0] += timeout or 0.0

    def prop(name: str):
        # mpv has already published its geometry, so the render-space wait ahead of the search
        # passes through without a turn — these tests are about what the *cue* search costs.
        return {"w": 1920, "h": 1080} if name == "osd-dimensions" else None

    unused = _never_reached
    return (
        SessionFacts(
            refresh_osd=lambda: None,
            prop=prop,
            get=get,
            tokens=unused,
            is_content_token=unused,
            osd_height=unused,
            painted=unused,
        ),
        SessionActs(
            drive_annotation_once=drive,
            prepare_subtitle=unused,
            prepare_hover=unused,
            mark_ready=unused,
            scroll_tip=unused,
            setup_secondary=unused,
            toggle_translation=unused,
            mine_current=unused,
            bulk_mine=unused,
        ),
    )


def _never_reached(*_a, **_k):
    """A member a cue search must not touch. Raising beats `None`: it names the change that broke it."""
    raise AssertionError("the cue search reached a member it does not own")


def test_a_cue_already_showing_needs_no_search() -> None:
    ipc = FakeIPC()
    clock = [0.0]
    facts, acts = ports_for(["猫を見る"], ipc, clock)

    assert (
        _demo_cue_text(
            SessionRuntime(facts, acts, ipc, clock=lambda: clock[0]),
            "/a.mkv",
        )
        == "猫を見る"
    )
    assert ipc.seeks == 0


def test_it_hops_until_a_cue_lands() -> None:
    ipc = FakeIPC()
    clock = [0.0]
    facts, acts = ports_for(["", "", "犬も見る"], ipc, clock)

    assert (
        _demo_cue_text(
            SessionRuntime(facts, acts, ipc, clock=lambda: clock[0]),
            "/a.mkv",
        )
        == "犬も見る"
    )
    assert ipc.seeks == 2  # stopped at the cue rather than running a fixed count out


def test_each_hop_is_bounded_so_the_search_keeps_seeking() -> None:
    """A step handed the whole remaining budget would park on the first wake and seek once. The
    per-hop cap is what makes this a search rather than a single long wait."""
    ipc = FakeIPC()
    waits: list[float | None] = []
    clock = [0.0]
    facts, acts = ports_for([""], ipc, clock)
    original = acts.drive_annotation_once

    def record(timeout: float | None) -> None:
        waits.append(timeout)
        original(timeout)  # still advances the clock, or the search never reaches its deadline

    # A new value rather than a patched one: the acts are frozen, which is what stops a drive from
    # rebinding what it was handed halfway through.
    acts = dataclasses.replace(acts, drive_annotation_once=record)

    _demo_cue_text(
        SessionRuntime(facts, acts, ipc, clock=lambda: clock[0]),
        "/a.mkv",
    )

    assert waits
    assert max(w for w in waits if w is not None) <= 0.12


def test_a_search_that_never_finds_a_cue_falls_back() -> None:
    """The bound is a deadline: on a slow machine a retry count means nothing, and the demo has to
    end up with *something* to render either way."""
    ipc = FakeIPC()
    clock = [0.0]
    facts, acts = ports_for([""], ipc, clock)

    assert (
        _demo_cue_text(
            SessionRuntime(facts, acts, ipc, clock=lambda: clock[0]),
            "/a.mkv",
        )
        == DEMO_LINE
    )


def test_no_video_never_seeks() -> None:
    """Nothing to seek through, so waiting could only ever time out."""
    ipc = FakeIPC()
    clock = [0.0]
    facts, acts = ports_for([""], ipc, clock)

    assert (
        _demo_cue_text(
            SessionRuntime(facts, acts, ipc, clock=lambda: clock[0]),
            None,
        )
        == DEMO_LINE
    )
    assert ipc.seeks == 0


def _tok(surface: str):
    return SimpleNamespace(surface=surface)


def test_the_demo_hovers_the_requested_word_when_it_is_present() -> None:
    tokens = [_tok("猫"), _tok("を"), _tok("見る")]

    assert choose_demo_token(tokens, "見る", lambda _t: True) == 2


def test_it_falls_back_to_the_first_content_word() -> None:
    """The requested surface is absent, so the demo must still land on something worth a tooltip —
    a particle would render an empty panel."""
    tokens = [_tok("を"), _tok("猫"), _tok("見る")]

    assert choose_demo_token(tokens, "読む", lambda t: t.surface != "を") == 1


def test_a_tokenizer_without_a_content_test_is_only_consulted_on_a_miss() -> None:
    """`is_content` is passed rather than read off the tokenizer because not every tokenizer has
    one. Resolving it eagerly turned a hit into an AttributeError on a path that never looked."""

    def explode(_token):
        raise AttributeError("is_content")

    assert choose_demo_token([_tok("猫"), _tok("見る")], "見る", explode) == 1


def test_no_content_word_anywhere_still_yields_a_renderable_index() -> None:
    """Every token is a particle. Returning -1 or raising here would abort the demo instead of
    rendering a less interesting tooltip."""
    assert choose_demo_token([_tok("を"), _tok("は")], "読む", lambda _t: False) == 0

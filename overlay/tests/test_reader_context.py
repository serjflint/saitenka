"""EpisodeContext composition + Reader delegation — the #30 lifetime split and the #100 re-slot seam.

The behavioural contract: episode-scoped state lives in one swappable object, and rebinding it resets
*all* of that state in a single move (no field can leak into the next episode). That leak-freedom is
exactly what the future file-change re-slot relies on, so it is asserted here directly.
"""

from __future__ import annotations

from overlay.app.controller import Reader
from overlay.app.reader_context import EpisodeContext


class FakeIPC:
    """Minimal mpv IPC stand-in (matches tests/test_controller.py) — enough to build a Reader."""

    def __init__(self):
        self.events = []
        self.props = {}
        self.commands = []

    def command(self, *args):
        self.commands.append(args)
        if args and args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        return {"data": None}

    def pump(self):
        pass

    def drain_events(self):
        return []


def test_episode_context_defaults_are_the_no_episode_state():
    ctx = EpisodeContext()
    assert (ctx.subtitle.jp_sid, ctx.subtitle.en_sid, ctx.subtitle.language) == (None, None, "jp")
    assert (ctx.sub_index, ctx.nav_idx, ctx.sub_settle_until, ctx.nav_prev_text) == (
        None,
        -1,
        0.0,
        "",
    )


def test_reader_delegates_episode_fields_to_the_context():
    r = Reader(FakeIPC())
    # a nested field (episode.subtitle) and a direct one (episode) both read through…
    assert r.jp_sid is r.episode.subtitle.jp_sid is None
    assert r._nav_idx == r.episode.nav_idx == -1
    # …and writes land on the owning context, under both the public and historical private names
    r.jp_sid = 3
    r.subtitle_language = "en"
    r._nav_idx = 7
    assert (r.episode.subtitle.jp_sid, r.episode.subtitle.language, r.episode.nav_idx) == (
        3,
        "en",
        7,
    )


def test_reslot_rebinds_the_episode_without_leaking_prior_state():
    r = Reader(FakeIPC())
    r.jp_sid = 5
    r.en_sid = 6
    r.subtitle_language = "en"
    r._nav_idx = 9
    r._sub_settle_until = 12.5
    r.episode.subtitle.retry_active = True  # a nested-cluster field, migrated fully off the Reader

    r.episode = EpisodeContext()  # the re-slot move: one rebind resets every episode field

    assert r.jp_sid is None
    assert r.en_sid is None
    assert r.subtitle_language == "jp"
    assert r._nav_idx == -1
    assert r._sub_settle_until == 0.0
    assert r.episode.subtitle.retry_active is False


def test_session_state_survives_an_episode_reslot():
    """The other half of the lifetime contract: session-scoped state (the deck-mined set, the Anki
    reachability cache, the backlog handle) is durable — an episode swap must NOT reset it, or #100's
    re-slot would forget what's already in the deck on every file change."""
    r = Reader(FakeIPC())
    r._mined.add("読む")
    r._anki_cache = (123.0, True)
    session_before = r.session

    r.episode = EpisodeContext()  # advance to the next file

    assert r.session is session_before  # same session object — not rebound
    assert "読む" in r._mined  # deck knowledge carried across the episode boundary
    assert r._anki_cache == (123.0, True)

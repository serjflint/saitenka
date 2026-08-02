"""Prefetch lookahead: WARM the next cues' words while the current line plays, and the cache-size /
RSS gauges the telemetry interval sampler reports."""

import queue

from overlay.app.config import PerfOptions, ReaderOptions
from overlay.app.controller import Reader
from overlay.app.sub_index import SubIndex, parse_srt
from overlay.app.subtitle_render import NullRenderer
from overlay.panel import Definition, Entry

_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\n本を読む\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\n本を書く\n\n"
    "3\n00:00:10,000 --> 00:00:12,000\n水を飲む\n"
)


class _FakeIPC:
    def __init__(self, props=None):
        self.props = props or {}
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


class _FakeDS:
    """Records every warmed word; the entry content is irrelevant to a warm job."""

    def __init__(self):
        self.warmed = []

    def entry_for(self, tok, _inflected=None):
        self.warmed.append(tok.surface)
        return Entry(headword=tok.surface, defs=[Definition("D", ["x"])])


def _reader(monkeypatch, *, lookahead, props=None):
    ipc = _FakeIPC(props)
    r = Reader(ipc, dict_set=_FakeDS())
    r.osd = (1280, 720)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r._sub_index = SubIndex(parse_srt(_SRT))
    r.prefetch_lookahead = lookahead
    return r


def _drain(r):
    out = []
    while True:
        try:
            out.append(r._prefetch_q.get_nowait())
        except queue.Empty:
            return out


def test_lookahead_warms_the_next_cues_words(monkeypatch):
    r = _reader(monkeypatch, lookahead=2)
    r.set_subtitle("本を読む")  # cue 1
    r._update_prefetch()
    surfaces = [i.token.surface for i in _drain(r)]
    # cue 2 (書く) and cue 3 (水, 飲む) get warmed; を is a particle, skipped.
    assert "書く" in surfaces and "水" in surfaces and "飲む" in surfaces


def test_lookahead_items_are_warm_only_even_while_engaged(monkeypatch):
    # Paused ⇒ the CURRENT line renders full; a future line is never engaged → always warm/unmined.
    r = _reader(monkeypatch, lookahead=1, props={"pause": True})
    r.set_subtitle("本を読む")
    r._update_prefetch()
    items = _drain(r)
    future = [i for i in items if i.token.surface == "書く"]  # only in cue 2
    assert future and all(i.full is False and i.mined is False for i in future)
    current = [i for i in items if i.token.surface == "読む"]  # only in cue 1
    assert current and all(i.full is True for i in current)


def test_lookahead_dedupes_against_the_current_line(monkeypatch):
    r = _reader(monkeypatch, lookahead=1)
    r.set_subtitle("本を読む")  # 本 is also cue 2's first word
    r._update_prefetch()
    surfaces = [i.token.surface for i in _drain(r)]
    assert surfaces.count("本") == 1  # warmed once by the current line, not again for the next


def test_no_lookahead_when_disabled(monkeypatch):
    r = _reader(monkeypatch, lookahead=0)
    r.set_subtitle("本を読む")
    r._update_prefetch()
    surfaces = [i.token.surface for i in _drain(r)]
    assert "書く" not in surfaces and "水" not in surfaces  # only the current line queued


def test_upcoming_cue_texts_bounds_at_the_tail(monkeypatch):
    r = _reader(monkeypatch, lookahead=5)
    r.set_subtitle("水を飲む")  # last cue
    assert r._upcoming_cue_texts(5) == []


def test_upcoming_cue_texts_is_empty_without_an_index(monkeypatch):
    r = _reader(monkeypatch, lookahead=2)
    r._sub_index = None
    r.set_subtitle("本を読む")
    assert r._upcoming_cue_texts(2) == []


def test_prefetch_lookahead_routes_through_reader_options():
    opts = ReaderOptions().with_overrides(prefetch_lookahead=3)
    assert opts.perf.prefetch_lookahead == 3
    assert PerfOptions().prefetch_lookahead == 0  # off by default


def test_telemetry_gauges_report_cache_occupancy(monkeypatch):
    r = _reader(monkeypatch, lookahead=0)

    class _Panel:
        def __init__(self, n):
            self.retained_nbytes = n

    r._panel_cache["a"] = _Panel(100)
    r._panel_cache["b"] = _Panel(250)
    monkeypatch.setattr(r.dict_set, "decoded_entry_count", lambda: 7, raising=False)
    gauges = r._telemetry_gauges()
    assert gauges["panel_cache.size"] == 2.0
    assert gauges["panel_cache.bytes"] == 350.0
    assert gauges["dict_cache.size"] == 7.0

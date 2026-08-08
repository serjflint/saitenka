"""Full-episode token prefetch: warm every cue of the sub index into the token cache ahead of
playback, so no cue pays cold tokenization mid-episode (a track switch supersedes a stale warm)."""

from __future__ import annotations

import overlay.app.controller as C
from overlay.app import prefetch
from overlay.app.controller import Reader
from overlay.app.sub_index import SubIndex, parse_srt
from overlay.app.subtitle_render import NullRenderer
from util import FakeIPC

_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\n本を読む\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\n水を飲む\n\n"
    "3\n00:00:10,000 --> 00:00:12,000\n山に登る\n"
)
_CUES = ("本を読む", "水を飲む", "山に登る")


class _ExistsDS:
    def terms_exist(self, _forms):
        return set()


def _reader(*, dict_set=None) -> Reader:
    reader = Reader(FakeIPC(), dict_set=dict_set)
    reader.osd = (1280, 720)
    reader.renderer = NullRenderer()
    reader._sub_index = SubIndex(parse_srt(_SRT))
    return reader


def test_warm_loop_caches_every_cue():
    reader = _reader(dict_set=_ExistsDS())
    prefetch._warm_episode_loop(reader, reader._sub_index)
    assert len(reader.token_cache) == len(_CUES)
    for cue in _CUES:
        assert reader.token_cache.get(cue) is not None


def test_warmed_cue_is_a_hit_with_no_retokenization(monkeypatch):
    reader = _reader(dict_set=_ExistsDS())
    prefetch._warm_episode_loop(reader, reader._sub_index)

    monkeypatch.setattr(
        C,
        "tokenize",
        lambda _ln: (_ for _ in ()).throw(AssertionError("re-tokenized a warmed cue")),
    )
    reader.set_subtitle("水を飲む")  # a warmed cue → served from the cache, annotated at cue time

    assert reader._sub_pending is None and [t.surface for t in reader.tokens]


def test_warm_loop_stops_when_the_index_was_replaced():
    reader = _reader(dict_set=_ExistsDS())
    stale = reader._sub_index
    reader._sub_index = SubIndex(parse_srt(_SRT))  # a track switch installed a new index object

    prefetch._warm_episode_loop(reader, stale)  # warming the OLD index must no-op

    assert len(reader.token_cache) == 0


def test_launcher_is_a_noop_without_a_dictionary():
    reader = _reader(dict_set=None)
    reader.warm_episode_tokens()
    assert reader._warmed_index is None  # never armed → nothing to warm


def test_launcher_skips_an_already_warmed_index():
    reader = _reader(dict_set=_ExistsDS())
    reader._warmed_index = reader._sub_index  # already warmed (or in flight)
    # Would raise if it re-entered the loop and re-tokenized; the guard returns before the thread.
    reader.warm_episode_tokens()
    assert reader._warmed_index is reader._sub_index

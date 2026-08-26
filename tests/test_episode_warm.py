"""Full-episode token prefetch: warm every cue of the sub index into the token cache ahead of
playback, so no cue pays cold tokenization mid-episode (a track switch supersedes a stale warm)."""

from __future__ import annotations

from util import FakeIPC

from saitenka.app.session.controller import SessionController
from saitenka.app.subtitle_render import NullRenderer
from saitenka.subtitles import CueIndex, parse_srt

_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\n本を読む\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\n水を飲む\n\n"
    "3\n00:00:10,000 --> 00:00:12,000\n山に登る\n"
)
_CUES = ("本を読む", "水を飲む", "山に登る")


class _ExistsDS:
    def terms_exist(self, _forms):
        return set()


class _ImmediateThread:
    def __init__(self, *, target, args=(), **_kwargs) -> None:
        self._target = target
        self._args = args

    def start(self) -> None:
        self._target(*self._args)


def _reader(*, dict_set=None) -> SessionController:
    reader = SessionController(FakeIPC(), dict_set=dict_set)
    reader.osd = (1280, 720)
    reader.renderer = NullRenderer()
    reader.episode.sub_index = CueIndex(parse_srt(_SRT))
    return reader


def test_warm_loop_tokenizes_every_cue(monkeypatch):
    reader = _reader(dict_set=_ExistsDS())
    calls: list[str] = []
    real = reader.profile_controller.tokenizer.tokenize
    monkeypatch.setattr(
        reader.profile_controller.tokenizer,
        "tokenize",
        lambda line: calls.append(line) or real(line),
    )
    monkeypatch.setattr(
        "saitenka.app.features.annotation.annotation_controller.threading.Thread",
        _ImmediateThread,
    )

    reader.warm_episode_tokens()

    assert calls == list(_CUES)


def test_warmed_cue_is_a_hit_with_no_retokenization(monkeypatch):
    reader = _reader(dict_set=_ExistsDS())
    monkeypatch.setattr(
        "saitenka.app.features.annotation.annotation_controller.threading.Thread",
        _ImmediateThread,
    )
    reader.warm_episode_tokens()

    monkeypatch.setattr(
        reader.profile_controller.tokenizer,
        "tokenize",
        lambda _ln: (_ for _ in ()).throw(AssertionError("re-tokenized a warmed cue")),
    )
    reader.set_subtitle("水を飲む")  # a warmed cue → served from the cache, annotated at cue time

    assert reader.annotation_controller.view.pending_text is None and [
        t.surface for t in reader.tokens
    ]


def test_warm_uses_the_replacement_index(monkeypatch):
    reader = _reader(dict_set=_ExistsDS())
    reader.episode.sub_index = CueIndex(parse_srt("1\n00:00:01,000 --> 00:00:03,000\n犬\n"))
    calls: list[str] = []
    real = reader.profile_controller.tokenizer.tokenize
    monkeypatch.setattr(
        reader.profile_controller.tokenizer,
        "tokenize",
        lambda line: calls.append(line) or real(line),
    )
    monkeypatch.setattr(
        "saitenka.app.features.annotation.annotation_controller.threading.Thread",
        _ImmediateThread,
    )

    reader.warm_episode_tokens()

    assert calls == ["犬"]


def test_launcher_is_a_noop_without_a_dictionary(monkeypatch):
    reader = _reader(dict_set=None)
    monkeypatch.setattr(
        reader.profile_controller.tokenizer,
        "tokenize",
        lambda _line: (_ for _ in ()).throw(AssertionError("unexpected warm")),
    )
    reader.warm_episode_tokens()


def test_launcher_skips_an_index_the_owner_already_admitted(monkeypatch):
    reader = _reader(dict_set=_ExistsDS())
    monkeypatch.setattr(
        "saitenka.app.features.annotation.annotation_controller.threading.Thread",
        _ImmediateThread,
    )
    reader.warm_episode_tokens()
    monkeypatch.setattr(
        reader.profile_controller.tokenizer,
        "tokenize",
        lambda _line: (_ for _ in ()).throw(AssertionError("re-warmed an owned index")),
    )

    reader.warm_episode_tokens()

    assert reader.annotation_controller.view.pending_text is None

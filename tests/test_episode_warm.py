"""Full-episode token prefetch: warm every cue of the sub index into the token cache ahead of
playback, so no cue pays cold tokenization mid-episode (a track switch supersedes a stale warm)."""

from __future__ import annotations

import pytest
from session_builder import build_session
from util import FakeIPC

from saitenka.app.session.factory import SessionInfrastructure, SessionServices
from saitenka.app.subtitle_render import NullRenderer

pytestmark = [pytest.mark.integration, pytest.mark.timeout(5)]

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


def _reader(monkeypatch, *, dict_set=None):
    reader = build_session(
        FakeIPC(),
        services=SessionServices(dictionaries=dict_set),
        infrastructure=SessionInfrastructure(renderer=NullRenderer()),
    )
    # Independent analysis shares this tokenizer.
    reader.graph.ipc.close_runtime_job_lane("episode-analysis")
    calls: list[str] = []
    real = reader.graph.profile.profile.tokenizer.tokenize
    monkeypatch.setattr(
        reader.graph.profile.profile.tokenizer,
        "tokenize",
        lambda line: calls.append(line) or real(line),
    )
    monkeypatch.setattr(
        "saitenka.app.features.annotation.annotation_controller.threading.Thread",
        _ImmediateThread,
    )
    return reader, calls


def _load(reader, path, text=_SRT):
    path.write_text(text, encoding="utf-8")
    reader.graph.subtitle_navigation.load_index(path)


def test_warm_loop_tokenizes_every_cue(monkeypatch, tmp_path):
    reader, calls = _reader(monkeypatch, dict_set=_ExistsDS())
    _load(reader, tmp_path / "episode.srt")
    assert calls == list(_CUES)

    reader.graph.profile_integration.warm_episode()
    assert calls == list(_CUES)

    for text in _CUES:
        reader.graph.playback.observe("sub-text", text)
        reader.pump()
        assert (
            "".join(t.surface for t in reader.graph.subtitle_presentation.cue.current.tokens)
            == text
        )
    assert calls == list(_CUES)


def test_warm_uses_the_replacement_index(monkeypatch, tmp_path):
    reader, calls = _reader(monkeypatch, dict_set=_ExistsDS())
    _load(reader, tmp_path / "episode.srt")
    assert calls == list(_CUES)
    calls.clear()

    _load(reader, tmp_path / "replacement.srt", "1\n00:00:01,000 --> 00:00:03,000\n犬\n")

    assert calls == ["犬"]


def test_launcher_is_a_noop_without_a_dictionary(monkeypatch, tmp_path):
    reader, calls = _reader(monkeypatch)

    _load(reader, tmp_path / "episode.srt")

    assert calls == []

"""Per-cue tokenization cache + plain-then-upgrade.

The cache (:mod:`saitenka.app.token_cache`) memoizes a COMPLETE, non-empty tokenization so a repeated
line is a hit; a pre-deps (incomplete) or empty result is never stored, so a later identical line
re-attempts. At the controller seam, a cue whose annotation can't complete yet (dictionaries still
loading) draws PLAIN at cue time and upgrades in place once deps land.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future
from itertools import starmap

import dicthelp
import pytest
from saitenka_subtitles import CueIndex
from saitenka_subtitles.model import Cue
from saitenka_tokenize.japanese import Token
from session_builder import build_session
from util import FakeIPC, RecordingRasterProvider, await_ready

from saitenka.app.features.profiles.dependencies import DependencyBundle
from saitenka.app.session.factory import SessionInfrastructure, SessionServices
from saitenka.app.subtitle_render import SubtitleRenderer
from saitenka.app.token_cache import TokenCache, TokenizedCue, event_lines
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome


def _tok(surface: str) -> Token:
    return Token(surface, surface, surface, "名詞", 0, len(surface))


def _cue(*surfaces: str) -> TokenizedCue:
    toks = [_tok(s) for s in surfaces]
    return TokenizedCue(lines=[toks], tokens=toks, styles=None)


class _ExistsDS:
    """A dict set exposing only the ``terms_exist`` capability the controller probes — its presence is
    what makes a tokenization 'complete' (cacheable, annotated-not-plain)."""

    def terms_exist(self, _forms):
        return set()  # nothing merges, but the capability is present


def test_empty_tokenization_is_never_stored():
    cache = TokenCache()
    cache.put("　", (), TokenizedCue(lines=[], tokens=[], styles=None))  # no tokens
    assert cache.get("　", ()) is None
    assert len(cache) == 0


def test_lru_evicts_oldest_beyond_capacity():
    cache = TokenCache(maxsize=2)
    cache.put("a", (), _cue("a"))
    cache.put("b", (), _cue("b"))
    cache.get("a", ())  # touch → "b" is now the oldest
    cache.put("c", (), _cue("c"))
    assert cache.get("b", ()) is None  # evicted
    assert cache.get("a", ()) is not None and cache.get("c", ()) is not None


# --- event_lines: where one subtitle event ends inside a frame -----------------------------------


def _index(*cues: tuple[float, float, str]) -> CueIndex:
    return CueIndex(list(starmap(Cue, cues)))


@pytest.mark.parametrize(
    ("index", "norm", "expected"),
    [
        (_index((0, 5, "猫"), (0, 5, "犬\n鳥")), "猫\n犬\n鳥", (1, 2)),
        (_index((0, 5, "猫\n犬")), "猫\n犬", ()),  # one wrapped event
        (None, "猫\n犬", ()),  # no index: the old single sentence
        (_index((0, 5, "猫"), (0, 5, "犬")), "猫\n馬", ()),  # not in the index
        (_index((0, 5, "猫 犬"), (0, 5, "鳥")), "猫\n犬 鳥", ()),  # same text up to whitespace only
        # The same text drawn as two events and as one wrapped event: ambiguous, so unsplit.
        (_index((0, 5, "猫"), (0, 5, "犬"), (100, 105, "猫\n犬")), "猫\n犬", ()),
    ],
    ids=["co-timed", "wrapped", "no-index", "unknown", "whitespace", "ambiguous"],
)
def test_event_lines_splits_only_a_frame_the_index_proves(index, norm, expected):
    assert event_lines(norm, index) == expected


class _InlineAnnotationIPC(FakeIPC):
    def __init__(self) -> None:
        super().__init__()
        self._annotation_handler = None

    def register_runtime_job_lane(self, name, policy, handler) -> bool:  # noqa: ARG002
        if name != "cue-annotation":
            return False
        self._annotation_handler = handler
        return True

    def submit_runtime_job(self, **kwargs) -> bool:
        if kwargs["lane"] != "cue-annotation" or self._annotation_handler is None:
            return False
        completion = self._annotation_handler(kwargs["request"], threading.Event())
        kwargs["on_finished"](
            EffectFinished(
                EffectId(1),
                kwargs["owner"],
                kwargs["identity"],
                EffectOutcome.SUCCEEDED,
                result=completion,
            )
        )
        return True


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_cue_while_dicts_load_draws_plain_then_upgrades_on_deps_ready(monkeypatch):
    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1920, "h": 1080}
    provider = RecordingRasterProvider()
    reader = build_session(
        ipc, infrastructure=SessionInfrastructure(renderer=SubtitleRenderer(provider))
    )
    reader.graph.presentation.refresh_osd()
    text = "猫を見る"
    reader.graph.playback.observe("sub-text", text)
    reader.pump()
    assert provider.styles[-1] == "plain"
    assert "".join(t.surface for t in reader.graph.subtitle_presentation.cue.current.tokens) == text

    calls: list[str] = []
    real = reader.graph.profile.profile.tokenizer.tokenize
    monkeypatch.setattr(
        reader.graph.profile.profile.tokenizer,
        "tokenize",
        lambda line: calls.append(line) or real(line),
    )
    deps = Future()
    reader.graph.profile.load({}, prebuilt=deps)

    deps.set_result(
        DependencyBundle(reader.graph.profile.identity, dictionaries=dicthelp.load_set())
    )
    ipc.fire_runtime_timer("lifecycle:deps-ready")
    await_ready(
        lambda: provider.styles[-1] == "styled",
        "ready dependencies did not upgrade the cue",
        pump=reader.pump,
        timeout=2,
    )

    assert "".join(t.surface for t in reader.graph.subtitle_presentation.cue.current.tokens) == text
    assert [line for line in calls if line] == [text]
    reader.graph.playback.observe("sub-text", "")
    reader.graph.playback.observe("sub-text", text)
    reader.pump()
    assert provider.styles[-1] == "styled"
    assert "".join(t.surface for t in reader.graph.subtitle_presentation.cue.current.tokens) == text
    assert [line for line in calls if line] == [text]


@pytest.mark.timeout(5)
def test_annotation_failure_keeps_plain_subtitle_on_later_redraw(make_session):
    reader = make_session(
        _InlineAnnotationIPC(), services=SessionServices(dictionaries=_ExistsDS())
    )
    reader.graph.screen.osd = (1920, 1080)
    provider = RecordingRasterProvider()
    reader.graph.subtitle_presentation.renderer = SubtitleRenderer(provider)

    class FailingTokenizer:
        def tokenize(self, _line: str):
            raise ValueError("broken tokenizer")

    reader.graph.profile.profile.use_tokenizer(FailingTokenizer())

    reader.graph.profile_integration.enable_async_annotation()
    reader.graph.profile_integration.dependencies_changed()
    reader.graph.cue.set_subtitle("猫")
    reader.graph.interaction.settle()
    reader.graph.subtitle_presentation.draw()
    reader.close()

    assert provider.styles == ["plain", "plain"]

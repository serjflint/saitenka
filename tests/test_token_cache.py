"""Per-cue tokenization cache + plain-then-upgrade.

The cache (:mod:`saitenka.app.token_cache`) memoizes a COMPLETE, non-empty tokenization so a repeated
line is a hit; a pre-deps (incomplete) or empty result is never stored, so a later identical line
re-attempts. At the controller seam, a cue whose annotation can't complete yet (dictionaries still
loading) draws PLAIN at cue time and upgrades in place once deps land.
"""

from __future__ import annotations

import threading

import pytest
from saitenka_tokenize.japanese import Token
from session_builder import TestSession, build_session
from util import FakeIPC, RecordingRasterProvider

from saitenka.app.session.factory import SessionServices
from saitenka.app.subtitle_render import NullRenderer, SubtitleRenderer
from saitenka.app.token_cache import TokenCache, TokenizedCue
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


# --- TokenCache unit: the two never-cache invariants + LRU ----------------------------------------


def test_get_returns_a_stored_complete_cue():
    cache = TokenCache()
    cue = _cue("猫")
    cache.put("猫", cue)
    assert cache.get("猫") is cue
    assert cache.get("犬") is None


def test_empty_tokenization_is_never_stored():
    cache = TokenCache()
    cache.put("　", TokenizedCue(lines=[], tokens=[], styles=None))  # no tokens
    assert cache.get("　") is None
    assert len(cache) == 0


def test_incomplete_tokenization_is_never_stored():
    """A pre-deps tokenization (no compound-merge dict) must not be memoized — the negative-cache the
    issue forbids, so a later identical line re-attempts once the dicts load."""
    cache = TokenCache()
    cache.put("猫", _cue("猫"), complete=False)
    assert cache.get("猫") is None


def test_lru_evicts_oldest_beyond_capacity():
    cache = TokenCache(maxsize=2)
    cache.put("a", _cue("a"))
    cache.put("b", _cue("b"))
    cache.get("a")  # touch → "b" is now the oldest
    cache.put("c", _cue("c"))
    assert cache.get("b") is None  # evicted
    assert cache.get("a") is not None and cache.get("c") is not None


# --- controller seam: plain-then-upgrade + cache hit ----------------------------------------------


def _reader(dict_set=None) -> TestSession:
    reader = build_session(FakeIPC(), services=SessionServices(dictionaries=dict_set))
    reader.graph.screen.osd = (1920, 1080)
    reader.graph.subtitle_presentation.renderer = NullRenderer()
    return reader


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


def test_cue_while_dicts_load_draws_plain_then_upgrades_on_deps_ready():
    """A cue renders plain at cue time on a miss while dicts load, then upgrades in place."""
    reader = _reader(dict_set=None)  # dicts still loading

    reader.graph.cue.set_subtitle("猫を見る")

    assert reader.graph.annotation.view.pending_text == "猫を見る"  # renderer draws plain while set
    assert (
        reader.graph.subtitle_presentation.cue.current.tokens
    )  # tokens ARE populated (fast) for hover/mining — only the DRAW is plain

    reader.graph.profile.profile.replace_dictionary_set(
        _ExistsDS()
    )  # deps land → reader_deps re-renders the on-screen cue
    reader.graph.cue.set_subtitle("猫を見る")

    assert reader.graph.annotation.view.pending_text is None  # now annotated in place


def test_upgrade_re_attempts_rather_than_serving_the_incomplete_result(monkeypatch):
    """DoD (b) negative control: the pre-deps miss is not cached, so the deps-ready call re-tokenizes
    (a MISS) instead of serving the stale, unannotated result. Would fail if the miss were cached."""
    reader = _reader(dict_set=None)
    reader.graph.cue.set_subtitle("本を読む")

    calls: list[str] = []
    real = reader.graph.profile.profile.tokenizer.tokenize
    monkeypatch.setattr(
        reader.graph.profile.profile.tokenizer,
        "tokenize",
        lambda ln: calls.append(ln) or real(ln),
    )

    reader.graph.profile.profile.replace_dictionary_set(_ExistsDS())
    reader.graph.cue.set_subtitle("本を読む")

    assert calls == ["本を読む"]  # re-attempted (a cached miss would have skipped tokenize)


def test_repeated_line_is_a_cache_hit_and_skips_tokenization(monkeypatch):
    reader = _reader(dict_set=_ExistsDS())
    reader.graph.cue.set_subtitle("水を飲む")  # miss → tokenized + cached, annotated (dicts ready)
    assert reader.graph.annotation.view.pending_text is None

    monkeypatch.setattr(
        reader.graph.profile.profile.tokenizer,
        "tokenize",
        lambda _ln: (_ for _ in ()).throw(AssertionError("re-tokenized a cached line")),
    )
    reader.graph.cue.set_subtitle("水を飲む")  # hit → no tokenize

    assert (
        reader.graph.annotation.view.pending_text is None
        and reader.graph.subtitle_presentation.cue.current.tokens
    )


# --- renderer: plain vs annotated follows the owner's pending state -------------------------------


def test_renderer_draws_plain_while_a_cue_is_pending():
    reader = _reader(dict_set=None)
    provider = RecordingRasterProvider()
    reader.graph.subtitle_presentation.renderer = SubtitleRenderer(provider)

    reader.graph.cue.set_subtitle("猫")
    reader.graph.profile.profile.replace_dictionary_set(_ExistsDS())
    reader.graph.cue.set_subtitle("猫")

    assert provider.styles == ["plain", "styled"]


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

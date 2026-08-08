"""Per-cue tokenization cache + plain-then-upgrade.

The cache (:mod:`overlay.app.token_cache`) memoizes a COMPLETE, non-empty tokenization so a repeated
line is a hit; a pre-deps (incomplete) or empty result is never stored, so a later identical line
re-attempts. At the controller seam, a cue whose annotation can't complete yet (dictionaries still
loading) draws PLAIN at cue time and upgrades in place once deps land.
"""

from __future__ import annotations

import overlay.app.controller as C
from overlay.app.controller import Reader
from overlay.app.subtitle_render import NullRenderer, SubtitleRenderer
from overlay.app.subtitles import SubtitleRender
from overlay.app.token_cache import TokenCache, TokenizedCue
from overlay.app.tokenize import Token
from PIL import Image
from util import FakeIPC


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


def _reader(dict_set=None) -> Reader:
    reader = Reader(FakeIPC(), dict_set=dict_set)
    reader.osd = (1920, 1080)
    reader.renderer = NullRenderer()
    return reader


def test_cue_while_dicts_load_draws_plain_then_upgrades_on_deps_ready():
    """A cue renders plain at cue time on a miss while dicts load, then upgrades in place."""
    reader = _reader(dict_set=None)  # dicts still loading

    reader.set_subtitle("猫を見る")

    assert reader._sub_pending == "猫を見る"  # renderer draws plain while set
    assert reader.tokens  # tokens ARE populated (fast) for hover/mining — only the DRAW is plain
    assert len(reader.token_cache) == 0  # incomplete → not memoized

    reader.dict_set = _ExistsDS()  # deps land → reader_deps re-renders the on-screen cue
    reader.set_subtitle("猫を見る")

    assert reader._sub_pending is None  # now annotated in place
    assert reader.token_cache.get("猫を見る") is not None  # complete → cached


def test_upgrade_re_attempts_rather_than_serving_the_incomplete_result(monkeypatch):
    """DoD (b) negative control: the pre-deps miss is not cached, so the deps-ready call re-tokenizes
    (a MISS) instead of serving the stale, unannotated result. Would fail if the miss were cached."""
    reader = _reader(dict_set=None)
    reader.set_subtitle("本を読む")

    calls: list[str] = []
    real = C.tokenize
    monkeypatch.setattr(C, "tokenize", lambda ln: calls.append(ln) or real(ln))

    reader.dict_set = _ExistsDS()
    reader.set_subtitle("本を読む")

    assert calls == ["本を読む"]  # re-attempted (a cached miss would have skipped tokenize)


def test_repeated_line_is_a_cache_hit_and_skips_tokenization(monkeypatch):
    reader = _reader(dict_set=_ExistsDS())
    reader.set_subtitle("水を飲む")  # miss → tokenized + cached, annotated (dicts ready)
    assert reader._sub_pending is None

    monkeypatch.setattr(
        C,
        "tokenize",
        lambda _ln: (_ for _ in ()).throw(AssertionError("re-tokenized a cached line")),
    )
    reader.set_subtitle("水を飲む")  # hit → no tokenize

    assert reader._sub_pending is None and reader.tokens


# --- renderer: plain vs annotated follows _sub_pending --------------------------------------------


def test_renderer_draws_plain_while_a_cue_is_pending(monkeypatch):
    reader = _reader(dict_set=_ExistsDS())
    reader.renderer = SubtitleRenderer()
    reader.subtitle_language = "jp"
    reader.tokens = [_tok("猫")]
    reader.lines = [[_tok("猫")]]
    reader.sub_text = "猫"
    drew: list[str] = []
    stub = SubtitleRender(Image.new("RGBA", (20, 10)), [])
    monkeypatch.setattr(
        "overlay.app.subtitle_render.render_plain_subtitle",
        lambda *_a, **_k: drew.append("plain") or stub,
    )
    monkeypatch.setattr(
        "overlay.app.subtitle_render.render_subtitle",
        lambda *_a, **_k: drew.append("annotated") or stub,
    )

    reader._sub_pending = "猫"
    reader._draw_subtitle()
    reader._sub_pending = None
    reader._draw_subtitle()

    assert drew == ["plain", "annotated"]

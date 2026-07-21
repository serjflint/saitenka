"""Jimaku filename parsing + Amazon inline-furigana stripping."""

import pytest

from overlay.app.jimaku import JimakuClient, JimakuError, parse_filename
from overlay.app.tokenize import tokenize


def test_parse_filename():
    assert parse_filename(
        "[Erai-raws] Nippon Sangoku - 10 [1080p AMZN WEBRip HEVC EAC3][MultiSub][189B848D].mkv"
    ) == ("Nippon Sangoku", 10)
    assert parse_filename("[SubsPlease] Sousou no Frieren - 28 (1080p) [ABCD].mkv") == (
        "Sousou no Frieren",
        28,
    )
    title, _ep = parse_filename("Some.Movie.2021.1080p.mkv")
    assert "Some Movie" in title


def test_jimaku_requires_key(monkeypatch):
    import overlay.app.jimaku as jm

    monkeypatch.delenv("JIMAKU_API_KEY", raising=False)
    monkeypatch.setattr(jm, "keychain_get", lambda: None)  # no key anywhere → must raise
    with pytest.raises(JimakuError):
        JimakuClient()


def test_jimaku_client_uses_key():
    c = JimakuClient(api_key="test-key")
    assert c.api_key == "test-key"
    assert c.base.endswith("/api")


def test_strip_inline_furigana_names():
    # Amazon ASS bakes the reading inline after the kanji; it must be removed for tokenization.
    out = [t.surface for t in tokenize("龍門光英りゅうもんみつひでは―")]
    assert out == ["龍門", "光英", "は", "―"]
    assert "りゅうもん" not in out


def test_strip_inline_furigana_keeps_trailing_particle():
    out = "".join(t.surface for t in tokenize("賀来泰明かくやすあきさえも"))
    assert "かくやすあき" not in out
    assert out.endswith("さえも")  # the trailing particles survive


def test_strip_does_not_touch_normal_okurigana():
    # 読む / 食べた etc. must be untouched (the hiragana is grammar, not furigana)
    assert [t.surface for t in tokenize("本を読む")] == ["本", "を", "読む"]
    before = tokenize("更には", strip_furigana=False)
    after = tokenize("更には", strip_furigana=True)
    assert [t.surface for t in before] == [t.surface for t in after]

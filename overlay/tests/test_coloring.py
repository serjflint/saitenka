"""Coloring: data loaders, N+1 algorithm, and the SubMiner priority model."""

from pathlib import Path

import dicthelp
import pytest

from overlay.app.scoring import FUNCTION_POS, Palette, Scorer, mark_n_plus_one
from overlay.app.tokenize import tokenize
from overlay.app.wordlists import FreqDict, KnownWords

PAL = Palette()


@pytest.fixture
def jlpt():
    """The bundled JLPT dict loaded from a per-test hermetic DB (imported on first use)."""
    return dicthelp.load_jlpt()


# --- data loaders ---------------------------------------------------------------------------------


def test_jlpt_levels(jlpt):
    assert jlpt.level("会う", None, "あう") == "N5"
    assert jlpt.level("相", None, "あい") == "N1"
    assert jlpt.level("完全に無い語", None, None) is None


def test_jlpt_keeps_highest_level(jlpt):
    # every mapped level is one of N1..N5
    assert set(jlpt.by_key.values()) <= {"N1", "N2", "N3", "N4", "N5"}


def test_freq_banding_math():
    assert FreqDict.band(1, top_x=10000, bands=5) == 1
    assert FreqDict.band(2000, top_x=10000, bands=5) == 1
    assert FreqDict.band(2001, top_x=10000, bands=5) == 2
    assert FreqDict.band(10000, top_x=10000, bands=5) == 5
    assert FreqDict.band(10001, top_x=10000, bands=5) is None


def test_known_words_reading_fallback():
    kw = KnownWords.from_set(["読む", "ほん"])
    assert kw.is_known("読む")
    assert kw.is_known("誰も知らない", "ほん")  # matches on reading form
    assert not kw.is_known("知らない語")


# --- N+1 algorithm --------------------------------------------------------------------------------


def test_n_plus_one_single_unknown():
    toks = tokenize("私は本を読む")  # content: 本, 読む
    known = [t.surface in ("私", "本") for t in toks]
    targets = mark_n_plus_one(toks, known, min_words=2)
    surfaces = {toks[i].surface for i in targets}
    assert surfaces == {"読む"}  # the one unknown content word


def test_n_plus_one_needs_min_words():
    toks = tokenize("本だ")  # too few content words
    known = [False] * len(toks)
    assert mark_n_plus_one(toks, known, min_words=3) == set()


def test_n_plus_one_not_fired_with_two_unknowns():
    toks = tokenize("新しい本を読む")  # 新しい, 本, 読む all unknown → 3 candidates
    known = [False] * len(toks)
    assert mark_n_plus_one(toks, known, min_words=3) == set()


# --- priority model -------------------------------------------------------------------------------


def _scorer(known_words, jlpt):
    return Scorer(known=KnownWords.from_set(known_words), jlpt=jlpt, enable_freq=False)


def test_priority_n_plus_one_over_known_and_base(jlpt):
    line = "私は本を読む"
    toks = tokenize(line)
    styles = _scorer(["私", "本"], jlpt).score_line(toks)
    by = {t.surface: s for t, s in zip(toks, styles, strict=True)}
    assert by["本"].color == PAL.known
    assert by["読む"].tag.startswith("n+1")
    assert by["読む"].color == PAL.n_plus_one
    assert by["は"].color == PAL.base  # function word stays base


def test_function_words_never_colored_by_freq(jlpt):
    zip_path = next(iter(sorted(Path("../tools/freq").glob("*.zip"))), None)
    if zip_path is None:
        pytest.skip("freq zips are user-supplied (not shipped in-repo) — none present")
    fq = dicthelp.load_freqdict(str(zip_path))
    sc = Scorer(known=KnownWords.from_set([]), freq=fq, jlpt=jlpt)
    toks = tokenize("私は本を読む")
    styles = sc.score_line(toks)
    for t, s in zip(toks, styles, strict=True):
        if t.pos in FUNCTION_POS:
            assert s.color == PAL.base


def test_jlpt_underline_is_additive(jlpt):
    # a known word that also has a JLPT level → known text color + JLPT underline
    toks = tokenize("会う")
    s = _scorer(["会う"], jlpt).score_line(toks)[0]
    assert s.color == PAL.known
    assert s.underline == PAL.jlpt["N5"]


# --- KnownWords from Anki: furigana fields + missing-field tolerance -------------------------------


def test_field_forms_parses_furigana():
    from overlay.app.wordlists import _field_forms

    # Kanji Study 'EntryFurigana' → both plain surface and reading
    assert set(_field_forms("お 孫[まご]さん")) == {"お孫さん", "おまごさん"}
    assert _field_forms("通[とお]り") == ["とおり"] or set(_field_forms("通[とお]り")) == {
        "通り",
        "とおり",
    }
    assert _field_forms("<b>奉書</b>") == ["奉書"]  # HTML stripped, no brackets
    assert _field_forms("   ") == []


def test_from_ankiconnect_uses_entry_and_furigana(monkeypatch):
    import urllib.request

    from overlay.app.wordlists import KnownWords

    notes = {
        1: {
            "modelName": "Kanji Study Word Model v3",
            "fields": {
                "Entry": {"value": "お孫さん"},
                "EntryFurigana": {"value": "お 孫[まご]さん"},
                "Meaning": {"value": "<b>grandchild</b>"},
            },
        }
    }

    class FakeResp:
        def __init__(self, payload):
            self._p = payload

        def read(self):
            return self._p

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        import json as _json

        body = _json.loads(req.data)
        if body["action"] == "findNotes":
            return FakeResp(b'{"result": [1]}')
        return FakeResp(_json.dumps({"result": [notes[1]]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # the user's fields Expression/Word don't exist here; Entry does, and EntryFurigana is auto-scanned
    kw = KnownWords.from_ankiconnect({"Saitenka::Known": ["Entry", "Expression", "Word"]})
    assert kw.is_known("お孫さん")  # via Entry
    assert kw.is_known("おまごさん")  # reading recovered from EntryFurigana

"""Mining: card builder, dedup query, sentence bolding, media args, toast (no real Anki add)."""

from overlay.app.anki import MineConfig, bold_word, build_note
from overlay.app.lookup import card_for
from overlay.app.media import Timespan, clip_audio
from overlay.app.toast import render_toast
from overlay.app.tokenize import tokenize


def test_card_data_from_token():
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    card = card_for(tok)
    assert card.expression == "読む"
    assert card.reading == "よむ"
    assert card.idseq.isdigit()  # JMdict ent_seq
    assert "to read" in card.glossary_html
    assert card.glossary_html.startswith("<ol>")


def test_bold_word():
    assert bold_word("私は本を読む", "本") == "私は<b>本</b>を読む"
    assert bold_word("no match here", "本") == "no match here"


def test_build_note_maps_lapis_fields():
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    note = build_note(
        MineConfig(),
        card_for(tok),
        "本を<b>読む</b>",
        picture="p.jpg",
        audio="a.mp3",
        misc="ep10 · 10:03",
    )
    f = note["fields"]
    assert note["modelName"] == "Lapis"
    assert note["deckName"] == "Saitenka::Mining"
    assert f["Expression"] == "読む"
    assert f["ExpressionReading"] == "よむ"
    assert f["Sentence"] == "本を<b>読む</b>"
    assert f["Picture"] == '<img src="p.jpg">'
    assert f["SentenceAudio"] == "[sound:a.mp3]"
    assert f["MiscInfo"] == "ep10 · 10:03"
    assert f["IsSentenceCard"] == "1"
    assert note["options"]["allowDuplicate"] is False
    assert "saitenka-overlay" in note["tags"]


def test_build_note_writes_frequency_fields():
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    note = build_note(
        MineConfig(), card_for(tok), "s", freq_html="<ul><li>FreqA: 12</li></ul>", freq_sort="12"
    )
    assert note["fields"]["Frequency"] == "<ul><li>FreqA: 12</li></ul>"  # plan: freq → Frequency
    assert note["fields"]["FreqSort"] == "12"


def test_build_note_merges_source_tags():
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    note = build_note(
        MineConfig(),
        card_for(tok),
        "s",
        tags=["saitenka::mined", "saitenka::source::Nippon_Sangoku", "saitenka::ep::10"],
    )
    assert "saitenka-overlay" in note["tags"]  # static tool tag kept
    assert "saitenka::source::Nippon_Sangoku" in note["tags"]  # + per-card source/episode
    assert "saitenka::ep::10" in note["tags"]
    assert len(note["tags"]) == len(set(note["tags"]))  # deduped


def test_custom_field_map_only_writes_mapped():
    cfg = MineConfig(model="Animecards", fields={"expression": "Word", "reading": "Reading"})
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    note = build_note(cfg, card_for(tok), "s")
    assert set(note["fields"]) == {"Word", "Reading", "IsSentenceCard"}
    assert note["fields"]["Word"] == "読む"


def test_timespan_padding():
    ts = Timespan(10.0, 12.0).padded(0.5)
    assert ts.start == 9.5 and ts.end == 12.5
    assert Timespan(0.1, 0.2).padded(0.5).start == 0.0  # clamps at 0


def test_clip_audio_builds_ffmpeg(monkeypatch):
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return None

    monkeypatch.setattr("overlay.app.media.subprocess.run", fake_run)
    # pin the binary so the assertion doesn't depend on the host's ffmpeg path (find_tool resolves it)
    monkeypatch.setattr("overlay.mpvio.discover.find_tool", lambda name: name)
    clip_audio("/v.mkv", Timespan(10, 12), "/out.m4a", pad=0.5, track=0)
    cmd = calls["cmd"]
    assert cmd[0] == "ffmpeg" and "aac" in cmd
    assert "0:a:0" in cmd
    assert "9.500" in cmd and "12.500" in cmd  # padded span


def test_toast_renders_each_kind():
    for kind in ("ok", "warn", "err"):
        img = render_toast(f"mined 読む ({kind})", kind)
        assert img.width > 60 and img.getextrema()[3][1] > 0


# --- Stage 3: HTML escaping in Anki fields --------------------------------------------------------


def test_bold_word_escapes_html_in_sentence():
    """bold_word must HTML-escape the sentence to prevent raw HTML injection into Anki fields."""
    # subtitle with < > & — should be escaped, not passed through as raw HTML
    sentence = "<漢字> & more"
    result = bold_word(sentence, "漢字")
    assert "&lt;" in result or "<b>漢字</b>" in result
    # The sentence must not contain a raw unescaped '<' from the original (the surface is wrapped in <b>)
    # but the surrounding '<' and '&' should be escaped.
    assert (
        "&amp;" in result
        or "&#38;" in result
        or result.count("<") <= result.count("<b>") + result.count("</b>")
    )


def test_dedupe_escapes_special_chars_in_query():
    """dedupe must escape * and spaces in the expression to avoid Anki query injection.
    The escaped query must contain \\* (backslash-star), not a bare unescaped *."""
    from overlay.app.anki import dedupe, MineConfig

    queries = []

    class _FakeAnki:
        def find_notes(self, query):
            queries.append(query)
            return []

    dedupe(_FakeAnki(), MineConfig(), "食べ*物 and more")
    assert queries, "find_notes not called"
    q = queries[0]
    # The * in the expression must be escaped as \* — i.e. \* appears in the query string.
    assert "\\*" in q, f"* not escaped in dedupe query: {q}"
    # Spaces must also be escaped.
    assert "\\ " in q, f"spaces not escaped in dedupe query: {q}"


# --- Miner flow through fakes (Stage 8b tooling: cover the mine/dedupe/bulk paths) -----------------


class _FakeAnki:
    def __init__(self, existing=()):
        self.existing = list(existing)
        self.added = []
        self.stored = []

    def find_notes(self, query):
        return self.existing

    def notes_info(self, ids):
        return []

    def can_add(self, note):
        return True

    def add_note(self, note):
        self.added.append(note)
        return 1

    def store_media(self, name, path):
        self.stored.append(name)
        return name


def test_mine_token_adds_note_with_fields(monkeypatch, tmp_path):
    from util import FakeIPC

    from overlay.app.controller import Reader

    ipc = FakeIPC()
    ipc.props["path"] = "/x/[Grp] Show - 03 [1080p].mkv"
    ipc.props["time-pos"] = 63
    anki = _FakeAnki()
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig())
    r.set_subtitle("本を読む")
    # media capture: no real mpv/ffmpeg — stub the capture step
    monkeypatch.setattr(r._miner, "capture_media", lambda base, video: ("p.jpg", "a.mp3"))
    shown = []
    monkeypatch.setattr(r, "_preview_mined", lambda card, tok, video: shown.append(card.expression))
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok)
    assert len(anki.added) == 1
    note = anki.added[0]
    assert note["fields"]["Expression"] == "読む"
    assert "<b>読む</b>" in note["fields"]["Sentence"]
    assert "saitenka::mined" in note["tags"]
    assert shown == ["読む"]


def test_mine_token_duplicate_shows_existing(monkeypatch):
    from util import FakeIPC

    from overlay.app.controller import Reader

    ipc = FakeIPC()
    anki = _FakeAnki(existing=[42])
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig())
    r.set_subtitle("本を読む")
    previewed = []
    monkeypatch.setattr(
        r, "_preview_existing", lambda nid, card, status: previewed.append((nid, status))
    )
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok)
    assert anki.added == []  # dedupe: nothing added
    assert previewed == [(42, "duplicate")]
    assert "読む" in r._mined  # ⊕ flips to ✓


def test_bulk_mine_counts_and_toasts(monkeypatch):
    from util import FakeIPC

    from overlay.app.controller import Reader

    ipc = FakeIPC()
    anki = _FakeAnki()
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig())
    r.set_subtitle("本を読む")
    monkeypatch.setattr(r._miner, "capture_media", lambda base, video: ("", ""))
    toasts = []
    monkeypatch.setattr(r, "_toast", lambda text, kind="ok", seconds=2.8: toasts.append(text))
    monkeypatch.setattr(r, "_mark_mined", lambda expr: None)  # skip the view refresh
    r.bulk_mine()
    assert len(anki.added) >= 1  # 本 and 読む are unknown content words
    assert any("mined" in t for t in toasts)


def _make_dict(path, title, entries):
    """Minimal Yomitan v3 dict zip (mirrors test_dictionary._make_dict)."""
    import json
    import zipfile

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": title, "format": 3}))
        bank = [[t, r, "", "", 0, g, i + 1, ""] for i, (t, r, g) in enumerate(entries)]
        zf.writestr("term_bank_1.json", json.dumps(bank, ensure_ascii=False))
    return str(path)


def test_mine_uses_user_dictionary_glossary(monkeypatch, tmp_path):
    """Dict-first mining: with a user dictionary configured, the mined card's Glossary comes from
    that dict — not the JMdict/jamdict fallback (which would gloss 読む as 'to read')."""
    from util import FakeIPC

    from overlay.app.controller import Reader
    from overlay.app.dictionary import DictionarySet

    d = _make_dict(tmp_path / "u.zip", "MyDict", [["読む", "よむ", ["DICTGLOSS-read"]]])
    ds = DictionarySet.load([d])
    ipc = FakeIPC()
    ipc.props["path"] = "/x/Show - 01.mkv"
    anki = _FakeAnki()
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig(), dict_set=ds)
    r.set_subtitle("本を読む")
    monkeypatch.setattr(r._miner, "capture_media", lambda base, video: ("", ""))
    monkeypatch.setattr(r, "_preview_mined", lambda card, tok, video: None)
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok)
    assert len(anki.added) == 1
    f = anki.added[0]["fields"]
    assert f["Expression"] == "読む"
    assert f["Glossary"] == "<ol><li>DICTGLOSS-read</li></ol>"  # from the user dict


def test_card_for_degrades_without_jamdict(monkeypatch):
    """When the optional jmdict extra (jamdict) isn't installed, card_for degrades to an
    expression-only card instead of crashing — the broad except in lookup is load-bearing."""
    import overlay.app.lookup as lookup

    lookup.card_data.cache_clear()

    def _no_jam():
        raise ImportError("No module named 'jamdict'")

    monkeypatch.setattr(lookup, "_jam", _no_jam)
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    card = lookup.card_for(tok)
    assert card.expression == "読む"
    assert card.glossary_html == ""
    lookup.card_data.cache_clear()  # don't leave the poisoned (jamdict-less) entry cached

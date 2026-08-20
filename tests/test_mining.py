"""Mining: card builder, dedup query, sentence bolding, media args, toast (no real Anki add)."""

import pytest

from saitenka.app.anki import KNOWN_MARKERS, CardContent, MineConfig, bold_word, build_note
from saitenka.app.lookup import card_for
from saitenka.app.media import AnimatedClip, Timespan, clip_audio
from saitenka.app.toast import render_toast
from saitenka.app.tokenize import tokenize


def test_card_data_from_token():
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    card = card_for(tok)
    assert card.expression == "読む"
    assert card.reading == "よむ"
    assert card.idseq.isdigit()  # JMdict ent_seq
    assert "to read" in card.glossary_html
    assert card.glossary_html.startswith("<ol>")


def test_known_entities_match_entity_values_keys():
    """KNOWN_ENTITIES (what doctor validates a [mine.fields] map against) must stay in lockstep with
    the actual entities build_note writes — else doctor flags a valid key or misses a bogus one."""
    from saitenka.app.anki import KNOWN_ENTITIES, _entity_values
    from saitenka.app.lookup import CardData

    values = _entity_values(CardData("x", "y", ""), CardContent())
    assert set(values) == set(KNOWN_ENTITIES)


def test_bold_word():
    assert bold_word("私は本を読む", "本") == "私は<b>本</b>を読む"
    assert bold_word("no match here", "本") == "no match here"


def test_build_note_maps_lapis_fields():
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    note = build_note(
        MineConfig(),
        card_for(tok),
        CardContent(
            sentence_html="本を<b>読む</b>", picture="p.jpg", audio="a.mp3", misc="ep10 · 10:03"
        ),
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
    assert (
        f["IsWordAndSentenceCard"] == "1"
    )  # default card kind (word-and-sentence, SubMiner default)
    assert note["options"]["allowDuplicate"] is False
    assert "saitenka" in note["tags"]


def test_build_note_writes_idseq_into_the_default_id_field():
    """#255 coverage gap: the ``card.idseq → 'id' entity → 'ID' field`` chain was only covered in
    halves. Under the default LAPIS map, a non-empty idseq must land in the real ``ID`` field the Kanji
    Study ``kanjistudy://word?id={{ID}}`` deep-link reads — proven with a constructed idseq (not a live
    jamdict lookup), so it runs regardless of the ``jmdict`` extra."""
    from saitenka.app.lookup import CardData

    note = build_note(MineConfig(), CardData("読む", "よむ", "", idseq="1456360"))
    assert note["fields"]["ID"] == "1456360"


def test_build_note_writes_frequency_fields():
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    note = build_note(
        MineConfig(),
        card_for(tok),
        CardContent(sentence_html="s", freq_html="<ul><li>FreqA: 12</li></ul>", freq_sort="12"),
    )
    assert note["fields"]["Frequency"] == "<ul><li>FreqA: 12</li></ul>"  # plan: freq → Frequency
    assert note["fields"]["FreqSort"] == "12"


def test_build_note_merges_source_tags():
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    note = build_note(
        MineConfig(),
        card_for(tok),
        CardContent(sentence_html="s"),
        tags=["saitenka::mined", "saitenka::source::Nippon_Sangoku", "saitenka::ep::10"],
    )
    assert "saitenka" in note["tags"]  # static tool tag kept
    assert "saitenka::source::Nippon_Sangoku" in note["tags"]  # + per-card source/episode
    assert "saitenka::ep::10" in note["tags"]
    assert len(note["tags"]) == len(set(note["tags"]))  # deduped


def test_custom_field_map_only_writes_mapped():
    cfg = MineConfig(model="Animecards", fields={"expression": "Word", "reading": "Reading"})
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    note = build_note(cfg, card_for(tok), CardContent(sentence_html="s"))
    assert set(note["fields"]) == {"Word", "Reading", "IsWordAndSentenceCard"}
    assert note["fields"]["Word"] == "読む"


@pytest.mark.parametrize(
    ("card_kind", "marker"),
    [
        ("sentence", "IsSentenceCard"),
        ("word-and-sentence", "IsWordAndSentenceCard"),
        ("click", "IsClickCard"),
        ("audio", "IsAudioCard"),
    ],
)
def test_build_note_card_kind_sets_exactly_one_marker(card_kind, marker):
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    note = build_note(
        MineConfig(card_kind=card_kind), card_for(tok), CardContent(sentence_html="s")
    )
    present = [m for m in KNOWN_MARKERS if m in note["fields"]]
    assert (
        present == [marker] and note["fields"][marker] == "1"
    )  # mutually exclusive by construction


def test_build_note_card_kind_none_sets_no_marker():
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    note = build_note(MineConfig(card_kind="none"), card_for(tok), CardContent(sentence_html="s"))
    assert not any(m in note["fields"] for m in KNOWN_MARKERS)  # no card-template marker at all


def test_build_note_unknown_card_kind_falls_back_to_default(caplog):
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    with caplog.at_level("WARNING"):
        note = build_note(
            MineConfig(card_kind="bogus"), card_for(tok), CardContent(sentence_html="s")
        )
    assert note["fields"]["IsWordAndSentenceCard"] == "1"  # typo didn't disable the marker
    assert "card_kind" in caplog.text


def test_build_note_card_format_wins_over_fields():
    # card_format present → ONLY its fields written (fields map ignored); card_kind flag still applies.
    cfg = MineConfig(
        fields={"expression": "Expression"},
        card_format={"Word": "{expression}", "Furigana": "{furigana}"},
    )
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    note = build_note(cfg, card_for(tok), CardContent(sentence_html="本を<b>読む</b>"))
    assert note["fields"]["Word"] == "読む" and note["fields"]["Furigana"] == "読[よ]む"
    assert "Expression" not in note["fields"]  # the entity map is ignored wholesale
    assert note["fields"]["IsWordAndSentenceCard"] == "1"  # card_kind marker still added


def test_build_note_card_format_fans_one_entity_into_two_fields():
    # the capability the entity→field map couldn't express: one marker in several fields
    cfg = MineConfig(card_format={"Word": "{expression}", "Key": "{expression}"})
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    note = build_note(cfg, card_for(tok), CardContent(sentence_html="s"))
    assert note["fields"]["Word"] == note["fields"]["Key"] == "読む"


def test_expression_field_resolves_from_card_format():
    from saitenka.app.anki import dedupe

    # dedup must key off the field that actually holds {expression} under card_format, not fields["expression"]
    cfg = MineConfig(card_format={"Word": "{expression}", "Note": "{glossary}"})
    assert cfg.expression_field() == "Word"

    queries = []

    class _A:
        def find_notes(self, q):
            queries.append(q)
            return []

    dedupe(_A(), cfg, "読む")
    assert queries and "Word:読む" in queries[0]  # queried the {expression} field, not Expression


def test_dedupe_allows_add_when_card_format_has_no_expression_field():
    from saitenka.app.anki import dedupe

    # no {expression} anywhere → no reliable dedup key → allow the add (never KeyError on fields["expression"])
    cfg = MineConfig(card_format={"Sentence": "{sentence}"})
    assert cfg.expression_field() == ""
    called = []

    class _A:
        def find_notes(self, q):
            called.append(q)
            return [1]

    assert dedupe(_A(), cfg, "読む") == [] and called == []  # short-circuits, no query


def test_mine_token_card_format_dedupes_on_the_expression_field(monkeypatch):
    # end-to-end: an already-mined word is detected under card_format (the both-KeyError/false-negative fix)
    from util import FakeIPC

    from saitenka.app.controller import Reader

    ipc = FakeIPC()
    anki = _FakeAnki(existing=[7])  # the dedup query returns a hit
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig(card_format={"Word": "{expression}"}))
    r.set_subtitle("本を読む")
    monkeypatch.setattr(r, "_preview_existing", lambda *_a: None)
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok)
    assert anki.added == [] and "読む" in r._mined  # deduped, not added; ⊕→✓ flipped


def test_build_note_card_format_uses_passed_markers():
    # the miner passes a full marker map (pitch/pos the args can't supply); build_note renders it
    cfg = MineConfig(card_format={"Pitch": "{pitch-accents}"})
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    note = build_note(
        cfg, card_for(tok), CardContent(sentence_html="s"), markers={"pitch-accents": "よむ [0]"}
    )
    assert note["fields"]["Pitch"] == "よむ [0]"


def test_mine_config_from_preset_kiku_uses_lapis_fields_and_word_and_sentence():
    cfg = MineConfig.from_preset("Kiku")
    assert cfg.model == "Kiku"
    assert cfg.fields["expression"] == "Expression" and cfg.fields["audio"] == "SentenceAudio"
    assert cfg.flags == {"IsWordAndSentenceCard": "1"}  # Kiku's word-and-sentence marker


def test_mine_config_from_unknown_preset_warns_and_uses_lapis(caplog):
    with caplog.at_level("WARNING"):
        cfg = MineConfig.from_preset("Nonesuch")
    assert cfg.fields == MineConfig().fields and "unknown mining preset" in caplog.text


def test_mine_config_from_wires_word_audio_pack_when_enabled(tmp_path):
    """#93: `_mine_config_from` (the run/attach-shared seam) resolves [mine].word_audio_* into the
    runtime MineConfig it hands the miner."""
    from saitenka.app.reader_deps import _mine_config_from

    cfg = _mine_config_from(
        {
            "word_audio_enabled": True,
            "word_audio_pack_dir": str(tmp_path),
            "word_audio_field": "Pronunciation",
        }
    )
    assert cfg.word_audio_pack == tmp_path
    assert cfg.word_audio_field == "Pronunciation"


def test_mine_config_from_leaves_word_audio_off_by_default():
    from saitenka.app.reader_deps import _mine_config_from

    cfg = _mine_config_from({"deck": "X"})
    assert cfg.word_audio_pack is None
    assert (
        cfg.word_audio_field == "WordAudio"
    )  # default field name still set (inert without a pack)


def test_mine_config_from_ignores_pack_dir_when_word_audio_disabled(tmp_path):
    from saitenka.app.reader_deps import _mine_config_from

    cfg = _mine_config_from({"word_audio_pack_dir": str(tmp_path)})  # enabled defaults False
    assert cfg.word_audio_pack is None


def test_timespan_padding():
    ts = Timespan(10.0, 12.0).padded(0.5)
    assert ts.start == 9.5 and ts.end == 12.5
    assert Timespan(0.1, 0.2).padded(0.5).start == 0.0  # clamps at 0


def test_clip_audio_builds_ffmpeg(monkeypatch):
    calls = {}

    def fake_run(cmd, **_kw):
        calls["cmd"] = cmd

    monkeypatch.setattr("saitenka.app.media.subprocess.run", fake_run)
    # pin the binary so the assertion doesn't depend on the host's ffmpeg path (find_tool resolves it)
    monkeypatch.setattr("saitenka.mpvio.discover.find_tool", lambda name: name)
    clip_audio("/v.mkv", Timespan(10, 12), "/out.m4a", pad=0.5, track=0)
    cmd = calls["cmd"]
    assert cmd[0] == "ffmpeg" and "aac" in cmd
    assert "0:a:0" in cmd
    assert "9.500" in cmd and "12.500" in cmd  # padded span
    assert "loudnorm" not in cmd[cmd.index("-af") + 1]  # normalization is opt-in


def test_clip_audio_normalize_prepends_loudnorm(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        "saitenka.app.media.subprocess.run", lambda cmd, **_kw: calls.__setitem__("cmd", cmd)
    )
    monkeypatch.setattr("saitenka.mpvio.discover.find_tool", lambda name: name)
    clip_audio("/v.mkv", Timespan(10, 12), "/out.m4a", normalize=True)
    af = calls["cmd"][calls["cmd"].index("-af") + 1]
    # loudnorm runs BEFORE the fades so it measures the raw span, not the faded-out tails
    assert af.startswith("loudnorm=I=-23:") and af.index("loudnorm") < af.index("afade")


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
    from saitenka.app.anki import MineConfig, dedupe

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

    def find_notes(self, _query):
        return self.existing

    def notes_info(self, _ids):
        return []

    def can_add(self, _note):
        return True

    def add_note(self, note):
        self.added.append(note)
        return 1

    def store_media(self, name, _path):
        self.stored.append(name)
        return name


def test_mine_token_adds_note_with_fields(monkeypatch):
    from util import FakeIPC

    from saitenka.app.controller import Reader

    ipc = FakeIPC()
    ipc.props["path"] = "/x/[Grp] Show - 03 [1080p].mkv"
    ipc.props["time-pos"] = 63
    anki = _FakeAnki()
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig())
    r.set_subtitle("本を読む")
    # media capture: no real mpv/ffmpeg — stub the capture step
    monkeypatch.setattr(r._miner, "capture_media", lambda _base, _video, **_k: ("p.jpg", "a.mp3"))
    shown = []
    monkeypatch.setattr(
        r, "_preview_mined", lambda card, _tok, _video, _st="mined": shown.append(card.expression)
    )
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok)
    assert len(anki.added) == 1
    note = anki.added[0]
    assert note["fields"]["Expression"] == "読む"
    assert "<b>読む</b>" in note["fields"]["Sentence"]
    assert "saitenka::mined" in note["tags"]
    assert shown == ["読む"]


def _capture_reader(tmp_path, *, animated_enabled: bool):
    from util import FakeIPC

    from saitenka.app.controller import Reader

    r = Reader(
        FakeIPC(),
        anki=_FakeAnki(),
        mine_cfg=MineConfig(animated=AnimatedClip(enabled=animated_enabled)),
    )
    r._tmp = tmp_path
    return r


def _stub_capture(monkeypatch, *, animated_result):
    """Stub the media boundary so capture_media runs without real mpv/ffmpeg. ``animated_result`` is
    what animated_screenshot returns (a Path = encode ok, None = no encoder)."""
    import saitenka.app.miner as _M

    monkeypatch.setattr(_M, "screenshot", lambda *_a: None)
    monkeypatch.setattr(_M, "current_timespan", lambda _ipc: Timespan(10, 12))
    monkeypatch.setattr(_M, "clip_audio", lambda *_a, **_k: None)
    calls: list = []
    monkeypatch.setattr(
        _M, "animated_screenshot", lambda *a, **_k: (calls.append(a), animated_result)[1]
    )
    return calls


def test_capture_media_uses_webp_when_encoder_available(monkeypatch, tmp_path):
    r = _capture_reader(tmp_path, animated_enabled=True)
    _stub_capture(monkeypatch, animated_result=tmp_path / "x.webp")
    pic, _audio = r._miner.capture_media("saitenka_1", "/v.mkv")
    assert pic.endswith(".webp")  # the animated clip becomes the card image
    assert r.preview.last_jpg is not None and str(r.preview.last_jpg).endswith(
        ".jpg"
    )  # still kept for preview/fallback


def test_capture_media_falls_back_to_still_when_encoder_absent(monkeypatch, tmp_path):
    r = _capture_reader(tmp_path, animated_enabled=True)
    _stub_capture(monkeypatch, animated_result=None)  # no animated encoder present
    pic, _audio = r._miner.capture_media("saitenka_1", "/v.mkv")
    assert pic.endswith(".jpg")  # falls back to the mpv still


def test_capture_media_animated_override_forces_clip_over_config_default(monkeypatch, tmp_path):
    # config default is OFF, but the per-mine override (the video-mine shortcut) forces the clip
    r = _capture_reader(tmp_path, animated_enabled=False)
    calls = _stub_capture(monkeypatch, animated_result=tmp_path / "x.webp")
    pic, _audio = r._miner.capture_media("saitenka_1", "/v.mkv", animated=True)
    assert calls and pic.endswith(".webp")  # the encode ran despite the config default being off


def test_capture_media_still_only_when_animated_disabled(monkeypatch, tmp_path):
    r = _capture_reader(tmp_path, animated_enabled=False)
    calls = _stub_capture(monkeypatch, animated_result=tmp_path / "x.webp")
    pic, _audio = r._miner.capture_media("saitenka_1", "/v.mkv")
    assert calls == [] and pic.endswith(".jpg")  # animated off + no override → never encodes


def test_capture_media_survives_a_timespan_read_error(monkeypatch, tmp_path):
    # A transient IPC error reading the cue timespan must NOT escape capture_media — in bulk_mine it would
    # propagate to poll_once and tear the session down. The still is still captured (image-only mine).
    import saitenka.app.miner as _M

    r = _capture_reader(tmp_path, animated_enabled=False)
    monkeypatch.setattr(_M, "screenshot", lambda *_a: None)
    monkeypatch.setattr(_M, "clip_audio", lambda *_a, **_k: None)

    def _boom(_ipc):
        raise OSError("broken pipe")

    monkeypatch.setattr(_M, "current_timespan", _boom)
    pic, audio = r._miner.capture_media("saitenka_1", "/v.mkv")  # must not raise
    assert pic.endswith(".jpg") and audio == ""  # still captured; audio skipped (no span)


def test_mine_token_with_explicit_card_mines_chosen_entry(monkeypatch):
    """A per-entry ⊕ passes an explicit CardData, so the mined note is that chosen entry (しりぞく),
    not whatever the default dict-first pick would derive for the token."""
    from util import FakeIPC

    from saitenka.app.controller import Reader
    from saitenka.app.lookup import CardData

    ipc = FakeIPC()
    anki = _FakeAnki()
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig())
    r.set_subtitle("本を読む")
    monkeypatch.setattr(r._miner, "capture_media", lambda _base, _video, **_k: ("p.jpg", "a.mp3"))
    monkeypatch.setattr(r, "_preview_mined", lambda *_a, **_k: None)
    chosen = CardData("退く", "しりぞく", "<ol><li>to retreat</li></ol>", glosses=("to retreat",))
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok, card=chosen)
    assert anki.added[0]["fields"]["Expression"] == "退く"
    assert anki.added[0]["fields"]["ExpressionReading"] == "しりぞく"


def test_mine_token_duplicate_shows_existing(monkeypatch):
    from util import FakeIPC

    from saitenka.app.controller import Reader

    ipc = FakeIPC()
    anki = _FakeAnki(existing=[42])
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig())
    r.set_subtitle("本を読む")
    previewed = []
    monkeypatch.setattr(
        r, "_preview_existing", lambda nid, _card, status: previewed.append((nid, status))
    )
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok)
    assert anki.added == []  # dedupe: nothing added
    assert previewed == [(42, "exists")]  # "✓ in deck" — nothing was duplicated
    assert "読む" in r._mined  # ⊕ flips to ✓


def test_preview_replay_key_is_tooltip_scoped():
    """`p` (replay preview) is bound only while a tooltip is up, so global `p` keeps mpv's pause
    (the Windows collision). It must NOT be a global startup binding."""
    from util import FakeIPC

    from saitenka.app.bindings import PREVIEW_MSG, active_bindings
    from saitenka.app.controller import Reader

    r = Reader(FakeIPC(), anki=object(), mine_cfg=MineConfig())
    global_msgs = {b.spec.message for b in active_bindings(r, "global")}
    tooltip_msgs = {b.spec.message for b in active_bindings(r, "tooltip")}
    assert PREVIEW_MSG not in global_msgs
    assert PREVIEW_MSG in tooltip_msgs


def test_esc_closes_card_preview_and_hands_key_back(monkeypatch):
    """Showing the preview grabs Esc → close; pressing it hides the preview; closing hands Esc back
    to a no-op when no tooltip is up."""
    from util import FakeIPC

    from saitenka.app import miner_ui
    from saitenka.app.bindings import PREVIEW_CLOSE_MSG
    from saitenka.app.card_preview import PreviewData
    from saitenka.app.controller import Reader

    ipc = FakeIPC()
    r = Reader(ipc, anki=object(), mine_cfg=MineConfig())
    # skip the PIL render
    monkeypatch.setattr(miner_ui, "render_preview", lambda *_args: None)
    pv = PreviewData(
        "exists", "読む", "よむ", ["本を読む"], "読む", ["to read"], None, None, "deck"
    )

    r._show_preview(pv, None)
    assert ("keybind", "ESC", f"script-message {PREVIEW_CLOSE_MSG}") in ipc.commands

    r._handle(PREVIEW_CLOSE_MSG)
    assert r.preview.last_preview is None  # Esc dismissed it
    assert ("keybind", "ESC", "ignore") in ipc.commands  # handed back (no tooltip up)


def test_add_anyway_after_exists_creates_an_explicit_duplicate(monkeypatch):
    """Mining an in-deck word shows "✓ in deck" and adds nothing, but stashes the token; the preview's
    ＋ "add anyway" then mines a second card for this scene with allowDuplicate set."""
    from util import FakeIPC

    from saitenka.app.controller import Reader

    ipc = FakeIPC()
    anki = _FakeAnki(existing=[42])  # 読む already in the mining deck
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig())
    r.set_subtitle("本を読む")
    monkeypatch.setattr(r._miner, "capture_media", lambda _base, _video, **_k: ("p.jpg", "a.mp3"))
    monkeypatch.setattr(r, "_preview_existing", lambda *_a: None)
    dup_status = []
    monkeypatch.setattr(
        r, "_preview_mined", lambda _c, _t, _v, status="mined": dup_status.append(status)
    )
    tok = next(t for t in r.tokens if t.surface == "読む")

    r._mine_token(tok)  # already in deck → nothing added, token remembered
    assert anki.added == []
    assert r.preview.dup_tok is tok

    r._add_duplicate()  # ＋ add anyway
    assert len(anki.added) == 1
    assert anki.added[0]["options"]["allowDuplicate"] is True
    assert dup_status == ["duplicate"]  # the new card's preview says "• duplicate" (accurate now)


def test_select_bulk_targets_dedupes_skips_known_and_caps():
    """Characterization test for the target-selection logic bulk_mine delegates to: content words
    only, "known"-tagged words skipped, duplicate lemmas collapsed to the first occurrence, capped
    at max_bulk."""
    from types import SimpleNamespace

    from saitenka.app.miner import _select_bulk_targets
    from saitenka.app.scoring import TokenStyle
    from saitenka.app.tokenize import Token
    from saitenka.app.tokenizer import UnidicTokenizer

    def tok(surface, lemma, pos="名詞"):
        return Token(surface=surface, lemma=lemma, reading="", pos=pos, start=0, end=len(surface))

    tokens = [
        tok("猫", "猫"),  # 0: content, novel -> kept
        tok("は", "は", pos="助詞"),  # 1: particle -> not content, skipped
        tok("犬", "犬"),  # 2: content, "known"-tagged -> skipped
        tok("猫", "猫"),  # 3: content, dup lemma of #0 -> skipped
        tok("鳥", "鳥"),  # 4: content, novel -> kept
    ]
    styles = [
        TokenStyle(color=(0, 0, 0, 255)),
        TokenStyle(color=(0, 0, 0, 255)),
        TokenStyle(color=(0, 0, 0, 255), tag="known"),
        TokenStyle(color=(0, 0, 0, 255)),
        TokenStyle(color=(0, 0, 0, 255)),
    ]
    r = SimpleNamespace(tokens=tokens, styles=styles, max_bulk=1, tokenizer=UnidicTokenizer())
    assert _select_bulk_targets(r) == [0]  # capped at 1 before reaching 鳥

    r.max_bulk = 10
    assert _select_bulk_targets(r) == [0, 4]  # は/犬/dup-猫 all filtered out


def test_bulk_mine_counts_and_toasts(monkeypatch):
    from util import FakeIPC

    from saitenka.app.controller import Reader

    ipc = FakeIPC()
    anki = _FakeAnki()
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig())
    r.set_subtitle("本を読む")
    monkeypatch.setattr(r._miner, "capture_media", lambda _base, _video, **_k: ("", ""))
    toasts = []
    monkeypatch.setattr(r, "_toast", lambda text, _kind="ok", _seconds=2.8: toasts.append(text))
    monkeypatch.setattr(r, "_mark_mined", lambda _expr: None)  # skip the view refresh
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


def test_mine_link_mines_the_selected_stacked_entry(monkeypatch, tmp_path):
    """The per-entry ⊕ arrives as a 'mine:<i>' LinkBox; _mine_link mines cards_for(tok)[i] — clicking
    the しりぞく block (index 1) mines that reading/gloss, not the default のく."""
    import dicthelp
    from util import FakeIPC

    from saitenka.app import tooltip
    from saitenka.app.controller import Reader
    from saitenka.app.tokenize import Token
    from saitenka.model import LinkBox

    d = _make_dict(
        tmp_path / "tk.zip",
        "Multi",
        [["退く", "しりぞく", ["to retreat"]], ["退く", "のく", ["to step aside"]]],
    )
    ds = dicthelp.load_set([d])
    ipc = FakeIPC()
    ipc.props["path"] = "/x/S - 01.mkv"
    anki = _FakeAnki()
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig(), dict_set=ds)
    r.set_subtitle("退いた")
    monkeypatch.setattr(r._miner, "capture_media", lambda _b, _v, **_k: ("", ""))
    monkeypatch.setattr(r, "_preview_mined", lambda *_a, **_k: None)
    tok = Token(surface="退いた", lemma="退く", reading="のいた", pos="動詞", start=0, end=3)
    handled = tooltip._mine_link(  # cards_for: のく=0, しりぞく=1
        r.dict_set, r._hover_meta.terms, r._mine_token, LinkBox("mine:1", 0, 0, 10, 10), tok
    )
    assert handled
    f = anki.added[0]["fields"]
    assert (f["Expression"], f["ExpressionReading"]) == ("退く", "しりぞく")
    assert f["Glossary"] == "<ol><li>to retreat</li></ol>"


def test_mine_token_card_format_renders_templated_fields(monkeypatch, tmp_path):
    """#192: with [mine.card_format] set, the mined note's fields are the rendered {marker} templates —
    furigana, pitch (from the dict), and a cloze-split sentence — not the entity→field map."""
    import dicthelp
    from util import FakeIPC

    from saitenka.app.controller import Reader

    d = _make_dict(tmp_path / "d.zip", "Def", [["読む", "よむ", ["to read"]]])
    pz = dicthelp.meta_zip(
        tmp_path / "p.zip",
        "Pitch",
        "pitch",
        [["読む", {"reading": "よむ", "pitches": [{"position": 1}]}]],
    )
    ds = dicthelp.load_set([d], pitch_zips=[pz])
    ipc = FakeIPC()
    ipc.props["path"] = "/x/Show - 01.mkv"
    anki = _FakeAnki()
    cfg = MineConfig(
        card_format={
            "Word": "{expression}",
            "Furigana": "{furigana}",
            "Pitch": "{pitch-accents}",
            "Sentence": "{cloze-prefix}<b>{cloze-body}</b>{cloze-suffix}",
            "Freq": "{frequency-rank}",
        }
    )
    r = Reader(ipc, anki=anki, mine_cfg=cfg, dict_set=ds)
    r.set_subtitle("本を読む")
    monkeypatch.setattr(r._miner, "capture_media", lambda _b, _v, **_k: ("", ""))
    monkeypatch.setattr(r, "_preview_mined", lambda *_a, **_k: None)
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok)
    f = anki.added[0]["fields"]
    assert f["Word"] == "読む" and f["Furigana"] == "読[よ]む"
    assert "よむ" in f["Pitch"] and "[1]" in f["Pitch"]  # pitch from the dict, not fabricated
    assert f["Sentence"] == "本を<b>読む</b>"  # cloze markers reassembled around the surface
    assert set(f) == {"Word", "Furigana", "Pitch", "Sentence", "Freq", "IsWordAndSentenceCard"}


# --- #93: word-pronunciation audio attach at the mine-time add_note seam --------------------------


def _word_audio_pack(tmp_path, term: str, reading: str, filename: str):
    import json

    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "index.json").write_text(
        json.dumps({"index": {term: {reading: [filename]}}}, ensure_ascii=False), encoding="utf-8"
    )
    (pack / filename).write_bytes(b"fake-opus-bytes")
    return pack


def test_mine_token_attaches_word_audio_when_pack_resolves(monkeypatch, tmp_path):
    from util import FakeIPC

    from saitenka.app.controller import Reader

    pack = _word_audio_pack(tmp_path, "読む", "よむ", "yomu.opus")
    ipc = FakeIPC()
    anki = _FakeAnki()
    cfg = MineConfig(word_audio_pack=pack, word_audio_field="WordAudio")
    r = Reader(ipc, anki=anki, mine_cfg=cfg)
    r.set_subtitle("本を読む")
    monkeypatch.setattr(r._miner, "capture_media", lambda _base, _video, **_k: ("p.jpg", "a.mp3"))
    monkeypatch.setattr(r, "_preview_mined", lambda *_a, **_k: None)
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok)
    assert len(anki.added) == 1
    note = anki.added[0]
    assert note["fields"]["WordAudio"] == "[sound:yomu.opus]"
    assert "yomu.opus" in anki.stored  # storeMediaFile called with the resolved file


def test_mine_token_leaves_word_audio_field_unset_on_a_pack_miss(monkeypatch, tmp_path):
    """The pack has no entry for this word — the field must stay unset, not an empty [sound:] tag."""
    from util import FakeIPC

    from saitenka.app.controller import Reader

    pack = _word_audio_pack(tmp_path, "書く", "かく", "kaku.opus")  # different word
    ipc = FakeIPC()
    anki = _FakeAnki()
    cfg = MineConfig(word_audio_pack=pack, word_audio_field="WordAudio")
    r = Reader(ipc, anki=anki, mine_cfg=cfg)
    r.set_subtitle("本を読む")
    monkeypatch.setattr(r._miner, "capture_media", lambda _base, _video, **_k: ("p.jpg", "a.mp3"))
    monkeypatch.setattr(r, "_preview_mined", lambda *_a, **_k: None)
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok)
    assert len(anki.added) == 1
    assert "WordAudio" not in anki.added[0]["fields"]
    assert anki.stored == []  # never stores media for a miss


def test_mine_token_never_uploads_an_out_of_pack_word_audio_file(monkeypatch, tmp_path):
    """P1 containment through the mine path: a poisoned index entry escaping the pack dir (`../` or an
    absolute path) resolves to a miss — the word-audio field stays unset and store_media is NEVER called,
    so a shared/downloaded pack can't read+upload an arbitrary local file into Anki."""
    import json

    from util import FakeIPC

    from saitenka.app.controller import Reader

    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"top-secret")
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "index.json").write_text(
        json.dumps(
            {"index": {"読む": {"よむ": ["../secret.txt", str(secret)]}}}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    ipc = FakeIPC()
    anki = _FakeAnki()
    r = Reader(
        ipc, anki=anki, mine_cfg=MineConfig(word_audio_pack=pack, word_audio_field="WordAudio")
    )
    r.set_subtitle("本を読む")
    monkeypatch.setattr(r._miner, "capture_media", lambda _base, _video, **_k: ("p.jpg", "a.mp3"))
    monkeypatch.setattr(r, "_preview_mined", lambda *_a, **_k: None)
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok)
    assert len(anki.added) == 1
    assert "WordAudio" not in anki.added[0]["fields"]  # out-of-pack entry → field unset
    assert not any("secret" in name for name in anki.stored)  # never uploaded the escaping file


def test_mine_token_skips_word_audio_when_pack_not_configured(monkeypatch):
    """The default MineConfig has no word_audio_pack — word-audio stays fully off, no crash."""
    from util import FakeIPC

    from saitenka.app.controller import Reader

    ipc = FakeIPC()
    anki = _FakeAnki()
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig())
    r.set_subtitle("本を読む")
    monkeypatch.setattr(r._miner, "capture_media", lambda _base, _video, **_k: ("p.jpg", "a.mp3"))
    monkeypatch.setattr(r, "_preview_mined", lambda *_a, **_k: None)
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok)
    assert "WordAudio" not in anki.added[0]["fields"]
    assert anki.stored == []


def test_group_mined_of_marks_entries_by_expression(tmp_path):
    """Per-stacked-entry ✓ state tracks deck membership by expression (Anki's dedup key): mining 退く
    flips every 退く reading-block, since a second reading would be a duplicate expression."""
    import dicthelp
    from util import FakeIPC

    from saitenka.app import tooltip_panel
    from saitenka.app.controller import Reader
    from saitenka.app.tokenize import Token

    d = _make_dict(
        tmp_path / "gm.zip",
        "Multi",
        [["退く", "しりぞく", ["to retreat"]], ["退く", "のく", ["to step aside"]]],
    )
    ds = dicthelp.load_set([d])
    r = Reader(FakeIPC(), dict_set=ds)
    tok = Token(surface="退いた", lemma="退く", reading="のいた", pos="動詞", start=0, end=3)
    assert (
        tooltip_panel.group_mined_of(tok, r._mined, r.dict_set) == ()
    )  # nothing mined yet → no per-group flags
    r._mined.add("退く")
    assert tooltip_panel.group_mined_of(tok, r._mined, r.dict_set) == (
        True,
        True,
    )  # both entries share expression 退く


def test_mine_uses_user_dictionary_glossary(monkeypatch, tmp_path):
    """Dict-first mining: with a user dictionary configured, the mined card's Glossary comes from
    that dict — not the JMdict/jamdict fallback (which would gloss 読む as 'to read')."""
    import dicthelp
    from util import FakeIPC

    from saitenka.app.controller import Reader

    d = _make_dict(tmp_path / "u.zip", "MyDict", [["読む", "よむ", ["DICTGLOSS-read"]]])
    ds = dicthelp.load_set([d])
    ipc = FakeIPC()
    ipc.props["path"] = "/x/Show - 01.mkv"
    anki = _FakeAnki()
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig(), dict_set=ds)
    r.set_subtitle("本を読む")
    monkeypatch.setattr(r._miner, "capture_media", lambda _base, _video, **_k: ("", ""))
    monkeypatch.setattr(r, "_preview_mined", lambda _card, _tok, _video: None)
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok)
    assert len(anki.added) == 1
    f = anki.added[0]["fields"]
    assert f["Expression"] == "読む"
    assert f["Glossary"] == "<ol><li>DICTGLOSS-read</li></ol>"  # from the user dict


def test_mine_fills_id_field_from_a_jmdict_derived_dicts_seq(monkeypatch, tmp_path):
    """#255 end-to-end: dict-first mining with an imported JMdict-derived dict (Jitendex-titled) and
    `[dictdb] persist_seq` on writes the real Kanji Study deep-link `ID` field — without jamdict."""
    import dicthelp
    from util import FakeIPC

    from saitenka.app.config import DictDbOptions
    from saitenka.app.controller import Reader
    from saitenka.app.dictdb import DictionaryDb

    d = _make_dict(tmp_path / "jx.zip", "Jitendex", [["読む", "よむ", ["to read"]]])  # seq=1
    db = DictionaryDb.open(db_opts=DictDbOptions(persist_seq=True))
    ds = dicthelp.load_set([d], on=db)
    ipc = FakeIPC()
    ipc.props["path"] = "/x/Show - 01.mkv"
    anki = _FakeAnki()
    r = Reader(ipc, anki=anki, mine_cfg=MineConfig(), dict_set=ds)
    r.set_subtitle("本を読む")
    monkeypatch.setattr(r._miner, "capture_media", lambda _base, _video, **_k: ("", ""))
    monkeypatch.setattr(r, "_preview_mined", lambda _card, _tok, _video: None)
    tok = next(t for t in r.tokens if t.surface == "読む")
    r._mine_token(tok)
    assert anki.added[0]["fields"]["ID"] == "1"


def test_card_for_degrades_without_jamdict(monkeypatch):
    """When the optional jmdict extra (jamdict) isn't installed, card_for degrades to an
    expression-only card instead of crashing — the broad except in lookup is load-bearing."""
    from saitenka.app import lookup

    lookup.card_data.cache_clear()

    def _no_jam():
        raise ImportError("No module named 'jamdict'")

    monkeypatch.setattr(lookup, "_jam", _no_jam)
    tok = next(t for t in tokenize("本を読む") if t.surface == "読む")
    card = lookup.card_for(tok)
    assert card.expression == "読む"
    assert card.glossary_html == ""
    lookup.card_data.cache_clear()  # don't leave the poisoned (jamdict-less) entry cached


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("mine:0", 0),
        ("mine:3", 3),
        ("それにしては", None),  # an ordinary cross-reference
        ("mine:", None),
        ("mine:abc", None),
        ("mine:1.5", None),
        (None, None),
        (42, None),
    ],
)
def test_a_stacked_entry_mine_link_is_read_without_breaking_navigation(query, expected):
    """The ⊕ rides the normal link hit-test, so this runs on EVERY link click. A malformed suffix
    has to read as "not a mine link" rather than raise, or one bad dictionary entry breaks
    navigation for every link in the panel."""
    from saitenka.app.tooltip import mine_index

    assert mine_index(query) == expected

"""Card-format markers (#192): Anki furigana, cloze split, the marker map, and template rendering."""

from overlay.app.card_markers import (
    MARKERS,
    anki_furigana,
    build_markers,
    render_card_format,
)
from overlay.app.lookup import CardData


def test_anki_furigana_aligns_okurigana():
    assert anki_furigana("読む", "よむ") == "読[よ]む"  # kanji core bracketed, okurigana tail kept
    assert anki_furigana("小僧", "こぞう") == "小僧[こぞう]"  # all-kanji core
    assert anki_furigana("お前", "おまえ") == "お 前[まえ]"  # space so [まえ] binds to 前, not お
    assert anki_furigana("する", "する") == "する"  # all-kana → no annotation
    assert anki_furigana("猫", "") == "猫"  # no reading → expression


def test_anki_furigana_aligns_interior_okurigana():
    # interior kana between kanji must split, not lump into one bracket (話し合[はなしあ]う was the bug)
    assert anki_furigana("話し合う", "はなしあう") == "話[はな]し 合[あ]う"
    assert anki_furigana("食べ物", "たべもの") == "食[た]べ 物[もの]"
    assert anki_furigana("待ち合わせ", "まちあわせ") == "待[ま]ち 合[あ]わせ"


def test_anki_furigana_falls_back_when_reading_cannot_align():
    # a reading whose kana anchor (べる) isn't in the reading degrades to the head/tail approximation
    assert anki_furigana("食べる", "くう") == "食べる[くう]"


def test_cloze_splits_sentence_on_the_bolded_surface():
    # build_markers derives cloze from the already-bolded sentence (bold_word wraps the surface)
    m = build_markers(
        CardData("読む", "よむ", ""),
        sentence_html="本を<b>読む</b>のが好き",
        picture="",
        audio="",
        misc="",
        doc_title="",
        freq_html="",
        freq_rank="",
        pos_en="verb",
        tags=(),
    )
    assert (m["cloze-prefix"], m["cloze-body"], m["cloze-suffix"]) == ("本を", "読む", "のが好き")


def test_cloze_whole_sentence_is_prefix_when_surface_absent():
    m = build_markers(
        CardData("x", "", ""),
        sentence_html="no bold here",
        picture="",
        audio="",
        misc="",
        doc_title="",
        freq_html="",
        freq_rank="",
        pos_en="",
        tags=(),
    )
    assert m["cloze-prefix"] == "no bold here" and m["cloze-body"] == "" and m["cloze-suffix"] == ""


def test_build_markers_populates_grounded_set_and_wraps_media():
    m = build_markers(
        CardData("読む", "よむ", "<ol><li>to read</li></ol>", idseq="1234", glosses=("to read",)),
        sentence_html="本を<b>読む</b>",
        picture="pic.webp",
        audio="a.m4a",
        misc="Show · ep01 · 10:03",
        doc_title="Show",
        freq_html="<ul><li>F: 5</li></ul>",
        freq_rank="5",
        pos_en="verb",
        tags=("saitenka::mined", "saitenka::ep::01"),
        pitch_html="よむ [0]",
        pitch_positions="0",
    )
    assert m["expression"] == "読む" and m["reading"] == "よむ"
    assert m["furigana"] == "読[よ]む"
    assert m["glossary"] == "<ol><li>to read</li></ol>" and m["glossary-plain"] == "to read"
    assert m["screenshot"] == '<img src="pic.webp">'  # Anki-ready wrapper, not the bare name
    assert m["sentence-audio"] == "[sound:a.m4a]"
    assert m["frequencies"] == "<ul><li>F: 5</li></ul>" and m["frequency-rank"] == "5"
    assert m["pitch-accents"] == "よむ [0]" and m["pitch-accent-positions"] == "0"
    assert m["document-title"] == "Show" and m["ent-seq"] == "1234"
    assert m["tags"] == "saitenka::mined saitenka::ep::01"
    assert set(m) <= MARKERS  # every produced marker is an advertised, doctor-known marker


def test_build_markers_pitch_empty_when_not_passed():
    m = build_markers(
        CardData("猫", "ねこ", ""),
        sentence_html="",
        picture="",
        audio="",
        misc="",
        doc_title="",
        freq_html="",
        freq_rank="",
        pos_en="noun",
        tags=(),
    )
    assert (
        m["pitch-accents"] == "" and m["pitch-accent-positions"] == ""
    )  # deferred/optional contract


def test_render_card_format_substitutes_and_survives_stray_braces():
    markers = {"expression": "読む", "reading": "よむ"}
    out = render_card_format(
        {"Word": "{expression}", "Reading": "【{reading}】", "Lit": "a { brace {expression}"},
        markers,
    )
    assert out == {"Word": "読む", "Reading": "【よむ】", "Lit": "a { brace 読む"}


def test_render_card_format_unknown_marker_renders_empty(caplog):
    with caplog.at_level("WARNING"):
        out = render_card_format({"F": "x{bogus}y"}, {"expression": "読む"})
    assert out == {"F": "xy"} and "unknown marker" in caplog.text


def test_render_card_format_catches_miscased_marker(caplog):
    # {Reading} (wrong case) must be treated as unknown → empty + warn, not left literal on the card
    with caplog.at_level("WARNING"):
        out = render_card_format({"R": "{Reading}"}, {"reading": "よむ"})
    assert out == {"R": ""} and "Reading" in caplog.text

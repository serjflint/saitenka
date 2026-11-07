"""Projection laws and the distinct authored-geometry/cache boundary contracts."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from saitenka_subtitles import Cue, CueIndex
from saitenka_tokenize.registry import UnidicTokenizer, get_tokenizer
from saitenka_wordstate import Scorer
from saitenka_wordstate.known import KnownWords

from saitenka.app.features.analysis.episode_analysis import analysis_key, analyze_cues
from saitenka.app.features.annotation.jobs import (
    AnnotationExecutor,
    AnnotationInputs,
    AnnotationPriority,
    AnnotationWorkKey,
    CueAnnotationCoordinator,
    annotate,
)
from saitenka.app.native_subtitles import token_annotations
from saitenka.app.profiles import resolve_profile
from saitenka.app.scoring import Coloring
from saitenka.app.study_text import (
    DISABLED,
    StudyRules,
    custom_pattern,
    lookup_boundaries,
    phrase_terms,
    project_line,
    rules_from_profile,
    tokenize_line,
)
from saitenka.app.subtitle_raster import (
    PillowRasterProvider,
    RasterStyle,
    SubtitleRasterRequest,
    raw_display_lines,
)
from saitenka.app.token_cache import TokenCache
from saitenka.runtime.jobs import JobLanePolicy, LocalJobLane

RULES = StudyRules(speaker_labels=True, annotations=True, music=True, join_lines=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("太郎：本を読む。", "本を読む。"),
        ("（太郎）「本を読む」", "「本を読む」"),
        ("(Alice): Hello!", "Hello!"),
        ("[雷鳴] 太郎：本を読む。", "本を読む。"),
        ("［雷鳴］｛足音｝本を読む。", "本を読む。"),
        ("♪ 本を読む ♪", "本を読む"),
        ("♫ ♪ ♬", ""),
        ("（本を読む）", "（本を読む）"),
        ("本を（静かに）読む。", "本を（静かに）読む。"),
        ("（どうしよう）帰ろう", "（どうしよう）帰ろう"),
    ],
)
def test_presets_remove_metadata_without_removing_parenthesized_dialogue(raw, expected):
    assert project_line(raw, RULES).text == expected


@given(st.text(max_size=160))
def test_projection_surviving_runs_map_exactly_to_authored_characters(raw):
    projected = project_line(raw, RULES)
    assert projected.text == "".join(raw[index] for index in projected.offsets)
    assert tuple(sorted(set(projected.offsets))) == projected.offsets
    assert all(raw[start : start + len(text)] == text for text, start in projected.runs())


@pytest.mark.parametrize(
    ("tokenizer_name", "raw"), [("unidic", "太郎：本を読む。\n♪"), ("latin", "Alice: Bonjour !")]
)
def test_disabled_annotation_is_identical_to_existing_tokenization_and_scoring(tokenizer_name, raw):
    tokenizer = get_tokenizer(tokenizer_name)
    scorer = Coloring(Scorer(known=KnownWords.from_set(["本"])))
    lines = [tokenizer.tokenize(line) for line in raw.split("\n") if line.strip()]
    tokens = [token for line in lines for token in line]
    cue = annotate(AnnotationInputs(raw, tokenizer, None, scorer))
    assert (cue.lines, cue.tokens, cue.styles, cue.study_lines) == (
        lines,
        tokens,
        scorer.score_line(tokens),
        None,
    )


def test_deleted_in_word_annotation_cannot_invent_a_token_or_phrase():
    tokenizer = UnidicTokenizer()
    tokens = tokenize_line("図[足音]書館", tokenizer, RULES, lambda forms: set(forms))
    assert all("図書" not in token.surface for token in tokens)
    assert all("図[足音]書館"[token.start : token.end] == token.surface for token in tokens)
    assert (
        phrase_terms(tokenizer, tokens, 0, lambda _term: True, lookup_boundaries([tokens])) is None
    )
    assert phrase_terms(tokenizer, tokens, 0, lambda _term: True) is not None


def test_multiline_empty_rows_keep_native_offsets_and_pillow_fillers_noninteractive():
    raw = "太郎：本を読む。\n♪\n［足音］猫を見る。"
    tokenizer = UnidicTokenizer()
    cue = annotate(AnnotationInputs(raw, tokenizer, lambda _forms: set(), None, study_rules=RULES))
    assert cue.study_lines == ("本を読む。 猫を見る。",)
    assert cue.lines[1] == []
    selection = token_annotations(raw, cue.lines, cue.tokens, tokenizer.is_skippable)
    assert [raw[item.text_start : item.text_end] for item in selection.annotations] == [
        token.surface for token in cue.tokens if not tokenizer.is_skippable(token)
    ]
    display, indices = raw_display_lines(raw, cue.lines)
    assert ["".join(token.surface for token in line) for line in display] == raw.split("\n")
    assert None in indices
    rendered = PillowRasterProvider().render(
        SubtitleRasterRequest(
            RasterStyle.STYLED,
            raw,
            tuple(tuple(line) for line in cue.lines),
            1280,
            40,
            (0, 0, 0, 128),
            preserve_raw=True,
        )
    )
    assert [box.index for box in rendered.boxes] == list(range(len(cue.tokens)))
    assert len(display[1]) == 1 and indices[sum(map(len, display[:1]))] is None


@pytest.mark.parametrize(
    "pattern",
    [
        "(",
        "(a+)+$",
        "a*a*",
        "a+z",
        "(?=a)",
        r"(a)\1",
        "ab|cd",
        r"^a{5000}",
        r"^a{999999999999999999999}",
        "(?i)a",
    ],
)
def test_invalid_or_unbounded_regex_visibly_preserves_raw_text(pattern, caplog):
    rules = rules_from_profile({"preprocess_speaker_labels": True, "preprocess_regex": pattern})
    assert rules.error and rules.error in caplog.text
    assert project_line("太郎：本を読む。", rules).text == "太郎：本を読む。"


def test_supported_custom_regex_is_anchored_and_bounded(caplog):
    rules = rules_from_profile({"preprocess_regex": r"^\[[^\]]+\]\s"})
    assert rules.error == ""
    assert project_line("[footsteps] Hello", rules).text == "Hello"
    oversized = "[" + "a" * 4096
    assert project_line(oversized, rules).text == oversized
    assert "exceeds 4096" in caplog.text
    with pytest.raises(ValueError, match="exceeds 256"):
        custom_pattern("a" * 257)


def test_profile_rules_qualify_token_and_analysis_caches():
    profile = resolve_profile(
        {
            "profile": {"preprocess_annotations": True},
            "profiles": {
                "fr": {
                    "language": "fr",
                    "preprocess_annotations": False,
                    "preprocess_speaker_labels": True,
                }
            },
            "active_profile": "fr",
        }
    )
    assert profile.study_rules == StudyRules(speaker_labels=True)
    raw = "太郎：本を読む。"
    cue = annotate(AnnotationInputs(raw, UnidicTokenizer(), None, None, study_rules=RULES))
    cache = TokenCache()
    cache.put(raw, (), cue, rules=RULES)
    assert cache.get(raw, (), RULES) == cue
    assert cache.get(raw, (), DISABLED) is None
    generation = cache.generation
    cache.clear()
    cache.put(raw, (), cue, rules=RULES, generation=generation)
    assert cache.get(raw, (), RULES) is None
    index = CueIndex([Cue(0, 1, raw)])
    assert analysis_key(index, None, RULES) != analysis_key(index, None)


@pytest.mark.timeout(5)
def test_intentionally_empty_music_is_successful_in_blocking_annotation_and_cache():
    executor = AnnotationExecutor()
    lane = LocalJobLane("cue-annotation", JobLanePolicy(capacity=1), executor.run)
    coordinator = CueAnnotationCoordinator(executor=executor, submitter=lane.submit)
    inputs = AnnotationInputs("♪", UnidicTokenizer(), lambda _forms: set(), None, study_rules=RULES)
    key = AnnotationWorkKey("♪", 1, 1, "jp", 1, 1, study_rules=RULES)
    try:
        cue = coordinator.resolve(key, inputs, priority=AnnotationPriority.CURRENT, timeout=2)
        assert cue.tokens == [] and cue.study_lines == ("",)
        assert coordinator.cached(key) == cue
        cache = TokenCache()
        cache.put("♪", (), cue, rules=RULES)
        assert cache.get("♪", (), RULES) == cue
    finally:
        coordinator.close()
        lane.close(1)


def test_analysis_equals_explicitly_cleaned_cue_without_counting_annotation_kanji():
    raw = "太郎：［雷鳴］本を読む。\n♪"
    tokenizer = UnidicTokenizer()
    expected = analyze_cues([Cue(0, 1, "本を読む。")], None, tokenizer)
    assert analyze_cues([Cue(0, 1, raw)], None, tokenizer, RULES) == expected
    assert analyze_cues([Cue(0, 1, raw)], None, tokenizer) != expected

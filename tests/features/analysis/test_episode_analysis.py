"""Whole-track lexical metrics share the subtitle scorer's eligibility model."""

from saitenka.app.features.analysis.episode_analysis import analysis_key, analyze_cues
from saitenka.app.fsrs import KnownSnap
from saitenka.app.scoring import Scorer, mark_n_plus
from saitenka.app.tokenize import tokenize
from saitenka.app.tokenizer import UnidicTokenizer
from saitenka.app.wordlists import FreqDict, JlptDict, KnownWords
from saitenka.subtitles import Cue, CueIndex

TOKENIZER = UnidicTokenizer()


def test_episode_metrics_dedupe_lemmas_and_kanji_and_classify_n_plus():
    cues = [
        Cue(0, 1, "私は本を読む。"),
        Cue(2, 3, "彼は新しい本を見る。"),
    ]
    scorer = Scorer(known=KnownWords.from_set(["私", "本", "彼"]))

    result = analyze_cues(cues, scorer, TOKENIZER)

    assert result.sentence_count == 2
    assert result.n_plus_one_count == 1
    assert result.n_plus_two_count == 1
    assert result.unique_lemmas >= {"本", "読む", "見る"}
    assert result.unique_kanji >= {"私", "本", "読", "彼", "新", "見"}
    assert "本" not in result.unknown_lemmas
    assert len(result.cues) == 2
    assert result.known_token_coverage == result.known_token_count / result.content_token_count


def test_sentence_count_includes_a_sentence_without_eligible_content_words():
    result = analyze_cues([Cue(0, 1, "はい。")], Scorer(known=KnownWords.from_set([])), TOKENIZER)

    assert result.sentence_count == 1
    assert result.content_token_count == 0


def test_n_plus_two_uses_the_n_plus_one_eligibility_rules():
    tokens = tokenize("太郎は新しい本を読む。")
    known = [token.surface == "本" for token in tokens]

    targets = mark_n_plus(tokens, known, unknowns=2, min_words=3)

    assert {tokens[index].lemma for index in targets} == {"新しい", "読む"}
    assert all(not tokens[index].is_proper_noun for index in targets)


def test_forgotten_words_are_unknown_and_proper_nouns_do_not_become_unknown_types():
    scorer = Scorer(
        known=KnownWords.from_set(["本"]),
        fsrs_snap=KnownSnap.of({"読む": "forgotten"}),
    )

    result = analyze_cues([Cue(0, 1, "太郎は本を読む。")], scorer, TOKENIZER)

    assert "読む" in result.unknown_lemmas
    assert "太郎" not in result.unknown_lemmas


def test_optional_sources_degrade_and_configured_sources_report_distributions():
    cue = Cue(0, 1, "本を読む。")
    plain = analyze_cues([cue], Scorer(known=KnownWords.from_set([])), TOKENIZER)
    sourced = analyze_cues(
        [cue],
        Scorer(
            known=KnownWords.from_set([]),
            jlpt=JlptDict({"本": "N5"}),
            freq=FreqDict({"本": 1, "読む": 5000}),
        ),
        TOKENIZER,
    )

    assert plain.jlpt_distribution is None
    assert plain.frequency_distribution is None
    assert dict(sourced.jlpt_distribution or ())["N5"] == 1
    assert dict(sourced.frequency_distribution or ())["Band 1"] == 1
    assert dict(sourced.frequency_distribution or ())["Band 3"] == 1


def test_cache_key_tracks_subtitle_and_vocabulary_snapshots():
    first = CueIndex([Cue(0, 1, "本を読む。")])
    changed_track = CueIndex([Cue(0, 1, "本を見る。")])
    known = Scorer(known=KnownWords.from_set(["本"]))
    changed_known = Scorer(known=KnownWords.from_set(["本", "読む"]))

    assert analysis_key(first, known) == analysis_key(first, known)
    assert analysis_key(first, known) != analysis_key(changed_track, known)
    assert analysis_key(first, known) != analysis_key(first, changed_known)

"""Pure, immutable lexical analysis for a Japanese subtitle track."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.dict_meta import FreqDict
from saitenka.app.scoring import Scorer, SentenceProfile, is_content, sentence_profiles

if TYPE_CHECKING:
    from saitenka.app.tokenize import Token
    from saitenka.app.tokenizer import Tokenizer
    from saitenka.subtitles import Cue, CueIndex

Distribution = tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class CueAnalysis:
    cue_index: int
    sentence_count: int
    content_token_count: int
    unique_lemmas: frozenset[str]
    unique_kanji: frozenset[str]
    unknown_lemmas: frozenset[str]
    known_token_count: int
    known_lemmas: frozenset[str]
    n_plus_one_count: int
    n_plus_two_count: int
    jlpt_distribution: Distribution | None
    frequency_distribution: Distribution | None


@dataclass(frozen=True, slots=True)
class EpisodeAnalysis:
    cues: tuple[CueAnalysis, ...]
    sentence_count: int
    content_token_count: int
    unique_lemmas: frozenset[str]
    unique_kanji: frozenset[str]
    unknown_lemmas: frozenset[str]
    known_token_count: int
    known_type_count: int
    known_token_coverage: float
    known_type_coverage: float
    n_plus_one_count: int
    n_plus_two_count: int
    jlpt_distribution: Distribution | None
    frequency_distribution: Distribution | None


@dataclass(frozen=True, slots=True)
class AnalysisKey:
    subtitle: str
    vocabulary: str


def _tokens(text: str, tokenizer: Tokenizer) -> list[Token]:
    normalized = text.replace("\\N", "\n").replace("\r", "")
    return [
        token
        for line in normalized.split("\n")
        if line.strip()
        for token in tokenizer.tokenize(line)
    ]


def _kanji(text: str) -> frozenset[str]:
    return frozenset(
        char for char in text if 0x3400 <= ord(char) <= 0x9FFF or 0xF900 <= ord(char) <= 0xFAFF
    )


def _lemma(token: Token) -> str:
    return token.lemma or token.surface


def _distribution(counts: dict[str, int], labels: tuple[str, ...]) -> Distribution:
    return tuple((label, counts.get(label, 0)) for label in labels)


def _jlpt_distribution(
    tokens: list[Token], content_indices: list[int], scorer: Scorer | None
) -> Distribution | None:
    if not (scorer and scorer.jlpt and scorer.enable_jlpt):
        return None
    jlpt_counts: dict[str, int] = {}
    for i in content_indices:
        token = tokens[i]
        level = scorer.jlpt.level(token.lemma, token.surface, token.reading) or "Unlisted"
        jlpt_counts[level] = jlpt_counts.get(level, 0) + 1
    return _distribution(jlpt_counts, ("N5", "N4", "N3", "N2", "N1", "Unlisted"))


def _frequency_distribution(
    tokens: list[Token], content_indices: list[int], scorer: Scorer | None
) -> Distribution | None:
    if not (scorer and scorer.freq and scorer.enable_freq):
        return None
    counts: dict[str, int] = {}
    for i in content_indices:
        token = tokens[i]
        rank = scorer.freq.rank(token.lemma, token.surface, token.reading)
        band = FreqDict.band(rank, scorer.freq_top_x, 5) if rank is not None else None
        label = f"Band {band}" if band else "Unranked"
        counts[label] = counts.get(label, 0) + 1
    return _distribution(counts, ("Band 1", "Band 2", "Band 3", "Band 4", "Band 5", "Unranked"))


def _n_plus_counts(profiles: tuple[SentenceProfile, ...], min_words: int) -> tuple[int, int]:
    eligible = [profile for profile in profiles if len(profile.content_indices) >= min_words]
    return (
        sum(len(profile.unknown_indices) == 1 for profile in eligible),
        sum(len(profile.unknown_indices) == 2 for profile in eligible),
    )


def _cue_analysis(index: int, cue: Cue, scorer: Scorer | None, tokenizer: Tokenizer) -> CueAnalysis:
    tokens = _tokens(cue.text, tokenizer)
    known = [scorer.is_known(token) if scorer else False for token in tokens]
    profiles = sentence_profiles(tokens, known)
    content_indices = [i for i, token in enumerate(tokens) if is_content(token)]
    lemmas = frozenset(_lemma(tokens[i]) for i in content_indices)
    known_lemmas = frozenset(_lemma(tokens[i]) for i in content_indices if known[i])
    unknown_lemmas = frozenset(
        _lemma(tokens[i]) for i in content_indices if not known[i] and not tokens[i].is_proper_noun
    )
    n_plus_one, n_plus_two = _n_plus_counts(profiles, scorer.min_sentence_words if scorer else 3)
    jlpt = _jlpt_distribution(tokens, content_indices, scorer)
    frequency = _frequency_distribution(tokens, content_indices, scorer)

    return CueAnalysis(
        cue_index=index,
        sentence_count=len(profiles),
        content_token_count=len(content_indices),
        unique_lemmas=lemmas,
        unique_kanji=_kanji(cue.text),
        unknown_lemmas=unknown_lemmas,
        known_token_count=sum(known[i] for i in content_indices),
        known_lemmas=known_lemmas,
        n_plus_one_count=n_plus_one,
        n_plus_two_count=n_plus_two,
        jlpt_distribution=jlpt,
        frequency_distribution=frequency,
    )


def _sum_distributions(cues: tuple[CueAnalysis, ...], attr: str) -> Distribution | None:
    available = [getattr(cue, attr) for cue in cues if getattr(cue, attr) is not None]
    if not available:
        return None
    labels = tuple(label for label, _count in available[0])
    return tuple((label, sum(dict(dist)[label] for dist in available)) for label in labels)


def analyze_cues(cues: list[Cue], scorer: Scorer | None, tokenizer: Tokenizer) -> EpisodeAnalysis:
    per_cue = tuple(_cue_analysis(i, cue, scorer, tokenizer) for i, cue in enumerate(cues))
    lemmas = frozenset().union(*(cue.unique_lemmas for cue in per_cue))
    known_lemmas = frozenset().union(*(cue.known_lemmas for cue in per_cue))
    unknown_lemmas = frozenset().union(*(cue.unknown_lemmas for cue in per_cue))
    kanji = frozenset().union(*(cue.unique_kanji for cue in per_cue))
    tokens = sum(cue.content_token_count for cue in per_cue)
    known_tokens = sum(cue.known_token_count for cue in per_cue)
    known_types = len(known_lemmas - unknown_lemmas)
    return EpisodeAnalysis(
        cues=per_cue,
        sentence_count=sum(cue.sentence_count for cue in per_cue),
        content_token_count=tokens,
        unique_lemmas=lemmas,
        unique_kanji=kanji,
        unknown_lemmas=unknown_lemmas,
        known_token_count=known_tokens,
        known_type_count=known_types,
        known_token_coverage=known_tokens / tokens if tokens else 0.0,
        known_type_coverage=known_types / len(lemmas) if lemmas else 0.0,
        n_plus_one_count=sum(cue.n_plus_one_count for cue in per_cue),
        n_plus_two_count=sum(cue.n_plus_two_count for cue in per_cue),
        jlpt_distribution=_sum_distributions(per_cue, "jlpt_distribution"),
        frequency_distribution=_sum_distributions(per_cue, "frequency_distribution"),
    )


def _digest(parts) -> str:
    h = hashlib.blake2b(digest_size=16)
    for part in parts:
        h.update(repr(part).encode())
        h.update(b"\0")
    return h.hexdigest()


def analysis_key(index: CueIndex, scorer: Scorer | None) -> AnalysisKey:
    subtitle = _digest((cue.start, cue.end, cue.text) for cue in index.cues)
    if scorer is None:
        return AnalysisKey(subtitle, "no-scorer")
    known = tuple(
        (surface, tuple(sorted(readings)))
        for surface, readings in sorted(scorer.known.by_surface.items())
    )
    snap = scorer.fsrs_snap
    fsrs = (
        (
            tuple(
                (surface, tuple(sorted(states.items())))
                for surface, states in sorted(snap.by_surface.items())
            ),
            tuple(sorted(snap.readings.items())),
        )
        if snap
        else ()
    )
    vocabulary = _digest((known, tuple(sorted(scorer.known.readings)), fsrs))
    return AnalysisKey(subtitle, vocabulary)


def cue_result(result: EpisodeAnalysis | None, cue_index: int) -> CueAnalysis | None:
    if result is None or not 0 <= cue_index < len(result.cues):
        return None
    return result.cues[cue_index]

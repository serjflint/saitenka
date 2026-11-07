"""Opt-in subtitle study projection; authored text and character positions stay authoritative."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from saitenka_tokenize.japanese import Token
    from saitenka_tokenize.registry import Tokenizer

log = logging.getLogger(__name__)
# CPython exposes its parsed regex grammar only through these internal modules.
_constants = import_module("re._constants")
_parser = import_module("re._parser")
MAX_PATTERN = 256
MAX_TEXT = 4096


@dataclass(frozen=True, slots=True)
class StudyRules:
    speaker_labels: bool = False
    annotations: bool = False
    music: bool = False
    join_lines: bool = False
    custom_regex: str = ""
    error: str = ""

    @property
    def enabled(self) -> bool:
        return not self.error and bool(
            self.speaker_labels
            or self.annotations
            or self.music
            or self.join_lines
            or self.custom_regex
        )


DISABLED = StudyRules()
_ATOMS = {_constants.LITERAL, _constants.NOT_LITERAL, _constants.ANY, _constants.CATEGORY}
_CLASS = {_constants.NEGATE, _constants.LITERAL, _constants.RANGE, _constants.CATEGORY}


def _single_atom(op, value) -> bool:
    return op in _ATOMS or (op is _constants.IN and all(kind in _CLASS for kind, _ in value))


@lru_cache(maxsize=64)
def custom_pattern(pattern: str) -> re.Pattern[str]:
    """Validate a bounded regex dialect before the expression can reach a cue worker."""
    if len(pattern) > MAX_PATTERN:
        raise ValueError(f"custom regex exceeds {MAX_PATTERN} characters")
    parsed = _parser.parse(pattern, 0)
    repeats = 0
    for op, value in parsed:
        if _single_atom(op, value) or op is _constants.AT:
            continue
        if op in {_constants.MAX_REPEAT, _constants.MIN_REPEAT}:
            minimum, maximum, child = value
            repeats += 1
            anchored = parsed[0] in {
                (_constants.AT, _constants.AT_BEGINNING),
                (_constants.AT, _constants.AT_BEGINNING_STRING),
            }
            if (
                anchored
                and repeats == 1
                and len(child) == 1
                and _single_atom(*child[0])
                and minimum <= MAX_TEXT
                and (maximum <= MAX_TEXT or maximum == _constants.MAXREPEAT)
            ):
                continue
        raise ValueError(
            "custom regex supports atoms/classes/anchors and one anchored single-atom repeat"
        )
    if parsed.state.flags & ~re.UNICODE:
        raise ValueError("custom regex flags are not supported")
    return re.compile(pattern)


def _profile_rules(raw: dict) -> StudyRules:
    values = {}
    for name in ("speaker_labels", "annotations", "music", "join_lines"):
        value = raw.get(f"preprocess_{name}", False)
        if not isinstance(value, bool):
            raise TypeError(f"preprocess_{name} must be boolean")
        values[name] = value
    pattern = raw.get("preprocess_regex", "")
    if not isinstance(pattern, str):
        raise TypeError("preprocess_regex must be a string")
    if pattern:
        custom_pattern(pattern)
    return StudyRules(
        speaker_labels=values["speaker_labels"],
        annotations=values["annotations"],
        music=values["music"],
        join_lines=values["join_lines"],
        custom_regex=pattern,
    )


def rules_from_profile(raw: dict) -> StudyRules:
    try:
        return _profile_rules(raw)
    except (TypeError, ValueError, OverflowError, re.error) as error:
        message = f"subtitle preprocessing disabled: {error}"
        log.warning(message)
        return StudyRules(error=message)


_SPEAKER = re.compile(
    r"^\s*(?:[^\s:：()（）「『!?！？。]{1,24}[:：]\s*|"
    r"[（(][^()（）\n!?！？。]{1,24}[）)](?:[:：]\s*|\s+|(?=[「『])))"
)
_ANNOTATION = re.compile(
    r"\[[^\[\]\n]*\]|［[^［］\n]*］|【[^【】\n]*】|\{[^{}\n]*\}|｛[^｛｝\n]*｝"
)


@dataclass(frozen=True, slots=True)
class StudyLine:
    raw: str
    text: str
    offsets: tuple[int, ...]

    def runs(self) -> tuple[tuple[str, int], ...]:
        """Contiguous surviving runs; a deleted in-word annotation never invents a compound."""
        runs: list[tuple[str, int]] = []
        start = 0
        for index in range(1, len(self.offsets) + 1):
            if index == len(self.offsets) or self.offsets[index] != self.offsets[index - 1] + 1:
                runs.append((self.text[start:index], self.offsets[start]))
                start = index
        return tuple(runs)


def project_line(raw: str, rules: StudyRules = DISABLED) -> StudyLine:
    if not rules.enabled:
        return StudyLine(raw, raw, tuple(range(len(raw))))
    if rules.custom_regex and len(raw) > MAX_TEXT:
        log.warning("subtitle preprocessing skipped: cue line exceeds %s characters", MAX_TEXT)
        return StudyLine(raw, raw, tuple(range(len(raw))))
    patterns = [
        pattern
        for enabled, pattern in (
            (rules.annotations, _ANNOTATION),
            (rules.speaker_labels, _SPEAKER),
        )
        if enabled
    ]
    text, offsets = raw, list(range(len(raw)))
    for pattern in patterns:
        text, offsets = _remove_matches(text, offsets, pattern)
    if rules.music:
        text, offsets = _music_edges(text, offsets)
    if rules.custom_regex:
        text, offsets = _remove_matches(text, offsets, custom_pattern(rules.custom_regex))
    return StudyLine(raw, text, tuple(offsets))


def _remove_matches(text, offsets, pattern):
    keep = [True] * len(text)
    for match in pattern.finditer(text):
        keep[match.start() : match.end()] = [False] * (match.end() - match.start())
    return (
        "".join(char for char, retained in zip(text, keep, strict=True) if retained),
        [offset for offset, retained in zip(offsets, keep, strict=True) if retained],
    )


def _music_edges(text, offsets):
    start, end = 0, len(text)
    while start < end and (text[start].isspace() or text[start] in "♪♫♬"):
        start += 1
    while end > start and (text[end - 1].isspace() or text[end - 1] in "♪♫♬"):
        end -= 1
    return text[start:end], offsets[start:end]


def tokenize_line(
    raw: str,
    tokenizer: Tokenizer,
    rules: StudyRules = DISABLED,
    terms_exist: Callable[[Sequence[str]], set[str]] | None = None,
) -> list[Token]:
    if not rules.enabled:
        tokens = tokenizer.tokenize(raw)
        return tokenizer.merge_dict_compounds(tokens, terms_exist) if terms_exist else tokens
    tokens = []
    for text, start in project_line(raw, rules).runs():
        run = tokenizer.tokenize(text)
        if terms_exist is not None:
            run = tokenizer.merge_dict_compounds(run, terms_exist)
        tokens.extend(
            replace(token, start=token.start + start, end=token.end + start) for token in run
        )
    return tokens


def sentence_lines(text: str, rules: StudyRules) -> tuple[str, ...] | None:
    if not rules.enabled:
        return None
    lines = tuple(project_line(line, rules).text for line in text.split("\n"))
    if rules.join_lines:
        return (" ".join(line.strip() for line in lines if line.strip()),)
    return lines


def tokenize_text(text: str, tokenizer: Tokenizer, rules: StudyRules = DISABLED) -> list[Token]:
    """Whole-cue consumers use global authored offsets; annotation keeps per-line offsets."""
    if not rules.enabled:
        return tokenizer.tokenize(text)
    normalized = text.replace("\\N", "\n").replace("\r", "")
    tokens: list[Token] = []
    offset = 0
    for line in normalized.split("\n"):
        tokens.extend(
            replace(token, start=token.start + offset, end=token.end + offset)
            for token in tokenize_line(line, tokenizer, rules)
        )
        offset += len(line) + 1
    return tokens


def lookup_boundaries(lines) -> tuple[int, ...]:
    boundaries = []
    index = 0
    for line in lines:
        previous = None
        for token in line:
            if previous is None or previous.end != token.start:
                boundaries.append(index)
            previous = token
            index += 1
    return (*boundaries, index)


def lookup_run(tokens: list[Token], index: int, boundaries: tuple[int, ...]):
    if not boundaries:
        return tokens, index, 0
    start = max(boundary for boundary in boundaries if boundary <= index)
    end = min(boundary for boundary in boundaries if boundary > index)
    return tokens[start:end], index - start, start


def phrase_terms(tokenizer, tokens, index, has_term, boundaries=()):
    run, selected, offset = lookup_run(tokens, index, boundaries)
    result = tokenizer.phrase_terms(tokens=run, index=selected, has_term=has_term)
    if result is None:
        return None
    terms, start, end = result
    return terms, start + offset, end + offset


def inflected(tokenizer, tokens, index, boundaries=()):
    run, selected, _ = lookup_run(tokens, index, boundaries)
    return tokenizer.inflected_in(run, selected)


def line_inflected(tokenizer, tokens, index, rules):
    return inflected(tokenizer, tokens, index, lookup_boundaries([tokens]) if rules.enabled else ())

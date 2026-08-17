"""Newest-wins worker for phrase and mining metadata used by hover presentation."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.lookup import card_for
from saitenka.app.tokenizer import get_tokenizer

if TYPE_CHECKING:
    from saitenka.app.tokenize import Token


@dataclass(frozen=True, slots=True)
class HoverMetadataKey:
    generation: int
    dependency_generation: int
    mined_generation: int
    cue_identity: object
    index: int
    job_id: int | None = None


@dataclass(frozen=True, slots=True)
class HoverMetadataRequest:
    key: HoverMetadataKey
    tokenizer_name: str
    tokens: tuple[Token, ...]
    dictionary: object | None
    mined: frozenset[str]


@dataclass(frozen=True, slots=True)
class HoverMetadata:
    key: HoverMetadataKey
    phrase_terms: tuple[str, ...]
    phrase_span: tuple[int, int] | None
    mined: bool
    group_mined: tuple[bool, ...]
    error: bool = False


@dataclass(frozen=True, slots=True)
class NestedMetadataKey:
    generation: int
    dependency_generation: int
    mined_generation: int
    tooltip_origin: int
    tail: str


@dataclass(frozen=True, slots=True)
class NestedMetadataRequest:
    key: NestedMetadataKey
    tokenizer_name: str
    dictionary: object | None
    mined: frozenset[str]


@dataclass(frozen=True, slots=True)
class NestedMetadata:
    key: NestedMetadataKey
    token: Token | None
    phrase_terms: tuple[str, ...]
    mined: bool
    group_mined: tuple[bool, ...]
    error: bool = False


class InteractionMetadataActor:
    """Own tokenizer/Jamdict work and publish immutable, identity-qualified results."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pending: HoverMetadataRequest | NestedMetadataRequest | None = None
        self._results: queue.SimpleQueue[HoverMetadata | NestedMetadata] = queue.SimpleQueue()
        self._closed = False
        self._thread: threading.Thread | None = None

    def submit(self, request: HoverMetadataRequest | NestedMetadataRequest) -> None:
        with self._condition:
            if self._closed:
                return
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name="saitenka-interaction-metadata",
                    daemon=True,
                )
                self._thread.start()
            self._pending = request
            self._condition.notify()

    def drain(self) -> list[HoverMetadata | NestedMetadata]:
        results: list[HoverMetadata | NestedMetadata] = []
        while True:
            try:
                results.append(self._results.get_nowait())
            except queue.Empty:
                return results

    def close(self, timeout: float = 1.0) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while request := self._next():
            try:
                result = (
                    _resolve_hover(request)
                    if isinstance(request, HoverMetadataRequest)
                    else _resolve_nested(request)
                )
            except Exception:  # noqa: BLE001 -- optional interaction metadata fails closed
                result = (
                    HoverMetadata(request.key, (), None, mined=False, group_mined=(), error=True)
                    if isinstance(request, HoverMetadataRequest)
                    else NestedMetadata(
                        request.key, None, (), mined=False, group_mined=(), error=True
                    )
                )
            with self._condition:
                if self._closed:
                    return
            self._results.put(result)

    def _next(self) -> HoverMetadataRequest | NestedMetadataRequest | None:
        with self._condition:
            while self._pending is None and not self._closed:
                self._condition.wait()
            if self._closed:
                return None
            request, self._pending = self._pending, None
            return request


def _resolve_hover(request: HoverMetadataRequest) -> HoverMetadata:
    tokenizer = get_tokenizer(request.tokenizer_name)
    tokens = list(request.tokens)
    token = tokens[request.key.index]
    terms: tuple[str, ...] = ()
    span: tuple[int, int] | None = None
    has_term = getattr(request.dictionary, "has_term", None)
    if has_term is not None:
        phrase = tokenizer.phrase_terms(tokens=tokens, index=request.key.index, has_term=has_term)
        if phrase is not None:
            found, start, end = phrase
            terms, span = tuple(found), (start, end)
    mined = bool(request.mined and card_for(token).expression in request.mined)
    group_mined: tuple[bool, ...] = ()
    cards_for = getattr(request.dictionary, "cards_for", None)
    if request.mined and cards_for is not None:
        cards = cards_for(token, extra_terms=terms)
        if len(cards) >= 2:
            group_mined = tuple(card.expression in request.mined for card in cards)
    return HoverMetadata(request.key, terms, span, mined, group_mined)


def _resolve_nested(request: NestedMetadataRequest) -> NestedMetadata:
    tokenizer = get_tokenizer(request.tokenizer_name)
    tokens = tokenizer.tokenize(request.key.tail)
    token = tokens[0] if tokens else None
    if token is None or tokenizer.is_skippable(token):
        return NestedMetadata(request.key, None, (), mined=False, group_mined=())
    terms: tuple[str, ...] = ()
    has_term = getattr(request.dictionary, "has_term", None)
    if has_term is not None:
        phrase = tokenizer.phrase_terms(tokens=tokens, index=0, has_term=has_term)
        if phrase is not None:
            terms = tuple(phrase[0])
    mined = bool(request.mined and card_for(token).expression in request.mined)
    group_mined: tuple[bool, ...] = ()
    cards_for = getattr(request.dictionary, "cards_for", None)
    if request.mined and cards_for is not None:
        cards = cards_for(token, extra_terms=terms)
        if len(cards) >= 2:
            group_mined = tuple(card.expression in request.mined for card in cards)
    return NestedMetadata(request.key, token, terms, mined, group_mined)

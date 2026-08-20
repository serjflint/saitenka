"""Newest-wins phrase and mining metadata jobs used by hover presentation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.lookup import card_for
from saitenka.app.tokenizer import get_tokenizer
from saitenka.runtime import EffectFinished, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy, JobSubmitter, configure_lane

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

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


MetadataRequest = HoverMetadataRequest | NestedMetadataRequest
MetadataResult = HoverMetadata | NestedMetadata


@dataclass(slots=True)
class InteractionMetadataState:
    """Event-thread state; the broker owns execution and lifetime."""

    next_sequence: int = 0
    inflight: tuple[int, MetadataRequest] | None = None
    pending: MetadataRequest | None = None
    publishing: bool = False
    closed: bool = False


def run_metadata(request: object, cancelled: threading.Event) -> object:
    if cancelled.is_set():
        return None
    if isinstance(request, HoverMetadataRequest):
        return _resolve_hover(request)
    if isinstance(request, NestedMetadataRequest):
        return _resolve_nested(request)
    raise TypeError("invalid interaction metadata request")


def configure_runtime_job(ipc) -> JobSubmitter | None:
    return configure_lane(
        ipc,
        "interaction-metadata",
        JobLanePolicy(capacity=1),
        run_metadata,
    )


def submit(
    state: InteractionMetadataState,
    request: MetadataRequest,
    job_submitter: JobSubmitter | None,
    on_finished: Callable[[EffectFinished], None],
) -> bool:
    """Admit immediately or retain only the newest intent behind the running job."""
    if state.closed:
        return False
    if state.inflight is not None or state.publishing:
        state.pending = request
        return True
    if job_submitter is None:
        return False
    state.next_sequence += 1
    sequence = state.next_sequence
    state.inflight = (sequence, request)
    accepted = job_submitter(
        owner=Owner.INTERACTION,
        identity=sequence,
        lane="interaction-metadata",
        request=request,
        on_finished=on_finished,
    )
    if not accepted and state.inflight == (sequence, request):
        state.inflight = None
    return accepted


def finish(state: InteractionMetadataState, completion: EffectFinished) -> MetadataResult | None:
    """Retire one correlated job and normalize every failure to a typed result."""
    current = state.inflight
    if state.closed or current is None or completion.identity != current[0]:
        return None
    _sequence, request = current
    state.inflight = None
    state.publishing = True
    result = completion.result if completion.outcome is EffectOutcome.SUCCEEDED else None
    if isinstance(request, HoverMetadataRequest):
        if isinstance(result, HoverMetadata) and result.key == request.key:
            return result
        return HoverMetadata(request.key, (), None, mined=False, group_mined=(), error=True)
    if isinstance(result, NestedMetadata) and result.key == request.key:
        return result
    return NestedMetadata(request.key, None, (), mined=False, group_mined=(), error=True)


def finish_publication(state: InteractionMetadataState) -> None:
    state.publishing = False


def submit_pending(
    state: InteractionMetadataState,
    job_submitter: JobSubmitter | None,
    on_finished: Callable[[EffectFinished], None],
) -> bool:
    request, state.pending = state.pending, None
    if request is None:
        return False
    return submit(state, request, job_submitter, on_finished)


def close(state: InteractionMetadataState) -> None:
    state.closed = True
    state.inflight = None
    state.pending = None
    state.publishing = False


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

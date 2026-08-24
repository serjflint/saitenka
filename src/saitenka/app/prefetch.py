"""Bounded speculative dictionary and tooltip warming."""

from __future__ import annotations

import heapq
import logging
import threading
from dataclasses import dataclass
from itertools import islice
from typing import TYPE_CHECKING, Protocol, cast

from saitenka import otel_metrics
from saitenka.app.perf import gil_disabled
from saitenka.runtime import EffectFinished, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy

if TYPE_CHECKING:
    from collections.abc import Callable, Container

    from saitenka.app.profile_controller import ProfileController
    from saitenka.app.scoring import Scorer
    from saitenka.app.token_cache import TokenCache
    from saitenka.app.tokenize import Token
    from saitenka.app.tokenizer import Tokenizer
    from saitenka.subtitles import CueIndex

log = logging.getLogger(__name__)

# Auto (``[perf].prefetch_workers = 0``) worker counts — flat, sane per-build defaults rather than a
# cpu-count formula: render parallelism plateaus by ~4 (see the render-parallelism notes) and each
# worker costs real RAM, so a bigger number buys idle-warming coverage we almost never need. Override
# with a positive ``prefetch_workers`` to pin/cap it.
_AUTO_WORKERS_FREE_THREADED = (
    4  # free-threaded: renders in parallel, 4 pairs with the render pool width
)
_AUTO_WORKERS_GIL = 2  # a GIL build can't render in parallel — extra workers only contend
_MAX_WARM_PENDING = 64
_MAX_HEAD_PENDING = 64
_MAX_HEAD_TOKEN_PROBES = 64


@dataclass(frozen=True, slots=True)
class PrefetchItem:
    """One speculative background job for ``token``: either a FULL panel render (``full=True``,
    when the user is *engaged* — paused or hovering the video, a hover is imminent) or a cheap
    dict-only WARM (``full=False``): just decode+cache each dictionary's semantic entry for this word,
    skipping layout/drawing entirely. Warm jobs run for every new subtitle line regardless of
    engagement — genuinely idle CPU time while the line is only being watched/listened to, not yet
    looked at — so that the JSON-decode cost (the single biggest cost in a `--stress` profile, see
    decoded-entry LRU) is usually already paid by the time a hover actually happens.

    ``gen`` is the prefetch generation at enqueue time — a line change / resume / seek bumps the
    SessionController's counter, so stale items are dropped by the worker. ``mined`` is evaluated on the MAIN
    thread (card_for → jamdict is not worker-safe) and selects the ⊕/✓ header variant (unused by a
    warm job, which never builds a header)."""

    gen: int
    token: Token
    inflected: str
    mined: bool
    full: bool = True


@dataclass(frozen=True, slots=True)
class HeadPrefetchItem:
    """A selective speculative HEAD render for an upcoming word: the SAME
    viewport-capped head a real hover would render (``panel_for(..., finish=False)``), not a whole
    ``finish()``, and not a separate cache — it's written straight into ``reader._panel_cache`` via
    the exact path a real hover reads, so a later hover is a plain cache hit with no promotion/
    key-matching logic to get wrong. Only enqueued for words the priority function judges worth the
    extra render cost over plain decode-warming (see ``_head_priority``); selectivity is the actual
    RAM/CPU cap, not just the queue's ``maxsize``. ``mined`` is resolved on the main thread (jamdict is
    not worker-safe), same contract as :class:`PrefetchItem`."""

    gen: int
    token: Token
    inflected: str
    mined: bool


class PrefetchState:
    """Bounded speculative intent and accepted-job state."""

    def __init__(self, head_queue_max: int) -> None:
        if not 1 <= head_queue_max <= _MAX_HEAD_PENDING:
            raise ValueError(f"head prefetch queue limit must be between 1 and {_MAX_HEAD_PENDING}")
        self.head_queue_max = head_queue_max
        self.head_built = 0
        self.gen = 0
        self.key: tuple[str, bool] | None = None
        self.sequence = 0
        self.workers = 0
        self.pending_limit = head_queue_max + _MAX_WARM_PENDING
        self.pending: list[tuple[int, int, PrefetchIdentity, PrefetchWork]] = []
        self.inflight: dict[int, tuple[PrefetchIdentity, PrefetchWork]] = {}
        self.submitter: JobSubmitter | None = None
        self.closed = False

    def cancel(self) -> int:
        """Invalidate queued and executing work and return the new generation."""
        self.gen += 1
        for _identity, work in self.inflight.values():
            work.superseded.set()
        pending = len(self.pending)
        for _priority, _sequence, _identity, work in self.pending:
            work.superseded.set()
        self.pending.clear()
        if pending and otel_metrics.prefetch_queue_depth is not None:
            otel_metrics.prefetch_queue_depth.add(-pending)
        return self.gen

    @property
    def snapshot(self) -> PrefetchSnapshot:
        return PrefetchSnapshot(
            self.gen,
            len(self.pending),
            len(self.inflight),
            self.pending_limit,
            self.head_built,
            self.closed,
        )


@dataclass(frozen=True, slots=True)
class PrefetchSnapshot:
    generation: int
    pending: int
    inflight: int
    pending_limit: int
    head_built: int
    closed: bool


@dataclass(frozen=True, slots=True)
class PrefetchIdentity:
    sequence: int
    generation: int
    kind: str


@dataclass(frozen=True, slots=True)
class PrefetchWork:
    item: PrefetchItem | HeadPrefetchItem
    superseded: threading.Event


class PrefetchHost(Protocol):
    profile_controller: ProfileController
    tip_scale: TipScale

    def _panel_for(self, token, inflected, **kwargs): ...

    def _worker_seed_head(self, panel, token, inflected, **kwargs) -> bool: ...

    def _precompose_head(self, panel, token, inflected, **kwargs) -> None: ...

    def _mem_fill(self, token, inflected, **kwargs) -> None: ...


class PrefetchDictionary(Protocol):
    def entry_for(self, token, inflected): ...


class JobSubmitter(Protocol):
    def __call__(
        self,
        *,
        owner: Owner,
        identity: object,
        lane: str,
        request: object,
        on_finished,
    ) -> bool: ...


class HostPrefetchBackend:
    """Worker adapter over the existing cache and panel seams."""

    def __init__(self, host: object) -> None:
        self._reader = cast("PrefetchHost", host)

    def run(self, item: PrefetchItem | HeadPrefetchItem, should_cancel) -> bool:
        if isinstance(item, HeadPrefetchItem):
            with otel_metrics.traced("prefetch_decode", kind="head_ahead"):
                return self._render_head(item, should_cancel)
        with otel_metrics.traced("prefetch_decode", kind="head" if item.full else "warm"):
            if item.full:
                return self._render_head(item, should_cancel)
            reader = self._reader
            if reader.profile_controller.dict_set is not None and not should_cancel():
                reader.profile_controller.dict_set.entry_for(item.token, item.inflected)
        return not should_cancel()

    def _render_head(self, item: PrefetchItem | HeadPrefetchItem, should_cancel) -> bool:
        reader = self._reader
        cap = reader.tip_scale.cap
        panel = reader._panel_for(item.token, item.inflected, min_h=cap, mined=item.mined)
        if should_cancel():
            return False
        if not reader._worker_seed_head(
            panel, item.token, item.inflected, mined=item.mined, cap=cap
        ):
            reader._precompose_head(panel, item.token, item.inflected, mined=item.mined, cap=cap)
            if not should_cancel():
                reader._mem_fill(item.token, item.inflected, mined=item.mined)
        return not should_cancel()


def run_prefetch(work: object, cancelled: threading.Event, backend: HostPrefetchBackend) -> bool:
    if not isinstance(work, PrefetchWork):
        raise TypeError("invalid speculative-prefetch request")
    should_cancel = lambda: cancelled.is_set() or work.superseded.is_set()  # noqa: E731
    if should_cancel():
        return False
    try:
        return backend.run(work.item, should_cancel)
    except Exception:
        log.debug("speculative prefetch failed", exc_info=True)
        raise


def prefetch_worker_count(tokenizer, configured: int) -> int:
    """An explicit ``[perf].prefetch_workers`` (>0) pins the count on both builds; ``0`` (auto) picks a
    flat per-build default (``_AUTO_WORKERS_FREE_THREADED`` / ``_AUTO_WORKERS_GIL``). Each worker is
    PERSISTENT and carries real per-thread RAM (SQLite page cache + FreeType faces + the free-threaded
    allocator arena), so this is primarily a RAM/coverage knob — see PerfOptions.

    ``gil_disabled()`` is only trustworthy AFTER fugashi has loaded — it wraps a C extension that
    hasn't declared free-threaded safety and silently re-enables the GIL on first use, not at import
    (``tokenize.py``). This is called from ``start_prefetch()`` during SessionController construction, before any
    subtitle line has ever been tokenized, so without this warm-up it always sees the pre-fugashi
    state — spawning the free-threaded worker count on a build that loses the GIL moments later anyway,
    paying the allocator's per-thread-arena memory tax (see ``vibe/hot-path-idle-spreading-plan.md``)
    for parallelism that never actually happens. Force the load now (its one-time cost lands at startup
    instead of on the first real subtitle line either way) so this reflects the GIL state that will
    actually hold for the session."""
    tokenizer.tokenize("")
    if configured > 0:  # explicit [perf].prefetch_workers pins it on BOTH builds
        return configured
    return _AUTO_WORKERS_FREE_THREADED if gil_disabled() else _AUTO_WORKERS_GIL


def start_prefetch(
    ipc,
    state: PrefetchState,
    backend: HostPrefetchBackend,
    tokenizer,
    workers: int,
    *,
    enabled: bool,
) -> None:
    if not enabled:
        return
    if state.closed or state.submitter is not None:
        return
    desired = prefetch_worker_count(tokenizer, workers)
    if not ipc.register_runtime_job_lane(
        "speculative-prefetch",
        JobLanePolicy(capacity=desired, workers=desired),
        lambda request, cancelled: run_prefetch(request, cancelled, backend),
    ):
        return
    state.workers = desired
    state.submitter = ipc.submit_runtime_job


def _submit_pending(
    state: PrefetchState,
    on_finished,
) -> None:
    submitter = state.submitter
    while (
        not state.closed
        and submitter is not None
        and len(state.inflight) < state.workers
        and state.pending
    ):
        _priority, _sequence, identity, work = heapq.heappop(state.pending)
        if otel_metrics.prefetch_queue_depth is not None:
            otel_metrics.prefetch_queue_depth.add(-1)
        state.inflight[identity.sequence] = (identity, work)
        accepted = submitter(
            owner=Owner.INTERACTION,
            identity=identity,
            lane="speculative-prefetch",
            request=work,
            on_finished=on_finished,
        )
        if accepted:
            continue
        state.inflight.pop(identity.sequence, None)
        log.debug("speculative-prefetch admission rejected")
        rejected = len(state.pending)
        for _queued_priority, _queued_sequence, _queued_identity, queued in state.pending:
            queued.superseded.set()
        state.pending.clear()
        if rejected and otel_metrics.prefetch_queue_depth is not None:
            otel_metrics.prefetch_queue_depth.add(-rejected)
        break


def schedule(
    state: PrefetchState,
    jobs: list[tuple[int, PrefetchItem | HeadPrefetchItem]],
    on_finished,
) -> bool:
    if state.closed or state.submitter is None or state.workers <= 0:
        return False
    jobs.sort(key=lambda job: job[0])
    admitted = False
    for priority, item in jobs[: state.pending_limit]:
        state.sequence += 1
        identity = PrefetchIdentity(
            state.sequence,
            item.gen,
            "head-ahead" if isinstance(item, HeadPrefetchItem) else "head" if item.full else "warm",
        )
        heapq.heappush(
            state.pending,
            (priority, state.sequence, identity, PrefetchWork(item, threading.Event())),
        )
        admitted = True
        if otel_metrics.prefetch_queue_depth is not None:
            otel_metrics.prefetch_queue_depth.add(1)
    _submit_pending(state, on_finished)
    return admitted


def finish(
    state: PrefetchState,
    completion: EffectFinished,
    on_finished,
) -> None:
    identity = completion.identity
    if not isinstance(identity, PrefetchIdentity):
        return
    current = state.inflight.pop(identity.sequence, None)
    if current is None or current[0] != identity:
        return
    if (
        completion.outcome is EffectOutcome.SUCCEEDED
        and completion.result is True
        and identity.generation == state.gen
        and identity.kind == "head-ahead"
    ):
        state.head_built += 1
    _submit_pending(state, on_finished)


def close(state: PrefetchState) -> None:
    state.cancel()
    state.closed = True
    state.inflight.clear()


def _candidates(tokens, styles, tokenizer) -> list[tuple[int, int, Token]]:
    """This line's content words worth warming, N+1 first (likeliest hover / mine target),
    de-duplicated by lemma. Each entry is ``(priority, token_index, token)``."""
    seen: set[str] = set()
    items: list[tuple[int, int, Token]] = []
    for i, t in enumerate(tokens):
        if not tokenizer.is_content(t) or t.lemma in seen:
            continue
        seen.add(t.lemma)
        np1 = bool(styles and i < len(styles) and styles[i].tag.startswith("n+1"))
        items.append((0 if np1 else 1, i, t))
    items.sort(key=lambda x: x[0])
    return items


@dataclass(frozen=True, slots=True)
class PrefetchPorts:
    """What one speculative-warming pass reads: the cue on screen, whether the user looks engaged,
    and where the queued work goes.

    `engaged` is a fact rather than the pause property and the mouse flag it is derived from — the
    pass only ever asks "is a hover imminent", and the two inputs answer that one question.
    """

    enabled: bool
    engaged: bool
    state: PrefetchState
    cues: LookaheadCues
    tokens: list[Token]
    styles: object
    tokenizer: Tokenizer
    inflected: Callable[[int], str]
    is_mined: Callable[[Token], bool]
    finish: Callable[[EffectFinished], None]


@dataclass(frozen=True, slots=True)
class HeadProbe:
    """What deciding a speculative HEAD render has to look at: the scorer that ranks a word, and the
    panel cache that says it is already warm. Separate from `PrefetchPorts` because the head pass is
    optional — without a scorer there is no ranking, and the warm pass runs anyway.
    """

    scorer: Scorer | None
    panel_key: Callable[..., object]
    panel_cache: Container[object]
    lookahead: int


def update_prefetch(ports: PrefetchPorts, head: HeadProbe) -> None:
    """Queue the current line's content words for background work every time the line (or engagement)
    changes — *engaged* (paused OR the cursor over the video) gets a viewport-first HEAD render (a
    hover is imminent); otherwise a cheap dict-only WARM (``full=False``): the video is just playing, but
    that's exactly the idle time to pay the JSON-decode cost. N+1 words go first. On any change bump
    the generation so in-flight renders are dropped; tokens pass by value (frozen), so a line change
    can't make a worker read stale state.

    With ``prefetch_lookahead`` set, the next few cues' words are then WARMED too (dict-only) so the
    first hover after the line advances is already decoded."""
    if not ports.enabled:
        return
    key = (ports.cues.text, ports.engaged)
    state = ports.state
    if key == state.key:
        return
    state.key = key
    gen = state.cancel()
    cands = _candidates(ports.tokens, ports.styles, ports.tokenizer)
    jobs: list[tuple[int, PrefetchItem | HeadPrefetchItem]] = []
    for _, i, t in cands:
        jobs.append(
            (
                0 if ports.engaged else 2,
                PrefetchItem(gen, t, ports.inflected(i), ports.is_mined(t), ports.engaged),
            )
        )
    if ports.cues.lookahead > 0:
        jobs.extend(
            _lookahead_items(ports.cues, ports.tokenizer, gen, {t.lemma for _, _, t in cands})
        )
    if head.lookahead > 0:
        jobs.extend(_head_prefetch_items(ports, head, gen, {t.lemma for _, _, t in cands}))
    schedule(state, jobs, ports.finish)


@dataclass(frozen=True, slots=True)
class LookaheadCues:
    """Which cues to warm ahead of the current one. One value, because these four are read together
    and a mismatched set warms the wrong line — `preferred` only means anything against `index`."""

    index: object
    text: str
    nav_index: int
    lookahead: int


def _lookahead_items(
    cues: LookaheadCues, tokenizer, gen: int, seen: set[str]
) -> list[tuple[int, PrefetchItem]]:
    """WARM (dict-only, never full, ``mined=False``) the content words of the next
    ``prefetch_lookahead`` cues — a future line is never *engaged* and never builds a header, so no
    main-thread jamdict/scorer work runs here. ``seen`` carries the current line's lemmas so a word
    already queued isn't warmed twice. No-op without an external sub index."""
    items: list[tuple[int, PrefetchItem]] = []
    cue_limit = min(max(0, cues.lookahead), _MAX_WARM_PENDING)
    for text in upcoming_cue_texts(cues.index, cue_limit, text=cues.text, preferred=cues.nav_index):
        toks = tokenizer.tokenize(text)
        for i, t in enumerate(toks):
            if not tokenizer.is_content(t) or t.lemma in seen:
                continue
            seen.add(t.lemma)
            items.append(
                (
                    3,
                    PrefetchItem(gen, t, tokenizer.inflected_in(toks, i), mined=False, full=False),
                )
            )
            if len(items) >= _MAX_WARM_PENDING:
                return items
    return items


def _head_priority(tag: str) -> int | None:
    """Lower sorts first; ``None`` means "not worth a render job at all," which is the
    real RAM/CPU cap on this feature (selectivity, not just the queue's ``maxsize``). n+1/forgotten
    (the word the system already expects to be looked up) first, then rarer frequency bands; already-
    ``known`` words are excluded outright — see :class:`saitenka.app.scoring.Scorer` for the tag
    vocabulary (``'n+1' | 'known' | 'forgotten' | 'freq-N' | 'base'``, ``+'/jlpt-Nx'``).

    Known blind spot: a word absent from the frequency list entirely (arguably the RAREST case) tags
    ``'base'`` — identical to a word that's merely low-signal — so it's excluded here rather than
    risking treating an ordinary word as high-priority. A finer ranking could read
    ``Scorer.freq.rank()`` directly instead of the coloring tag string."""
    base = tag.split("/", 1)[0]
    if base in {"n+1", "forgotten"}:
        return 0
    if base.startswith("freq-"):
        band = int(base.split("-", 1)[1])
        return 1 + (5 - band)  # rarer (higher band) sorts first
    return None  # 'known' or 'base' (no strong signal) — plain decode-warming is enough


def _head_prefetch_candidate(
    ports: PrefetchPorts, head: HeadProbe, gen: int, toks: list[Token], i: int, t: Token, styles
) -> tuple[int, HeadPrefetchItem] | None:
    """Is token `t` (at index `i`) worth a speculative head-render? None if not — either
    :func:`_head_priority` says no, it's already mined, or it's already warm in the panel cache."""
    priority = _head_priority(styles[i].tag)
    if priority is None:
        return None
    if ports.is_mined(t):  # main thread only (jamdict) — see HeadPrefetchItem docstring
        return None
    inflected = ports.tokenizer.inflected_in(toks, i)
    key = head.panel_key(t, inflected, mined=False)
    if key in head.panel_cache:
        return None  # already warm (hovered earlier, or a prior speculative render)
    return priority, HeadPrefetchItem(gen, t, inflected, mined=False)


def _head_candidates_for_text(
    text: str,
    seen: set[str],
    tokenize: Callable[[str], list[Token]],
    is_content: Callable[[Token], bool],
    score: Callable[[list[Token]], object],
    candidate_for: Callable[[list[Token], int, Token, object], tuple[int, HeadPrefetchItem] | None],
    candidate_limit: int,
    probe_limit: int,
) -> tuple[list[tuple[int, HeadPrefetchItem]], int]:
    toks = tokenize(text)
    styles = score(toks)
    candidates: list[tuple[int, HeadPrefetchItem]] = []
    probes = 0
    for i, token in islice(enumerate(toks), probe_limit):
        probes += 1
        if not is_content(token) or token.lemma in seen:
            continue
        seen.add(token.lemma)
        candidate = candidate_for(toks, i, token, styles)
        if candidate is not None:
            candidates.append(candidate)
            if len(candidates) >= candidate_limit:
                break
    return candidates, probes


def _head_prefetch_items(
    ports: PrefetchPorts, head: HeadProbe, gen: int, seen: set[str]
) -> list[tuple[int, HeadPrefetchItem]]:
    """Speculative HEAD render for a SELECTIVE subset of the next
    ``head_prefetch_lookahead`` cues' words: only ones :func:`_head_priority` judges worth the extra
    render cost over plain decode-warming, in priority order, bounded by ``head_queue_max`` (the
    transient-RSS cap — panel_cache's LRU only bounds RETAINED size). Needs a scorer for the n+1/
    known/freq signal; a no-op without one or a subtitle index."""
    if head.scorer is None:
        return []
    candidates: list[tuple[int, HeadPrefetchItem]] = []
    probe_budget = _MAX_HEAD_TOKEN_PROBES
    queue_max = ports.state.head_queue_max
    cue_limit = min(max(0, head.lookahead), queue_max)
    for text in upcoming_cue_texts(
        ports.cues.index, cue_limit, text=ports.cues.text, preferred=ports.cues.nav_index
    ):
        found, probes = _head_candidates_for_text(
            text,
            seen,
            ports.tokenizer.tokenize,
            ports.tokenizer.is_content,
            head.scorer.score_line,
            lambda toks, i, token, styles: _head_prefetch_candidate(
                ports, head, gen, toks, i, token, styles
            ),
            queue_max - len(candidates),
            probe_budget,
        )
        candidates.extend(found)
        probe_budget -= probes
        if len(candidates) >= queue_max or probe_budget <= 0:
            break
    candidates.sort(key=lambda candidate: candidate[0])
    return [(1, item) for _priority, item in candidates[:queue_max]]


def upcoming_cue_texts(index, n: int, *, text: str, preferred: int) -> list[str]:
    """Text of the ``n`` cues after the one on screen, from the external sub index (empty when there's
    no index, the line isn't located, or we're at the tail). Located by the displayed text alone — the
    reliable signal per :meth:`CueIndex.locate` — so it stays off the mpv IPC path."""
    if index is None or not len(index) or n <= 0:
        return []
    current = index.locate(text=text, preferred=preferred)
    if current < 0:
        return []
    return [index.cues[i].text for i in range(current + 1, min(len(index), current + 1 + n))]


def warm_episode_tokens(ports: WarmPorts) -> None:
    """Fire-and-forget: tokenize EVERY cue of the current sub index into ``reader.token_cache`` on a
    background thread, so no cue pays cold tokenization mid-playback (the whole episode is warm ahead
    of playback, not just a short window). Best-effort — a key mismatch (mpv re-wrapping a line) just
    re-tokenizes that cue on demand; a track switch (new index object) supersedes a stale warm. No-op
    without prefetch, a dictionary, or an index; skips an index already warmed."""
    idx = ports.index
    if not ports.enabled or idx is None or not ports.claim(idx):
        return
    if ports.annotate_async:
        ports.start_annotation(idx)
        return
    threading.Thread(
        target=lambda: _warm_episode_loop(idx, ports=ports.loop),
        name="saitenka-episode-warm",
        daemon=True,
    ).start()


@dataclass(frozen=True, slots=True)
class EpisodeWarmPorts:
    """What the background warm keeps asking, never what it saw once.

    Every member is a callable or a live object on purpose: the loop's whole job is to notice that
    the ground moved under it — closing, a track switch, a profile swap — so a snapshot would turn
    the supersede checks into three constants.
    """

    stop: threading.Event
    token_cache: TokenCache
    current_index: Callable[[], CueIndex | None]
    normalise: Callable[[str], str]
    tokenize: Callable[..., object]


@dataclass(frozen=True, slots=True)
class WarmPorts:
    """What starting an episode warm decides on: whether there is anything to warm, which index, and
    which of the two warm paths runs.

    `claim` is one act rather than a read of the last-warmed index and a write of this one: the check
    and the mark have to be indivisible, or two starts on the same index both warm it.
    """

    enabled: bool
    index: CueIndex | None
    claim: Callable[[CueIndex], bool]
    annotate_async: bool
    start_annotation: Callable[[CueIndex], None]
    loop: EpisodeWarmPorts


def _warm_episode_loop(idx: CueIndex, *, ports: EpisodeWarmPorts) -> None:
    warmed = 0
    # The generation this warm belongs to: a live profile swap (#254 D8) clears the cache and bumps it,
    # so a worker mid-tokenize with the OLD tokenizer can't put() a stale-language entry after the swap
    # — the gen is threaded into every put (dropped under the cache lock on mismatch) AND breaks the loop.
    gen = ports.token_cache.generation
    for cue in list(idx.cues):
        if (
            ports.stop.is_set()
            or ports.current_index() is not idx
            or ports.token_cache.generation != gen
        ):
            return  # closing, a track switch replaced the index, or a profile swap → drop the stale warm
        try:
            ports.tokenize(ports.normalise(cue.text), generation=gen)
            warmed += 1
        except Exception:
            log.debug("episode token warm failed for a cue", exc_info=True)  # never kill the warm
    log.info("episode token warm: %d/%d cues into the token cache", warmed, len(idx.cues))


# The tooltip's FIXED reference resolution. Tooltip geometry (width, viewport-height cap) is computed
# against this, NOT the live OSD, so the persistent render cache is resolution-independent: a 1080p
# prewarm hits at any playback resolution, and osd_h/REF_H scales the composited bitmap to the actual
# display at upload time (``TipScale.display``). The tooltip is a VIDEO-OVERLAY element that tracks
# the vertical viewport, NOT the app-chrome ui_scale (its fonts are theme scale 1.0). Matches the
# interaction goldens pinned at 1080p (scale 1.0 = the reference, unscaled).
REF_W, REF_H = 1920, 1080


def cap_for(frac: float) -> int:
    """A viewport-height cap: ``frac`` of the REFERENCE height, clear of the header/footer margin.
    REFERENCE-based (not the live OSD, not ui_scale) so the tooltip render cache is resolution-independent;
    the display scale (osd_h/REF_H) then maps ``frac`` onto the ACTUAL viewport at upload."""
    margin = max(16, round(REF_H * 0.05))
    return min(round(REF_H * frac), REF_H - 2 * margin)


def tip_cap(max_frac: float) -> int:
    """Max BASE tooltip viewport height (≤ ``tip_max_frac`` of the video). The nested popup has its
    own, deliberately roomier cap (``nested_max_frac``) so shrinking the base doesn't cramp it."""
    return cap_for(max_frac)


# The tooltip's reference width. ``REF_W * 0.36`` clamped to 640 — displayed width ≈ 0.59 × osd_h,
# derived from the VERTICAL viewport, so it stays narrow on an ultra-wide (an osd_WIDTH formula would
# not). Theme scale 1.0, like the fonts ``panel_rows`` draws it with.
TIP_W = int(min(REF_W * 0.36, 640))

# mpv's osd dimensions wobble a few px, which jitters osd_h/REF_H in the third decimal. Snapping the
# raster scale to a bucket means that wobble reuses cached native bands instead of re-rastering.
SCALE_BUCKET = 0.05


@dataclass(frozen=True, slots=True)
class TipScale:
    """The tooltip's reference geometry and the one factor that maps it onto the live display.

    ``width``, ``cap`` and ``ref_h`` are reference-space and do not move with the resolution — that is
    what makes the render cache resolution-independent, so a 1080p prewarm hits at any playback size.
    ``display`` and ``raster`` are the only two that do.
    """

    #: REFERENCE→display factor: ``osd_h / REF_H`` (1.0 at 1080p, 2.0 at 4K), or a fixed ``tip_scale``
    #: preference. Applied to the composited BGRA at upload and inverted in the hit-test.
    display: float
    #: ``display`` snapped to ``SCALE_BUCKET`` — the scale the crisp path rasters, composites AND
    #: inverts the hit-test at. All three must agree, so there is one bucketed value, not three.
    raster: float
    #: Max BASE viewport height, in reference pixels.
    cap: int
    width: int = TIP_W
    ref_h: int = REF_H


def tip_scale(osd_h: int, *, override: float, max_frac: float) -> TipScale:
    """The whole tooltip scale boundary from the three inputs it actually depends on."""
    display = override if override > 0 else osd_h / REF_H
    return TipScale(display, round(display / SCALE_BUCKET) * SCALE_BUCKET, tip_cap(max_frac))

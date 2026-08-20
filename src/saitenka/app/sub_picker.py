"""Window 1: the in-mpv subtitle-source picker, across every enabled provider.

The default fetch auto-picks a candidate by resolution + size, which can't tell a WebRip source from
a broadcast rip when both carry a plain ``1080p`` tag — and their cue timing differs by tens of
seconds (found live: a broadcast rip put ep02 seconds late on a CR WebRip). This panel exposes the raw
candidate list (best-match first, tagged by provider) and lets the user choose the source whose timing
matches this encode. Download is deliberately un-resynced: the point is to pick a natively co-timed
source; ``Ctrl+Shift+T`` stays the per-file fallback.

Provider-agnostic by construction: the reader carries a *lister* thunk (built from ``enabled_providers``
in the CLI, exactly like the retry factory), so this module never imports jimaku/tsukihime — it renders
:class:`~saitenka.app.subselect.SubtitleCandidate` rows and runs the chosen one's ``download`` thunk
through the normal subtitle-fetch pipeline (:func:`saitenka.app.subtitle_modes.start_fetch` →
:func:`~saitenka.app.subtitle_modes.apply_fetch_result`), so track add / select / re-index come free
and no mpv IPC ever runs off the reader thread. Modelled on :mod:`saitenka.app.sidebar` (click / scroll
/ hover-suppression surface) and :mod:`saitenka.app.help_overlay` (open / close lifecycle).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka.app.overlay_ids import OverlayId
from saitenka.app.subtitles import SidebarRow, render_picker
from saitenka.model import claims_pointer, in_rect
from saitenka.runtime import EffectFinished, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy, JobSubmitter, configure_lane

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from saitenka.app.subselect import SubtitleCandidate
    from saitenka.app.subtitle_modes import FetchSubmitter
    from saitenka.app.surfaces import ClickTarget, HoverSuppression, WheelStep

log = logging.getLogger(__name__)

PICKER_ID = OverlayId.PICKER
ROWS_PER_WHEEL_STEP = 3


@dataclass
class PickerState:
    """Window 1's runtime state. Transient UI; rebuilt on every open."""

    open: bool = False
    loading: bool = False
    error: str | None = None
    candidates: tuple[SubtitleCandidate, ...] = ()
    warnings: tuple[str, ...] = ()
    scroll: int = 0
    rect: tuple[int, int, int, int] | None = None
    hits: tuple = ()
    generation: int = 0


@dataclass(frozen=True, slots=True)
class ListingRequest:
    lister: Callable[[str], tuple]
    video: str


@dataclass(frozen=True, slots=True)
class ListingResult:
    candidates: tuple[SubtitleCandidate, ...]
    warnings: tuple[str, ...]
    error: str | None = None


def run_listing(request: object, cancelled: threading.Event) -> object:
    if not isinstance(request, ListingRequest):
        raise TypeError("invalid subtitle listing request")
    if cancelled.is_set():
        return None
    try:
        candidates, warnings = request.lister(request.video)
        return ListingResult(tuple(candidates), tuple(warnings))
    except Exception as exc:  # provider failures are soft and shown in the picker
        log.warning("subtitle candidate listing failed", exc_info=True)
        return ListingResult((), (), f"subtitle search failed: {exc}")


def configure_runtime_job(ipc) -> JobSubmitter | None:
    return configure_lane(
        ipc,
        "subtitle-picker",
        JobLanePolicy(capacity=2, workers=2),
        run_listing,
    )


def _human_size(size: int) -> str:
    if size <= 0:
        return "—"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} K"
    return f"{size / (1024 * 1024):.1f} M"


@dataclass(frozen=True, slots=True)
class ListingPorts:
    """What one listing needs to run and to publish itself back.

    `current_episode` stays a callable — it is the staleness check, and a snapshot of the episode
    would compare the completion against the episode it was issued for rather than the one playing.
    """

    #: `None` when no provider is configured — the picker refuses to open rather than showing empty.
    lister: Callable[[str], tuple] | None
    state: PickerState
    redraw: Callable[[], None]
    submit: JobSubmitter | None
    stop: threading.Event
    current_episode: Callable[[], object]
    toast: Callable[..., None]


def _start_listing(video: str, ports: ListingPorts) -> None:
    episode = ports.current_episode()
    generation = ports.state.generation
    lister, submitter = ports.lister, ports.submit
    # `lister is None` cannot be reached through `open_picker`, which refuses with its own message —
    # it is here so the two ways a listing cannot run have one answer rather than two.
    if lister is None or submitter is None:
        apply_listing(
            ports.state,
            ports.redraw,
            generation,
            ListingResult((), (), "subtitle search unavailable"),
        )
        return

    def finished(completion: EffectFinished) -> None:
        finished_listing = finish_listing(completion)
        if (
            finished_listing is not None
            and episode is ports.current_episode()
            and not ports.stop.is_set()
        ):
            finished_generation, result = finished_listing
            apply_listing(ports.state, ports.redraw, finished_generation, result)

    submitter(
        owner=Owner.SUBTITLE,
        identity=generation,
        lane="subtitle-picker",
        request=ListingRequest(lister, video),
        on_finished=finished,
    )


def open_picker(ports: ListingPorts, video: object, *, retire_hover: Callable[[], None]) -> None:
    """Open the picker and start one listing. `ports.lister` being `None` is the "no provider"
    case, and `video` being empty is "nothing loaded" — both are refusals, not errors."""
    if ports.lister is None:
        ports.toast("Subtitle picker needs a provider — run with --jimaku or --tsukihime", "warn")
        return
    if not video:
        ports.toast("No media loaded", "warn")
        return
    state = ports.state
    state.open = True
    state.loading = True
    state.error = None
    state.candidates = ()
    state.warnings = ()
    state.scroll = 0
    state.generation += 1
    retire_hover()
    ports.redraw()
    _start_listing(str(video), ports)


def close_picker(state, lifecycle_surfaces) -> None:
    if not retire(state):
        return
    lifecycle_surfaces.remove(PICKER_ID)


def retire(state) -> bool:
    """Close the picker and bump its generation, reporting whether it was open.

    The bump is what makes an in-flight listing stale: a reopen starts a new generation, so a result
    for the closed one is dropped by `apply_listing` rather than repopulating a picker the user has
    since closed and reopened.
    """
    if not state.open:
        return False
    state.open = False
    state.generation += 1
    state.rect = None
    state.hits = ()
    return True


def adopt_listing(state, generation: int, result: ListingResult) -> bool:
    """Install ``result`` if it still belongs to the open picker; report whether it did.

    Returns rather than redrawing so the staleness rule is separable from the paint: a listing for a
    closed or superseded generation must leave the state untouched, not merely skip a redraw.
    """
    if not state.open or generation != state.generation:
        return False
    state.loading = False
    state.error = result.error
    state.candidates = result.candidates
    state.warnings = result.warnings
    return True


def apply_listing(state, redraw, generation: int, result: ListingResult) -> None:
    if adopt_listing(state, generation, result):
        redraw()


def finish_listing(completion: EffectFinished) -> tuple[int, ListingResult] | None:
    result = completion.result if completion.outcome is EffectOutcome.SUCCEEDED else None
    if not isinstance(result, ListingResult):
        result = ListingResult((), (), "subtitle search unavailable")
    if isinstance(completion.identity, int):
        return completion.identity, result
    return None


def _rows(state: PickerState) -> list[SidebarRow]:
    rows: list[SidebarRow] = []
    for index, candidate in enumerate(state.candidates):
        # provider · format · match — same dot-tag idiom as the provider pill; `match` = the release
        # RESOLUTION matches this encode (a picker-fetch is never pre-downloaded), `srt`/`ass` the format.
        ext = Path(candidate.name).suffix.lstrip(".").lower()
        tags = [
            candidate.provider,
            *([ext] if ext else []),
            *(["match"] if candidate.match else []),
        ]
        rows.append(
            SidebarRow(
                value=index,
                timestamp=_human_size(candidate.size),
                text=candidate.name,
                status=" · ".join(tags),
                click_kind="picker-download",
            )
        )
    return rows


def _message(state: PickerState) -> str | None:
    if state.loading:
        return "Searching subtitle providers…"
    if state.error:
        return state.error
    if not state.candidates:
        return "No subtitle candidates found"
    return None


def _footer(state: PickerState, close_key: str, total: int, shown: int) -> str:
    if state.warnings:
        return f"{'  ·  '.join(state.warnings)}  ·  {close_key} closes"
    if not total:
        return f"{close_key} closes"
    return (
        f"{state.scroll + 1}–{state.scroll + shown} / {total}  ·  click to download  ·  "
        f"{close_key} closes"
    )


def picker_panel(state: PickerState, *, osd: tuple[int, int], scale: float, close_key: str):
    """Render the picker for a screen, returning ``(rendered, x, y, width, height)``.

    Pure apart from reading ``state``: every dimension is bounded by the OSD, which is exactly the
    arithmetic that stops tracking a resize unnoticed. The caller stores the geometry and presents.
    """
    width = max(round(480 * scale), min(round(960 * scale), round(osd[0] * 0.62)))
    width = min(width, osd[0] - round(36 * scale))
    height = max(round(220 * scale), round(osd[1] * 0.7))
    rows = _rows(state)
    visible = rows[state.scroll :]  # render_picker clips to its own row capacity
    rendered = render_picker(
        visible,
        width=width,
        height=height,
        message=_message(state),
        footer=_footer(state, close_key, len(rows), len(visible)),
        scale=scale,
    )
    return rendered, (osd[0] - width) // 2, (osd[1] - height) // 2, width, height


def contains(state: PickerState, x: float, y: float) -> bool:
    """Whether ``(x, y)`` is inside the shown picker."""
    if not (state.open and state.rect):
        return False
    return in_rect(state.rect, x, y)


def suppress_hover(suppression: HoverSuppression) -> bool:
    state = suppression.interaction.sub_picker
    if not state.open:
        return False
    if not claims_pointer(state.rect, suppression.pointer, open_=state.open):
        return False
    suppression.release_hover()
    return True


def scroll(wheel: WheelStep, steps: int) -> bool:
    state = wheel.interaction.sub_picker
    if not state.open:
        return False
    if not claims_pointer(state.rect, wheel.pointer, open_=state.open):
        return False
    state.scroll = clamp_scroll(state.scroll, steps, len(state.candidates))
    wheel.redraw_picker()
    return True


def clamp_scroll(scroll: int, steps: int, count: int) -> int:
    """Where a wheel notch leaves the picker. Clamped at both ends rather than wrapped: a list that
    jumped from the last row back to the first on one more notch reads as a lost scroll position."""
    return max(0, min(max(0, count - 1), scroll + steps * ROWS_PER_WHEEL_STEP))


@dataclass(frozen=True, slots=True)
class DownloadPorts:
    """What fetching a chosen subtitle needs from the session: how to say so, how to submit the
    fetch, how to read a property, and where the panel lives."""

    toast: Callable[..., object]
    submit_fetch: FetchSubmitter
    get_property: Callable[[str], int | str | None]
    surfaces: object


def _download(state: PickerState, index: int, ports: DownloadPorts) -> None:
    """Fetch the chosen candidate and close the panel.

    Everything this needs is already a value or a port — `start_fetch` and `close_picker` both take
    facts, so the host was only being carried through to reach them.
    """
    if not (0 <= index < len(state.candidates)):
        return
    candidate = state.candidates[index]
    from saitenka.app.subtitle_modes import start_fetch

    ports.toast(f"Downloading {candidate.name}…")
    # force_select: the user explicitly chose this source in the picker, so select it NOW even if the
    # current track is English (the keep-current background contract is for unattended fetches, not this).
    start_fetch(
        ports.submit_fetch,
        ports.get_property,
        candidate.download,
        name="sub-picker-download",
        force_select=True,
    )
    # panel closes; the swap lands from the broker completion when the file arrives
    close_picker(state, ports.surfaces)


def on_click(target: ClickTarget, x: float, y: float) -> bool:
    state = target.interaction.sub_picker
    if not contains(state, x, y) or state.rect is None:
        return False
    local_x, local_y = x - state.rect[0], y - state.rect[1]
    hit = next((box for box in state.hits if box.contains(local_x, local_y)), None)
    if hit is not None and hit.kind == "picker-download":
        _download(state, hit.value, target.download)
    return True

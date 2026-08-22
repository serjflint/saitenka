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
from saitenka.runtime import EffectFinished, EffectOutcome, Owner, events
from saitenka.runtime.jobs import JobLanePolicy, JobSubmitter, configure_lane
from saitenka.runtime.picker import ListingAdopted, PickerRetired, PickerState

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from saitenka.app.subselect import SubtitleCandidate
    from saitenka.app.subtitle_modes import FetchSubmitter
    from saitenka.app.surfaces import ClickTarget, HoverSuppression, WheelStep
    from saitenka.runtime.interaction_slice import PickerStore

log = logging.getLogger(__name__)

PICKER_ID = OverlayId.PICKER


@dataclass
class PickerPanel:
    """Where the last paint put the picker, and the boxes that paint hit-tests to.

    Kept beside the slice rather than in it. These describe one paint on one screen — a resize or a
    scroll replaces both — and a per-paint observation folded into a session-lived slot is the
    lifetime mistake the geometry split already made once.
    """

    rect: tuple[int, int, int, int] | None = None
    hits: tuple = ()

    def clear(self) -> None:
        self.rect = None
        self.hits = ()


@dataclass(frozen=True, slots=True)
class ListingRequest:
    lister: Callable[[str], tuple]
    video: str


@dataclass(frozen=True, slots=True)
class ListingResult:
    candidates: tuple[SubtitleCandidate, ...]
    warnings: tuple[str, ...]
    error: str | None = None


#: What a picker with nothing adopted shows. One value rather than a `None` arm in every reader:
#: "no listing yet" and "a listing that came back empty" render the same, and the difference the UI
#: does care about — a search still running — is `PickerState.loading`.
NO_LISTING = ListingResult((), ())


def listing_of(state: PickerState) -> ListingResult:
    """The picker's adopted listing. The slice carries it opaquely, so this is where it narrows."""
    return state.listing if isinstance(state.listing, ListingResult) else NO_LISTING


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
    store: PickerStore
    redraw: Callable[[], None]
    submit: JobSubmitter | None
    stop: threading.Event
    current_episode: Callable[[], object]
    toast: Callable[..., None]


def _start_listing(video: str, ports: ListingPorts) -> None:
    episode = ports.current_episode()
    generation = ports.store.current.generation
    lister, submitter = ports.lister, ports.submit
    # `lister is None` cannot be reached through `open_picker`, which refuses with its own message —
    # it is here so the two ways a listing cannot run have one answer rather than two.
    if lister is None or submitter is None:
        apply_listing(
            ports.store,
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
            apply_listing(ports.store, ports.redraw, finished_generation, result)

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
    ports.store.dispatch(events.PickerOpened())
    retire_hover()
    ports.redraw()
    _start_listing(str(video), ports)


def close_picker(store: PickerStore, panel: PickerPanel, lifecycle_surfaces) -> None:
    if not retire(store, panel):
        return
    lifecycle_surfaces.remove(PICKER_ID)


def retire(store: PickerStore, panel: PickerPanel) -> bool:
    """Close the picker, reporting whether it was up.

    The generation bump the close carries is what makes an in-flight listing stale: a reopen starts
    a new one, so a result for the closed picker is dropped rather than repopulating a list the user
    has since closed and reopened. The drawn geometry is cleared here because it is this side's — a
    rectangle for a picker that is no longer up would still answer a hit test.
    """
    retired = any(
        isinstance(decision, PickerRetired) for decision in store.dispatch(events.PickerClosed())
    )
    if retired:
        panel.clear()
    return retired


def apply_listing(store: PickerStore, redraw, generation: int, result: ListingResult) -> None:
    """Offer a result to the picker; repaint only if it was still the one waiting for it."""
    if any(
        isinstance(decision, ListingAdopted)
        for decision in store.dispatch(events.PickerListed(generation, result))
    ):
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
    for index, candidate in enumerate(listing_of(state).candidates):
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
    listing = listing_of(state)
    if listing.error:
        return listing.error
    if not listing.candidates:
        return "No subtitle candidates found"
    return None


def _footer(state: PickerState, close_key: str, total: int, shown: int) -> str:
    warnings = listing_of(state).warnings
    if warnings:
        return f"{'  ·  '.join(warnings)}  ·  {close_key} closes"
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


def contains(state: PickerState, panel: PickerPanel, x: float, y: float) -> bool:
    """Whether ``(x, y)`` is inside the shown picker."""
    if not (state.open and panel.rect):
        return False
    return in_rect(panel.rect, x, y)


def suppress_hover(suppression: HoverSuppression) -> bool:
    state = suppression.interaction.sub_picker
    if not state.open:
        return False
    if not claims_pointer(
        suppression.interaction.picker_panel.rect, suppression.pointer, open_=state.open
    ):
        return False
    suppression.release_hover()
    return True


def scroll(wheel: WheelStep, steps: int) -> bool:
    state = wheel.interaction.sub_picker
    if not state.open:
        return False
    if not claims_pointer(wheel.interaction.picker_panel.rect, wheel.pointer, open_=state.open):
        return False
    wheel.interaction.picker_store.dispatch(
        events.PickerScrolled(steps, len(listing_of(state).candidates))
    )
    wheel.redraw_picker()
    return True


@dataclass(frozen=True, slots=True)
class DownloadPorts:
    """What fetching a chosen subtitle needs from the session: how to say so, how to submit the
    fetch, how to read a property, and where the panel lives."""

    toast: Callable[..., object]
    submit_fetch: FetchSubmitter
    get_property: Callable[[str], object]
    surfaces: object


def _download(target: ClickTarget, index: int, ports: DownloadPorts) -> None:
    """Fetch the chosen candidate and close the panel.

    Everything this needs is already a value or a port — `start_fetch` and `close_picker` both take
    facts, so the host was only being carried through to reach them.
    """
    candidates = listing_of(target.interaction.sub_picker).candidates
    if not (0 <= index < len(candidates)):
        return
    candidate = candidates[index]
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
    close_picker(target.interaction.picker_store, target.interaction.picker_panel, ports.surfaces)


def on_click(target: ClickTarget, x: float, y: float) -> bool:
    state = target.interaction.sub_picker
    panel = target.interaction.picker_panel
    if not contains(state, panel, x, y) or panel.rect is None:
        return False
    local_x, local_y = x - panel.rect[0], y - panel.rect[1]
    hit = next((box for box in panel.hits if box.contains(local_x, local_y)), None)
    if hit is not None and hit.kind == "picker-download":
        _download(target, hit.value, target.download)
    return True

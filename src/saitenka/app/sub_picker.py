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
from typing import TYPE_CHECKING, Protocol

from saitenka.app.overlay_ids import OverlayId
from saitenka.app.subtitles import SidebarRow, render_picker
from saitenka.runtime import EffectFinished, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from saitenka.app.controller import Reader
    from saitenka.app.subselect import SubtitleCandidate

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


class JobSubmitter(Protocol):
    def __call__(
        self,
        *,
        owner: Owner,
        identity: object,
        lane: str,
        request: object,
        on_finished: Callable[[EffectFinished], None],
    ) -> bool: ...


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
    register = getattr(ipc, "register_runtime_job_lane", None)
    if register is None or not register(
        "subtitle-picker",
        JobLanePolicy(capacity=2, workers=2),
        run_listing,
    ):
        return None
    return ipc.submit_runtime_job


def configure(reader: Reader, lister) -> None:
    """Enable the picker for this session with a provider-agnostic candidate lister. Called wherever the
    subtitle-retry factory is wired, so the key binding is a no-op (with a toast) unless at least one
    provider is enabled."""
    reader._sub_picker_lister = lister


def _human_size(size: int) -> str:
    if size <= 0:
        return "—"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} K"
    return f"{size / (1024 * 1024):.1f} M"


def _start_listing(reader: Reader, video: str) -> None:
    lister = reader._sub_picker_lister
    assert lister is not None  # open_picker guards this
    episode = reader.episode
    generation = reader.sub_picker.generation
    submitter = reader._sub_picker_submit
    if submitter is None:
        apply_listing(
            reader,
            generation,
            ListingResult((), (), "subtitle search unavailable"),
        )
        return

    def finished(completion: EffectFinished) -> None:
        finished_listing = finish_listing(completion)
        if finished_listing is not None and episode is reader.episode and not reader._stop.is_set():
            finished_generation, result = finished_listing
            apply_listing(reader, finished_generation, result)

    submitter(
        owner=Owner.SUBTITLE,
        identity=generation,
        lane="subtitle-picker",
        request=ListingRequest(lister, video),
        on_finished=finished,
    )


def open_picker(reader: Reader) -> None:
    if reader._sub_picker_lister is None:
        reader._toast("Subtitle picker needs a provider — run with --jimaku or --tsukihime", "warn")
        return
    video = reader._get("path")
    if not video:
        reader._toast("No media loaded", "warn")
        return
    state = reader.sub_picker
    state.open = True
    state.loading = True
    state.error = None
    state.candidates = ()
    state.warnings = ()
    state.scroll = 0
    state.generation += 1
    reader.set_hover(-1)
    reader.redraw_sub_picker()
    _start_listing(reader, str(video))


def close_picker(reader: Reader) -> None:
    if not retire(reader.sub_picker):
        return
    reader.lifecycle_surfaces.remove(PICKER_ID)


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


def apply_listing(reader: Reader, generation: int, result: ListingResult) -> None:
    if adopt_listing(reader.sub_picker, generation, result):
        reader.redraw_sub_picker()


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
    left, top, width, height = state.rect
    return left <= x < left + width and top <= y < top + height


def suppress_hover(reader: Reader) -> bool:
    state = reader.sub_picker
    if not state.open:
        return False
    mp = reader._prop("mouse-pos") or {}
    if not contains(state, mp.get("x", -1), mp.get("y", -1)):
        return False
    reader.set_hover(-1)
    return True


def scroll(reader: Reader, steps: int) -> bool:
    state = reader.sub_picker
    if not state.open:
        return False
    mp = reader._prop("mouse-pos") or {}
    if not contains(state, mp.get("x", -1), mp.get("y", -1)):
        return False
    maximum = max(0, len(state.candidates) - 1)
    state.scroll = max(0, min(maximum, state.scroll + steps * ROWS_PER_WHEEL_STEP))
    reader.redraw_sub_picker()
    return True


def _download(reader: Reader, index: int) -> None:
    state = reader.sub_picker
    if not (0 <= index < len(state.candidates)):
        return
    candidate = state.candidates[index]
    from saitenka.app.subtitle_modes import start_fetch

    reader._toast(f"Downloading {candidate.name}…")
    # force_select: the user explicitly chose this source in the picker, so select it NOW even if the
    # current track is English (the keep-current background contract is for unattended fetches, not this).
    start_fetch(
        reader,
        candidate.download,
        name="sub-picker-download",
        force_select=True,
    )
    close_picker(
        reader
    )  # panel closes; the swap lands from the broker completion when the file arrives


def on_click(reader: Reader, x: float, y: float) -> bool:
    state = reader.sub_picker
    if not contains(state, x, y) or state.rect is None:
        return False
    local_x, local_y = x - state.rect[0], y - state.rect[1]
    hit = next((box for box in state.hits if box.contains(local_x, local_y)), None)
    if hit is not None and hit.kind == "picker-download":
        _download(reader, hit.value)
    return True

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
:func:`~saitenka.app.subtitle_modes.apply_fetch_results`), so track add / select / re-index come free
and no mpv IPC ever runs off the reader thread. Modelled on :mod:`saitenka.app.sidebar` (click / scroll
/ hover-suppression surface) and :mod:`saitenka.app.help_overlay` (open / close lifecycle).
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka.app.overlay_ids import OverlayId
from saitenka.app.subtitles import SidebarRow, render_picker

if TYPE_CHECKING:
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
    results: queue.SimpleQueue = field(default_factory=queue.SimpleQueue)


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

    def work() -> None:
        try:
            candidates, warnings = lister(video)
            reader.sub_picker.results.put((candidates, warnings, None))
        except Exception as exc:  # provider failures are soft — surfaced in the panel, not raised
            log.warning("subtitle candidate listing failed", exc_info=True)
            reader.sub_picker.results.put((None, None, f"subtitle search failed: {exc}"))

    thread = threading.Thread(target=work, name="saitenka-sub-picker", daemon=True)
    reader._subtitle_fetch_threads.append(thread)
    thread.start()


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
    reader.set_hover(-1)
    redraw(reader)
    _start_listing(reader, str(video))


def close_picker(reader: Reader) -> None:
    if not reader.sub_picker.open:
        return
    reader.sub_picker.open = False
    reader.ov.hide(PICKER_ID)
    reader.sub_picker.rect = None
    reader.sub_picker.hits = ()


def toggle(reader: Reader) -> None:
    close_picker(reader) if reader.sub_picker.open else open_picker(reader)


def update(reader: Reader) -> None:
    """Drain the off-thread listing result and repaint. Called once per poll tick (reader thread)."""
    state = reader.sub_picker
    changed = False
    while True:
        try:
            candidates, warnings, error = state.results.get_nowait()
        except queue.Empty:
            break
        state.loading = False
        state.error = error
        state.candidates = tuple(candidates or ())
        state.warnings = tuple(warnings or ())
        changed = True
    if changed and state.open:
        redraw(reader)


def _rows(reader: Reader) -> list[SidebarRow]:
    rows: list[SidebarRow] = []
    for index, candidate in enumerate(reader.sub_picker.candidates):
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


def _footer(reader: Reader, total: int, shown: int) -> str:
    if reader.sub_picker.warnings:
        return f"{'  ·  '.join(reader.sub_picker.warnings)}  ·  {reader.sub_picker_key} closes"
    if not total:
        return f"{reader.sub_picker_key} closes"
    scroll = reader.sub_picker.scroll
    return (
        f"{scroll + 1}–{scroll + shown} / {total}  ·  click to download  ·  "
        f"{reader.sub_picker_key} closes"
    )


def redraw(reader: Reader) -> None:
    state = reader.sub_picker
    if not state.open:
        return
    scale = reader.chrome_scale
    width = max(round(480 * scale), min(round(960 * scale), round(reader.osd[0] * 0.62)))
    width = min(width, reader.osd[0] - round(36 * scale))
    height = max(round(220 * scale), round(reader.osd[1] * 0.7))
    x = (reader.osd[0] - width) // 2
    y = (reader.osd[1] - height) // 2
    rows = _rows(reader)
    total = len(rows)
    visible = rows[state.scroll :]  # render_picker clips to its own row capacity
    rendered = render_picker(
        visible,
        width=width,
        height=height,
        message=_message(state),
        footer=_footer(reader, total, len(visible)),
        scale=scale,
    )
    state.rect = (x, y, width, height)
    state.hits = rendered.hitboxes
    reader.ov.show(rendered.image, x, y, oid=PICKER_ID)


def contains(reader: Reader, x: float, y: float) -> bool:
    state = reader.sub_picker
    return bool(state.open and state.rect and reader._in_rect(state.rect, x, y))


def suppress_hover(reader: Reader) -> bool:
    if not reader.sub_picker.open:
        return False
    mp = reader._prop("mouse-pos") or {}
    if not contains(reader, mp.get("x", -1), mp.get("y", -1)):
        return False
    reader.set_hover(-1)
    return True


def scroll(reader: Reader, steps: int) -> bool:
    state = reader.sub_picker
    if not state.open:
        return False
    mp = reader._prop("mouse-pos") or {}
    if not contains(reader, mp.get("x", -1), mp.get("y", -1)):
        return False
    maximum = max(0, len(state.candidates) - 1)
    state.scroll = max(0, min(maximum, state.scroll + steps * ROWS_PER_WHEEL_STEP))
    redraw(reader)
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
    )  # panel closes; the swap lands via apply_fetch_results when the file arrives


def on_click(reader: Reader, x: float, y: float) -> bool:
    state = reader.sub_picker
    if not contains(reader, x, y) or state.rect is None:
        return False
    local_x, local_y = x - state.rect[0], y - state.rect[1]
    hit = next((box for box in state.hits if box.contains(local_x, local_y)), None)
    if hit is not None and hit.kind == "picker-download":
        _download(reader, hit.value)
    return True

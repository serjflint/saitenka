"""Windowed in-mpv subtitle sidebar and deferred-capture backlog surface."""

from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from typing import TYPE_CHECKING

from overlay.app.backlog import BacklogEntry, BacklogStore, MediaRecord, db_path
from overlay.app.overlay_ids import OverlayId
from overlay.app.subtitles import SidebarAction, SidebarRow, render_sidebar
from overlay.app.tokenize import tokenize

if TYPE_CHECKING:
    from overlay.app.controller import Reader
    from overlay.app.sub_index import SubCue

SIDEBAR_ID = OverlayId.SIDEBAR
MANUAL_SCROLL_HOLD = 1.5
ROWS_PER_WHEEL_STEP = 3
PLAIN = (236, 241, 247, 255)


def _format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def _capacity(reader: Reader) -> int:
    height = max(180, round(reader.osd[1] * 0.84))
    return max(1, (height - 52 - 28) // 54)


def _active_index(reader: Reader) -> int:
    index = reader._sub_index
    if index is None:
        return -1
    return index.locate(
        text=reader.sub_text,
        sub_start=reader._get("sub-start"),
        time_pos=reader._get("time-pos"),
        preferred=reader._nav_idx,
    )


def _ensure_store(reader: Reader) -> BacklogStore:
    if reader._backlog_store is None:
        reader._backlog_store = BacklogStore()
    return reader._backlog_store


def _cue_parts(reader: Reader, cue_index: int, cue: SubCue) -> tuple[tuple[str, tuple], ...]:
    if reader.subtitle_language == "en" or reader.scorer is None:
        return ((cue.text.replace("\n", " "), PLAIN),)
    key = (reader.subtitle_language, cue_index, cue.text, id(reader.scorer))
    cached = reader._sidebar_style_cache.get(key)
    if cached is not None:
        return cached
    tokens = tokenize(cue.text.replace("\\N", "\n").replace("\r", ""))
    styles = reader.scorer.score_line(tokens)
    parts = tuple((token.surface, style.color) for token, style in zip(tokens, styles, strict=True))
    reader._sidebar_style_cache[key] = parts
    return parts


def _cue_statuses(reader: Reader) -> dict[int, str]:
    index = reader._sub_index
    video = reader._get("path")
    if index is None or not video:
        return {}
    entries = _ensure_store(reader).entries_for_path(video)
    cue_by_span = {
        (round(cue.start, 1), round(cue.end, 1)): cue_index
        for cue_index, cue in enumerate(index.cues)
    }
    statuses: dict[int, str] = {}
    for entry in entries:
        cue_index = cue_by_span.get((round(entry.cue_start, 1), round(entry.cue_end, 1)))
        if cue_index is not None:
            statuses[cue_index] = entry.status
    return statuses


def _track_rows(
    reader: Reader, first: int, capacity: int, active: int
) -> tuple[list[SidebarRow], int]:
    index = reader._sub_index
    if index is None:
        return [], 0
    statuses = _cue_statuses(reader)
    rows: list[SidebarRow] = []
    for cue_index in range(first, min(len(index), first + capacity)):
        cue = index.cues[cue_index]
        actions: tuple[SidebarAction, ...] = ()
        if cue_index == active:
            actions = (SidebarAction("B", "bookmark", cue_index),)
            if reader.anki and reader.mine_cfg:
                actions += (SidebarAction("+", "mine", cue_index),)
        rows.append(
            SidebarRow(
                value=cue_index,
                timestamp=_format_time(cue.start),
                text=cue.text,
                parts=_cue_parts(reader, cue_index, cue),
                status=statuses.get(cue_index),
                active=cue_index == active,
                click_kind="seek",
                actions=actions,
            )
        )
    return rows, len(index)


def _status_groups(store: BacklogStore) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = defaultdict(list)
    for item in store.summary():
        media_id = item["id"]
        if isinstance(media_id, int) and item["status"]:
            grouped[media_id].append(f"{item['status']} {item['count']}")
    return grouped


def _candidate_rows(store: BacklogStore, video: str | None) -> list[SidebarRow]:
    if not video:
        return []
    match = store.match(video)
    if match.confirmed or not match.choices:
        return []
    label = "Possible match" if match.kind == "candidate" else "Choose same-name media"
    return [
        SidebarRow(
            value=choice.id,
            timestamp="link",
            text=f"{label}: {choice.original_basename}",
            parts=((f"{label}: {choice.original_basename}", PLAIN),),
            actions=(SidebarAction("✓", "relink", choice.id),),
        )
        for choice in match.choices
    ]


def _media_row(record: MediaRecord, statuses: list[str]) -> SidebarRow:
    status = " · ".join(statuses) or "empty"
    count = sum(int(item.split()[-1]) for item in statuses)
    return SidebarRow(
        value=record.id,
        timestamp=str(count),
        text=record.original_basename,
        parts=((record.original_basename, PLAIN),),
        status=status,
    )


def _entry_text(reader: Reader, entry: BacklogEntry) -> str:
    if reader.subtitle_language == "en":
        return entry.en_text or entry.jp_text
    return entry.jp_text or entry.en_text


def _entry_row(reader: Reader, entry: BacklogEntry, active: int) -> SidebarRow:
    index = reader._sub_index
    active_cue = index.cues[active] if index is not None and active >= 0 else None
    is_active = bool(
        active_cue
        and abs(active_cue.start - entry.cue_start) < 0.05
        and abs(active_cue.end - entry.cue_end) < 0.05
    )
    text = _entry_text(reader, entry)
    return SidebarRow(
        value=entry.id,
        timestamp=_format_time(entry.cue_start),
        text=text,
        parts=((text.replace("\n", " "), PLAIN),),
        status=entry.status,
        active=is_active,
        click_kind="backlog-seek",
    )


def _matched_entry_rows(reader: Reader, store: BacklogStore, video: str | None) -> list[SidebarRow]:
    if not video:
        return []
    active = _active_index(reader)
    return [_entry_row(reader, entry, active) for entry in store.entries_for_path(video)]


def _summary_rows(reader: Reader) -> list[SidebarRow]:
    store = _ensure_store(reader)
    grouped = _status_groups(store)
    video = reader._get("path")
    candidates = _candidate_rows(store, video)
    matched = _matched_entry_rows(reader, store, video)
    return (
        candidates
        + matched
        + [_media_row(record, grouped.get(record.id, [])) for record in store.all_media()]
    )


def redraw(reader: Reader) -> None:
    if not reader._sidebar_open:
        return
    width = min(620, max(360, round(reader.osd[0] * 0.4)))
    height = max(180, round(reader.osd[1] * 0.84))
    x, y = reader.osd[0] - width - 18, round(reader.osd[1] * 0.08)
    capacity = _capacity(reader)
    unavailable = None
    try:
        if reader._sidebar_view == "track":
            active = _active_index(reader)
            rows, total = _track_rows(reader, reader._sidebar_scroll, capacity, active)
            if reader._sub_index is None or len(reader._sub_index) == 0:
                unavailable = "Subtitle cue index unavailable"
        else:
            all_rows = _summary_rows(reader)
            total = len(all_rows)
            rows = all_rows[reader._sidebar_scroll : reader._sidebar_scroll + capacity]
            if not rows:
                unavailable = "Backlog is empty"
    except (OSError, sqlite3.Error, ValueError) as exc:
        rows, total, unavailable = [], 0, f"Backlog unavailable: {exc}"
    rendered = render_sidebar(
        rows,
        width=width,
        height=height,
        view=reader._sidebar_view,
        total=total,
        first=reader._sidebar_scroll,
        unavailable=unavailable,
    )
    reader._sidebar_rect = (x, y, width, height)
    reader._sidebar_hits = rendered.hitboxes
    reader._sidebar_total = total
    reader.ov.show(rendered.image, x, y, oid=SIDEBAR_ID)


def toggle(reader: Reader) -> None:
    reader._sidebar_open = not reader._sidebar_open
    if reader._sidebar_open:
        reader.set_hover(-1)
        active = _active_index(reader)
        reader._sidebar_scroll = max(0, active - _capacity(reader) // 2)
        reader._sidebar_last_active = active
        redraw(reader)
    else:
        reader.ov.hide(SIDEBAR_ID)
        reader._sidebar_rect = None
        reader._sidebar_hits = ()


def on_index_changed(reader: Reader) -> None:
    reader._sidebar_style_cache.clear()
    reader._sidebar_scroll = 0
    reader._sidebar_last_active = -1
    redraw(reader)


def contains(reader: Reader, x: float, y: float) -> bool:
    return bool(
        reader._sidebar_open
        and reader._sidebar_rect
        and reader._in_rect(reader._sidebar_rect, x, y)
    )


def suppress_hover(reader: Reader) -> bool:
    if not reader._sidebar_open:
        return False
    mp = reader._prop("mouse-pos") or {}
    if not contains(reader, mp.get("x", -1), mp.get("y", -1)):
        return False
    reader.set_hover(-1)
    return True


def scroll(reader: Reader, steps: int) -> bool:
    if not reader._sidebar_open:
        return False
    mp = reader._prop("mouse-pos") or {}
    if not contains(reader, mp.get("x", -1), mp.get("y", -1)):
        return False
    maximum = max(0, reader._sidebar_total - _capacity(reader))
    reader._sidebar_scroll = max(
        0, min(maximum, reader._sidebar_scroll + steps * ROWS_PER_WHEEL_STEP)
    )
    reader._sidebar_manual_until = time.monotonic() + MANUAL_SCROLL_HOLD
    redraw(reader)
    return True


def update(reader: Reader) -> None:
    if not reader._sidebar_open:
        return
    if reader._sidebar_view != "track":
        return
    active = _active_index(reader)
    geometry = (reader.osd, id(reader._sub_index), reader.subtitle_language, id(reader.scorer))
    changed = active != reader._sidebar_last_active or geometry != reader._sidebar_geometry
    if not changed and time.monotonic() < reader._sidebar_manual_until:
        return
    capacity = _capacity(reader)
    old_scroll = reader._sidebar_scroll
    visible = reader._sidebar_scroll <= active < reader._sidebar_scroll + capacity
    if active >= 0 and (time.monotonic() >= reader._sidebar_manual_until or visible):
        reader._sidebar_scroll = max(0, active - capacity // 2)
        reader._sidebar_manual_until = 0.0
    reader._sidebar_last_active = active
    reader._sidebar_geometry = geometry
    if changed or old_scroll != reader._sidebar_scroll:
        redraw(reader)


def _seek_hit(reader: Reader, hit) -> bool:
    if hit.kind == "seek" and reader._sub_index is not None:
        reader.ipc.command("set_property", "time-pos", reader._sub_index.cues[hit.value].start)
        return True
    if hit.kind == "backlog-seek":
        reader.ipc.command(
            "set_property", "time-pos", _ensure_store(reader).entry(hit.value).cue_start
        )
        return True
    return False


def _activate_hit(reader: Reader, hit) -> None:
    if hit.kind.startswith("view:"):
        reader._sidebar_view = hit.kind.split(":", 1)[1]
        reader._sidebar_scroll = 0
    elif _seek_hit(reader, hit):
        return
    elif hit.kind == "bookmark" and hit.value == _active_index(reader):
        reader.toggle_bookmark()
    elif hit.kind == "mine" and hit.value == _active_index(reader):
        reader.mine_current()
    elif hit.kind == "relink":
        video = reader._get("path")
        if video:
            _ensure_store(reader).relink(hit.value, video)


def on_click(reader: Reader, x: float, y: float) -> bool:
    if not contains(reader, x, y) or reader._sidebar_rect is None:
        return False
    local_x, local_y = x - reader._sidebar_rect[0], y - reader._sidebar_rect[1]
    hit = next((box for box in reader._sidebar_hits if box.contains(local_x, local_y)), None)
    if hit is None:
        return True
    _activate_hit(reader, hit)
    redraw(reader)
    return True


def mark_active_mined(reader: Reader) -> None:
    video = reader._get("path")
    index = reader._sub_index
    active = _active_index(reader)
    if not video or index is None or active < 0:
        return
    if reader._backlog_store is None and not db_path().exists():
        return
    cue = index.cues[active]
    try:
        store = _ensure_store(reader)
        for entry in store.entries_for_path(video):
            if abs(entry.cue_start - cue.start) < 0.05 and abs(entry.cue_end - cue.end) < 0.05:
                store.set_status(entry.id, "mined")
    except (OSError, sqlite3.Error, ValueError, KeyError):
        return
    redraw(reader)

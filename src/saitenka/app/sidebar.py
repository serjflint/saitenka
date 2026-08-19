"""Windowed in-mpv subtitle sidebar and deferred-capture backlog surface."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app import analysis_overlay, mined_store
from saitenka.app.backlog import BacklogEntry, BacklogStore, MediaRecord, db_path
from saitenka.app.languages import SECOND_LANG
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.subtitles import SidebarAction, SidebarRow, render_sidebar

if TYPE_CHECKING:
    from saitenka.app.controller import Reader
    from saitenka.app.mined_store import MinedCard
    from saitenka.subtitles import Cue, CueIndex

SIDEBAR_ID = OverlayId.SIDEBAR
MANUAL_SCROLL_HOLD = 1.5
ROWS_PER_WHEEL_STEP = 3
PLAIN = (236, 241, 247, 255)


@dataclass
class SidebarState:
    """The in-mpv sidebar's runtime state, grouped off the Reader (was ~10 ``_sidebar_*`` fields)."""

    open: bool = False
    view: str = "track"
    scroll: int = 0
    #: Honour the user's manual scroll over auto-follow until its deadline lands. A flag, not
    #: a timestamp: the deadline owns when the hold ends, so nothing here reads a clock.
    manual_hold: bool = False
    last_active: int = -1
    total: int = 0
    rect: tuple[int, int, int, int] | None = None
    hits: tuple = ()
    style_cache: dict = field(default_factory=dict)
    geometry: tuple | None = None


def _format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def _capacity(reader: Reader) -> int:
    height = max(180, round(reader.osd[1] * 0.84))
    scale = reader.chrome_scale
    return max(1, (height - round(52 * scale) - round(28 * scale)) // round(54 * scale))


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
    store = reader._backlog_store  # local narrows cleanly (the shim's __get__ re-widens each read)
    if store is None:
        store = reader._backlog_store = BacklogStore()
    return store


def _cue_parts(reader: Reader, cue_index: int, cue: Cue) -> tuple[tuple[str, tuple], ...]:
    if reader.subtitle_language == SECOND_LANG or reader.scorer is None:
        return ((cue.text.replace("\n", " "), PLAIN),)
    key = (reader.subtitle_language, cue_index, cue.text, id(reader.scorer))
    cached = reader.sidebar.style_cache.get(key)
    if cached is not None:
        return cached
    tokens = reader.tokenizer.tokenize(cue.text.replace("\\N", "\n").replace("\r", ""))
    styles = reader.scorer.score_line(tokens)
    parts = tuple((token.surface, style.color) for token, style in zip(tokens, styles, strict=True))
    reader.sidebar.style_cache[key] = parts
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


def _analysis_status(reader: Reader, cue_index: int) -> str | None:
    result = analysis_overlay.cue_result(reader.analysis.current, cue_index)
    if result is None:
        return None
    labels = []
    for label, count in (("N+1", result.n_plus_one_count), ("N+2", result.n_plus_two_count)):
        if count:
            labels.append(label if count == 1 else f"{label} ×{count}")
    return " · ".join(labels) or None


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
        status = " · ".join(
            label
            for label in (statuses.get(cue_index), _analysis_status(reader, cue_index))
            if label
        )
        rows.append(
            SidebarRow(
                value=cue_index,
                timestamp=_format_time(cue.start),
                text=cue.text,
                parts=_cue_parts(reader, cue_index, cue),
                status=status or None,
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
    if reader.subtitle_language == SECOND_LANG:
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


def _mined_row(card: MinedCard, active_cue: Cue | None) -> SidebarRow:
    text = f"{card.expression}（{card.reading}）" if card.reading else card.expression
    is_active = bool(
        active_cue
        and abs(active_cue.start - card.cue_start) < 0.05
        and abs(active_cue.end - card.cue_end) < 0.05
    )
    return SidebarRow(
        value=card.note_id,
        timestamp=_format_time(card.cue_start),
        text=text,
        parts=((text.replace("\n", " "), PLAIN),),
        status="mined",
        active=is_active,
        click_kind="mine-open",
    )


def _mine_rows(reader: Reader) -> list[SidebarRow]:
    """This episode's mined cards (#253), newest cue order, each openable in the card preview. Never
    materialises an empty store just by opening the tab (mirrors ``mark_active_mined``'s guard)."""
    video = reader._get("path")
    if not video:
        return []
    if reader._mined_store is None and not mined_store.db_path().exists():
        return []
    index: CueIndex | None = reader._sub_index
    active = _active_index(reader)
    active_cue = index.cues[active] if index is not None and active >= 0 else None
    store = mined_store.ensure_store(reader)
    return [_mined_row(card, active_cue) for card in store.for_path(video)]


def _open_mined(reader: Reader, note_id: int) -> None:
    """Open a mined card from the Mine tab: seek to its cue (offline-safe), then round-trip the full
    preview via the retained note id when Anki is reachable."""
    store = mined_store.ensure_store(reader)
    card = store.by_note_id(note_id)
    if card is None:
        return
    reader.ipc.command("set_property", "time-pos", card.cue_start)
    if reader.anki and reader.mine_cfg:
        from saitenka.app import miner_ui

        miner_ui.preview_existing(
            reader, note_id, _MinedPreviewCard(card.expression, card.reading), "exists"
        )


@dataclass(frozen=True)
class _MinedPreviewCard:
    """The minimal card shape ``miner_ui.preview_existing`` reads — the live Anki note fields override
    these, so only expression/reading (retained at mine time) are needed as the offline fallback."""

    expression: str
    reading: str
    glosses: tuple[str, ...] = ()


def redraw(reader: Reader) -> None:
    if not reader.sidebar.open:
        return
    scale = reader.chrome_scale
    margin = round(18 * scale)
    target_width = min(
        round(620 * scale),
        max(round(360 * scale), round(reader.osd[0] * 0.4 * scale)),
    )
    width = max(320, min(target_width, reader.osd[0] - margin * 2))
    height = max(180, round(reader.osd[1] * 0.84))
    x, y = reader.osd[0] - width - margin, round(reader.osd[1] * 0.08)
    capacity = _capacity(reader)
    unavailable = None
    try:
        if reader.sidebar.view == "track":
            active = _active_index(reader)
            rows, total = _track_rows(reader, reader.sidebar.scroll, capacity, active)
            if reader._sub_index is None or len(reader._sub_index) == 0:
                unavailable = "Subtitle cue index unavailable"
        elif reader.sidebar.view == "mine":
            all_rows = _mine_rows(reader)
            total = len(all_rows)
            rows = all_rows[reader.sidebar.scroll : reader.sidebar.scroll + capacity]
            if not rows:
                unavailable = "No mined cards for this episode"
        else:
            all_rows = _summary_rows(reader)
            total = len(all_rows)
            rows = all_rows[reader.sidebar.scroll : reader.sidebar.scroll + capacity]
            if not rows:
                unavailable = "Backlog is empty"
    except (OSError, sqlite3.Error, ValueError) as exc:
        rows, total, unavailable = [], 0, f"{reader.sidebar.view.title()} unavailable: {exc}"
    rendered = render_sidebar(
        rows,
        width=width,
        height=height,
        view=reader.sidebar.view,
        total=total,
        first=reader.sidebar.scroll,
        unavailable=unavailable,
        scale=scale,
    )
    reader.sidebar.rect = (x, y, width, height)
    reader.sidebar.hits = rendered.hitboxes
    reader.sidebar.total = total
    reader.lifecycle_surfaces.present(rendered.image, x, y, oid=SIDEBAR_ID)


def set_open(reader: Reader, *, open: bool) -> None:  # noqa: A002
    """Show or hide the sidebar. The caller decides which — `panel_intents` owns the toggle."""
    reader.sidebar.open = open
    if reader.sidebar.open:
        active = _active_index(reader)
        reader.sidebar.scroll = max(0, active - _capacity(reader) // 2)
        reader.sidebar.last_active = active
        redraw(reader)
    else:
        reader.lifecycle_surfaces.remove(SIDEBAR_ID)
        reader.sidebar.rect = None
        reader.sidebar.hits = ()


def on_index_changed(reader: Reader) -> None:
    reader.sidebar.style_cache.clear()
    reader.sidebar.scroll = 0
    reader.sidebar.last_active = -1
    redraw(reader)


def contains(reader: Reader, x: float, y: float) -> bool:
    return bool(
        reader.sidebar.open and reader.sidebar.rect and reader._in_rect(reader.sidebar.rect, x, y)
    )


def suppress_hover(reader: Reader) -> bool:
    if not reader.sidebar.open:
        return False
    mp = reader._prop("mouse-pos") or {}
    if not contains(reader, mp.get("x", -1), mp.get("y", -1)):
        return False
    reader.set_annotation_hover(revealed=False)
    reader.set_hover(-1)
    return True


def scroll(reader: Reader, steps: int) -> bool:
    if not reader.sidebar.open:
        return False
    mp = reader._prop("mouse-pos") or {}
    if not contains(reader, mp.get("x", -1), mp.get("y", -1)):
        return False
    maximum = max(0, reader.sidebar.total - _capacity(reader))
    reader.sidebar.scroll = max(
        0, min(maximum, reader.sidebar.scroll + steps * ROWS_PER_WHEEL_STEP)
    )
    # Fails closed: a hold that cannot be released would suppress auto-follow for the rest of
    # the session, which is worse than a manual scroll the next cue scrolls away from.
    reader.sidebar.manual_hold = reader.hold_sidebar_scroll(MANUAL_SCROLL_HOLD)
    redraw(reader)
    return True


def update(reader: Reader) -> None:
    if not reader.sidebar.open:
        return
    if reader.sidebar.view != "track":
        return
    active = _active_index(reader)
    geometry = (reader.osd, id(reader._sub_index), reader.subtitle_language, id(reader.scorer))
    changed = active != reader.sidebar.last_active or geometry != reader.sidebar.geometry
    if not changed and reader.sidebar.manual_hold:
        return
    capacity = _capacity(reader)
    old_scroll = reader.sidebar.scroll
    visible = reader.sidebar.scroll <= active < reader.sidebar.scroll + capacity
    if active >= 0 and (not reader.sidebar.manual_hold or visible):
        reader.sidebar.scroll = max(0, active - capacity // 2)
        reader.sidebar.manual_hold = False
    reader.sidebar.last_active = active
    reader.sidebar.geometry = geometry
    if changed or old_scroll != reader.sidebar.scroll:
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
        reader.sidebar.view = hit.kind.split(":", 1)[1]
        reader.sidebar.scroll = 0
    elif _seek_hit(reader, hit):
        return
    elif hit.kind == "bookmark":
        # No active-index re-check: the B/+ actions render ONLY on the active row, so re-gating on
        # `hit.value == _active_index` at click time just DROPS the click when playback drifted a cue
        # between redraw and click (#252) — the silent no-op the ungated Alt+b never had. Both paths now
        # act on the live cue; capture_current toasts if there genuinely isn't one.
        reader.toggle_bookmark()
    elif hit.kind == "mine":
        reader.mine_current()
    elif hit.kind == "mine-open":
        _open_mined(reader, hit.value)
    elif hit.kind == "relink":
        video = reader._get("path")
        if video:
            _ensure_store(reader).relink(hit.value, video)


def on_click(reader: Reader, x: float, y: float) -> bool:
    if not contains(reader, x, y) or reader.sidebar.rect is None:
        return False
    local_x, local_y = x - reader.sidebar.rect[0], y - reader.sidebar.rect[1]
    hit = next((box for box in reader.sidebar.hits if box.contains(local_x, local_y)), None)
    if hit is None:
        return True
    # Was blind: a sidebar click can mine / bookmark / relink (main-thread SQLite) + a full redraw. Span
    # it (kind = the action) so a report shows whether a click stutters — the surface #293 left uncovered.
    with otel_metrics.traced("sidebar_click", kind=hit.kind):
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
        with otel_metrics.traced("backlog_write", op="set_status"):  # main-thread SQLite on a mine
            for entry in store.entries_for_path(video):
                if abs(entry.cue_start - cue.start) < 0.05 and abs(entry.cue_end - cue.end) < 0.05:
                    store.set_status(entry.id, "mined")
    except (OSError, sqlite3.Error, ValueError, KeyError):
        return
    redraw(reader)

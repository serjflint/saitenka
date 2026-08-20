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
from saitenka.app.mpv_egress import send_correlated
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.subtitles import SidebarAction, SidebarRow, render_sidebar
from saitenka.model import claims_pointer, in_rect
from saitenka.runtime import Owner

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.controller import Reader
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.mined_store import MinedCard
    from saitenka.app.surfaces import HoverSuppression
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


def _capacity(osd: tuple[int, int], scale: float) -> int:
    """How many rows fit, from the screen and the chrome scale alone."""
    height = max(180, round(osd[1] * 0.84))
    return max(1, (height - round(52 * scale) - round(28 * scale)) // round(54 * scale))


@dataclass(frozen=True, slots=True)
class SidebarView:
    """Everything the sidebar renders from, cut by contract rather than by call chain.

    The groups are the sanctioned contracts and nothing else: the surface's own state, the cue
    identity it highlights, presentation, the media path, mining, and the dictionary. No member
    reaches a sixth thing, which is what keeps this from being the host under a new name.

    `backlog` and `mined` are factories rather than stores because drawing must not materialise a
    database — two of the three views guard on the file existing first, and building the stores
    when the view is built would defeat both guards.

    `active` is located once per operation instead of per call. `update` used to compare against
    one `_active_index` and then redraw against a second, freshly read one; a redraw that drew a
    different cue than the comparison decided on is a bug shape, not a feature.
    """

    state: SidebarState
    active: int
    index: CueIndex | None
    language: str
    osd: tuple[int, int]
    chrome_scale: float
    surfaces: LifecycleSurfaces
    video: str | None
    backlog: Callable[[], BacklogStore]
    mined: Callable[[], mined_store.MinedCardStore]
    mined_exists: bool
    scorer: object
    tokenizer: object
    analysis: object
    can_mine: bool

    @property
    def capacity(self) -> int:
        return _capacity(self.osd, self.chrome_scale)

    @property
    def active_cue(self) -> Cue | None:
        if self.index is None or self.active < 0:
            return None
        return self.index.cues[self.active]


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


def view_of(reader: Reader) -> SidebarView:
    """Snapshot the host into the value the sidebar draws from.

    The one host-taking row in this chain, deliberately, exactly as `hover_suppression` is for the
    hover chain: a hook's own test needs to build what production builds, and a test that
    assembles the value by hand is a second definition of it.
    """
    return SidebarView(
        state=reader.sidebar,
        active=_active_index(reader),
        index=reader._sub_index,
        language=reader.subtitle_language,
        osd=reader.osd,
        chrome_scale=reader.chrome_scale,
        surfaces=reader.lifecycle_surfaces,
        video=reader._get("path"),
        backlog=lambda: _ensure_store(reader),
        mined=lambda: reader.mined_store,
        mined_exists=reader._mined_store is not None or mined_store.db_path().exists(),
        scorer=reader.scorer,
        tokenizer=reader.tokenizer,
        analysis=reader.analysis.current,
        can_mine=bool(reader.anki and reader.mine_cfg),
    )


def _ensure_store(reader: Reader) -> BacklogStore:
    store = reader._backlog_store  # local narrows cleanly (the shim's __get__ re-widens each read)
    if store is None:
        store = reader._backlog_store = BacklogStore()
    return store


def _cue_parts(
    cache: dict, cue_index: int, cue: Cue, *, language: str, scorer, tokenizer
) -> tuple[tuple[str, tuple], ...]:
    """Coloured spans for one cue, memoised in ``cache``.

    The key carries the scorer's identity as well as the language: the same line scores differently
    once the known-word set changes, and a cache keyed only on text would keep serving the colours
    from before.
    """
    if language == SECOND_LANG or scorer is None:
        return ((cue.text.replace("\n", " "), PLAIN),)
    key = (language, cue_index, cue.text, id(scorer))
    cached = cache.get(key)
    if cached is not None:
        return cached
    tokens = tokenizer.tokenize(cue.text.replace("\\N", "\n").replace("\r", ""))
    styles = scorer.score_line(tokens)
    parts = tuple((token.surface, style.color) for token, style in zip(tokens, styles, strict=True))
    cache[key] = parts
    return parts


def _cue_statuses(index: CueIndex, video: str, store: BacklogStore) -> dict[int, str]:
    """Mined status per cue index, for the Mine column.

    Takes the three facts rather than the host. The store stays lazily built by the caller: opening
    the tab for a session with no video must not materialise an empty backlog, which is why this
    asks for a store instead of reaching for one.
    """
    entries = store.entries_for_path(video)
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


def _analysis_status(analysis, cue_index: int) -> str | None:
    result = analysis_overlay.cue_result(analysis, cue_index)
    if result is None:
        return None
    labels = []
    for label, count in (("N+1", result.n_plus_one_count), ("N+2", result.n_plus_two_count)):
        if count:
            labels.append(label if count == 1 else f"{label} ×{count}")
    return " · ".join(labels) or None


def _track_rows(view: SidebarView, first: int, capacity: int) -> tuple[list[SidebarRow], int]:
    index = view.index
    if index is None:
        return [], 0
    video = view.video
    statuses = _cue_statuses(index, video, view.backlog()) if video else {}
    active = view.active
    rows: list[SidebarRow] = []
    for cue_index in range(first, min(len(index), first + capacity)):
        cue = index.cues[cue_index]
        actions: tuple[SidebarAction, ...] = ()
        if cue_index == active:
            actions = (SidebarAction("B", "bookmark", cue_index),)
            if view.can_mine:
                actions += (SidebarAction("+", "mine", cue_index),)
        status = " · ".join(
            label
            for label in (statuses.get(cue_index), _analysis_status(view.analysis, cue_index))
            if label
        )
        rows.append(
            SidebarRow(
                value=cue_index,
                timestamp=_format_time(cue.start),
                text=cue.text,
                parts=_cue_parts(
                    view.state.style_cache,
                    cue_index,
                    cue,
                    language=view.language,
                    scorer=view.scorer,
                    tokenizer=view.tokenizer,
                ),
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


def _entry_text(entry: BacklogEntry, language: str) -> str:
    """The side of a bookmark the reader's current language wants, falling back to the other."""
    if language == SECOND_LANG:
        return entry.en_text or entry.jp_text
    return entry.jp_text or entry.en_text


def _entry_row(entry: BacklogEntry, active_cue, *, language: str) -> SidebarRow:
    """One backlog row. ``active_cue`` is the cue playing now, or None — the row highlights when the
    bookmark's span matches it to within 50 ms, which is looser than equality because the entry's
    times were rounded when it was saved."""
    is_active = bool(
        active_cue
        and abs(active_cue.start - entry.cue_start) < 0.05
        and abs(active_cue.end - entry.cue_end) < 0.05
    )
    text = _entry_text(entry, language)
    return SidebarRow(
        value=entry.id,
        timestamp=_format_time(entry.cue_start),
        text=text,
        parts=((text.replace("\n", " "), PLAIN),),
        status=entry.status,
        active=is_active,
        click_kind="backlog-seek",
    )


def _matched_entry_rows(
    active: int, index, language: str, store: BacklogStore, video: str | None
) -> list[SidebarRow]:
    if not video:
        return []
    active_cue = index.cues[active] if index is not None and active >= 0 else None
    return [
        _entry_row(entry, active_cue, language=language) for entry in store.entries_for_path(video)
    ]


def _summary_rows(view: SidebarView) -> list[SidebarRow]:
    store = view.backlog()
    grouped = _status_groups(store)
    video = view.video
    candidates = _candidate_rows(store, video)
    matched = _matched_entry_rows(view.active, view.index, view.language, store, video)
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


def _mine_rows(view: SidebarView) -> list[SidebarRow]:
    """This episode's mined cards (#253), newest cue order, each openable in the card preview. Never
    materialises an empty store just by opening the tab (mirrors ``mark_active_mined``'s guard)."""
    if not view.video or not view.mined_exists:
        return []
    store = view.mined()
    return [_mined_row(card, view.active_cue) for card in store.for_path(view.video)]


def _open_mined(reader: Reader, note_id: int) -> None:
    """Open a mined card from the Mine tab: seek to its cue (offline-safe), then round-trip the full
    preview via the retained note id when Anki is reachable."""
    store = reader.mined_store
    card = store.by_note_id(note_id)
    if card is None:
        return
    send_correlated(
        reader.ipc,
        "sidebar-seek-mined",
        "set_property",
        "time-pos",
        card.cue_start,
        owner=Owner.PLAYBACK,
    )
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


def draw(view: SidebarView) -> None:
    state = view.state
    if not state.open:
        return
    scale = view.chrome_scale
    margin = round(18 * scale)
    target_width = min(
        round(620 * scale),
        max(round(360 * scale), round(view.osd[0] * 0.4 * scale)),
    )
    width = max(320, min(target_width, view.osd[0] - margin * 2))
    height = max(180, round(view.osd[1] * 0.84))
    x, y = view.osd[0] - width - margin, round(view.osd[1] * 0.08)
    capacity = view.capacity
    unavailable = None
    try:
        if state.view == "track":
            rows, total = _track_rows(view, state.scroll, capacity)
            if view.index is None or len(view.index) == 0:
                unavailable = "Subtitle cue index unavailable"
        elif state.view == "mine":
            all_rows = _mine_rows(view)
            total = len(all_rows)
            rows = all_rows[state.scroll : state.scroll + capacity]
            if not rows:
                unavailable = "No mined cards for this episode"
        else:
            all_rows = _summary_rows(view)
            total = len(all_rows)
            rows = all_rows[state.scroll : state.scroll + capacity]
            if not rows:
                unavailable = "Backlog is empty"
    except (OSError, sqlite3.Error, ValueError) as exc:
        rows, total, unavailable = [], 0, f"{state.view.title()} unavailable: {exc}"
    rendered = render_sidebar(
        rows,
        width=width,
        height=height,
        view=state.view,
        total=total,
        first=state.scroll,
        unavailable=unavailable,
        scale=scale,
    )
    state.rect = (x, y, width, height)
    state.hits = rendered.hitboxes
    state.total = total
    view.surfaces.present(rendered.image, x, y, oid=SIDEBAR_ID)


def redraw(reader: Reader) -> None:
    """Draw from a freshly snapshotted view. The seam, kept for the callers that hold a host."""
    draw(view_of(reader))


def show(view: SidebarView) -> None:
    """Open the sidebar, centred on the active row."""
    view.state.open = True
    view.state.scroll = max(0, view.active - view.capacity // 2)
    view.state.last_active = view.active
    draw(view)


def hide(view: SidebarView) -> None:
    view.state.open = False
    view.surfaces.remove(SIDEBAR_ID)
    view.state.rect = None
    view.state.hits = ()


def set_open(reader: Reader, *, open: bool) -> None:  # noqa: A002
    """Show or hide the sidebar. The caller decides which — `panel_intents` owns the toggle."""
    (show if open else hide)(view_of(reader))


def index_changed(view: SidebarView) -> None:
    view.state.style_cache.clear()
    view.state.scroll = 0
    view.state.last_active = -1
    draw(view)


def on_index_changed(reader: Reader) -> None:
    index_changed(view_of(reader))


def contains(state: SidebarState, x: float, y: float) -> bool:
    """Whether ``(x, y)`` is inside the shown sidebar."""
    if not (state.open and state.rect):
        return False
    return in_rect(state.rect, x, y)


def suppress_hover(suppression: HoverSuppression) -> bool:
    state = suppression.interaction.sidebar
    if not state.open:
        return False
    if not claims_pointer(state.rect, suppression.pointer, open_=state.open):
        return False
    suppression.hide_annotation()  # the sidebar overlaps the cue, so the reveal goes with the hover
    suppression.release_hover()
    return True


def scroll(reader: Reader, steps: int) -> bool:
    if not reader.sidebar.open:
        return False
    mp = reader._prop("mouse-pos") or {}
    if not contains(reader.sidebar, mp.get("x", -1), mp.get("y", -1)):
        return False
    maximum = max(0, reader.sidebar.total - _capacity(reader.osd, reader.chrome_scale))
    reader.sidebar.scroll = max(
        0, min(maximum, reader.sidebar.scroll + steps * ROWS_PER_WHEEL_STEP)
    )
    # Fails closed: a hold that cannot be released would suppress auto-follow for the rest of
    # the session, which is worse than a manual scroll the next cue scrolls away from.
    reader.sidebar.manual_hold = reader.hold_sidebar_scroll(MANUAL_SCROLL_HOLD)
    redraw(reader)
    return True


def follow(view: SidebarView) -> None:
    """Re-centre the track view on the active row unless the user's manual hold says otherwise."""
    state = view.state
    if not state.open or state.view != "track":
        return
    active = view.active
    geometry = (view.osd, id(view.index), view.language, id(view.scorer))
    changed = active != state.last_active or geometry != state.geometry
    if not changed and state.manual_hold:
        return
    capacity = view.capacity
    old_scroll = state.scroll
    visible = state.scroll <= active < state.scroll + capacity
    if active >= 0 and (not state.manual_hold or visible):
        state.scroll = max(0, active - capacity // 2)
        state.manual_hold = False
    state.last_active = active
    state.geometry = geometry
    if changed or old_scroll != state.scroll:
        draw(view)


def update(reader: Reader) -> None:
    follow(view_of(reader))


def _seek_hit(reader: Reader, hit) -> bool:
    if hit.kind == "seek" and reader._sub_index is not None:
        send_correlated(
            reader.ipc,
            "sidebar-seek-cue",
            "set_property",
            "time-pos",
            reader._sub_index.cues[hit.value].start,
            owner=Owner.PLAYBACK,
        )
        return True
    if hit.kind == "backlog-seek":
        send_correlated(
            reader.ipc,
            "sidebar-seek-backlog",
            "set_property",
            "time-pos",
            _ensure_store(reader).entry(hit.value).cue_start,
            owner=Owner.PLAYBACK,
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
    if not contains(reader.sidebar, x, y) or reader.sidebar.rect is None:
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

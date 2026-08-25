"""Windowed in-mpv subtitle sidebar and deferred-capture backlog surface."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app import analysis_overlay, mined_store
from saitenka.app.backlog import BacklogEntry, BacklogStore, MediaRecord
from saitenka.app.languages import SECOND_LANG
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.subtitles import SidebarAction, SidebarRow, render_sidebar
from saitenka.model import in_rect
from saitenka.runtime import events
from saitenka.runtime.sidebar import MANUAL_SCROLL_HOLD, SidebarState

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.mined_store import MinedCard
    from saitenka.app.miner_ui import CardSource, PreviewPorts
    from saitenka.app.reader_context import SessionContext
    from saitenka.runtime.interaction_slice import SidebarStore
    from saitenka.subtitles import Cue, CueIndex

SIDEBAR_ID = OverlayId.SIDEBAR
PLAIN = (236, 241, 247, 255)


@dataclass
class SidebarPanel:
    """What the sidebar's last paint left behind: where it landed, what it hit-tests to, how many
    rows it measured, and the per-cue colouring it memoised.

    None of it is in the slice. The first three describe one paint on one screen — the same cut the
    picker makes — and `style_cache` is a memo rather than a fact: a reducer carrying it would be
    threading a cache through a state transition, and the state would then differ from itself
    depending on what had been drawn.
    """

    rect: tuple[int, int, int, int] | None = None
    hits: tuple = ()
    #: Rows the last draw measured. The wheel clamps against it, so it is read back — but it is an
    #: output of rendering, which is why it rides on `SidebarScrolled` rather than living in the slice.
    total: int = 0
    style_cache: dict = field(default_factory=dict)


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

    store: SidebarStore
    panel: SidebarPanel
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
    #: Same shape and same reason as `mined_exists`: a view that could answer this by asking the
    #: store would have had to open one, which is exactly what the guard exists to prevent.
    backlog_exists: bool
    scorer: object
    tokenizer: object
    analysis: object
    can_mine: bool

    @property
    def state(self) -> SidebarState:
        """Read through to the slice, never snapshotted onto the view.

        The view is built once per operation and `show`/`scroll`/`follow` all dispatch and *then*
        draw — so a captured state would have the draw render the state from before the event that
        prompted it, which for `show` means drawing a sidebar that is still closed.
        """
        return self.store.current

    @property
    def capacity(self) -> int:
        return _capacity(self.osd, self.chrome_scale)

    @property
    def active_cue(self) -> Cue | None:
        if self.index is None or self.active < 0:
            return None
        return self.index.cues[self.active]


def _active_index(index: CueIndex | None, text: str, *, sub_start, time_pos, preferred: int) -> int:
    """Which cue the sidebar highlights: the one matching `text`, disambiguated by timing.

    Pure, and takes the facts rather than the host — the whole body was already a `locate` over
    four values the caller reads anyway, so holding the SessionController only made it untestable without one.
    """
    if index is None:
        return -1
    return index.locate(text=text, sub_start=sub_start, time_pos=time_pos, preferred=preferred)


def _ensure_store(session: SessionContext) -> BacklogStore:
    """The session's backlog store, opened on first ask.

    Takes the session rather than the host: the store is the only thing it wanted, and a view that
    may never materialise it (see `view_of`'s guards) should not be able to reach anything else.
    """
    store = session.backlog_store  # local narrows cleanly (the shim's __get__ re-widens each read)
    if store is None:
        store = session.backlog_store = BacklogStore()
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
                    view.panel.style_cache,
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


def _open_mined(
    view: SidebarView,
    actions: SidebarActions,
    preview: PreviewPorts,
    source: CardSource,
    note_id: int,
) -> None:
    """Open a mined card from the Mine tab: seek to its cue (offline-safe), then round-trip the full
    preview via the retained note id when Anki is reachable."""
    card = view.mined().by_note_id(note_id)
    if card is None:
        return
    actions.seek("sidebar-seek-mined", card.cue_start)
    if view.can_mine:
        from saitenka.app import miner_ui

        miner_ui.preview_existing(
            preview, source, note_id, _MinedPreviewCard(card.expression, card.reading), "exists"
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
    panel = view.panel
    panel.rect = (x, y, width, height)
    panel.hits = rendered.hitboxes
    panel.total = total
    view.surfaces.present(rendered.image, x, y, oid=SIDEBAR_ID)


def _drive(view: SidebarView, event) -> bool:
    """Reduce one sidebar event and carry out the redraw it decided on, if it decided on one."""
    if not view.store.dispatch(event):
        return False
    draw(view)
    return True


def show(view: SidebarView) -> None:
    """Open the sidebar, centred on the active row."""
    _drive(view, events.SidebarShown(view.active, view.capacity))


def hide(view: SidebarView) -> None:
    view.store.dispatch(events.SidebarHidden())
    view.surfaces.remove(SIDEBAR_ID)
    view.panel.rect = None
    view.panel.hits = ()


def index_changed(view: SidebarView) -> None:
    view.panel.style_cache.clear()
    _drive(view, events.SidebarReindexed())


def contains(state: SidebarState, panel: SidebarPanel, x: float, y: float) -> bool:
    """Whether ``(x, y)`` is inside the shown sidebar."""
    if not (state.open and panel.rect):
        return False
    return in_rect(panel.rect, x, y)


def wheel(view: SidebarView, steps: int, pointer, *, hold: Callable[[float], bool]) -> bool:
    if not view.state.open:
        return False
    at = pointer or {}
    if not contains(view.state, view.panel, at.get("x", -1), at.get("y", -1)):
        return False
    # `hold` is armed here and its answer carried, not assumed: the reducer decides what a refusal
    # means, and a hold that could never be released would suppress auto-follow for the rest of the
    # session — worse than a manual scroll the next cue scrolls away from.
    _drive(
        view,
        events.SidebarScrolled(
            steps, max(0, view.panel.total - view.capacity), hold(MANUAL_SCROLL_HOLD)
        ),
    )
    return True


def follow(view: SidebarView) -> None:
    """Offer the sidebar a chance to re-centre on the active row; the slice decides whether it does."""
    _drive(
        view,
        events.SidebarFollowed(
            view.active,
            view.capacity,
            (view.osd, id(view.index), view.language, id(view.scorer)),
        ),
    )


@dataclass(frozen=True, slots=True)
class SidebarActions:
    """What a sidebar click can ask the session to do — the counterpart to `SidebarView`.

    Four members, one per owner the click reaches: PLAYBACK seeks, the mining feature bookmarks,
    mines and opens a mined card. In the target each is an event that owner reduces; this is the
    port that lets the click chain stop taking the host before those slices exist.
    """

    seek: Callable[[str, float], None]
    bookmark: Callable[[], None]
    mine: Callable[[], None]
    open_mined: Callable[[int], None]


def _seek_hit(view: SidebarView, actions: SidebarActions, hit) -> bool:
    if hit.kind == "seek" and view.index is not None:
        actions.seek("sidebar-seek-cue", view.index.cues[hit.value].start)
        return True
    if hit.kind == "backlog-seek":
        actions.seek("sidebar-seek-backlog", view.backlog().entry(hit.value).cue_start)
        return True
    return False


def activate_hit(view: SidebarView, actions: SidebarActions, hit) -> None:
    if hit.kind.startswith("view:"):
        view.store.dispatch(events.SidebarViewSelected(hit.kind.split(":", 1)[1]))
    elif _seek_hit(view, actions, hit):
        return
    elif hit.kind == "bookmark":
        # No active-index re-check: the B/+ actions render ONLY on the active row, so re-gating on
        # `hit.value == _active_index` at click time just DROPS the click when playback drifted a cue
        # between redraw and click (#252) — the silent no-op the ungated Alt+b never had. Both paths now
        # act on the live cue; capture_current toasts if there genuinely isn't one.
        actions.bookmark()
    elif hit.kind == "mine":
        actions.mine()
    elif hit.kind == "mine-open":
        actions.open_mined(hit.value)
    elif hit.kind == "relink" and view.video:
        view.backlog().relink(hit.value, view.video)


def click(view: SidebarView, actions: SidebarActions, x: float, y: float) -> bool:
    panel = view.panel
    if not contains(view.state, panel, x, y) or panel.rect is None:
        return False
    local_x, local_y = x - panel.rect[0], y - panel.rect[1]
    hit = next((box for box in panel.hits if box.contains(local_x, local_y)), None)
    if hit is None:
        return True
    # Was blind: a sidebar click can mine / bookmark / relink (main-thread SQLite) + a full redraw. Span
    # it (kind = the action) so a report shows whether a click stutters — the surface #293 left uncovered.
    with otel_metrics.traced("sidebar_click", kind=hit.kind):
        activate_hit(view, actions, hit)
        draw(view)
    return True


def mine_active(view: SidebarView) -> None:
    """Mark the backlog entries covering the active cue as mined, then redraw."""
    cue = view.active_cue
    if not view.video or cue is None or not view.backlog_exists:
        return
    try:
        store = view.backlog()
        with otel_metrics.traced("backlog_write", op="set_status"):  # main-thread SQLite on a mine
            for entry in store.entries_for_path(view.video):
                if abs(entry.cue_start - cue.start) < 0.05 and abs(entry.cue_end - cue.end) < 0.05:
                    store.set_status(entry.id, "mined")
    except (OSError, sqlite3.Error, ValueError, KeyError):
        return
    draw(view)

"""The base tooltip: hover a subtitle word → look up its dictionary entry → show a scrollable panel
anchored to the word, with a header ⊕ (mine) / 🔊 (speak). Owns the hover-hysteresis state machine
(word switches need a brief dwell; leaving the tooltip/nested-popup area lingers before hiding), the
click routing, and link navigation. Building, placing and compositing a panel is
:mod:`saitenka.app.tooltip_panel`, which both this module and the nested popup render through.

Takes ``reader: Reader`` (the AGENTS.md seam pattern) with thin delegating methods on Reader.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app import nested_popup, tooltip_engaged
from saitenka.app.hover_metadata import HoverMetadataKey
from saitenka.app.lifecycle_timers import LifecycleTimerKind
from saitenka.app.media import copy_clipboard, speak
from saitenka.app.mpv_egress import send_correlated
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.perf import timed
from saitenka.app.popups import NO_HOVER_METADATA, HoverMetadata, Panel, PopupView
from saitenka.app.subtitles import box_for_token
from saitenka.app.tooltip_panel import (
    anki_ok,
    compose_kind,
    decorate_and_upload,
    dispatch_hover,
    entry_for_tok,
    group_mined_of,
    is_mined,
    panel_for,
    panel_key,
    panel_style,
    place_tip,
    render_view,
    rows_panel,
    scan_hit,
    scroll_view,
)
from saitenka.model import in_rect
from saitenka.panel import header_add_rect, header_speaker_rect
from saitenka.runtime import Owner, events
from saitenka.runtime import hover as hover_machine

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.controller import Reader
    from saitenka.app.popups import TooltipState
    from saitenka.app.tooltip_panel import PanelStyle

_HIT_TEST_SAMPLE_EVERY = 8  # OTel hit-test histogram samples 1-in-N poll ticks (unlike perf.timed,
# which is an unconditional deque append and stays on every tick)
log = logging.getLogger(__name__)  # DEBUG lands in overlay.log → bundled by `saitenka report`


# --- hover -----------------------------------------------------------------------------------------


def update_hover(reader: Reader) -> None:
    """Hover with hysteresis across the popup stack: keep each level alive while the cursor is on
    its trigger OR on the popup itself, lingering ``hide_delay`` after leaving both. Hovering a
    word *inside* the tooltip opens a nested scan popup."""

    with timed("hover_hit_test"):
        # Sampled, not every tick: this runs at poll cadence (~40Hz).
        reader._hit_test_tick = (reader._hit_test_tick + 1) % _HIT_TEST_SAMPLE_EVERY
        if reader._hit_test_tick == 0:
            update_hover_instrumented(reader)
        else:
            update_hover_impl(reader)


def _hover_targets(mx: float, my: float, *, inside: bool, tip_rect, nest_rect, hit):
    """Which of (subtitle word, base tooltip, nested popup) the cursor is currently over.

    ``hit`` is the subtitle hit-test, called only when no popup claims the point — the occlusion
    rule below is the reason it is a callable rather than a precomputed index.
    """
    over_tip = inside and tip_rect is not None and in_rect(tip_rect, mx, my)
    over_nest = inside and nest_rect is not None and in_rect(nest_rect, mx, my)
    # The popups are drawn ON TOP of the subtitle, so a hit on a popup occludes the word beneath it:
    # keep the lease on the open tooltip instead of switching to the word it happens to cover (e.g. the
    # tooltip for the lower line, drawn up over the upper line of a two-line cue). Without this the base
    # hit-test still sees that covered word and `hover_switch_delay` only *delays* the hijack.
    over_word = hit(mx, my) if (inside and not (over_tip or over_nest)) else -1
    return over_word, over_tip, over_nest


def route_hover(reader: Reader, event) -> None:
    """Route one interaction observation to `Owner.INTERACTION` and perform what it decided.

    The decisions are drained and performed immediately, in order, where the call was — the outbox
    pattern, for the same reason playback's deltas use it: a hover decision that arrived a turn
    later would act on a cursor position that has moved.
    """
    for decision in dispatch_hover(reader, event):
        _perform(reader, decision)


#: The machine names its own dwells; this is the one place they meet the session's named deadlines.
_TIMER_OF = {
    hover_machine.Dwell.SWITCH: LifecycleTimerKind.HOVER_SWITCH,
    hover_machine.Dwell.HIDE_TIP: LifecycleTimerKind.TOOLTIP_HIDE,
    hover_machine.Dwell.OPEN_SCAN: LifecycleTimerKind.SCAN_OPEN,
    hover_machine.Dwell.HIDE_NESTED: LifecycleTimerKind.NESTED_HIDE,
}


def _perform(reader: Reader, decision: hover_machine.Decision) -> None:
    match decision:
        case hover_machine.Cancel(dwell):
            reader.cancel_hover_deadline(_TIMER_OF[dwell])
        case hover_machine.Arm(dwell, delay, intent):
            kind = _TIMER_OF[dwell]
            if not reader.arm_hover_deadline(kind, delay, lambda: _dwell_elapsed(reader, intent)):
                route_hover(reader, events.HoverDwellRefused(intent))
        case hover_machine.ShowWord(index):
            reader.set_hover(index)
        case hover_machine.RetireWord():
            reader.retire_hover()
        case hover_machine.OpenNested(scan):
            nested_popup.show_nested(reader, scan)
        case hover_machine.CloseNested():
            nested_popup.hide_nested(reader)


def _dwell_elapsed(reader: Reader, intent: hover_machine.Intent) -> None:
    route_hover(reader, events.HoverDwellElapsed(intent, reader.tip.nest.tail))


def observe_hover(reader: Reader, mx: float, my: float, *, inside: bool):
    """What the cursor is over, as the machine's input. Returns the observation and the raw target
    triple, which the instrumented path labels its span with.

    The scan cell is link-filtered here: a cross-reference LINK is click-to-open, NOT hover-scan, so
    reading past one must not spawn scan popups that clutter the panel.
    """
    over_word, over_tip, over_nest = _hover_targets(
        mx,
        my,
        inside=inside,
        tip_rect=reader.tip.view.rect,
        nest_rect=reader.tip.nest.rect,
        hit=lambda x, y: reader._hit(x, y) if reader.tokens else -1,
    )
    scan = (
        scan_hit(reader.tip, reader.tip_scale.raster, mx, my)
        if (over_tip and not over_nest)
        else None
    )
    if scan is not None and reader._tip_link_hit(mx, my):
        scan = None
    return (
        hover_machine.HoverObservation(
            hover=reader.hover,
            word=over_word,
            over_tip=over_tip,
            over_nest=over_nest,
            scan=scan,
            nest_open=reader.tip.nest.state is not None,
            nest_tail=reader.tip.nest.tail,
        ),
        (over_word, over_tip, over_nest),
    )


def _read_mouse(reader: Reader) -> tuple[float, float, bool]:
    mp = reader._prop("mouse-pos") or {}
    inside = bool(mp.get("hover"))
    reader._mouse_in = inside  # engagement signal for prefetch
    mx, my = mp.get("x", -1), mp.get("y", -1)
    reader.tip.last_mouse = (mx, my)
    return mx, my, inside


def update_hover_impl(reader: Reader) -> None:
    mx, my, inside = _read_mouse(reader)
    obs, (over_word, _tip, _nest) = observe_hover(reader, mx, my, inside=inside)
    reader.set_annotation_hover(revealed=over_word >= 0)
    route_hover(reader, events.HoverObserved(obs))


def update_hover_instrumented(reader: Reader) -> None:
    """Sampled split between pure target lookup and transition/tooltip work."""
    mx, my, inside = _read_mouse(reader)
    with otel_metrics.traced("hover_target_lookup") as lookup:
        obs, (over_word, over_tip, over_nest) = observe_hover(reader, mx, my, inside=inside)
        lookup.set(
            "region",
            "nested"
            if over_nest
            else "base"
            if over_tip
            else "subtitle"
            if over_word >= 0
            else "none",
        )
        lookup.set("token_count", min(len(reader.tokens), 64))
        lookup.set("box_count", min(len(reader.boxes), 64))
    with otel_metrics.traced("hover_transition") as transition:
        previous = reader.hover
        reader.set_annotation_hover(revealed=over_word >= 0)
        route_hover(reader, events.HoverObserved(obs))
        transition.set("changed", previous != reader.hover)
        transition.set(
            "cue_state",
            "empty"
            if not reader.sub_text.strip()
            else "retired"
            if reader._cue_retired
            else "pending"
            if reader._sub_pending is not None
            else "ready",
        )


def resolve_hover(reader: Reader, index: int) -> None:
    """Set the hovered word's stacked phrase terms + highlight span. Multi-token dictionary terms
    starting at ``index`` (数ある over 数) are looked up as extra terms on the hovered word, so the
    tooltip stacks them above the bare word and the underline spans the longest. Runs before the
    subtitle redraw so the highlight covers the span on the first paint."""
    terms: tuple[str, ...] = ()
    span: tuple[int, int] | None = None
    has_term = getattr(
        reader.dict_set, "has_term", None
    )  # phrase merge is an optional dict capability
    if has_term is not None:
        got = reader.tokenizer.phrase_terms(tokens=reader.tokens, index=index, has_term=has_term)
        if got is not None:
            term_list, start, end = got
            terms, span = tuple(term_list), (start, end)
    reader.tip.hover = HoverMetadata(
        terms=terms,
        span=span,
        mined=is_mined(reader.tokens[index], reader.session.mined),
        group_mined=group_mined_of(
            reader.tokens[index], reader.session.mined, reader.dict_set, extra_terms=terms
        ),
    )


def hover_key(reader: Reader, index: int) -> HoverMetadataKey:
    """The identity a hover-metadata result has to still match to be worth applying.

    One definition. The request built this tuple and the completion rebuilt it field for field, so
    a field added to one and not the other would read as a stale result — a silently dropped
    tooltip, with nothing failing at the seam.
    """
    return HoverMetadataKey(
        reader.prefetch_state.gen,
        reader._dependency_generation,
        reader.session.mined.generation,
        reader._current_cue_identity,
        index,
        reader.tip.view.job_id,
    )


def _request_hover_metadata(reader: Reader, index: int) -> None:
    from saitenka.app.hover_metadata import HoverMetadataRequest

    reader._request_interaction_metadata(
        HoverMetadataRequest(
            hover_key(reader, index),
            reader.tokenizer.name,
            tuple(reader.tokens),
            reader.dict_set,
            reader.session.mined.snapshot(),
        )
    )


def apply_hover_metadata(reader: Reader, result) -> None:
    key = result.key
    current = hover_key(reader, reader.hover)
    if current != key:
        if current.same_target(key):
            _request_hover_metadata(reader, key.index)
        return
    if result.error:
        reader._interaction_jobs.finish("tooltip", "failed")
        return
    reader.tip.hover = HoverMetadata(
        terms=result.phrase_terms,
        span=result.phrase_span,
        mined=result.mined,
        group_mined=result.group_mined,
    )
    reader._draw_subtitle()
    if show_tooltip(reader, key.index):
        if reader.episode.session_recorder is not None:
            reader.episode.session_recorder.record_lookup()
        reader._sync_auto_translation()


def retire_hover(reader: Reader) -> None:
    """Nothing is hovered any more: clear the metadata, redraw the cue, tear the tip down.

    Split from `set_hover` because every caller of the old `set_hover(-1)` meant exactly this and
    nothing else — a sidebar taking the pointer, a picker opening, the mouse leaving the cue. One
    function that both retires and builds makes a caller wanting the teardown inherit the build
    chain, and in the target this teardown is a fact the tooltip feature reduces, not a call.
    """
    if reader.hover < 0:
        return
    reader.hover = -1
    reader.tip.hover = NO_HOVER_METADATA
    reader._draw_subtitle()
    reader._teardown_tip()  # hide OverlayId.TIP/OverlayId.NESTED, reset all state, release pause


def set_hover(reader: Reader, index: int) -> None:
    if index < 0:
        retire_hover(reader)  # any negative index means "nothing hovered"
        return
    if index == reader.hover:
        return
    reader.hover = index
    reader.tip.view.job_id = reader._interaction_jobs.begin("tooltip")
    reader.tip.view.job_kind = "tooltip"
    if reader._interaction_metadata_submit is not None:
        # Retire the previous tooltip's logical identity immediately. Its acknowledged pixels may stay
        # until the replacement paints, but stale nested/open results can no longer attach to it.
        nested_popup.hide_nested(reader)
        reader.tip.tip_nav = []
        reader.tip.view.state = None
        reader.tip.view.rect = None
        reader.tip.hover = NO_HOVER_METADATA
        reader._draw_subtitle()
        _request_hover_metadata(reader, index)
        return
    resolve_hover(reader, index)  # deterministic demo/test path
    reader._draw_subtitle()
    if not show_tooltip(reader, index):
        return
    if reader.episode.session_recorder is not None:
        reader.episode.session_recorder.record_lookup()
    reader._sync_auto_translation()  # hovering a word → auto-reveal the translation


def spoken_form(token, hover_reading: str) -> str:
    """What TTS should say for a hovered word: the DICTIONARY-form reading (習う → ならう), not the
    kanji surface (say reads 習 as しゅう → "shuuwa") nor the bare stem reading ならわ. Falls back to
    the token's own reading, then its surface."""
    return hover_reading or token.reading or token.surface


def copy_hovered(toast: Callable[..., object], tokens, hover: int) -> None:
    """Copy the hovered token, if one is hovered. Takes the three facts, like `copy_token` beside it:
    the host was only ever reached for the cue's tokens and the acknowledgement."""
    if 0 <= hover < len(tokens):
        copy_token(toast, tokens[hover])


def token_clip(t) -> str:
    return f"{t.surface}【{t.reading}】" if t.reading else t.surface


def copy_token(toast: Callable[..., object], t) -> None:
    """Copy a token and say so. Takes the toast, not the host: the clipboard write is the whole
    behaviour and the host was only ever reached for the acknowledgement."""
    copy_clipboard(token_clip(t))
    toast(f"copied {t.surface}", "ok", 1.2)


def flash(reader: Reader, oid: int) -> None:
    """Pulse a "copied" highlight border on a popup as copy feedback, retired by a named deadline.

    Fails closed: a pulse that cannot be retired is a border stuck on the popup until the next
    redraw happens to clear it, which reads as a rendering bug rather than as missing feedback. So
    the highlight is only drawn once its own expiry is armed.
    """
    if not reader.schedule_flash_expiry():
        return
    reader.tip.flash_oid = oid
    render_view(reader, reader.tip.nest) if oid == OverlayId.NESTED else render_view(
        reader, reader.tip.view
    )


def copy_click(reader: Reader) -> None:
    """Right-click — copy the word under the cursor (the inner scanned word if over the nested
    popup, else the hovered/pointed subtitle word), with a brief highlight flash."""
    mp = reader._get("mouse-pos") or {}
    x, y = mp.get("x", -1), mp.get("y", -1)
    if reader.tip.nest.rect is not None and in_rect(reader.tip.nest.rect, x, y):
        if reader.tip.nest.token is not None:
            copy_token(reader._toast, reader.tip.nest.token)
            flash(reader, OverlayId.NESTED)
        return
    if reader.tip.view.rect is not None and in_rect(reader.tip.view.rect, x, y):
        copy_hovered(reader._toast, reader.tokens, reader.hover)
        flash(reader, OverlayId.TIP)
        return
    idx = reader._hit(x, y) if reader.tokens else -1  # not over a popup → the subtitle word, if any
    if idx >= 0:
        copy_token(reader._toast, reader.tokens[idx])


# --- header hit-testing (⊕ / 🔊, shared by base tooltip and nested popup) -------------------------


def hit_header_region(
    x: float, y: float, prect, xy, scroll: int, view_h: int, *, scale: float
) -> bool:
    """Is (x, y) on a header button (panel-space ``prect``)? Only while it's inside the scrolled
    viewport (the header scrolls off). Shared by the base tooltip and the nested popup."""
    px, py, pw, ph = prect
    top = py - scroll
    if top < 0 or top + ph > view_h:  # header scrolled out of the viewport (all in reference px)
        return False
    sx, sy = xy
    s = scale  # panel-space rect → display px (origin is already display px)
    return in_rect((sx + px * s, sy + top * s, pw * s, ph * s), x, y)


def hit_header_add(chrome: HeaderChrome, x: float, y: float) -> bool:
    """The ⊕ button of whichever popup `chrome` describes."""
    if chrome.view.state is None or not chrome.anki:  # ⊕ only when Anki is reachable now
        return False
    return hit_header_region(
        x,
        y,
        header_add_rect(chrome.width, speak_button=chrome.tts),
        chrome.view.xy,
        chrome.view.scroll,
        chrome.view.view_h,
        scale=chrome.scale,
    )


@dataclass(frozen=True, slots=True)
class HeaderChrome:
    """What the tooltip header's buttons need to hit-test: the panel and its on-screen geometry,
    plus whether each button is shown at all.

    One value because the hit-tests need the same five things and a mismatched set puts a button
    where it is not drawn — `add_rect` shifts depending on whether 🔊 is present, so `tts` is
    geometry here, not only a capability.

    Carries the *view*, not the tooltip state, which is what lets the base tooltip and the nested
    popup share one pair of hit-tests: they were four functions differing only in which `PopupView`
    they read, and `PopupView` is where `xy`, `scroll`, `view_h` and `state` live for both.
    """

    view: PopupView
    width: int
    scale: float
    tts: bool
    anki: bool


def chrome_for(reader: Reader, view: PopupView) -> HeaderChrome:
    """Snapshot the host into a popup's header port — the seam, as `build_draw_request` is for the
    draw. One function for both popups: the caller says which view, which is the only difference."""
    return HeaderChrome(
        view,
        reader.tip_scale.width,
        reader.tip_scale.display,
        reader._tts_ok,
        anki_ok(reader.anki, reader._anki_capability),
    )


def hit_header_speaker(chrome: HeaderChrome, x: float, y: float) -> bool:
    """The 🔊 button of whichever popup `chrome` describes."""
    if chrome.view.state is None or not chrome.tts:  # 🔊 hidden when no JA TTS voice
        return False
    return hit_header_region(
        x,
        y,
        header_speaker_rect(chrome.width),
        chrome.view.xy,
        chrome.view.scroll,
        chrome.view.view_h,
        scale=chrome.scale,
    )


def _mine_link(dict_set, terms, mine_token, lb, tok) -> bool:
    """A stacked entry's ⊕ arrives as a ``LinkBox('mine:<card_index>')`` (it rides the normal link
    hit-test). Mine that exact entry via ``cards_for(tok)[i]`` and report handled, so the caller does
    not treat it as a cross-reference navigation. Not a mine link → False.

    `terms` is the hovered word's extra terms, passed rather than read: the card list has to be the
    one the stacked panel was built from, so the ⊕'s index aligns with the group it sits on.
    """
    idx = mine_index(getattr(lb, "query", None))
    if tok is None or idx is None:
        return False
    cards = dict_set.cards_for(tok, extra_terms=terms) if dict_set else []
    if 0 <= idx < len(cards):
        mine_token(tok, card=cards[idx])
    return True


def mine_index(query: object) -> int | None:
    """The card index in a stacked entry's ``mine:<i>`` ⊕ link, or None when it is an ordinary
    cross-reference.

    The ⊕ rides the normal link hit-test, so this runs on EVERY link click — a malformed suffix has
    to read as "not a mine link" rather than raise, or one bad dictionary entry breaks navigation
    for every link in the panel.
    """
    if not isinstance(query, str) or not query.startswith("mine:"):
        return None
    try:
        return int(query[len("mine:") :])
    except ValueError:
        return None


def _click_nested(reader: Reader, x: float, y: float) -> bool:
    """Handle a click landing on the nested popup. Returns True if it did (regardless of what, if
    anything, it hit) so the caller doesn't fall through to the base tooltip underneath."""
    if reader.tip.nest.rect is None or not in_rect(reader.tip.nest.rect, x, y):
        return False
    if (
        hit_header_add(chrome_for(reader, reader.tip.nest), x, y)
        and reader.tip.nest.token is not None
    ):
        reader._mine_token(reader.tip.nest.token)  # ⊕ → mine the *inner* (scanned) word
    elif hit_header_speaker(chrome_for(reader, reader.tip.nest), x, y) and reader.tip.nest.state:
        speak(reader.tip.nest.state.reading)  # 🔊 → read the inner word aloud
    else:
        lb = reader._nest_link_hit(x, y)
        if lb is not None and not _mine_link(
            reader.dict_set,
            reader.tip.hover.terms,
            reader._mine_token,
            lb,
            reader.tip.nest.token,
        ):
            nested_popup.open_link(
                reader, lb, reader.tip.nest.xy, reader.tip.nest.scroll
            )  # cross-ref → navigate
    return True


def _click_tip(reader: Reader, x: float, y: float) -> bool:
    """Handle a click landing on the base tooltip. Returns True if it did."""
    if reader.tip.view.rect is None or not in_rect(reader.tip.view.rect, x, y):
        return False
    chrome = chrome_for(reader, reader.tip.view)  # one snapshot: both buttons, same geometry
    if hit_header_add(chrome, x, y):
        reader.mine_current()  # ⊕ → mine the hovered word into Anki
        return True
    if hit_header_speaker(chrome, x, y):
        reader.speak_hovered()  # 🔊 → hear the word (TTS)
        return True
    lb = reader._tip_link_hit(x, y)
    if lb is not None:
        tok = reader.tokens[reader.hover] if 0 <= reader.hover < len(reader.tokens) else None
        # stacked entry ⊕ → mine that entry
        if _mine_link(reader.dict_set, reader.tip.hover.terms, reader._mine_token, lb, tok):
            log.debug("tip click → mine link %r", lb.query)
        else:
            # A headword kanji (``kanji:<ch>``) and a cross-reference both navigate the base tooltip IN
            # PLACE (Yomitan; Esc/back returns). A click must NEVER spawn a nested popup — that popup is
            # hover-governed, so it dismisses itself unless the cursor chases it into it.
            log.debug("tip click → navigate %r", lb.query)
            navigate_tip(reader, lb.query)
    else:
        # No link under the cursor: a single-ideograph scan cell opens its kanji entry. If this fires on
        # a headword kanji click, the headword's kanji LinkBox was MISSED by _tip_link_hit (geometry).
        log.debug("tip click → no link at (%.0f,%.0f); kanji fallback", x, y)
        nested_popup.click_kanji_fallback(reader, x, y)
    return True


def on_click(reader: Reader) -> None:
    # Left-click drives buttons only — the card preview's ✕/screenshot/▶, and each popup's ⊕/🔊.
    # Clicking an empty area does NOTHING: audio must not fire on a stray body click.
    mp = reader._get("mouse-pos") or {}
    x, y = mp.get("x", -1), mp.get("y", -1)
    in_tip = reader.tip.view.rect is not None and in_rect(reader.tip.view.rect, x, y)
    if reader._click_preview(x, y):
        captured = "preview"
    elif _click_nested(reader, x, y):  # the nested popup sits on top → test it first
        captured = "nested"
    elif _click_tip(reader, x, y):
        captured = "tip"
    else:
        captured = "none"  # fell through — nothing under the click
    # Diagnostic: correlate a click with whether it landed on the tip rect and the pause lease, so the
    # report shows if a click while paused misses _tip_rect (mouse-pos↔OSD mismatch) or tears the tip down.
    log.debug(
        "click at (%.0f,%.0f) hover=%s in_tip=%s captured=%s tip_rect=%s paused_by_tip=%s mpv_pause=%s",
        x,
        y,
        bool(mp.get("hover")),
        in_tip,
        captured,
        reader.tip.view.rect,
        reader.tip.paused_by_tip,
        reader._prop("pause"),
    )


# --- panel building ----------------------------------------------------------------------------


# --- showing / placing / rendering the base tooltip ---------------------------------------------


def show_tooltip(reader: Reader, index: int) -> bool:
    # "tooltip_show" is the end-to-end hover→drawn span (symmetric with scroll_frame/sub_seek); the
    # perf ring buffer stays for doctor/crashlog. Metrics recorded outside the spans so the kind
    # label (cold vs warm) — only known after impl builds/hits the panel — can split the histogram.
    tip = reader.tip
    start = time.perf_counter()
    with (
        otel_metrics.traced("tooltip_show", layout_backend=reader.layout_engine) as span,
        timed("show_tooltip"),
    ):
        shown = show_tooltip_impl(reader, index)
        # Attribute a slow (usually cold) hover: whether it was a panel build vs a cache hit, the word
        # length + panel height (a tall multi-dict entry is the coldest), and bands rastered on the
        # first paint. All low-cardinality — no raw word surface. Sort spans by dur → read the why.
        span.set("anchored", shown)
        span.set(
            "outcome",
            "unanchored"
            if not shown
            else "deferred-worker"
            if tip.view.state is None
            else "painted-precomposed"
            if tip.tip_show_cold
            else "painted-cache",
        )
        if shown:
            st = tip.view.state
            span.set("cold", tip.tip_show_cold)
            span.set("chars", len(reader.tokens[index].surface))
            if st is not None:
                span.set("full_h", st.full_height)
                span.set("bands", st.last_frame_rasters)
    if shown:
        _record_show_metrics((time.perf_counter() - start) * 1000.0, cold=tip.tip_show_cold)
    return shown


def _record_show_metrics(elapsed_ms: float, *, cold: bool) -> None:
    """Live percentiles + the cold-first-paint overshoot count for one base-tooltip show. The
    overshoot counter fires only on a COLD show past the budget — a warm cache-hit show over budget
    isn't a first-paint miss, so it must not pollute the signal viewport-first rendering is judged by."""
    if otel_metrics.show_tooltip_duration_ms is not None:
        otel_metrics.show_tooltip_duration_ms.record(
            elapsed_ms, {"kind": "cold" if cold else "warm"}
        )
    if (
        cold
        and elapsed_ms > otel_metrics.COLD_FIRST_PAINT_BUDGET_MS
        and otel_metrics.cold_first_paint_overshoot is not None
    ):
        otel_metrics.cold_first_paint_overshoot.add(1)


def _freeze_frame(ipc, prop, *, enabled: bool, already_paused: bool) -> bool:
    """Pause playback for a hover. Returns whether THIS call paused, so the caller records it.

    Host-free and separated because it must run before the panel build: a hover has to pause
    instantly, and the cue cannot be allowed to advance while the tooltip is still rendering. Its
    own span keeps the IPC cost attributable — ~5ms of round-trips against a build it now precedes.
    """
    if not enabled or already_paused or prop("pause"):
        return False
    send_correlated(
        ipc,
        "hover-pause",
        "set_property",
        "pause",
        True,  # noqa: FBT003  # mpv IPC wire value
        owner=Owner.PLAYBACK,
    )
    return True


def show_tooltip_impl(reader: Reader, index: int) -> bool:
    tip = reader.tip
    view = tip.view
    nested_popup.hide_nested(reader)  # switching the base word drops any stale scan popup
    tip.tip_nav = []  # a newly hovered word abandons any link-navigation back-history
    tip.kanji_index = 0  # a new word restarts the `k` kanji cycle
    tok = reader.tokens[index]
    b = box_for_token(reader.boxes, index)
    if b is None:
        log.debug("tooltip anchor disappeared for token index %d", index)
        reader.hover = -1
        tip.hover = NO_HOVER_METADATA
        reader._interaction_jobs.finish("tooltip", "failed")
        reader._teardown_tip()
        return False
    inflected = reader._inflected_surface(index)
    cap = reader.tip_scale.cap
    with otel_metrics.traced("pause_ipc"):
        if _freeze_frame(
            reader.ipc,
            reader._prop,
            enabled=reader.pause_on_tooltip,
            already_paused=tip.paused_by_tip,
        ):
            tip.paused_by_tip = True
    # Viewport-first: warm + measure only the head that fills the viewport now (placement); the
    # windowed engine composites the rest on scroll with overscan look-ahead.
    # jamdict card_for on the main thread (not worker-safe) — untraced until now; a suspect for the
    # tooltip_show self-time under --mine, where reader._mined is populated so this actually looks up.
    meta = tip.hover
    key = panel_key(
        reader,
        tok,
        inflected,
        mined=meta.mined,
        phrase=meta.terms,
        group_mined=meta.group_mined,
    )
    tip.tip_show_cold = key not in tip.panel_cache  # cold = a panel build, not a cache hit
    ox, oy = reader.sub_origin
    anchor = (ox + b.x, oy + b.y, b.h)

    # Direct paint (#149): a COLD pathological hover the persistent cache has → place by the cached
    # full_h + decorate + upload the cached pixels NOW, skipping the whole build+measure+raster pipeline
    # so the user sees the tooltip in ~upload-time. The real interactive Panel is built right after (its
    # pixels are identical), off this paint's critical path — the reaction-latency window covers it.
    painted = _paint_from_cache(reader, key, cap, anchor) if tip.tip_show_cold else False

    # Cold miss (nothing in the panel cache AND tier-2 direct-paint missed): do NOT build/raster on the
    # main thread — that synchronous build is what balloons tooltip_show p95+. Enqueue a TOP-priority
    # bounded compose and show NOTHING; the typed completion re-invokes this once the panel is warm,
    # when it becomes an ordinary cache-hit show. If admission is unavailable, build synchronously.
    if tip.tip_show_cold and not painted:
        request = tooltip_engaged.HoverRequest(
            tok,
            inflected,
            meta.mined,
            tuple(key),
            cap,
            tuple(meta.terms),
            job_id=view.job_id,
        )
        if reader._request_engaged_tooltip(request):
            return True

    st = panel_for(
        reader,
        tok,
        inflected,
        min_h=cap,
        mined=meta.mined,
        extra_terms=meta.terms,
        group_mined=meta.group_mined,
    )
    # Direct-paint hit built a fresh interactive panel — seed its first viewport from tier-2 (RAM inflate,
    # no disk on the main thread) so scrolling back to 0 later re-blits warm.
    if tip.tip_show_cold and st.windowed.first_view is None:
        reader._seed_precomposed(st, key, cap)
    view.state, view.key = st, key
    tip.hover_reading = st.reading
    log.debug(
        "tooltip shown: word=%r phrases=%r reading=%r mined=%s painted_from_cache=%s",
        tok.surface,
        list(meta.terms),
        st.reading,
        meta.mined,
        painted,
    )

    if not painted:
        place_tip(
            view,
            st.width,
            st.full_height,
            cap,
            anchor,
            scale=reader.tip_scale.display,
            osd=reader.osd,
        )
        render_view(reader, reader.tip.view)
    reader._bind_tip_keys()  # UP/DOWN/ESC live only while the tip shows
    # One panel: the blit above painted soft (instant) if the native viewport wasn't warm yet — the
    # direct-paint (#149) path is soft too. Ask the raster lane to warm the native bands; its completion
    # upgrades soft→crisp. Keep the source token for scroll warms.
    tip.tip_tok, tip.tip_inflected = tok, inflected
    if painted:
        view.crisp_pending = True  # direct-paint is soft → poll upgrades once bands warm
    reader._request_render_ahead(view, 1)
    return True


def _paint_from_cache(reader: Reader, key, cap: int, anchor) -> bool:
    """Paint a cold hover DIRECTLY from the persistent render cache (#149): place by the cached ``full_h``
    and decorate + upload the cached premul-BGRA first viewport, skipping the entire build+measure+raster
    pipeline. Sets ``_tip_xy``/``_tip_view_h``/``_tip_scroll``/``_tip_rect`` so the real Panel built right
    after slots in without a re-blit. ``True`` when it painted (the caller then skips the re-render).

    The array is copied because ``decorate_and_upload`` mutates it in place (scrollbar/flash) and the
    disk-backed buffer is read-only. Same content ⇒ the real panel's geometry matches this placement."""
    loaded = reader._peek_render_cache(key)
    if loaded is None:
        if otel_metrics.render_cache_misses is not None:
            otel_metrics.render_cache_misses.add(
                1
            )  # cold hover, nothing cached → full build follows
        return False
    if otel_metrics.render_cache_hits is not None:
        otel_metrics.render_cache_hits.add(1)  # cold hover served straight from disk (the #149 win)
    tip = reader.tip
    full_h = loaded.full_h
    xy = place_tip(
        tip.view,
        loaded.array.shape[1],
        full_h,
        cap,
        anchor,
        scale=reader.tip_scale.display,
        osd=reader.osd,
    )
    with otel_metrics.traced(
        "tip_compose",
        cached="1",
        kind=compose_kind(OverlayId.TIP, navigated=bool(tip.tip_nav)),
    ):
        pixels = loaded.array.copy()
    tip.view.rect = decorate_and_upload(reader, pixels, 0, full_h, xy, OverlayId.TIP)
    return True


_CRISP_MIN_SCALE = (
    1.05  # below this the soft upscale IS the native render (1080p ≈ 1.0) — no crisp pass
)


def apply_engaged_open(reader: Reader, result: tooltip_engaged.OpenReady) -> None:
    """Place a worker-warmed clicked/keyed nested open, iff still valid: same generation, and the base
    tip is still up and is the SAME one that was showing at click time (``origin``). REPLACES any current
    nested popup (an explicit open/`k`-cycle wins over a hover-scan popup — newest-wins on the slot keeps
    only the latest intent). Re-selects the (now-warm) cached panel via the shared builder and
    ``place_nested``s it at the carried anchor — a cache hit whose bands the worker rastered, no getmask2."""
    if reader.tip.view.state is None:
        return
    if id(reader.tip.view.state) != result.origin:
        return  # the base tooltip switched under us — don't open onto the new word
    built = nested_popup._engaged_open_panel(
        reader, result.source, result.query
    )  # main thread → recompute mined
    if built is None:
        return
    st, key, token, word, _mined = built
    nested_popup.place_nested(reader, st, key, token, word, nested_popup.Anchor(*result.anchor))


def apply_engaged_nav(reader: Reader, result: tooltip_engaged.NavigateReady) -> None:
    """Swap in a worker-built navigated panel (clicked cross-ref), iff still valid: same generation, a
    tooltip is still up, and it's the SAME tooltip that was showing at click time (``origin`` = its
    ``id``) — a word switch in the defer window must not be hijacked into the clicked target. The bands
    are worker-warmed, so the swap's blit is a cheap assemble, not a raster."""
    if result.panel is None or reader.tip.view.state is None:
        return
    if id(reader.tip.view.state) != result.origin:
        return  # the tooltip changed under us — don't hijack the new one
    _install_navigated(reader, result.panel)


def _apply_engaged_base(reader: Reader, _key: tuple) -> None:
    """Re-enter the current hover after a composed cold-miss head (generation already checked)."""
    if reader.tip.view.state is not None:
        return  # a warm hover raced ahead — a tooltip is already up
    i = reader.hover
    if not (0 <= i < len(reader.tokens)):
        return  # not hovering a word anymore
    # Capability state is part of PanelKey and may publish while the worker is composing.  Re-entering
    # the regular path on a mismatch queues the current key instead of stranding the accepted hover.
    show_tooltip(reader, i)


def _apply_engaged_nested(reader: Reader, tail: str) -> None:
    """Show the nested scan popup for a composed cold-miss head, iff the cursor still rests on the same
    inner word — re-run the scan hit-test at the current mouse and match its tail. This re-derives a fresh
    anchor (the inner cell may have scrolled) and re-opens through the (now-warm, worker-composed) panel:
    a cache hit whose bands the worker already rastered, so no getmask2 lands on this tick."""
    if reader.tip.nest.state is not None:
        return  # a nested popup already showing
    sb = scan_hit(reader.tip, reader.tip_scale.raster, *reader.tip.last_mouse)
    if sb is None or sb.text != tail:
        return  # cursor left the inner word — never flash a stale nested popup
    key, token = reader.tip.nest.key, reader.tip.nest.token
    panel = None if key is None else reader.tip.panel_cache.get(key)
    if panel is None or token is None:
        return
    sx, sy = reader.tip.view.xy
    anchor = nested_popup.Anchor(sx + sb.x, sy + (sb.y - reader.tip.view.scroll), sb.h)
    nested_popup.place_nested(
        reader,
        panel,
        key,
        token,
        token.surface,
        anchor,
        tail,
    )


def apply_engaged_hover(reader: Reader, result: tooltip_engaged.HoverReady) -> None:
    popup = reader.tip.nest if result.nested else reader.tip.view
    if popup.job_id != result.job_id:
        return
    if result.nested:
        _apply_engaged_nested(reader, result.tail)
    else:
        _apply_engaged_base(reader, result.key)


def _capture_tip_view(tip: TooltipState) -> tuple:
    """Snapshot the base tooltip's renderable view for the link-navigation back-stack. Includes the
    source token so a restored HOVERED view can still re-request crisp band-warming on scroll.

    Takes the tooltip state, not the host: all eight fields live on it, six of them on its view.
    Eight `Delegated` reads through a Reader was the flat-name layer showing through — the snapshot
    is of one object and now says so.
    """
    return (
        tip.view.state,
        tip.view.key,
        tip.hover_reading,
        tip.view.view_h,
        tip.view.xy,
        tip.view.scroll,
        tip.tip_tok,
        tip.tip_inflected,
    )


def _restore_tip_view(tip: TooltipState, view: tuple) -> None:
    """Put back what `_capture_tip_view` took. Same object, same order, so the pair reads as one
    round trip rather than two lists of names that have to be diffed to be trusted."""
    (
        tip.view.state,
        tip.view.key,
        tip.hover_reading,
        tip.view.view_h,
        tip.view.xy,
        tip.view.scroll,
        tip.tip_tok,
        tip.tip_inflected,
    ) = view
    tip.view.desired_scroll = tip.view.scroll


def _navigated_panel(style: PanelStyle, query: str) -> Panel | None:
    """The read-only reference Panel for a navigation target: a wildcard/prefix query → search results,
    else the exact term. Built at 1× like every panel; the one-panel blit composites it natively at
    the display scale."""
    # The dictionary and the tokenizer travel together — `panel_style` reads both off one host,
    # and a navigated query is looked up whole by one and rendered by the other.
    if style.dict_set is None or style.tokenizer is None:
        return None
    if query.startswith("kanji:"):  # a headword kanji click → the kanji entry, navigated in place
        entry = style.dict_set.kanji_for(
            query[len("kanji:") :], stroke_order=style.kanji_stroke_order
        )
        if entry is None:
            return None
        reading = getattr(entry, "reading", "") or ""
    elif any(c in query for c in "*?＊？"):
        entry = style.dict_set.search(query)
        reading = ""
    else:
        # the WHOLE query as one term — never tokenize a link target
        tok = style.tokenizer.query_token(query)
        if tok is None:
            return None
        entry = entry_for_tok(tok, tok.surface, dict_set=style.dict_set, scorer=style.scorer)
        reading = getattr(entry, "reading", "") or tok.reading
    return rows_panel(style, entry, reading)


def navigate_tip(reader: Reader, query: str) -> None:
    """Replace the base tooltip's content with the entry for ``query`` (a clicked cross-reference),
    pushing the current view onto the back-stack (Esc/back returns). The popup stays put — same anchor,
    same TIP slot — so this reads as an in-place navigation, not a new floating popup.

    Deferred off the main thread (tier-3): building + rastering the navigated panel's first viewport on
    the click tick is a synchronous getmask2 raster (the ``tip_compose[clicked]`` tail). With a worker
    running, enqueue it — the worker builds + warms the bands, the tick swaps from warm bands (a cheap
    assemble, no raster). No worker → the synchronous path (unchanged)."""
    if reader.dict_set is None:
        return
    if reader.tip.view.state is not None:
        request = tooltip_engaged.NavigateRequest(query, id(reader.tip.view.state))
        if reader._request_engaged_tooltip(request):
            return
    st = _navigated_panel(panel_style(reader), query)
    if st is None:
        return
    st.render_head(
        reader.tip_scale.cap
    )  # warm the head so full_height sizes the viewport correctly
    _install_navigated(reader, st)


def _install_navigated(reader: Reader, st: Panel) -> None:
    """Swap ``st`` in as the base tooltip's content: hide the stale scan popup, push the current view
    onto the back-stack, and blit. Shared by the synchronous nav and the deferred (worker-built) swap."""
    nested_popup.hide_nested(reader)  # the old content's scan popup is stale
    reader.tip.tip_nav.append(_capture_tip_view(reader.tip))
    reader.tip.view.state = st
    # A navigated view is keyless (not a subtitle token) — the one panel composites native from its own
    # reference panel, so no synthetic key is needed. _tip_tok=None so scroll won't rebuild from a token.
    reader.tip.view.key = None
    reader.tip.tip_tok = reader.tip.tip_inflected = None
    reader.tip.hover_reading = st.reading
    reader.tip.view.scroll = 0
    reader.tip.view.desired_scroll = 0
    reader.tip.view.view_h = min(st.full_height, reader.tip_scale.cap)
    render_view(reader, reader.tip.view)


def tip_back(reader: Reader) -> bool:
    """Pop one link-navigation step, restoring the previous base view.

    Returns False when there is no history. `interaction_intents` makes that decision from
    `Reader.tip_can_go_back` now, so the return is for callers that still ask-and-act in one go.
    """
    if not reader.tip.tip_nav:
        return False
    _restore_tip_view(reader.tip, reader.tip.tip_nav.pop())
    render_view(reader, reader.tip.view)
    return True


def scroll_tip(reader: Reader, delta: int) -> None:
    # route the wheel to whichever popup the cursor is over (nested sits on top)
    if reader.tip.nest.rect is not None and in_rect(reader.tip.nest.rect, *reader.tip.last_mouse):
        scroll_view(reader, reader.tip.nest, delta)
        return
    if scroll_view(reader, reader.tip.view, delta):
        # Scrolling counts as interacting. Through the machine, not as a field write: the
        # hysteresis has one writer, and a second one leaves the next tick deciding against a state
        # it does not hold.
        route_hover(reader, events.HoverScrolled(nested=False))


# --- nested scanning: hover a word INSIDE the tooltip → its own popup ---------------------------

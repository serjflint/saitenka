"""The base tooltip: hover a subtitle word → look up its dictionary entry → show a scrollable panel
anchored to the word, with a header ⊕ (mine) / 🔊 (speak). Also owns the hover-hysteresis state
machine (word switches need a brief dwell; leaving the tooltip/nested-popup area lingers before
hiding), the panel cache (LRU, keyed by :class:`PanelKey`), and the windowed (banded) render path —
the sole tooltip compositor, shared by the base tooltip and every nested/kanji/search popup.

Takes ``reader: Reader`` (the AGENTS.md seam pattern) with thin delegating methods on Reader. This
is the largest and most tightly-coupled extraction of the controller.py split (touches prefetch,
mining-mined-state, and the nested popup) — done last, after those neighbors had already shrunk and
clarified their own seams.
"""

from __future__ import annotations

import dataclasses as _dc
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from saitenka import otel_metrics
from saitenka.app import nested_popup, tooltip_engaged
from saitenka.app.lifecycle_timers import LifecycleTimerKind
from saitenka.app.lookup import card_for, entry_for
from saitenka.app.media import copy_clipboard, speak
from saitenka.app.mpv_egress import send_correlated
from saitenka.app.nested_popup import TIP_GAP
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.perf import timed
from saitenka.app.popups import NO_HOVER_METADATA, HoverMetadata, Panel, PopupView
from saitenka.app.subtitles import box_for_token
from saitenka.model import in_rect
from saitenka.panel import Freq, header_add_rect, header_speaker_rect, panel_rows
from saitenka.runtime import Owner

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from saitenka.app.controller import Reader
    from saitenka.render.layout_backend import LayoutBackend

_HIT_TEST_SAMPLE_EVERY = 8  # OTel hit-test histogram samples 1-in-N poll ticks (unlike perf.timed,
# which is an unconditional deque append and stays on every tick)
FLASH_BGRA = (90, 214, 255, 255)  # premultiplied BGRA of the warm highlight (RGB 255,214,90)
JLPT_DARKEN = (
    0.62  # darken the pastel underline hue for the pill name-segment so white text is legible
)
log = logging.getLogger(__name__)  # DEBUG lands in overlay.log → bundled by `saitenka report`


class PanelKey(NamedTuple):
    """Identity of a rendered tooltip panel — the ``_panel_cache`` key. Named (not a bare tuple) so
    callers read ``.mined`` / ``.anki_ok`` instead of brittle positions, and adding a field can't
    silently shift another. Still a tuple, so hashing, dict-key use, and equality with a plain tuple
    of the same values are all unchanged."""

    lemma: str
    surface: str
    reading: str
    inflected: str
    width: int
    anki_ok: bool  # is Anki reachable now → is the ⊕ button drawn (rechecked per show, ~3s TTL)
    mined: bool  # is the word already in the deck → its ⊕ shows ✓ (tests read this by name)
    tts_ok: bool = False
    # Per-stacked-entry mined state (aligned to cards_for order): flips a group's ⊕→✓ and, as part of
    # the key, rebuilds the panel when one stacked entry gets mined. () for single-entry words.
    group_mined: tuple[bool, ...] = ()
    # Longer multi-token phrase terms stacked above the bare word (数ある over 数); part of the key so
    # the same word hovered mid-phrase vs standalone caches distinctly. () when no longer term.
    phrase_terms: tuple[str, ...] = ()


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


def _open_scan_popup(reader: Reader, scan) -> None:
    """A scan cell is under the cursor: open its nested popup once the dwell elapses.

    Fails closed. The dwell exists so that dragging across the panel does not spawn a popup per
    cell passed over; with no timer to wait on, opening instantly would produce exactly that, so
    the popup stays shut instead.
    """
    reader.cancel_hover_deadline(LifecycleTimerKind.NESTED_HIDE)
    reader._nest.hide_pending = False
    if scan.text == reader._scan_target:
        return  # this cell's dwell is already armed, or already resolved
    reader._scan_target = scan.text
    if reader._nest.tail == scan.text:
        return  # already shown

    def opened() -> None:
        if reader._scan_target == scan.text and reader._nest.tail != scan.text:
            reader._show_nested(scan)

    reader.arm_hover_deadline(LifecycleTimerKind.SCAN_OPEN, reader.scan_delay, opened)


def _linger_nested(reader: Reader) -> None:
    """No scan cell under the cursor: let an already-open nested popup linger, then hide it.

    Fails closed: a hide that can never fire leaves the popup on screen for the rest of the
    session, so with no timer it hides at once and only the linger is lost.
    """
    reader._scan_target = None
    reader.cancel_hover_deadline(LifecycleTimerKind.SCAN_OPEN)
    if reader._nest.state is None or reader._nest.hide_pending:
        return

    def hidden() -> None:
        reader._nest.hide_pending = False
        reader._hide_nested()

    if reader.arm_hover_deadline(LifecycleTimerKind.NESTED_HIDE, reader.hide_delay, hidden):
        reader._nest.hide_pending = True
    else:
        reader._hide_nested()


def _update_nested_hover(
    reader: Reader, mx: float, my: float, *, over_tip: bool, over_nest: bool
) -> None:
    """Scan a word inside the tooltip; keep its popup alive while engaged. A cross-reference LINK is
    click-to-open, NOT hover-scan — so scrolling past / reading a link doesn't spawn scan popups
    that clutter the panel."""
    scan = scan_hit(reader, mx, my) if (over_tip and not over_nest) else None
    if scan is not None and reader._tip_link_hit(mx, my):
        scan = None
    if scan is not None:
        _open_scan_popup(reader, scan)
    elif over_nest:
        reader._scan_target = None
        reader.cancel_hover_deadline(LifecycleTimerKind.NESTED_HIDE)
        reader._nest.hide_pending = False
    else:
        _linger_nested(reader)


def _switch_word_hover(reader: Reader, over_word: int) -> None:
    """First open is instant, but SWITCHING to a different word needs a brief dwell — so dragging the
    cursor up to the tooltip across the OTHER line of a two-line sub doesn't hijack it onto every
    word it passes over. Only resting on a new word switches.

    Fails open, unlike the hides: the dwell only refines *which* word wins, so with no timer the
    switch happens at once. Never switching would strand the tooltip on a word the cursor left.
    """
    if over_word == reader.hover:
        reader._word_target = None
        reader.cancel_hover_deadline(LifecycleTimerKind.HOVER_SWITCH)
        return
    if reader.hover < 0:
        reader.set_hover(over_word)  # nothing open yet: no hijack to guard against
        reader._word_target = None
        return
    if over_word == reader._word_target:
        return  # this word's dwell is already armed
    reader._word_target = over_word

    def switched() -> None:
        if reader._word_target == over_word:
            reader.set_hover(over_word)
            reader._word_target = None

    if not reader.arm_hover_deadline(
        LifecycleTimerKind.HOVER_SWITCH, reader.hover_switch_delay, switched
    ):
        switched()


def _linger_word_hover(reader: Reader) -> None:
    """No word under the cursor: let the base tooltip linger, then hide it.

    Fails closed, for the same reason as the nested popup: a tooltip whose hide can never fire
    stays on screen for the rest of the session.
    """
    reader._word_target = None
    reader.cancel_hover_deadline(LifecycleTimerKind.HOVER_SWITCH)
    if reader._hide_pending:
        return

    def hidden() -> None:
        reader._hide_pending = False
        reader.set_hover(-1)

    if reader.arm_hover_deadline(LifecycleTimerKind.TOOLTIP_HIDE, reader.hide_delay, hidden):
        reader._hide_pending = True
    else:
        reader.set_hover(-1)


def _update_word_hover(reader: Reader, over_word: int, *, over_tip: bool, over_nest: bool) -> None:
    """Base tooltip: also kept alive while the cursor is on the nested popup."""
    if over_word >= 0:
        _switch_word_hover(reader, over_word)
        reader.cancel_hover_deadline(LifecycleTimerKind.TOOLTIP_HIDE)
        reader._hide_pending = False
    elif over_tip or over_nest:
        reader.cancel_hover_deadline(LifecycleTimerKind.TOOLTIP_HIDE)  # keep it alive
        reader._hide_pending = False
        reader._word_target = None
    elif reader.hover != -1:
        _linger_word_hover(reader)


def update_hover_impl(reader: Reader) -> None:
    mp = reader._prop("mouse-pos") or {}
    inside = bool(mp.get("hover"))
    reader._mouse_in = inside  # engagement signal for prefetch
    mx, my = mp.get("x", -1), mp.get("y", -1)
    reader._last_mouse = (mx, my)
    over_word, over_tip, over_nest = _hover_targets(
        mx,
        my,
        inside=inside,
        tip_rect=reader._tip_rect,
        nest_rect=reader._nest.rect,
        hit=lambda x, y: reader._hit(x, y) if reader.tokens else -1,
    )
    reader.set_annotation_hover(revealed=over_word >= 0)
    _update_nested_hover(reader, mx, my, over_tip=over_tip, over_nest=over_nest)
    _update_word_hover(reader, over_word, over_tip=over_tip, over_nest=over_nest)


def update_hover_instrumented(reader: Reader) -> None:
    """Sampled split between pure target lookup and transition/tooltip work."""
    mp = reader._prop("mouse-pos") or {}
    inside = bool(mp.get("hover"))
    reader._mouse_in = inside
    mx, my = mp.get("x", -1), mp.get("y", -1)
    reader._last_mouse = (mx, my)
    with otel_metrics.traced("hover_target_lookup") as lookup:
        over_word, over_tip, over_nest = _hover_targets(
            mx,
            my,
            inside=inside,
            tip_rect=reader._tip_rect,
            nest_rect=reader._nest.rect,
            hit=lambda x, y: reader._hit(x, y) if reader.tokens else -1,
        )
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
        _update_nested_hover(reader, mx, my, over_tip=over_tip, over_nest=over_nest)
        _update_word_hover(reader, over_word, over_tip=over_tip, over_nest=over_nest)
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
    reader._hover_meta = HoverMetadata(
        terms=terms,
        span=span,
        mined=is_mined(reader.tokens[index], reader._mined),
        group_mined=group_mined_of(
            reader.tokens[index], reader._mined, reader.dict_set, extra_terms=terms
        ),
    )


def _request_hover_metadata(reader: Reader, index: int) -> None:
    from saitenka.app.hover_metadata import HoverMetadataKey, HoverMetadataRequest

    reader._request_interaction_metadata(
        HoverMetadataRequest(
            HoverMetadataKey(
                reader._prefetch_gen,
                reader._dependency_generation,
                reader._mined.generation,
                reader._current_cue_identity,
                index,
                reader._tip_view.job_id,
            ),
            reader.tokenizer.name,
            tuple(reader.tokens),
            reader.dict_set,
            frozenset(reader._mined),
        )
    )


def apply_hover_metadata(reader: Reader, result) -> None:
    key = result.key
    current = (
        reader._prefetch_gen,
        reader._dependency_generation,
        reader._mined.generation,
        reader._current_cue_identity,
        reader.hover,
        reader._tip_view.job_id,
    )
    expected = (
        key.generation,
        key.dependency_generation,
        key.mined_generation,
        key.cue_identity,
        key.index,
        key.job_id,
    )
    if current != expected:
        same_target = current[:2] + current[3:] == expected[:2] + expected[3:]
        if same_target:
            _request_hover_metadata(reader, key.index)
        return
    if result.error:
        reader._interaction_jobs.finish("tooltip", "failed")
        return
    reader._hover_meta = HoverMetadata(
        terms=result.phrase_terms,
        span=result.phrase_span,
        mined=result.mined,
        group_mined=result.group_mined,
    )
    reader._draw_subtitle()
    if show_tooltip(reader, key.index):
        if reader._session_recorder is not None:
            reader._session_recorder.record_lookup()
        reader._sync_auto_translation()


def set_hover(reader: Reader, index: int) -> None:
    if index == reader.hover:
        return
    reader.hover = index
    if index < 0:
        reader._hover_meta = NO_HOVER_METADATA
        reader._draw_subtitle()
        reader._teardown_tip()  # hide OverlayId.TIP/OverlayId.NESTED, reset all state, release pause
        return
    reader._tip_view.job_id = reader._interaction_jobs.begin("tooltip")
    reader._tip_view.job_kind = "tooltip"
    if reader._interaction_metadata_submit is not None:
        # Retire the previous tooltip's logical identity immediately. Its acknowledged pixels may stay
        # until the replacement paints, but stale nested/open results can no longer attach to it.
        reader._hide_nested()
        reader._tip_nav = []
        reader._tip_state = None
        reader._tip_rect = None
        reader._hover_meta = NO_HOVER_METADATA
        reader._draw_subtitle()
        _request_hover_metadata(reader, index)
        return
    resolve_hover(reader, index)  # deterministic demo/test path
    reader._draw_subtitle()
    if not show_tooltip(reader, index):
        return
    if reader._session_recorder is not None:
        reader._session_recorder.record_lookup()
    reader._sync_auto_translation()  # hovering a word → auto-reveal the translation


def spoken_form(token, hover_reading: str) -> str:
    """What TTS should say for a hovered word: the DICTIONARY-form reading (習う → ならう), not the
    kanji surface (say reads 習 as しゅう → "shuuwa") nor the bare stem reading ならわ. Falls back to
    the token's own reading, then its surface."""
    return hover_reading or token.reading or token.surface


def copy_hovered(reader: Reader) -> None:
    if 0 <= reader.hover < len(reader.tokens):
        copy_token(reader._toast, reader.tokens[reader.hover])


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
    reader._flash_oid = oid
    reader._render_nested_view() if oid == OverlayId.NESTED else render_view(
        reader, reader.tip.view
    )


def copy_click(reader: Reader) -> None:
    """Right-click — copy the word under the cursor (the inner scanned word if over the nested
    popup, else the hovered/pointed subtitle word), with a brief highlight flash."""
    mp = reader._get("mouse-pos") or {}
    x, y = mp.get("x", -1), mp.get("y", -1)
    if reader._nest.rect is not None and in_rect(reader._nest.rect, x, y):
        if reader._nest.token is not None:
            copy_token(reader._toast, reader._nest.token)
            flash(reader, OverlayId.NESTED)
        return
    if reader._tip_rect is not None and in_rect(reader._tip_rect, x, y):
        copy_hovered(reader)
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


def hit_header_add(reader: Reader, x: float, y: float) -> bool:
    if reader._tip_state is None or not anki_ok(
        reader.anki, reader._anki_capability
    ):  # ⊕ only when Anki is reachable now
        return False
    return hit_header_region(
        x,
        y,
        header_add_rect(reader.tip_width, speak_button=reader._tts_ok),
        reader._tip_xy,
        reader._tip_scroll,
        reader._tip_view_h,
        scale=reader._tip_display_scale,
    )


def hit_header_speaker(reader: Reader, x: float, y: float) -> bool:
    if reader._tip_state is None or not reader._tts_ok:  # 🔊 hidden when no JA TTS voice
        return False
    return hit_header_region(
        x,
        y,
        header_speaker_rect(reader.tip_width),
        reader._tip_xy,
        reader._tip_scroll,
        reader._tip_view_h,
        scale=reader._tip_display_scale,
    )


def hit_nested_add(reader: Reader, x: float, y: float) -> bool:
    if reader._nest.state is None or not anki_ok(reader.anki, reader._anki_capability):
        return False
    return hit_header_region(
        x,
        y,
        header_add_rect(reader.tip_width, speak_button=reader._tts_ok),
        reader._nest.xy,
        reader._nest.scroll,
        reader._nest.view_h,
        scale=reader._tip_display_scale,
    )


def hit_nested_speaker(reader: Reader, x: float, y: float) -> bool:
    if reader._nest.state is None or not reader._tts_ok:  # 🔊 hidden when no JA TTS voice
        return False
    return hit_header_region(
        x,
        y,
        header_speaker_rect(reader.tip_width),
        reader._nest.xy,
        reader._nest.scroll,
        reader._nest.view_h,
        scale=reader._tip_display_scale,
    )


# --- click routing -----------------------------------------------------------------------------


def _mine_link(reader: Reader, lb, tok) -> bool:
    """A stacked entry's ⊕ arrives as a ``LinkBox('mine:<card_index>')`` (it rides the normal link
    hit-test). Mine that exact entry via ``cards_for(tok)[i]`` and report handled, so the caller does
    not treat it as a cross-reference navigation. Not a mine link → False."""
    idx = mine_index(getattr(lb, "query", None))
    if tok is None or idx is None:
        return False
    # Same expanded card list the stacked panel was built from (phrase terms included), so the ⊕'s
    # card_index aligns with the group it sits on.
    cards = (
        reader.dict_set.cards_for(tok, extra_terms=reader._hover_meta.terms)
        if reader.dict_set
        else []
    )
    if 0 <= idx < len(cards):
        reader._mine_token(tok, card=cards[idx])
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
    if reader._nest.rect is None or not in_rect(reader._nest.rect, x, y):
        return False
    if hit_nested_add(reader, x, y) and reader._nest.token is not None:
        reader._mine_token(reader._nest.token)  # ⊕ → mine the *inner* (scanned) word
    elif hit_nested_speaker(reader, x, y) and reader._nest.state:
        speak(reader._nest.state.reading)  # 🔊 → read the inner word aloud
    else:
        lb = reader._nest_link_hit(x, y)
        if lb is not None and not _mine_link(reader, lb, reader._nest.token):
            reader._open_link(lb, reader._nest.xy, reader._nest.scroll)  # cross-ref → navigate
    return True


def _click_tip(reader: Reader, x: float, y: float) -> bool:
    """Handle a click landing on the base tooltip. Returns True if it did."""
    if reader._tip_rect is None or not in_rect(reader._tip_rect, x, y):
        return False
    if hit_header_add(reader, x, y):
        reader.mine_current()  # ⊕ → mine the hovered word into Anki
        return True
    if hit_header_speaker(reader, x, y):
        reader.speak_hovered()  # 🔊 → hear the word (TTS)
        return True
    lb = reader._tip_link_hit(x, y)
    if lb is not None:
        tok = reader.tokens[reader.hover] if 0 <= reader.hover < len(reader.tokens) else None
        if _mine_link(reader, lb, tok):  # stacked entry ⊕ → mine that entry
            log.debug("tip click → mine link %r", lb.query)
        else:
            # A headword kanji (``kanji:<ch>``) and a cross-reference both navigate the base tooltip IN
            # PLACE (Yomitan; Esc/back returns). A click must NEVER spawn a nested popup — that popup is
            # hover-governed, so it dismisses itself unless the cursor chases it into it.
            log.debug("tip click → navigate %r", lb.query)
            reader._navigate_tip(lb.query)
    else:
        # No link under the cursor: a single-ideograph scan cell opens its kanji entry. If this fires on
        # a headword kanji click, the headword's kanji LinkBox was MISSED by _tip_link_hit (geometry).
        log.debug("tip click → no link at (%.0f,%.0f); kanji fallback", x, y)
        reader._click_kanji_fallback(x, y)
    return True


def on_click(reader: Reader) -> None:
    # Left-click drives buttons only — the card preview's ✕/screenshot/▶, and each popup's ⊕/🔊.
    # Clicking an empty area does NOTHING: audio must not fire on a stray body click.
    mp = reader._get("mouse-pos") or {}
    x, y = mp.get("x", -1), mp.get("y", -1)
    in_tip = reader._tip_rect is not None and in_rect(reader._tip_rect, x, y)
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
        reader._tip_rect,
        reader._paused_by_tip,
        reader._prop("pause"),
    )


# --- panel building ----------------------------------------------------------------------------


def panel_key(
    reader: Reader,
    tok,
    inflected,
    *,
    mined: bool = False,
    phrase: tuple[str, ...] = (),
    group_mined: tuple[bool, ...] | None = None,
) -> PanelKey:
    # anki_ok is live (rebuilds the cached panel when Anki opens/closes; stable within its ~3s TTL).
    # ``phrase`` is the word's stacked multi-token terms — the base word's, or a nested scan's
    # longest-match under the cursor (コンサート over コン); empty for prefetch and clicked links.
    return PanelKey(
        tok.lemma,
        tok.surface,
        tok.reading,
        inflected,
        reader.tip_width,
        anki_ok(reader.anki, reader._anki_capability),
        mined,
        reader._tts_ok,
        group_mined_of(tok, reader._mined, reader.dict_set, extra_terms=phrase)
        if group_mined is None
        else group_mined,
        # the stacked phrase terms are part of the base panel's identity (数 alone vs 数 under 数ある)
        phrase,
    )


def is_mined(tok, mined: Collection[str]) -> bool:
    """Is this token's word already in the deck? (its ⊕ shows ✓ instead). Cheap short-circuit
    while nothing has been mined; else a card_for lookup (lru-cached)."""
    if not mined:
        return False
    try:
        return card_for(tok).expression in mined
    except Exception:  # noqa: BLE001  # render hot path - any lookup hiccup just hides the mined mark
        return False


def group_mined_of(tok, mined, dict_set, *, extra_terms: tuple[str, ...] = ()) -> tuple[bool, ...]:
    """Per-stacked-entry mined flags (aligned to ``cards_for`` order) for a multi-reading word — each
    entry's ⊕ shows ✓ when that exact (expression, reading) is already in the deck. () when nothing is
    mined yet (cheap short-circuit) or the word has fewer than two entries (no stacking).
    ``extra_terms`` must match the panel's phrase stacking so the flags align with the shown groups."""
    if not mined or dict_set is None:
        return ()
    try:
        cards = dict_set.cards_for(tok, extra_terms=extra_terms)
    except Exception:  # noqa: BLE001  # render hot path - a lookup hiccup just hides the mined marks
        return ()
    if len(cards) < 2:
        return ()
    return tuple(c.expression in mined for c in cards)


def anki_ok(anki, capability) -> bool:
    """Is AnkiConnect reachable RIGHT NOW? Gates the ⊕ button per card show, so it appears/hides as
    the user opens/closes Anki mid-session (not frozen at startup). Kept fast: a short timeout with
    0 retries fails immediately when Anki is closed, and the result is cached ``anki_ok_ttl``
    seconds so rapid hovers don't ping repeatedly. False when mining isn't configured at all."""
    if anki is None or capability is None:
        return False
    capability.request()
    return bool(capability.value)


def _darken(rgba, f: float = JLPT_DARKEN):
    r, g, b, a = rgba
    return (round(r * f), round(g * f), round(b * f), a)


def jlpt_pill(tok, scorer) -> Freq | None:
    """A ``JLPT | Nx`` pill for the tooltip's frequency row, shown only when the word has a JLPT
    level — the same signal the subtitle draws as an underline (``Scorer._style``). The pill's hue
    is the level's underline color (darkened for legible white text), so the tooltip and the
    underline read as the same thing."""
    from saitenka.app.scoring import _is_content

    sc = scorer
    if sc is None or not getattr(sc, "enable_jlpt", False) or sc.jlpt is None:
        return None
    # Gate on content POS exactly like the subtitle underline (Scorer._style). Without this a
    # particle/aux (は, ね) whose bare-kana READING collides with an N1 word's reading in the JLPT
    # map gets mislabelled — usually N1, since _put keeps the highest level. Better no pill.
    if not _is_content(tok):
        return None
    level = sc.jlpt.level(tok.lemma, tok.surface, tok.reading)
    if not level:
        return None
    base = sc.palette.jlpt.get(level, (96, 125, 175, 255))
    return Freq("JLPT", level, _darken(base))


def rareness_pill(tok, dict_set) -> Freq | None:
    """The blended-rareness "diff" pill: harmonic mean of the word's rank across every loaded freq
    dict, colored by band (:func:`fsrs.rareness_color`). Summarizes the row of 7+ per-dict pills into
    one rareness read. ``None`` when no freq dict has the word, so the caller skips it cleanly."""
    from saitenka.app.fsrs import diff_pill, harmonic_of

    ds = dict_set
    sources = getattr(ds, "freqs", None)
    if not sources:
        return None
    # Only rank-based dicts may be blended — an occurrence-based dict's converted rank is a per-corpus
    # dense rank on an incomparable scale (see FreqSource.occurrence_based); it stays in the per-dict
    # pill row but never in the harmonic mean.
    forms = (tok.lemma, tok.surface, tok.reading)
    ranks = [
        r
        for fs in sources
        if not getattr(fs, "occurrence_based", False)
        and (r := fs.rank(forms, tok.reading)) is not None
    ]
    return diff_pill(harmonic_of([float(r) for r in ranks]))


def entry_for_tok(tok, inflected, *, dict_set, scorer, extra_terms: tuple[str, ...] = ()):
    """Look up the panel entry and fold in the blended-rareness pill and the JLPT pill (leading the
    frequency pills) when the word has them, so they mirror the subtitle underline / freq row.
    ``extra_terms`` are longer multi-token phrases starting at this word (数ある over 数); the dict set
    stacks them above the bare word.

    Never mutates the lru_cached Entry from lookup.lookup_entry / dict_set.entry_for — returns
    a shallow copy with a new freqs list so repeated calls do not accumulate pills."""
    if dict_set is None:
        entry = entry_for(tok)
    elif extra_terms:  # only the phrase path needs the expanded lookup
        entry = dict_set.entry_for(tok, inflected=inflected, extra_terms=extra_terms)
    else:
        entry = dict_set.entry_for(tok, inflected)
    extra = [p for p in (rareness_pill(tok, dict_set), jlpt_pill(tok, scorer)) if p is not None]
    if extra and hasattr(entry, "freqs"):
        # Build the pill list into a shallow copy — never mutate the cached original.
        entry = _dc.replace(entry, freqs=[*extra, *entry.freqs])
    return entry


@dataclass(frozen=True, slots=True)
class PanelStyle:
    """Everything a panel build needs that does not change between hovers.

    Deliberately NOT "the panel context": the union across the whole build chain is sixteen fields
    including a live mined set and a per-turn flag, and a value object holding those is `Reader`
    under another name. This is the session-lifetime half; per-turn facts stay parameters.
    """

    width: int
    band_cache_max: int
    raw_band_ceiling: int
    layout_backend: LayoutBackend | None
    layout_engine: str
    add_button: bool
    speak_button: bool
    dict_set: object = None
    scorer: object = None


def panel_style(reader: Reader) -> PanelStyle:
    """Snapshot the build configuration off the host. The one host read in the build chain."""
    return PanelStyle(
        width=reader.tip_width,
        band_cache_max=reader.band_cache_max,
        raw_band_ceiling=reader.raw_band_ceiling,
        layout_backend=reader.layout_backend,
        layout_engine=reader.layout_engine,
        add_button=anki_ok(reader.anki, reader._anki_capability),
        speak_button=reader._tts_ok,
        dict_set=reader.dict_set,
        scorer=reader.scorer,
    )


def _build_panel(
    style: PanelStyle,
    _key: PanelKey,
    tok,
    inflected,
    *,
    mined: bool,
    nested: bool = False,
    extra_terms: tuple[str, ...] = (),
    during_scroll: bool = False,
) -> Panel:
    if otel_metrics.panel_cache_misses is not None:
        otel_metrics.panel_cache_misses.add(1)
    # kind is the base/nested IDENTITY. during_scroll flags a render triggered by the scan-hit-test
    # recomputing which cell is under a STATIONARY cursor after content moved under it (a nested popup
    # opening as a side effect of scrolling the base tooltip in the same turn), not a mouse move.
    with otel_metrics.instrumented(
        otel_metrics.render_duration_ms,
        "render",
        kind="nested" if nested else "base",
        during_scroll="1" if during_scroll else "0",
        layout_backend=style.layout_engine,
    ):
        # The base tooltip stacks the hovered word's longer phrase terms (passed in); nested popups
        # (inner scanned words) and prefetch pass none and look up the bare word only.
        entry = entry_for_tok(
            tok,
            inflected,
            dict_set=style.dict_set,
            scorer=style.scorer,
            extra_terms=extra_terms,
        )
        return Panel.from_rows(
            panel_rows(
                entry,
                style.width,
                add_button=style.add_button,
                mined=mined,
                speak_button=style.speak_button,
                group_mined=_key.group_mined,
            ),
            style.width,
            getattr(entry, "reading", "") or tok.reading,
            band_cache_max=style.band_cache_max,
            raw_band_ceiling=style.raw_band_ceiling,
            layout_backend=style.layout_backend,
        )


def panel_for(
    reader: Reader,
    tok,
    inflected=None,
    min_h: int | None = None,
    *,
    mined: bool | None = None,
    nested: bool = False,
    extra_terms: tuple[str, ...] = (),
    group_mined: tuple[bool, ...] | None = None,
) -> Panel:
    """The memoised :class:`Panel` for a token: warm + measure the head that fills ``min_h`` px now;
    the windowed engine composites the rest on scroll. Re-hovering is instant and scrolling is cheap.
    ``mined`` (default: look it up) selects the ⊕ vs ✓ header variant and is part of the cache key.
    ``nested`` is the base/nested IDENTITY (drives the perf ``kind`` label); a nested popup passes
    ``nested=True``.

    Thread-safe: the panel is *built* lock-free (thread-local DB conns + fonts, each render owns its
    images), and only the tiny cache write/LRU update is locked. On a free-threaded (no-GIL) build,
    OrderedDict.get() is NOT atomic, so cache hits also acquire the lock briefly to move_to_end.
    Hovers remain snappy because the lock is held for only a few microseconds (no rendering inside)."""
    if mined is None:
        mined = is_mined(tok, reader._mined)
    key = panel_key(
        reader,
        tok,
        inflected,
        mined=mined,
        phrase=extra_terms,
        group_mined=group_mined,
    )
    # No `_panel_cache_get` wrapper any more: it existed to hold the fetch-or-build-then-LRU-touch
    # protocol, and `PanelCache` owns that now.
    style = panel_style(reader)
    during_scroll = reader._scrolled_this_tick
    st = reader._panel_cache.get_or_build(
        key,
        lambda: _build_panel(
            style,
            key,
            tok,
            inflected,
            mined=mined,
            nested=nested,
            extra_terms=extra_terms,
            during_scroll=during_scroll,
        ),
    )
    # The head walk+wrap (offset measure for placement) — runs on every hover, cold or warm, and was
    # the untraced bulk of tooltip_show's self-time (#158 territory). Cheap on a re-measured cached
    # panel, a full walk on a fresh one. Nests under tooltip_show / prefetch_decode.
    with otel_metrics.traced("measure"):
        st.render_head(min_h if min_h is not None else reader._tip_cap())
    return st


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


def _place_tip(
    view, width: int, full_height: int, cap: int, anchor, *, scale: float, osd
) -> tuple[int, int]:
    """Put a panel on screen: reset the scroll, cap the height, choose the position.

    Takes the view rather than the host because every value it touches already lives on it. Width
    and height rather than a panel, because the direct-paint path (#149) places a cached ARRAY that
    has no panel yet, and it was repeating this arithmetic — including the safe area — verbatim.

    Safe area: the cap keeps the tooltip clear of the OSC header and the controls at the bottom, so
    it never spills under the window chrome. It scrolls, so capping beats trying to fit a tall entry
    — `full_height` is the windowed engine's estimate, exact once the head measured a short panel.
    """
    wx, wy, box_h = anchor
    view.scroll = 0
    view.desired_scroll = 0
    view.view_h = min(full_height, cap)
    view.xy = place_panel(width, wx, wy, box_h, view.view_h, scale=scale, osd=osd)
    return view.xy


def show_tooltip_impl(reader: Reader, index: int) -> bool:
    tip = reader.tip
    view = tip.view
    reader._hide_nested()  # switching the base word drops any stale scan popup
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
    cap = reader._tip_cap()
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
        _place_tip(
            view,
            st.width,
            st.full_height,
            cap,
            anchor,
            scale=reader._tip_display_scale,
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


def place_panel(
    full_w: int, wx: float, wy: float, wh: float, view_h: int, *, scale: float, osd: tuple[int, int]
) -> tuple[int, int]:
    """Choose a top-left (tx, ty) for a panel of REFERENCE size ``full_w`` × ``view_h`` anchored to an
    on-screen word box (wx, wy, wh): above it if there's room, else below, clamped to the safe area. The
    panel is composited at reference size then upscaled by ``_tip_display_scale`` at upload, so placement
    uses the DISPLAYED size. Shared by the base tooltip and nested popups."""
    s = scale
    disp_w, disp_h = full_w * s, view_h * s
    margin = max(16, round(osd[1] * 0.05))
    above_room = wy - TIP_GAP - margin
    below_room = (osd[1] - margin) - (wy + wh + TIP_GAP)
    if above_room >= disp_h or above_room >= below_room:
        ty = wy - TIP_GAP - disp_h  # above the word
    else:
        ty = wy + wh + TIP_GAP  # below the word
    tx = max(margin, min(wx, osd[0] - disp_w - margin))
    ty = max(margin, min(ty, osd[1] - margin - disp_h))
    return int(tx), int(ty)


def _compose_kind(oid: int, *, navigated: bool) -> str:
    """Classify a ``tip_compose`` for telemetry so a report can separate the paints the user perceives
    as distinct: a ``nested`` scan popup, a ``clicked`` link-navigation of the base tooltip (nav stack
    non-empty), or a plain ``base`` hover. The blit already knows its ``oid``; navigation is the only
    state not in it."""
    if oid == OverlayId.NESTED:
        return "nested"
    return "clicked" if navigated else "base"


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
    xy = _place_tip(
        tip.view,
        loaded.array.shape[1],
        full_h,
        cap,
        anchor,
        scale=reader._tip_display_scale,
        osd=reader.osd,
    )
    with otel_metrics.traced(
        "tip_compose",
        cached="1",
        kind=_compose_kind(OverlayId.TIP, navigated=bool(tip.tip_nav)),
    ):
        pixels = loaded.array.copy()
    tip.view.rect = decorate_and_upload(reader, pixels, 0, full_h, xy, OverlayId.TIP)
    return True


def blit_panel(
    reader: Reader,
    panel: Panel,
    scroll: int,
    view_h: int,
    xy,
    oid: int,
    *,
    soft_reason: str = "n/a",
):
    """Composite the ``[y0, y0+vh)`` viewport from the windowed engine (O(viewport)) and decorate +
    upload it. ``overscan`` renders one viewport of blocks BELOW the fold and keeps them warm, so the
    next wheel notch composites without a hot-path render. The sole popup blit — base and nested.
    ``soft_reason`` is the blitting view's crisp-miss reason (its own, so a nested soft blit doesn't
    read the base's) — attributed on the ``tip_compose`` span."""
    full_h = panel.full_height
    vh = min(view_h, full_h)
    y0 = max(0, min(scroll, max(0, full_h - vh)))
    # The first-paint composite + any SYNCHRONOUS overscan band raster runs here on the calling
    # thread, was untraced, and is the bulk of tooltip_show's wall time that neither `render` (panel
    # build) nor `upload` (IPC) covered — a cold hover's tooltip_show read ~130ms of on-thread CPU
    # outside every child span until this span existed. Nests under tooltip_show / scroll_frame.
    # soft_reason/scale attribute WHY this blit is soft (vs crisp) + at what display scale, so a report
    # shows e.g. a run of `stale_scale` misses (the OSD scale jittered and orphaned the native panel).
    with otel_metrics.traced(
        "tip_compose",
        soft_reason=soft_reason or "n/a",
        scale=f"{reader._tip_display_scale:.4f}",
        kind=_compose_kind(oid, navigated=bool(reader._tip_nav)),
    ):
        view = panel.viewport(y0, vh, overscan=vh)  # exact BGRA viewport + one screen look-ahead
    return decorate_and_upload(reader, view, y0, full_h, xy, oid)


def decorate_and_upload(
    reader: Reader, view, y0: int, full_h: int, xy, oid: int, *, prescaled: bool = False
):
    """Draw the scrollbar thumb and the copy-flash border onto a REFERENCE-sized viewport BGRA array,
    then upscale by ``_tip_display_scale`` to the live display and upload. Decorations are drawn in
    reference px (crisp thumb) before the scale; the returned rect is in DISPLAY px (what the hit-test
    compares the OSD cursor against). ``prescaled`` (the idle crisp re-render) means ``view`` is ALREADY
    at display resolution, so the upscale is skipped — but ``full_h``/``y0`` are still display px so the
    thumb geometry stays right."""
    vh, full_w = view.shape[0], view.shape[1]
    if full_h > vh:  # scrollbar thumb (premultiplied BGRA gray)
        track = vh - 8
        th = max(28, int(track * vh / full_h))
        tyb = 4 + int((track - th) * (y0 / max(1, full_h - vh)))
        view[tyb : tyb + th, full_w - 7 : full_w - 3] = (99, 99, 99, 210)
    tip = reader.tip
    if tip.flash_oid == oid:  # the deadline owns when this stops being true
        b = 4  # "copied" highlight border (a brief visual pulse)
        view[:b, :] = view[-b:, :] = FLASH_BGRA
        view[:, :b] = view[:, -b:] = FLASH_BGRA
    s = reader._tip_display_scale
    if not prescaled and abs(s - 1.0) > 1e-3:  # only hi-dpi pays the resize; 1080p is a 1:1 no-op
        from saitenka.bgra import scale_bgra

        view = scale_bgra(view, s)
    tx, ty = xy
    popup = tip.nest if oid == OverlayId.NESTED else tip.view
    kind = popup.job_kind
    job_id = popup.job_id

    def settled(*, painted: bool) -> None:
        if job_id is None:
            return
        reader._interaction_jobs.finish(kind, "painted" if painted else "failed", job_id=job_id)

    # Fenced: a paint acknowledged after a newer paint or a hide settles nobody, so the intent's
    # latency is never closed out against pixels something else has already replaced.
    reader.interaction_surfaces.present_bgra(view, tx, ty, oid=oid, on_settled=settled)
    return (tx, ty, view.shape[1], view.shape[0])


_CRISP_MIN_SCALE = (
    1.05  # below this the soft upscale IS the native render (1080p ≈ 1.0) — no crisp pass
)


def hit_target(nest, tip_state, tip_scroll: int, raster_scale: float, *, nested: bool):
    """The ``(panel, scale, scroll)`` to hit-test a popup against — the ONE reference panel, always. It's
    composited natively (glyph masks over 1× geometry), so the DRAWN panel IS the hit-tested panel and the
    inverse is a single ``(mx-sx)/scale + scroll`` against 1× geometry — the two-geometry seam bug can't
    occur. ``scale`` is the BUCKETED raster scale the blit drew at, so hit-test == draw exactly."""
    ref, scroll = (nest.state, nest.scroll) if nested else (tip_state, tip_scroll)
    return ref, raster_scale, scroll


def render_view(reader: Reader, view: PopupView) -> None:
    """The SOLE blit path (SSOT) for BOTH the base tooltip and the nested popup: composite ``view``'s
    current viewport CRISP straight from the cached native-scale panel when it's built (the common case
    once a word is shown — so scrolling stays crisp, no soft flash), else the soft reference upscale, and
    store its screen rect. Every re-blit — show, scroll, flash expiry, OSD change — routes through here,
    so nothing can flip a crisp viewport back to blurry, and each popup owns its own crisp flags."""
    st = view.state
    if st is None:
        return
    view.rect = _blit_crisp_or_soft(reader, view, st)


def apply_engaged_open(reader: Reader, result: tooltip_engaged.OpenReady) -> None:
    """Place a worker-warmed clicked/keyed nested open, iff still valid: same generation, and the base
    tip is still up and is the SAME one that was showing at click time (``origin``). REPLACES any current
    nested popup (an explicit open/`k`-cycle wins over a hover-scan popup — newest-wins on the slot keeps
    only the latest intent). Re-selects the (now-warm) cached panel via the shared builder and
    ``place_nested``s it at the carried anchor — a cache hit whose bands the worker rastered, no getmask2."""
    if reader._tip_state is None:
        return
    if id(reader._tip_state) != result.origin:
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
    if result.panel is None or reader._tip_state is None:
        return
    if id(reader._tip_state) != result.origin:
        return  # the tooltip changed under us — don't hijack the new one
    _install_navigated(reader, result.panel)


def _apply_engaged_base(reader: Reader, _key: tuple) -> None:
    """Re-enter the current hover after a composed cold-miss head (generation already checked)."""
    if reader._tip_state is not None:
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
    if reader._nest.state is not None:
        return  # a nested popup already showing
    sb = scan_hit(reader, *reader._last_mouse)
    if sb is None or sb.text != tail:
        return  # cursor left the inner word — never flash a stale nested popup
    key, token = reader._nest.key, reader._nest.token
    panel = None if key is None else reader._panel_cache.get(key)
    if panel is None or token is None:
        return
    sx, sy = reader._tip_xy
    anchor = nested_popup.Anchor(sx + sb.x, sy + (sb.y - reader._tip_scroll), sb.h)
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
    popup = reader._nest if result.nested else reader._tip_view
    if popup.job_id != result.job_id:
        return
    if result.nested:
        _apply_engaged_nested(reader, result.tail)
    else:
        _apply_engaged_base(reader, result.key)


def apply_pending_crisp(reader: Reader, view: PopupView) -> None:
    """Poll loop: once a soft first paint's native bands are warmed by the scroll-ahead worker, re-blit
    ``view`` ONCE to upgrade soft→crisp (``_blit_native`` composites crisp when warm and clears the
    flag). Per-view, so a soft nested paint upgrades the NESTED popup (not the base). No-op until warm /
    when nothing is pending — a cheap warmth check per tick, never a re-blit-per-tick churn."""
    if not view.crisp_pending:
        return
    st = view.state
    if st is None:
        view.crisp_pending = False
        return
    vh = min(view.view_h, st.full_height)
    y0 = max(0, min(view.scroll, max(0, st.full_height - vh)))
    if st.native_viewport_warm(y0, vh, reader._raster_scale):
        render_view(
            reader, view
        )  # warm now → _blit_native composites crisp and clears crisp_pending


def _blit_native(reader: Reader, view: PopupView, st: Panel):
    """One-panel (scale-boundary) blit: composite the ONE reference panel's viewport at the display scale
    — native crisp glyph masks over the 1× geometry — and upload 1:1. Soft below the crisp threshold
    (≈1080p, where native == the upscale). No second panel, no crisp cache: the drawn panel IS the
    reference panel, so it can't disagree with the hit-test (which reads the same 1× geometry). ``view``
    owns the scroll/viewport/xy + the soft→crisp flags, so base and nested each track their own."""
    scroll, view_h, xy, oid = view.scroll, view.view_h, view.xy, view.oid
    scale = (
        reader._raster_scale
    )  # bucketed → matches hit_target's inverse; reuses cached native bands
    if scale <= _CRISP_MIN_SCALE:  # 1080p — native == soft upscale, take the cheaper 1× path
        view.crisp_miss = "not_hidpi"
        view.crisp_pending = False
        return blit_panel(reader, st, scroll, view_h, xy, oid, soft_reason=view.crisp_miss)
    full_h = st.full_height
    vh = min(view_h, full_h)
    y0 = max(0, min(scroll, max(0, full_h - vh)))
    # SOFT-FIRST (plan B3): a cold native viewport rasters O(viewport) glyph masks synchronously (~scale²
    # px) — too slow for the hot path. Paint the instant 1× upscale now, flag the poll loop to upgrade,
    # and let the scroll-ahead worker warm the native bands. Only composite crisp when they're already warm
    # (a cheap memoised assemble). This keeps show/scroll responsive; crisp lands a frame or two later.
    if not st.native_viewport_warm(y0, vh, scale):
        view.crisp_miss = "warming"
        view.crisp_pending = True  # poll's apply_pending_crisp re-blits once the bands warm
        return blit_panel(reader, st, scroll, view_h, xy, oid, soft_reason=view.crisp_miss)
    try:
        # crisp=native (soft_reason="" — this IS the crisp path, not a soft fallback). warm_only: the
        # main thread NEVER rasters — the bands are warm (gated above); a raced eviction shows bg, not a
        # synchronous raster. All rasterisation is a worker job (structural, not a thread check).
        with otel_metrics.traced(
            "tip_compose",
            soft_reason="",
            scale=f"{scale:.4f}",
            kind=_compose_kind(oid, navigated=bool(reader._tip_nav)),
        ):
            arr = st.viewport(y0, vh, overscan=vh, scale=scale, warm_only=True)  # native, no raster
    except Exception:  # a composite failure falls back to the soft upscale (never a blank tooltip)
        log.debug("native compose failed", exc_info=True)
        return blit_panel(reader, st, scroll, view_h, xy, oid, soft_reason=view.crisp_miss)
    view.crisp_miss = ""
    view.crisp_pending = False
    if otel_metrics.crisp_swaps is not None:
        otel_metrics.crisp_swaps.add(1)
    # y0/full_h are display px so decorate_and_upload's scrollbar-thumb geometry stays right; the array
    # is already native (prescaled) so no scale_bgra.
    return decorate_and_upload(
        reader, arr, round(y0 * scale), round(full_h * scale), xy, oid, prescaled=True
    )


def _blit_crisp_or_soft(reader: Reader, view: PopupView, st: Panel):
    """Composite ``view``'s current viewport and return its display-px rect. One panel: the reference
    panel composites natively at the display scale (``_blit_native``), soft below the crisp threshold.
    The SSOT both popups blit through, so each is crisp exactly when hi-dpi."""
    return _blit_native(reader, view, st)


def _capture_tip_view(reader: Reader) -> tuple:
    """Snapshot the base tooltip's renderable view for the link-navigation back-stack. Includes the
    source token so a restored HOVERED view can still re-request crisp band-warming on scroll."""
    return (
        reader._tip_state,
        reader._tip_key,
        reader._hover_reading,
        reader._tip_view_h,
        reader._tip_xy,
        reader._tip_scroll,
        reader._tip_tok,
        reader._tip_inflected,
    )


def _restore_tip_view(reader: Reader, view: tuple) -> None:
    (
        reader._tip_state,
        reader._tip_key,
        reader._hover_reading,
        reader._tip_view_h,
        reader._tip_xy,
        reader._tip_scroll,
        reader._tip_tok,
        reader._tip_inflected,
    ) = view
    reader._tip_view.desired_scroll = reader._tip_scroll


def _navigated_panel(reader: Reader, query: str) -> Panel | None:
    """The read-only reference Panel for a navigation target: a wildcard/prefix query → search results,
    else the exact term. No ⊕ — the header mine button acts on the hovered SUBTITLE word, which the
    navigated term is not, so mining stays on the base word (reachable via back). Built at 1× like every
    panel; the one-panel blit composites it natively at the display scale."""
    if reader.dict_set is None:
        return None
    if query.startswith("kanji:"):  # a headword kanji click → the kanji entry, navigated in place
        entry = reader.dict_set.kanji_for(
            query[len("kanji:") :], stroke_order=reader.kanji_stroke_order
        )
        if entry is None:
            return None
        reading = getattr(entry, "reading", "") or ""
    elif any(c in query for c in "*?＊？"):
        entry = reader.dict_set.search(query)
        reading = ""
    else:
        tok = reader.tokenizer.query_token(
            query
        )  # look up the WHOLE query as one term — never tokenize a link target
        if tok is None:
            return None
        entry = entry_for_tok(tok, tok.surface, dict_set=reader.dict_set, scorer=reader.scorer)
        reading = getattr(entry, "reading", "") or tok.reading
    rows = panel_rows(entry, reader.tip_width, add_button=False, speak_button=reader._tts_ok)
    return Panel.from_rows(
        rows,
        reader.tip_width,
        reading,
        band_cache_max=reader.band_cache_max,
        raw_band_ceiling=reader.raw_band_ceiling,
        layout_backend=reader.layout_backend,
    )


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
    if reader._tip_state is not None:
        request = tooltip_engaged.NavigateRequest(query, id(reader._tip_state))
        if reader._request_engaged_tooltip(request):
            return
    st = _navigated_panel(reader, query)
    if st is None:
        return
    st.render_head(reader._tip_cap())  # warm the head so full_height sizes the viewport correctly
    _install_navigated(reader, st)


def _install_navigated(reader: Reader, st: Panel) -> None:
    """Swap ``st`` in as the base tooltip's content: hide the stale scan popup, push the current view
    onto the back-stack, and blit. Shared by the synchronous nav and the deferred (worker-built) swap."""
    reader._hide_nested()  # the old content's scan popup is stale
    reader._tip_nav.append(_capture_tip_view(reader))
    reader._tip_state = st
    # A navigated view is keyless (not a subtitle token) — the one panel composites native from its own
    # reference panel, so no synthetic key is needed. _tip_tok=None so scroll won't rebuild from a token.
    reader._tip_key = None
    reader._tip_tok = reader._tip_inflected = None
    reader._hover_reading = st.reading
    reader._tip_scroll = 0
    reader._tip_view.desired_scroll = 0
    reader._tip_view_h = min(st.full_height, reader._tip_cap())
    render_view(reader, reader.tip.view)


def tip_back(reader: Reader) -> bool:
    """Pop one link-navigation step, restoring the previous base view.

    Returns False when there is no history. `interaction_intents` makes that decision from
    `Reader.tip_can_go_back` now, so the return is for callers that still ask-and-act in one go.
    """
    if not reader._tip_nav:
        return False
    _restore_tip_view(reader, reader._tip_nav.pop())
    render_view(reader, reader.tip.view)
    return True


def scroll_view(reader: Reader, view: PopupView, delta: int) -> bool:
    """Scroll ``view`` by ``delta`` (clamped to the windowed full height, a converging estimate) and
    re-blit crisp, warming the NEXT native bands off the main thread so continued scrolling composites
    crisp without a synchronous raster. Shared by the base tooltip and the nested popup — nested finally
    gets the same render-ahead lookahead the base has. ``True`` iff it actually scrolled."""
    st = view.state
    if st is None:
        return False
    maxs = max(0, st.full_height - view.view_h)
    ns = min(maxs, max(0, view.desired_scroll + delta))
    if ns == view.desired_scroll:
        return False
    view.job_id = reader._interaction_jobs.begin("scroll")
    view.job_kind = "scroll"
    view.desired_scroll = ns
    view.hide_pending = False  # scrolling counts as interacting → keep this popup up
    deferred = reader._request_render_ahead(view, 1 if delta > 0 else -1)
    if not deferred:
        view.scroll = ns
        render_view(reader, view)
        return True
    if st.viewport_warm(ns, min(view.view_h, st.full_height)):
        view.scroll = ns
        render_view(reader, view)
    return True


def apply_pending_scroll(reader: Reader, view: PopupView) -> None:
    """Publish the newest desired viewport once its raw bands are fully warm."""
    st = view.state
    if st is None or view.desired_scroll == view.scroll:
        return
    view_h = min(view.view_h, st.full_height)
    if not st.viewport_warm(view.desired_scroll, view_h):
        return
    view.scroll = view.desired_scroll
    render_view(reader, view)


def scroll_tip(reader: Reader, delta: int) -> None:
    # route the wheel to whichever popup the cursor is over (nested sits on top)
    if reader._nest.rect is not None and in_rect(reader._nest.rect, *reader._last_mouse):
        scroll_view(reader, reader._nest, delta)
        return
    if scroll_view(reader, reader._tip_view, delta):
        reader.cancel_hover_deadline(LifecycleTimerKind.TOOLTIP_HIDE)  # scrolling keeps it up
        reader._hide_pending = False
        reader._scan_target = None  # content moved under the cursor → restart the scan dwell


# --- nested scanning: hover a word INSIDE the tooltip → its own popup ---------------------------


def scan_hit(reader: Reader, mx: float, my: float):
    """Which per-character scan cell of the base tooltip is under (mx, my)? Maps screen → panel
    coords (accounting for scroll) and returns the :class:`~saitenka.model.ScanBox`, or None. Hit-tests the
    panel actually DRAWN (crisp native when shown, else reference) so a hover lands on the right cell."""
    if reader._tip_state is None or reader._tip_rect is None:
        return None
    panel, s, scroll = hit_target(  # the on-screen panel + its scale/scroll
        reader._nest, reader._tip_state, reader._tip_scroll, reader._raster_scale, nested=False
    )
    if panel is None:
        return None
    sx, sy = reader._tip_xy
    px = (mx - sx) / s
    py = (my - sy) / s + scroll
    return panel.windowed.scan_hit(
        int(px), int(py)
    )  # windowed hit-test (retained per-block geometry)

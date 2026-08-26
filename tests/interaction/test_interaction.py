"""L1 interface tests — moves/clicks/wheel driven through the REAL mouse-pos / hit-test path.

These use the :class:`Driver` (tests/driver.py) so they read as interaction scripts and go through
``_hit`` / ``on_click`` / ``scan_hit`` — the same code a real cursor drives — rather than calling
``set_hover`` / ``_show_tooltip`` directly. (Live real-mpv input injection is L3: tests/test_live_mpv.py.)
"""

from __future__ import annotations

import pytest
from driver import Driver
from util import FakeIPC

from saitenka.app.anki import MineConfig
from saitenka.app.bindings import TIP_CLOSE_MSG
from saitenka.app.features.mining.mining_controller import MiningSpec, MiningTarget
from saitenka.app.session.controller import SessionController
from saitenka.panel import Definition, Entry


class _FakeDS:
    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        para = "とても長い定義の本文で" * 8  # tall + dense → scrollable, yields scan cells
        return Entry(
            headword=[tok.surface],
            reading=getattr(tok, "reading", "") or tok.surface,
            defs=[Definition(f"辞書{i}", [para]) for i in range(3)],
        )

    def has_term(self, *forms):
        # Only the individual subtitle words are terms — never a multi-token concatenation — so the
        # phrase-merge probe stays off and these geometry goldens keep their single-token hover.
        return any(f in {"本命", "を", "読む", "命", "ほんめい", "よむ"} for f in forms)


def _reader():
    # Pin tip_max_frac so the fixed hit-points and layout goldens below are independent of the product
    # default: a change to the default tooltip height must not silently move these interaction goldens.
    # osd = 1080p so the UI scale is 1.0 (REF_H) — the goldens capture the reference (unscaled) layout.
    r = SessionController(FakeIPC(), dict_set=_FakeDS(), tip_max_frac=0.5)
    r.osd = (1920, 1080)
    # the default SubtitleRenderer produces the real per-word boxes these goldens hit-test against
    r.set_subtitle("本命を読む")  # → 本命 / を / 読む
    return r


def _content_word(r) -> int:
    return next(i for i, t in enumerate(r.tokens) if r.profile_controller.tokenizer.is_content(t))


def _enable_mining(reader: SessionController) -> None:
    identity = reader.mining_controller.desired_spec.identity
    config = MineConfig()
    reader.mining_controller.select_mining_spec(
        MiningSpec(identity, {"deck": config.deck, "model": config.model})
    )
    assert reader.mining_controller.publish_mining_target(MiningTarget(identity, object(), config))
    reader.mining_controller.close_capability()


class _RecSpan:
    """Records span name, entry attributes, and post-hoc ``.set(...)`` attributes — a test double for
    the SpanSetter that ``traced`` yields, so a Driver-driven hover can be inspected without a real
    OTel provider (which is a once-per-process global)."""

    def __init__(self, name, attrs):
        self.name = name
        self.attrs = dict(attrs)

    def set(self, key, value):
        self.attrs[key] = value


def _patch_traced(monkeypatch, sink):
    import contextlib

    from saitenka import otel_metrics

    @contextlib.contextmanager
    def _record(name, **attrs):
        rec = _RecSpan(name, attrs)
        sink.append(rec)
        yield rec

    monkeypatch.setattr(otel_metrics, "traced", _record)


def test_hover_composite_is_traced_under_tooltip_show(monkeypatch):
    # The first-viewport composite (blit_panel → panel.viewport) is the bulk of a cold hover's
    # wall time and used to be untraced — invisible between the `render` and `upload` spans. It now
    # opens a `tip_compose` span on the same synchronous call path as tooltip_show.
    spans: list = []
    _patch_traced(monkeypatch, spans)
    r = _reader()
    ui = Driver(r)
    ui.move_to_word(_content_word(r))
    assert ui.tip_shown
    names = [s.name for s in spans]
    assert "tip_compose" in names  # the composite is now attributable
    assert "tooltip_show" in names  # and shares the hover's synchronous span stack
    assert "measure" in names  # the head walk+wrap, split out of tooltip_show self-time
    assert "mined" not in names  # card lookup belongs to the metadata worker, not this event span
    assert "pause_ipc" in names  # the pause-on-hover mpv round-trips, likewise


def test_tooltip_show_span_attributes_the_cold_hover(monkeypatch):
    # The coldest hover is diagnosable from its span: a build vs a hit (cold), the word length, the
    # panel height, and bands rastered on first paint — all low-cardinality, no raw word surface.
    spans: list = []
    _patch_traced(monkeypatch, spans)
    r = _reader()
    ui = Driver(r)
    ui.move_to_word(_content_word(r))
    show = next(s for s in spans if s.name == "tooltip_show")
    assert show.attrs["cold"] is True  # first hover of this word builds the panel
    assert show.attrs["chars"] >= 1
    assert show.attrs["full_h"] > 0
    # 0, not >=1: the first paint composes the cached/offline head and paints background where a
    # band is missing. Every tier is warm-only on the interactive thread now, so this attribute
    # reads "did the main thread violate that", and the answer must always be no.
    assert show.attrs["bands"] == 0


def test_subtitle_render_span_is_emitted(monkeypatch):
    # The subtitle-render path (every cue redraw; the `subtitle_render` bench signal) opens a span that
    # was produced but never asserted. Patch traced BEFORE set_subtitle so the render is captured.
    spans: list = []
    _patch_traced(monkeypatch, spans)
    r = SessionController(FakeIPC(), dict_set=_FakeDS(), tip_max_frac=0.5)
    r.osd = (1920, 1080)
    r.set_subtitle("本命を読む")
    assert "subtitle_render" in [s.name for s in spans]


def test_tip_compose_span_carries_a_kind(monkeypatch):
    # tip_compose's kind (base/nested/clicked) is what a report uses to separate perceived paints; the
    # span name was asserted but not this low-cardinality attribute — a kind regression passed silently.
    spans: list = []
    _patch_traced(monkeypatch, spans)
    r = _reader()
    Driver(r).move_to_word(_content_word(r))
    compose = next(s for s in spans if s.name == "tip_compose")
    assert compose.attrs.get("kind") == "base"  # a plain hover, no nesting, empty nav stack


def test_scroll_frame_span_attributes_bands_and_height(monkeypatch):
    spans: list = []
    r = _reader()
    ui = Driver(r)
    ui.move_to_word(_content_word(r))  # show first (real spans), THEN patch for the scroll
    _patch_traced(monkeypatch, spans)
    ui.wheel(1)  # one notch down
    scroll = next(s for s in spans if s.name == "scroll_frame")
    assert "bands" in scroll.attrs and scroll.attrs["bands"] >= 0
    assert scroll.attrs["full_h"] > 0


def test_move_over_word_shows_tooltip_and_switching_words():
    r = _reader()
    ui = Driver(r)
    i = _content_word(r)
    ui.move_to_word(i)
    assert ui.hover == i and ui.tip_shown, "moving the cursor onto a word must show its tooltip"
    j = next(k for k in range(len(r.tokens)) if k != i and r.tokens[k].is_content)
    ui.move_to_word(j)
    assert ui.hover == j, "resting on a different word must switch the tooltip to it"


def test_main_flow_renders_with_caches_disabled_even_when_files_exist(tmp_path, monkeypatch):
    # Opt-out of BOTH caches must beat use-when-available: a prebuilt file on disk is ignored, and the
    # live pipeline still renders the tooltip (the caches are pure accelerators, never load-bearing).
    from saitenka import mask_atlas
    from saitenka.app.config import ReaderOptions, TooltipOptions
    from saitenka.app.render_cache import RenderCache

    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path))
    RenderCache.open(tmp_path / "render-cache.sqlite", max_bytes=1 << 20).close()  # files DO exist
    atlas = mask_atlas.MaskAtlas.open(tmp_path / "mask-atlas.sqlite")
    assert atlas is not None
    atlas.close()

    r = SessionController(
        FakeIPC(),
        dict_set=_FakeDS(),
        options=ReaderOptions(
            tooltip=TooltipOptions(tip_max_frac=0.5, render_cache=False, mask_atlas=False)
        ),
    )
    r.osd = (1920, 1080)
    r.set_subtitle("本命を読む")
    assert r._render_cache() is None  # opt-out beats a present render-cache.sqlite
    assert r.session.render_cache.mask_atlas is None  # opt-out beats a present mask-atlas.sqlite

    ui = Driver(r)
    ui.move_to_word(_content_word(r))
    assert ui.tip_shown  # the live pipeline renders the tooltip with no cache help


def test_main_flow_renders_at_4k_without_caches():
    # Cache-free AND scale ≠ 1: the reference-render → display-upscale path must stand on its own.
    r = SessionController(FakeIPC(), dict_set=_FakeDS(), tip_max_frac=0.5)
    r.osd = (3840, 2160)  # 4K → tip_scale.display 2.0, no prebuilt caches (hermetic)
    r.set_subtitle("本命を読む")
    ui = Driver(r)
    ui.move_to_word(_content_word(r))
    assert ui.tip_shown and r.tip.view.rect is not None


def test_tooltip_keeps_lease_over_occluded_word(monkeypatch):
    """A tooltip drawn over another subtitle word keeps the lease: resting the cursor on the covered
    word (inside the tip rect) must NOT hijack the tooltip onto it. Regression for the two-line cue
    where the lower line's tooltip is drawn up over the upper line. `instant` zeroes hover_switch_delay,
    so the pre-fix code (which only *delayed* the hijack) would switch immediately."""
    r = _reader()
    ui = Driver(r)
    i = _content_word(r)
    ui.move_to_word(i)
    assert ui.hover == i and ui.tip_shown
    j = next(k for k in range(len(r.tokens)) if k != i and r.tokens[k].is_content)
    # simulate a subtitle word (j) sitting UNDER the shown tooltip: _hit reports j everywhere now
    monkeypatch.setattr(r, "_hit", lambda *_a: j)
    ui.move_into_tip(0.5, 0.5)  # cursor over the tip — and, per _hit, over word j beneath it
    assert ui.hover == i, (
        "cursor over the tooltip must keep its lease, not switch to the covered word"
    )
    ui.move(
        5, 5
    )  # off the tooltip (top-left) — the same _hit now DOES switch, proving the lease held it
    assert ui.hover == j, "off the tooltip, the word under the cursor is hovered normally"


def test_hover_over_phrase_start_spans_the_multi_token_term(monkeypatch):
    """Hovering the first token of a multi-token dictionary term (数ある-style) sets the hover span
    over the whole phrase — the underline covers both tokens, Yomitan-style longest-match. Moving off
    clears it."""
    r = _reader()  # subtitle 本命を読む → 本命 / を / 読む
    # pretend 本命を is a dictionary term so the phrase probe fires over tokens 0..1
    monkeypatch.setattr(r.profile_controller.dict_set, "has_term", lambda *forms: "本命を" in forms)
    ui = Driver(r)
    ui.move_to_word(0)
    assert r.interaction.hovered_word_meta.terms == ("本命を",)
    assert r.interaction.hovered_word_meta.span == (0, 2), (
        "the highlight must span the hovered token and its phrase partner"
    )
    ui.move_to_word(2)  # switch to 読む — a word with no following phrase term
    assert (
        r.interaction.hovered_word_meta.span is None and r.interaction.hovered_word_meta.terms == ()
    )


def test_phrase_reaches_panel_lookup(monkeypatch):
    """Regression: the hovered word's multi-token phrase terms must reach the entry lookup as
    ``extra_terms``. The build once gated extra_terms on a visual toggle, so お休み never stacked —
    hovering お showed the bare 御 instead."""
    r = SessionController(FakeIPC(), dict_set=_FakeDS(), tip_max_frac=0.5)
    r.osd = (1920, 1080)
    r.set_subtitle("本命を読む")
    monkeypatch.setattr(r.profile_controller.dict_set, "has_term", lambda *forms: "本命を" in forms)
    seen: dict[str, tuple] = {}
    real = r.profile_controller.dict_set.entry_for

    def record(tok, inflected=None, *, extra_terms=()):
        seen["extra"] = tuple(extra_terms)
        return real(tok, inflected, extra_terms=extra_terms)

    monkeypatch.setattr(r.profile_controller.dict_set, "entry_for", record)
    Driver(r).move_to_word(0)
    assert seen["extra"] == ("本命を",), "phrase must reach the panel lookup"


def test_move_off_words_does_not_hover():
    r = _reader()
    ui = Driver(r)
    ui.move(5, 5)  # top-left corner — no word there
    assert ui.hover == -1


def test_move_inside_tooltip_opens_nested_scan_popup():
    r = _reader()
    ui = Driver(r)  # instant → scan_delay 0
    ui.move_to_word(_content_word(r))
    assert ui.tip_shown
    ui.move_into_tip(0.5, 0.6)  # rest on a word INSIDE the tooltip body
    assert ui.nested_shown, "hovering a word inside the tooltip must open a nested scan popup"


def test_full_stress_chain_through_the_hit_test_path():
    # The --stress bench chain (show → scroll → nested → scroll → dismiss) validated as ONE accumulating
    # session through the REAL hit-test path (_hit / scan_hit). The correctness twin test_stress.py runs
    # the same chain but via direct _show_tooltip/show_nested entry points, bypassing hit-testing — this
    # is the only test that drives the whole chain the way a cursor does.
    r = _reader()
    ui = Driver(r)  # instant → no dwell to wait out
    i = _content_word(r)

    ui.move_to_word(i)
    assert ui.tip_shown, "hover shows the tooltip"

    ui.move_into_tip(0.5, 0.5)  # cursor over the tip so the wheel routes to it
    before = r.tip.view.scroll
    ui.wheel(1)
    assert r.tip.view.scroll > before, "wheel over the tip scrolls it (hit-test-routed)"

    ui.move_into_tip(0.5, 0.6)  # rest on an inner word → nested scan popup
    assert ui.nested_shown, "hovering inside the body opens the nested popup"

    ui.wheel(1)  # scroll while the nested popup is up — no crash, session stays coherent
    assert ui.tip_shown and ui.nested_shown

    j = next(k for k in range(len(r.tokens)) if k != i and r.tokens[k].is_content)
    ui.move_to_word(j)  # switch base word through hit-test → the nested popup is dropped
    assert ui.hover == j and not ui.nested_shown, "switching words drops the nested popup"

    ui.key(TIP_CLOSE_MSG)  # the Esc/close gesture tears the base tooltip down
    assert not ui.tip_shown, "closing dismisses the base tooltip — the whole session unwinds"


def test_empty_body_click_does_nothing(monkeypatch):
    r = _reader()
    _enable_mining(r)
    ui = Driver(r)
    ui.move_to_word(_content_word(r))
    assert ui.tip_shown
    events: list[str] = []
    monkeypatch.setattr(r._stateless_commands, "run", lambda _command: events.append("mine"))
    # click low in the body, away from the ⊕/🔊 header buttons
    x, y, w, h = r.tip.view.rect
    ui.move(x + w * 0.5, y + h - 6).click()
    assert events == [], "a click in an empty body area must not mine or speak"


def test_wheel_scrolls_the_tooltip():
    r = _reader()
    ui = Driver(r)
    ui.move_to_word(_content_word(r))
    ui.move_into_tip(0.5, 0.5)  # cursor over the tip so the wheel routes to it
    before = r.tip.view.scroll
    ui.wheel(1)  # one notch down
    assert r.tip.view.scroll > before, "wheeling over a scrollable tooltip must scroll it down"


def test_scroll_warms_native_bands_ahead_at_hidpi():
    # One-panel: a scroll must warm the NEXT native bands off the main thread (render-ahead), so continued
    # scrolling composites crisp without a synchronous raster (the old bug: only the first band was crisp).
    r = _reader()
    submitted = []
    r.tooltip_controller.render_ahead_submitter = lambda **kwargs: submitted.append(kwargs) or True
    r.osd = (3840, 2160)  # 4K → display scale 2.0, crisp active
    ui = Driver(r).move_to_word(_content_word(r))  # show the (tall, scrollable) tooltip
    assert r.tip.view.state.full_height > r.tip.view.view_h  # scrollable
    ui.wheel(1)
    assert r.tip.view.scroll > 0  # scrolled
    pending = r.tooltip_controller.render_ahead.pending
    assert pending is not None
    req = pending[1]
    assert (
        req is not None and req.scroll == r.tip.view.scroll and req.direction == 1
    )  # warm follows scroll


# --- L2: golden-pin the rendered bitmap that a hover produces ----------------------------------------


def _full_panel_image(panel):
    """The whole rendered panel as an image: measure every block, then composite the full height from
    the windowed engine (pixel-identical to a one-shot render_panel crop)."""
    from util import bgra_to_image

    panel.windowed.measure_to(panel.full_height)
    return bgra_to_image(panel.viewport(0, panel.full_height))


def test_golden_base_and_nested_render():
    """L2: pin the rendered BASE tooltip and NESTED popup bitmaps an interaction produces. A layout /
    geometry regression shows up as a golden diff. Both composite the whole panel from the windowed
    engine (== a render_panel crop)."""
    from util import assert_golden

    r = _reader()
    ui = Driver(r)
    ui.move_to_word(_content_word(r))
    assert r.hover_view().tip.state is not None
    assert_golden(_full_panel_image(r.tip.view.state), "interaction_base_tooltip.png", tol=3.0)

    ui.move_into_tip(0.5, 0.6)  # open the nested scan popup
    assert r.hover_view().nested.state is not None
    assert_golden(_full_panel_image(r.tip.nest.state), "interaction_nested_popup.png", tol=3.0)


def test_link_click_navigates_the_base_tooltip_in_place_with_back():
    """A cross-reference click replaces the base tooltip's content in place (Yomitan historyMode:new)
    and pushes the previous view; back restores it. No fragile floating popup, no auto-hide race."""
    from saitenka.app.features.tooltip import tooltip

    r = _reader()
    ui = Driver(r)
    ui.move_to_word(_content_word(r))
    assert ui.tip_shown
    base = r.tip.view.state

    tooltip.navigate_tip(r.tip_ports, r.panel_ports, "本命")  # what _click_tip routes a link to
    assert r.tip.view.state is not None and r.tip.view.state is not base
    assert len(r.interaction.tip_nav.back) == 1, "the previous view is pushed for back"
    assert ui.tip_shown, "the same base slot stays shown — an in-place navigation"

    assert tooltip.tip_back(r.tip_ports) is True
    assert r.tip.view.state is base and r.interaction.tip_nav.back == ()
    assert tooltip.tip_back(r.tip_ports) is False, "no history left → caller falls through to close"


def test_navigation_history_resets_when_hovering_a_new_subtitle_word():
    from saitenka.app.features.tooltip import tooltip

    r = _reader()
    ui = Driver(r)
    i = _content_word(r)
    ui.move_to_word(i)
    tooltip.navigate_tip(r.tip_ports, r.panel_ports, "本命")
    assert r.interaction.tip_nav.back

    j = next(k for k in range(len(r.tokens)) if k != i and r.tokens[k].is_content)
    ui.move_to_word(j)  # a newly hovered word abandons the link-navigation
    assert r.interaction.tip_nav.back == ()


def test_esc_steps_back_through_navigation_then_closes():
    r = _reader()
    ui = Driver(r)
    ui.move_to_word(_content_word(r))
    from saitenka.app.features.tooltip import tooltip

    tooltip.navigate_tip(r.tip_ports, r.panel_ports, "本命")
    tooltip.navigate_tip(r.tip_ports, r.panel_ports, "読む")
    assert len(r.interaction.tip_nav.back) == 2

    ui.key(TIP_CLOSE_MSG)
    assert len(r.interaction.tip_nav.back) == 1 and ui.tip_shown
    ui.key(TIP_CLOSE_MSG)
    assert r.interaction.tip_nav.back == () and ui.tip_shown
    ui.key(TIP_CLOSE_MSG)  # at the root → close
    assert not ui.tip_shown


def _targets(mx, my, *, inside=True, tip=None, nest=None, word=7):
    from saitenka.app.features.tooltip.tooltip import _hover_targets

    return _hover_targets(
        mx, my, inside=inside, tip_rect=tip, nest_rect=nest, hit=lambda _x, _y: word
    )


def test_a_popup_occludes_the_word_drawn_underneath_it():
    """The popups are drawn ON TOP of the subtitle, so a point inside one is not also a hit on the
    word beneath. The regression: a tooltip for the lower line of a two-line cue is placed up over
    the upper line, and without this the base hit-test still saw that covered word —
    `hover_switch_delay` only *delayed* the hijack rather than preventing it.
    """
    assert _targets(50, 50, tip=(0, 0, 100, 100)) == (-1, True, False)
    assert _targets(50, 50, nest=(0, 0, 100, 100)) == (-1, False, True)


def test_a_point_outside_every_popup_falls_through_to_the_word():
    assert _targets(500, 500, tip=(0, 0, 100, 100)) == (7, False, False)


def test_a_cursor_outside_the_video_is_over_nothing():
    """`inside` is False when mpv reports the pointer off the video surface. Nothing is hovered
    there — not even a popup whose rectangle the coordinates happen to fall inside."""
    assert _targets(50, 50, inside=False, tip=(0, 0, 100, 100)) == (-1, False, False)


def test_the_nested_popup_and_the_tooltip_can_both_claim_a_shared_point():
    """The nested popup is anchored over the base tooltip, so their rectangles overlap by design.
    Both read as hovered; which one acts is the caller's topmost-first decision, not this one's."""
    assert _targets(50, 50, tip=(0, 0, 100, 100), nest=(40, 40, 100, 100)) == (-1, True, True)


@pytest.mark.parametrize(
    ("mouse_pos", "expected"),
    [
        ({"x": 50, "y": 50}, True),
        ({"x": 500, "y": 50}, False),
        ({}, False),  # mpv answers without x/y before the pointer has entered the window
        (None, False),  # …and answers nothing at all when it has no surface yet
    ],
)
def test_a_panel_claims_only_a_pointer_it_can_actually_see(mouse_pos: object, *, expected: bool):
    """Missing coordinates must read as outside. Defaulting them to the origin would let a panel
    anchored at the top-left swallow a pointer that is not there — and the sidebar and picker are
    both anchored at an edge."""
    from saitenka.model import claims_pointer

    assert claims_pointer((0, 0, 100, 100), mouse_pos, open_=True) is expected


def test_a_closed_or_unplaced_panel_claims_nothing():
    from saitenka.model import claims_pointer

    assert claims_pointer((0, 0, 100, 100), {"x": 50, "y": 50}, open_=False) is False
    assert claims_pointer(None, {"x": 50, "y": 50}, open_=True) is False

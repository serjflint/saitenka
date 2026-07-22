"""Controller: live-run startup + hover hysteresis (Yomitan-style linger)."""

import time

import pytest

import overlay.app.controller as C
from overlay.app.controller import Reader
import functools


class FakeIPC:
    """Minimal mpv IPC stand-in; `props` feeds get_property, records all commands."""

    def __init__(self):
        self.events = []
        self.props = {}
        self.commands = []

    def command(self, *args):
        self.commands.append(args)
        if args and args[0] == "get_property":
            return {"data": self.props.get(args[1])}
        return {"data": None}

    def pump(self):
        pass

    def drain_events(self):
        return []


def test_keybinds_use_single_string_command():
    # mpv `keybind` needs (keybind, KEY, "script-message <msg>") — one command string, not split args.
    ipc = FakeIPC()
    Reader(ipc, anki=object())._register_keybinds()
    binds = [c for c in ipc.commands if c and c[0] == "keybind"]
    assert binds, "no keybinds registered"
    for c in binds:
        assert len(c) == 3, f"malformed keybind (must be 3 parts): {c}"
        assert c[2].startswith("script-message "), c
    keys = {c[1] for c in binds}
    assert {"a", "c", "WHEEL_UP", "WHEEL_DOWN", "MBTN_LEFT", "Ctrl+m"} <= keys


# --- Stage 4: subtitle navigation keys (Alt+←/→/↓, sub-delay) ------------------------------------


def test_sub_nav_keybinds_registered_with_single_string():
    """Alt+LEFT/RIGHT/DOWN and z/Z/x must be registered as keybind + single-string script-message
    (the known mpv gotcha: split args = key silently dead)."""
    ipc = FakeIPC()
    Reader(ipc)._register_keybinds()
    binds = {c[1]: c[2] for c in ipc.commands if c and c[0] == "keybind"}
    # Sub-nav keys must be registered
    assert "Alt+LEFT" in binds, f"Alt+LEFT not registered; binds={list(binds)}"
    assert "Alt+RIGHT" in binds
    assert "Alt+DOWN" in binds
    # Sub-delay keys
    assert "z" in binds
    assert "Z" in binds
    assert "x" in binds
    # All must use the one-string convention
    for key in ("Alt+LEFT", "Alt+RIGHT", "Alt+DOWN", "z", "Z", "x"):
        assert binds[key].startswith("script-message "), f"{key}: not script-message: {binds[key]}"


def test_sub_seek_prev_sends_ipc_command():
    """Receiving the sub-prev client-message must send sub-seek -1 to mpv IPC."""
    ipc = FakeIPC()
    r = Reader(ipc)
    r._register_keybinds()
    binds = {
        c[1]: c[2].split("script-message ", 1)[1] for c in ipc.commands if c and c[0] == "keybind"
    }
    sub_prev_msg = binds.get("Alt+LEFT")
    assert sub_prev_msg, "no Alt+LEFT keybind"
    # Dispatch the message and verify sub-seek -1 was sent
    r._handle(sub_prev_msg)
    assert ("sub-seek", "-1") in [(c[0], c[1]) for c in ipc.commands], (
        f"sub-seek -1 not sent; commands={ipc.commands}"
    )


def test_sub_seek_next_sends_ipc_command():
    """Receiving the sub-next client-message must send sub-seek 1 to mpv IPC."""
    ipc = FakeIPC()
    r = Reader(ipc)
    r._register_keybinds()
    binds = {
        c[1]: c[2].split("script-message ", 1)[1] for c in ipc.commands if c and c[0] == "keybind"
    }
    r._handle(binds["Alt+RIGHT"])
    assert ("sub-seek", "1") in [(c[0], c[1]) for c in ipc.commands]


def test_sub_seek_replay_sends_ipc_command():
    """Receiving the sub-replay client-message must send sub-seek 0 to mpv IPC."""
    ipc = FakeIPC()
    r = Reader(ipc)
    r._register_keybinds()
    binds = {
        c[1]: c[2].split("script-message ", 1)[1] for c in ipc.commands if c and c[0] == "keybind"
    }
    r._handle(binds["Alt+DOWN"])
    assert ("sub-seek", "0") in [(c[0], c[1]) for c in ipc.commands]


def test_sub_nav_config_knobs_respected():
    """Custom sub_prev_key/sub_next_key/sub_replay_key config knobs must be registered."""
    ipc = FakeIPC()
    r = Reader(ipc, sub_prev_key="Alt+a", sub_next_key="Alt+d", sub_replay_key="Alt+s")
    r._register_keybinds()
    binds = {c[1] for c in ipc.commands if c and c[0] == "keybind"}
    assert "Alt+a" in binds
    assert "Alt+d" in binds
    assert "Alt+s" in binds


# --- #5: instant subtitle navigation via the parsed cue index -----------------------------------

_NAV_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nいち\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nに\n\n"
    "3\n00:00:10,000 --> 00:00:12,000\nさん\n"
)


def _reader_with_index(monkeypatch):
    from overlay.app.sub_index import SubIndex, parse_srt

    ipc = FakeIPC()
    r = Reader(ipc)
    r.osd = (1280, 720)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)  # skip the raster; assert state only
    r._sub_index = SubIndex(parse_srt(_NAV_SRT))
    r._register_keybinds()
    return r, ipc


def _msg_for(ipc, key):
    binds = {
        c[1]: c[2].split("script-message ", 1)[1] for c in ipc.commands if c and c[0] == "keybind"
    }
    return binds[key]


def test_sub_nav_renders_target_line_instantly_and_still_seeks(monkeypatch):
    """Next must render the following cue's text in the overlay right away AND still issue the real
    sub-seek so the video catches up behind it."""
    r, ipc = _reader_with_index(monkeypatch)
    ipc.props["sub-text"] = "いち"
    r.set_subtitle("いち")  # currently on cue 1
    ipc.props["sub-start"] = 1.0
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.sub_text == "に"  # cue 2 rendered instantly, before any seek settles
    assert ("sub-seek", "1") in [(c[0], c[1]) for c in ipc.commands]  # video seek still fired


def test_sub_nav_prev_and_replay(monkeypatch):
    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("に")  # cue 2
    ipc.props["sub-start"] = 4.0
    r._handle(_msg_for(ipc, "Alt+LEFT"))
    assert r.sub_text == "いち" and ("sub-seek", "-1") in [(c[0], c[1]) for c in ipc.commands]
    r.set_subtitle("に")
    ipc.props["sub-start"] = 4.0
    r._handle(_msg_for(ipc, "Alt+DOWN"))  # replay → same cue
    assert r.sub_text == "に"


def test_sub_nav_chains_forward_with_stale_position(monkeypatch):
    """Rapid next/next while the seek is still in flight (sub-start/time-pos stale) must keep
    stepping forward, resolved by the rendered text + the _nav_idx hint."""
    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("いち")
    ipc.props["sub-start"] = 1.0  # stale for the whole burst (video hasn't caught up)
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.sub_text == "に"
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.sub_text == "さん"  # advanced past cue 2 despite the stale sub-start


def test_sub_nav_from_a_gap_opens_the_upcoming_cue(monkeypatch):
    """Navigating NEXT while no sub is on screen (a gap) must land ON the upcoming cue — matching
    mpv's sub-seek 1 — not skip past it."""
    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("")  # nothing showing (between cues)
    ipc.props["time-pos"] = 8.5  # gap before cue 3 (starts at 10.0)
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.sub_text == "さん"  # cue 3, the upcoming one — not skipped


def test_sub_nav_without_index_only_seeks(monkeypatch):
    ipc = FakeIPC()
    r = Reader(ipc)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_subtitle("いち")
    r._register_keybinds()
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.sub_text == "いち"  # no index → overlay unchanged; mpv drives it via the seek
    assert ("sub-seek", "1") in [(c[0], c[1]) for c in ipc.commands]


def test_settle_guard_swallows_transient_empty_then_reconciles(monkeypatch):
    """After a nav render, an empty sub-text within the settle window is ignored (no blank flash);
    a non-empty mpv value (source of truth) reconciles and disarms the guard."""
    r, ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("いち")
    ipc.props["sub-start"] = 1.0
    r._handle(_msg_for(ipc, "Alt+RIGHT"))
    assert r.sub_text == "に"  # rendered target
    r._reconcile_sub_text("")  # mpv's mid-seek blank
    assert r.sub_text == "に"  # swallowed — overlay didn't flash to nothing
    r._reconcile_sub_text("に")  # mpv settled on the matching cue
    assert r.sub_text == "に"
    r._reconcile_sub_text("さん")  # a genuine later change still adopts mpv's truth
    assert r.sub_text == "さん"


def test_settle_guard_expires_and_adopts_empty(monkeypatch):
    """Outside the settle window an empty sub-text is honoured (a real gap between cues clears it)."""
    r, _ipc = _reader_with_index(monkeypatch)
    r.set_subtitle("に")
    r._sub_settle_until = 0.0  # window already expired
    r._reconcile_sub_text("")
    assert r.sub_text == ""


def test_reader_has_subtitle_state_before_any_cue():
    r = Reader(FakeIPC())
    assert r.sub_text == "" and r.tokens == [] and r.hover == -1


def test_poll_once_before_subtitle_does_not_raise():
    assert Reader(FakeIPC()).poll_once() is True


def test_word_switch_needs_dwell_but_first_open_is_instant(monkeypatch):
    ipc = FakeIPC()
    r = Reader(ipc)
    r.tokens = ["a", "b"]
    seen = []
    monkeypatch.setattr(r, "set_hover", lambda i: (seen.append(i), setattr(r, "hover", i)))
    # word 0 near (5,5), word 1 near (5,50); tooltip is off elsewhere
    monkeypatch.setattr(r, "_hit", lambda x, y: 0 if y < 10 else (1 if y < 60 else -1))
    clock = [1000.0]
    monkeypatch.setattr(C.time, "monotonic", lambda: clock[0])

    def mouse(x, y):
        ipc.props["mouse-pos"] = {"hover": True, "x": x, "y": y}
        r._update_hover()

    mouse(5, 5)  # first hover → opens INSTANTLY (no dwell)
    assert seen == [0] and r.hover == 0
    mouse(5, 50)  # transit onto word 1 en route to the tooltip
    assert r.hover == 0  # …does NOT switch yet (dwell not elapsed)
    clock[0] += 0.05
    mouse(5, 50)
    assert r.hover == 0  # still within the dwell window
    clock[0] += r.hover_switch_delay  # rest long enough on word 1
    mouse(5, 50)
    assert r.hover == 1  # …now it switches


def test_transit_over_word_does_not_switch(monkeypatch):
    # dragging up to the tooltip: brush word 1, then reach the tooltip — tooltip must stay on word 0
    ipc = FakeIPC()
    r = Reader(ipc)
    r.tokens = ["a", "b"]
    r._tip_rect = (100, 100, 80, 60)
    monkeypatch.setattr(r, "set_hover", lambda i: setattr(r, "hover", i))
    monkeypatch.setattr(r, "_hit", lambda x, y: 0 if y < 10 else (1 if y < 60 else -1))
    clock = [1000.0]
    monkeypatch.setattr(C.time, "monotonic", lambda: clock[0])

    def mouse(x, y):
        ipc.props["mouse-pos"] = {"hover": True, "x": x, "y": y}
        r._update_hover()

    mouse(5, 5)  # tooltip on word 0
    clock[0] += 0.03
    mouse(5, 50)  # brush word 1 briefly (transit)
    clock[0] += 0.03
    mouse(130, 130)  # arrive at the tooltip
    assert r.hover == 0 and r._hide_at == 0.0  # never hijacked; tooltip alive


def test_hover_lingers_and_keeps_alive_over_tooltip(monkeypatch):
    ipc = FakeIPC()
    r = Reader(ipc)
    r.tokens = ["x"]
    r._tip_rect = (100, 100, 60, 40)
    seen = []
    monkeypatch.setattr(r, "set_hover", lambda i: (seen.append(i), setattr(r, "hover", i)))
    monkeypatch.setattr(r, "_hit", lambda x, y: 0 if (x < 10 and y < 10) else -1)
    clock = [1000.0]
    monkeypatch.setattr(C.time, "monotonic", lambda: clock[0])

    def mouse(x, y):
        ipc.props["mouse-pos"] = {"hover": True, "x": x, "y": y}
        r._update_hover()

    mouse(5, 5)  # on the word → hovered, no pending hide
    assert r.hover == 0 and r._hide_at == 0.0

    mouse(300, 300)  # left the word → schedule hide, still shown
    assert r.hover == 0 and r._hide_at == 1000.0 + C.HIDE_DELAY

    mouse(120, 120)  # reached the tooltip in time → stays alive
    assert r._hide_at == 0.0 and r.hover == 0

    mouse(300, 300)  # leave everything → reschedule hide
    assert r._hide_at > 0.0
    clock[0] += C.HIDE_DELAY + 0.1  # …and let it elapse
    mouse(300, 300)
    assert seen[-1] == -1  # hidden only after the delay


def test_tooltip_capped_and_inside_safe_area():
    from overlay.app.subtitles import WordBox
    from overlay.app.tokenize import tokenize

    r = Reader(FakeIPC(), tip_max_frac=0.5)
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = tokenize("本")
    r.boxes = [WordBox(0, 600, 660, 40, 40)]  # word near the bottom (like a subtitle)
    r._show_tooltip(0)

    margin = max(16, round(720 * 0.05))
    assert r._tip_view_h <= round(720 * 0.5)  # height capped
    _tx, ty = r._tip_xy
    assert ty >= margin  # top clears the header margin
    assert ty + r._tip_view_h <= 720 - margin  # bottom stays inside the window


def test_panel_cache_avoids_rerender_on_revisit():
    from overlay.app.subtitles import WordBox
    from overlay.app.tokenize import Token
    from overlay.panel import Definition, Entry

    calls = []

    class FakeDS:
        def entry_for(self, tok, inflected=None):
            calls.append(tok.surface)
            return Entry(headword=tok.surface, defs=[Definition("D", ["x"])])

    r = Reader(FakeIPC(), dict_set=FakeDS())
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [
        Token("本命", "本命", "ほんめい", "名詞", 0, 2),
        Token("読む", "読む", "よむ", "動詞", 2, 4),
    ]
    r.boxes = [WordBox(0, 100, 100, 40, 40), WordBox(1, 200, 100, 40, 40)]
    r._show_tooltip(0)
    r._show_tooltip(1)
    r._show_tooltip(0)  # revisit → served from cache
    assert calls == ["本命", "読む"]  # each word rendered once, not on every hover


class _FakeDS:
    def entry_for(self, tok, inflected=None):
        from overlay.panel import Definition, Entry

        return Entry(headword=tok.surface, defs=[Definition("D", ["x"])])


def _reader_with_word(ipc):
    from overlay.app.subtitles import WordBox
    from overlay.app.tokenize import Token

    r = Reader(ipc, dict_set=_FakeDS(), pause_on_tooltip=True)
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 100, 40, 40)]
    return r


def test_pause_on_tooltip_pauses_then_resumes(monkeypatch):
    ipc = FakeIPC()
    ipc.props["pause"] = False
    r = _reader_with_word(ipc)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)  # keep our boxes
    r._show_tooltip(0)  # tooltip shown → pause
    assert r._paused_by_tip and ("set_property", "pause", True) in ipc.commands
    r.hover = 0
    r.set_hover(-1)  # tooltip hidden → resume
    assert not r._paused_by_tip and ("set_property", "pause", False) in ipc.commands


def test_pause_on_tooltip_respects_manual_pause():
    ipc = FakeIPC()
    ipc.props["pause"] = True  # user already paused
    r = _reader_with_word(ipc)
    r._show_tooltip(0)
    assert not r._paused_by_tip  # never took ownership → won't resume


def test_prefetch_queues_content_words_only_when_paused():
    ipc = FakeIPC()
    ipc.props["pause"] = True
    r = _reader_with_word(ipc)
    r.sub_text = "本命"
    r._update_prefetch()
    queued = []
    while not r._prefetch_q.empty():
        queued.append(r._prefetch_q.get().token.surface)  # typed PrefetchItem (Stage 8b)
    assert queued == ["本命"]  # the content word got queued


def test_prefetch_cancels_generation_when_resumed():
    ipc = FakeIPC()
    ipc.props["pause"] = False  # playing
    r = _reader_with_word(ipc)
    g0 = r._prefetch_gen
    r._update_prefetch()
    assert r._prefetch_gen == g0 + 1  # bumped → in-flight work is invalidated
    assert r._prefetch_q.empty()  # nothing queued while playing


def test_prefetch_worker_warms_cache_then_close_joins():
    ipc = FakeIPC()
    ipc.props["pause"] = True
    r = _reader_with_word(ipc)
    r.start_prefetch()
    try:
        r._update_prefetch()  # queue 本命 for the worker
        # PanelKey(lemma, surface, reading, inflected, width, anki_ok, mined); no anki → anki_ok False.
        # A plain tuple of the same values matches the PanelKey dict key (NamedTuple compares as a tuple).
        key = ("本命", "本命", "ほんめい", "本命", r.tip_width, False, False)
        for _ in range(300):
            if key in r._panel_cache:
                break
            time.sleep(0.01)
        assert key in r._panel_cache  # prefetched in the background, no hover needed
    finally:
        r.close()
    assert not any(
        t.is_alive() for t in r._prefetch_threads
    )  # close() stopped + joined the workers


def test_hover_off_window_still_lingers(monkeypatch):
    ipc = FakeIPC()
    r = Reader(ipc)
    r.tokens = ["x"]
    r.hover = 0
    monkeypatch.setattr(r, "set_hover", lambda i: setattr(r, "hover", i))
    ipc.props["mouse-pos"] = {"hover": False, "x": -1, "y": -1}  # cursor left the window
    r._update_hover()
    assert r.hover == 0 and r._hide_at > 0.0  # scheduled, not instant


class _TallDS:
    """A dictionary entry far taller than one viewport — several long def bodies."""

    def entry_for(self, tok, inflected=None):
        from overlay.panel import Definition, Entry

        para = "とても長い定義の本文でありスクロールが必要になるほど縦に伸びます。" * 6
        return Entry(
            headword=tok.surface,
            reading="ほんめい",
            defs=[Definition(f"辞書{i}", [para]) for i in range(6)],
        )


def _tall_reader(ipc):
    from overlay.app.subtitles import WordBox
    from overlay.app.tokenize import Token

    r = Reader(ipc, dict_set=_TallDS())
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    return r


def test_show_tooltip_finishes_synchronously_without_worker():
    # No prefetch worker running → the panel must be rendered whole, never left partial.
    r = _tall_reader(FakeIPC())
    r._show_tooltip(0)
    assert r._tip_state.complete
    assert r._finish_q.empty()
    assert r._tip_bgra.shape[0] == r._tip_state.image.height


def test_show_tooltip_defers_tail_when_worker_available(monkeypatch):
    # With a worker available, first paint renders only the head that fills the viewport; the rest is
    # queued for the worker and swapped in on completion.
    r = _tall_reader(FakeIPC())
    monkeypatch.setattr(r, "_finish_available", lambda: True)  # pretend a prefetch worker exists
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r._show_tooltip(0)
    st = r._tip_state
    assert not st.complete  # tall entry → head only
    head_h = st.image.height
    assert head_h >= r._tip_cap()  # …but the viewport is fully covered
    assert r._finish_q.qsize() == 1  # the tail was queued for a worker

    # run the finish job like a worker would, then let the poll loop refresh the view
    job = r._finish_q.get()  # typed FinishItem (Stage 8b)
    job.panel.finish()
    r._tip_dirty = True
    assert st.complete and st.image.height > head_h
    r.poll_once()
    assert r._tip_bgra.shape[0] == st.image.height  # the full, taller panel is now uploaded


def _click_center_of_add_button(r, ipc):
    from overlay.panel import header_add_rect

    px, py, pw, ph = header_add_rect(r.tip_width, top_reserve=r._tip_reserve())
    sx, sy = r._tip_xy
    cx = sx + px + pw / 2
    cy = sy + (py - r._tip_scroll) + ph / 2
    ipc.props["mouse-pos"] = {"hover": True, "x": cx, "y": cy}
    return cx, cy


def test_header_add_button_click_mines_hovered_word(monkeypatch):
    ipc = FakeIPC()
    r = _tall_reader(ipc)
    r.anki = object()  # mining available → ⊕ drawn and hit-testable
    r.hover = 0
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r._show_tooltip(0)
    events = []
    monkeypatch.setattr(r, "mine_current", lambda: events.append("mine"))
    monkeypatch.setattr(r, "speak_hovered", lambda: events.append("speak"))
    _click_center_of_add_button(r, ipc)
    r.on_click()
    assert events == ["mine"]  # ⊕ mined; did not fall through to TTS


def test_tooltip_empty_click_does_nothing(monkeypatch):
    # clicking an empty area of the card must NOT play audio (the 🔊 button is the only play affordance)
    ipc = FakeIPC()
    r = _tall_reader(ipc)
    r.anki = object()
    r.hover = 0
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r._show_tooltip(0)
    events = []
    monkeypatch.setattr(r, "mine_current", lambda: events.append("mine"))
    monkeypatch.setattr(r, "speak_hovered", lambda: events.append("speak"))
    tx, ty, tw, th = r._tip_rect
    ipc.props["mouse-pos"] = {"hover": True, "x": tx + tw / 2, "y": ty + th - 5}  # low in the body
    r.on_click()
    assert events == []  # neither speaks nor mines


def test_tooltip_speaker_button_click_speaks(monkeypatch):
    from overlay.panel import header_speaker_rect

    ipc = FakeIPC()
    r = _tall_reader(ipc)
    r.hover = 0
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r._show_tooltip(0)
    events = []
    monkeypatch.setattr(r, "speak_hovered", lambda: events.append("speak"))
    px, py, pw, ph = header_speaker_rect(r.tip_width, top_reserve=r._tip_reserve())
    sx, sy = r._tip_xy
    ipc.props["mouse-pos"] = {
        "hover": True,
        "x": sx + px + pw / 2,
        "y": sy + (py - r._tip_scroll) + ph / 2,
    }
    r.on_click()
    assert events == ["speak"]  # only the 🔊 button plays audio


def test_header_add_button_absent_without_anki(monkeypatch):
    ipc = FakeIPC()
    r = _tall_reader(ipc)  # no anki
    r.hover = 0
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r._show_tooltip(0)
    cx, cy = _click_center_of_add_button(r, ipc)
    assert not r._hit_header_add(cx, cy)  # no ⊕ button when mining is unavailable


# --- R4: nested scanning (hover a word inside the tooltip) ------------------------------------------


class _ScanDS:
    """A dictionary entry with a CJK (monolingual) body, so the panel carries scan hitboxes."""

    def entry_for(self, tok, inflected=None):
        from overlay.panel import Definition, Entry

        return Entry(
            headword=tok.surface,
            reading="ほんめい",
            defs=[Definition("MonoC", ["追いかけること。また、その人。"])],
        )


def _scan_reader(ipc):
    from overlay.app.subtitles import WordBox
    from overlay.app.tokenize import Token

    r = Reader(ipc, dict_set=_ScanDS(), scan_delay=0.0)  # open immediately; dwell has its own tests
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    return r


def _hover_first_scan_cell(r, ipc):
    """Point the cursor at the first scan cell of the base tooltip; return the ScanBox."""
    sb = r._tip_state.lazy.scan_boxes[0]
    sx, sy = r._tip_xy
    ipc.props["mouse-pos"] = {
        "hover": True,
        "x": sx + sb.x + sb.w / 2,
        "y": sy + (sb.y - r._tip_scroll) + sb.h / 2,
    }
    return sb


def test_scan_hit_maps_cursor_to_inner_char(monkeypatch):
    r = _scan_reader(FakeIPC())
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.hover = 0
    r._show_tooltip(0)
    boxes = r._tip_state.lazy.scan_boxes
    assert boxes
    sb = boxes[0]
    sx, sy = r._tip_xy
    hit = r._scan_hit(sx + sb.x + sb.w / 2, sy + sb.y + sb.h / 2)
    assert hit is not None and hit.text.startswith("追")


def test_hover_inner_word_opens_nested_popup(monkeypatch):
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)  # base tooltip on the subtitle word
    _hover_first_scan_cell(r, ipc)
    r._update_hover()
    assert r._nest.state is not None  # a nested popup opened…
    assert r._nest.rect is not None
    assert r._nest.word.startswith("追")  # …for the inner word under the cursor


def test_nested_scan_waits_for_dwell(monkeypatch):
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    r.scan_delay = 0.25  # require the cursor to settle before opening
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    clock = [1000.0]
    monkeypatch.setattr(C.time, "monotonic", lambda: clock[0])
    r.set_hover(0)
    _hover_first_scan_cell(r, ipc)
    r._update_hover()
    assert r._nest.state is None  # just arrived — nothing opens yet
    clock[0] += 0.1
    r._update_hover()
    assert r._nest.state is None  # still settling
    clock[0] += 0.2  # past the dwell now
    r._update_hover()
    assert r._nest.state is not None  # opened only after the cursor rested


def test_nested_scan_dwell_restarts_when_cursor_moves(monkeypatch):
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    r.scan_delay = 0.25
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    clock = [1000.0]
    monkeypatch.setattr(C.time, "monotonic", lambda: clock[0])
    r.set_hover(0)
    boxes = r._tip_state.lazy.scan_boxes
    sx, sy = r._tip_xy

    def hover(sb):
        ipc.props["mouse-pos"] = {
            "hover": True,
            "x": sx + sb.x + sb.w / 2,
            "y": sy + sb.y + sb.h / 2,
        }

    hover(boxes[0])
    r._update_hover()
    assert r._scan_target == boxes[0].text and r._scan_since == 1000.0
    clock[0] += 0.2  # drift to a different cell before the dwell elapses
    hover(boxes[1])
    r._update_hover()
    assert r._scan_target == boxes[1].text and r._scan_since == 1000.2  # timer restarted
    assert r._nest.state is None  # no popup fired mid-drift


def test_switch_base_word_drops_nested(monkeypatch):
    from overlay.app.subtitles import WordBox
    from overlay.app.tokenize import Token

    ipc = FakeIPC()
    r = _scan_reader(ipc)
    r.tokens = [
        Token("本命", "本命", "ほんめい", "名詞", 0, 2),
        Token("読む", "読む", "よむ", "動詞", 2, 4),
    ]
    r.boxes = [WordBox(0, 100, 300, 40, 40), WordBox(1, 500, 300, 40, 40)]
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    _hover_first_scan_cell(r, ipc)
    r._update_hover()
    assert r._nest.state is not None
    r.set_hover(1)  # move to a different subtitle word
    assert r._nest.state is None  # the stale scan popup is dropped


def test_nested_lingers_then_dismisses(monkeypatch):
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    clock = [1000.0]
    monkeypatch.setattr(C.time, "monotonic", lambda: clock[0])
    r.set_hover(0)
    _hover_first_scan_cell(r, ipc)
    r._update_hover()
    assert r._nest.state is not None
    ipc.props["mouse-pos"] = {"hover": True, "x": 5, "y": 5}  # leave the whole stack
    r._update_hover()
    assert r._nest.hide_at > 0  # scheduled, not instant
    clock[0] += C.HIDE_DELAY + 0.1
    r._update_hover()
    assert r._nest.state is None  # dismissed after the linger


def test_nested_add_button_mines_inner_word(monkeypatch):
    from overlay.panel import header_add_rect

    ipc = FakeIPC()
    r = _scan_reader(ipc)
    r.anki = object()
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    _hover_first_scan_cell(r, ipc)
    r._update_hover()
    assert r._nest.token is not None
    mined = []
    monkeypatch.setattr(r, "_mine_token", lambda tok: mined.append(tok.surface))
    px, py, pw, ph = header_add_rect(r.tip_width, top_reserve=r._tip_reserve())
    nx, ny = r._nest.xy
    ipc.props["mouse-pos"] = {
        "hover": True,
        "x": nx + px + pw / 2,
        "y": ny + (py - r._nest.scroll) + ph / 2,
    }
    r.on_click()
    assert mined and mined[0].startswith("追")  # ⊕ mined the scanned inner word


# --- R4b: clickable cross-reference links (open the target term in the nested popup) ---------------


class _LinkDS:
    """A dictionary entry whose def body contains an internal <a> cross-reference to 見る."""

    def entry_for(self, tok, inflected=None):
        from overlay.panel import Definition, Entry

        body = ["同義語は", {"tag": "a", "href": "?query=見る", "content": "見る"}, "。"]
        return Entry(headword=tok.surface, reading="みる", defs=[Definition("MonoA", body)])


def _link_reader(ipc):
    from overlay.app.subtitles import WordBox
    from overlay.app.tokenize import Token

    r = Reader(ipc, dict_set=_LinkDS())
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [Token("観る", "観る", "みる", "動詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    return r


def _point_at_link(r, ipc):
    lb = r._tip_state.lazy.link_boxes[0]
    sx, sy = r._tip_xy
    ipc.props["mouse-pos"] = {
        "hover": True,
        "x": sx + lb.x + lb.w / 2,
        "y": sy + (lb.y - r._tip_scroll) + lb.h / 2,
    }
    return lb


def test_click_cross_reference_opens_target_in_nested(monkeypatch):
    ipc = FakeIPC()
    r = _link_reader(ipc)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    assert r._tip_state.lazy.link_boxes  # the def body exposed a clickable link
    _point_at_link(r, ipc)
    r.on_click()
    assert r._nest.state is not None  # a nested popup opened…
    assert r._nest.word.startswith("見")  # …for the link's target term (見る)


class _WildcardDS:
    """A def body whose cross-reference is a WILDCARD, plus a search() that returns clickable results."""

    def entry_for(self, tok, inflected=None):
        from overlay.panel import Definition, Entry

        body = ["類語は", {"tag": "a", "href": "?query=食べ*", "content": "食べ…"}, "など。"]
        return Entry(headword=tok.surface, reading="みる", defs=[Definition("MonoA", body)])

    def search(self, pattern, limit=30):
        from overlay.panel import Definition, Entry

        li = [
            {"tag": "li", "content": [{"tag": "a", "href": "?query=食べる", "content": "食べる"}]}
        ]
        return Entry(
            headword=[pattern], defs=[Definition(f"検索 {pattern}", [{"tag": "ul", "content": li}])]
        )


def test_click_wildcard_link_opens_search_popup(monkeypatch):
    from overlay.app.subtitles import WordBox
    from overlay.app.tokenize import Token

    ipc = FakeIPC()
    r = Reader(ipc, dict_set=_WildcardDS())
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [Token("観る", "観る", "みる", "動詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    lb = r._tip_state.lazy.link_boxes[0]
    assert "*" in lb.query  # the cross-ref is a wildcard pattern
    sx, sy = r._tip_xy
    ipc.props["mouse-pos"] = {
        "hover": True,
        "x": sx + lb.x + lb.w / 2,
        "y": sy + (lb.y - r._tip_scroll) + lb.h / 2,
    }
    r.on_click()
    assert r._nest.state is not None and r._nest.word == "食べ*"  # opened a search-results popup
    assert r._nest.token is None  # results aren't one word → ⊕ mine disabled
    assert (
        r._nest.state.lazy.link_boxes[0].query == "食べる"
    )  # each result drills into an exact term


def test_external_link_is_not_a_clickable_region(monkeypatch):
    # an external source link (Bilingual 'JMdict') is styled blue but captures NO LinkBox → inert
    from overlay.app.subtitles import WordBox
    from overlay.app.tokenize import Token

    ipc = FakeIPC()

    class _ExternalDS:
        def entry_for(self, tok, inflected=None):
            from overlay.panel import Definition, Entry

            body = [
                "出典 ",
                {"tag": "a", "href": "https://www.edrdg.org/x?q=1", "content": "JMdict"},
            ]
            return Entry(headword=tok.surface, reading="みる", defs=[Definition("Bilingual", body)])

    r = Reader(ipc, dict_set=_ExternalDS())
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [Token("観る", "観る", "みる", "動詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    assert r._tip_state.lazy.link_boxes == []  # external link → no clickable region


def test_nested_popup_shrinks_to_stay_above_inner_word():
    r = Reader(FakeIPC())
    r.osd = (1280, 720)
    margin = max(16, round(720 * 0.05))
    # a TALL entry anchored to an inner word in the upper-middle: default would drop below (more room
    # below), but the nested popup shrinks its viewport to the room above and stays ABOVE the word.
    wy = 220
    view_h = r._nested_view_h(full_h=800, wy=wy)
    above_room = wy - C.TIP_GAP - margin
    assert view_h == above_room  # shrunk to fit above
    _, ty = r._place_panel(300, 100, wy, 40, view_h)
    assert ty + view_h <= wy  # …so it sits entirely above the inner word


def test_nested_popup_drops_below_when_no_room_above():
    r = Reader(FakeIPC())
    r.osd = (1280, 720)
    wy = 90  # inner word near the very top → can't fit above
    view_h = r._nested_view_h(full_h=800, wy=wy)
    _, ty = r._place_panel(300, 100, wy, 40, view_h)
    assert ty >= wy  # falls back to below (safe)


def test_hover_over_link_does_not_open_scan_popup(monkeypatch):
    # links are click-to-open, not hover-scan → scrolling/reading over a cross-ref doesn't clutter
    ipc = FakeIPC()
    r = _link_reader(ipc)
    r.scan_delay = 0.0  # would fire immediately if not suppressed
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    _point_at_link(r, ipc)  # cursor on the link cell
    r._update_hover()
    assert r._nest.state is None  # hover did NOT open a scan popup over the link
    r.on_click()  # …but a click still opens it
    assert r._nest.state is not None and r._nest.word.startswith("見")


def test_scroll_resets_scan_dwell(monkeypatch):
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    r.scan_delay = 0.25
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    _hover_first_scan_cell(r, ipc)
    r._update_hover()
    assert r._scan_target is not None  # a scan target is settling
    r._tip_view_h = 20  # make the panel scrollable
    r._scroll_tip(20)  # scrolling the panel…
    assert r._scan_target is None  # …restarts the dwell so no popup fires mid-scroll


def test_click_link_does_not_mine_or_speak(monkeypatch):
    # a link click must open the target, not fall through to mining / TTS
    ipc = FakeIPC()
    r = _link_reader(ipc)
    r.anki = object()
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    events = []
    monkeypatch.setattr(r, "mine_current", lambda: events.append("mine"))
    monkeypatch.setattr(r, "speak_hovered", lambda: events.append("speak"))
    _point_at_link(r, ipc)
    r.on_click()
    assert events == [] and r._nest.state is not None


# --- copy: Shift+C (whole line) and right-click (word under cursor) + highlight flash ---------------


def test_copy_line_copies_all_lines(monkeypatch):
    from overlay.app.tokenize import Token

    r = _scan_reader(FakeIPC())
    r.lines = [
        [Token("本命", "本命", "ほんめい", "名詞", 0, 2)],
        [Token("読む", "読む", "よむ", "動詞", 0, 2)],
    ]
    got = []
    monkeypatch.setattr(C, "copy_clipboard", lambda s: got.append(s))
    r.copy_line()
    assert got == ["本命\n読む"]  # the whole cue, line by line


def test_right_click_copies_hovered_word_and_flashes(monkeypatch):
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    got = []
    monkeypatch.setattr(C, "copy_clipboard", lambda s: got.append(s))
    tx, ty, tw, _th = r._tip_rect
    ipc.props["mouse-pos"] = {
        "hover": True,
        "x": tx + tw / 2,
        "y": ty + 5,
    }  # header, not a scan cell
    r.copy_click()
    assert got and "本命" in got[0]  # copied the hovered word
    assert r._flash_oid == C.TIP_ID and r._flash_until > 0


def test_right_click_on_nested_copies_inner_word(monkeypatch):
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    _hover_first_scan_cell(r, ipc)
    r._update_hover()  # open the nested popup
    got = []
    monkeypatch.setattr(C, "copy_clipboard", lambda s: got.append(s))
    nx, ny, nw, nh = r._nest.rect
    ipc.props["mouse-pos"] = {"hover": True, "x": nx + nw / 2, "y": ny + nh / 2}
    r.copy_click()
    assert got and got[0].startswith("追")  # copied the inner scanned word
    assert r._flash_oid == C.NESTED_ID


def test_flash_border_drawn_then_cleared(monkeypatch):
    import numpy as np

    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    clock = [1000.0]
    monkeypatch.setattr(C.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(C, "copy_clipboard", lambda s: None)
    r.set_hover(0)
    shots = []
    monkeypatch.setattr(r.ov, "show_bgra", lambda bgra, x, y, oid: shots.append((oid, bgra.copy())))
    tx, ty, tw, _th = r._tip_rect
    ipc.props["mouse-pos"] = {"hover": True, "x": tx + tw / 2, "y": ty + 5}
    r.copy_click()
    oid, view = shots[-1]
    hl = np.array(C.FLASH_BGRA, np.uint8)
    assert oid == C.TIP_ID and (view[0] == hl).all()  # top border row is the highlight
    clock[0] += C.FLASH_SECS + 0.01
    r.poll_once()  # flash expires → redraw without the border
    _, view2 = shots[-1]
    assert not (view2[0] == hl).all()


# --- card preview: click-to-play audio, image zoom toggle, ✕ close, and the ⊕→✓ mined state --------


def _preview_reader(ipc, with_audio=True, with_image=True):
    from PIL import Image as PILImage

    from overlay.app.card_preview import PreviewData

    r = Reader(ipc)
    r.osd = (1280, 720)
    frame = PILImage.new("RGBA", (320, 180), (40, 70, 90, 255)) if with_image else None
    pv = PreviewData(
        "mined",
        "本",
        "ほん",
        ["本を読む"],
        "本",
        ["book"],
        frame,
        3.9 if with_audio else None,
        "Saitenka::Mining · Lapis",
    )
    r._show_preview(pv, "/tmp/a.mp3" if with_audio else None)
    return r


def _point_at(ipc, rect):
    x, y, w, h = rect
    ipc.props["mouse-pos"] = {"hover": True, "x": x + w / 2, "y": y + h / 2}


def test_preview_does_not_autoplay(monkeypatch):
    played = []
    monkeypatch.setattr(C, "play_audio", lambda p: played.append(p))
    _preview_reader(FakeIPC())
    assert played == []  # showing the preview no longer autoplays


def test_preview_audio_button_plays_on_click(monkeypatch):
    played = []
    monkeypatch.setattr(C, "play_audio", lambda p: played.append(p))
    ipc = FakeIPC()
    r = _preview_reader(ipc)
    _point_at(ipc, r._preview_audio_rect)
    r.on_click()
    assert played == ["/tmp/a.mp3"]  # ▶ button plays the mined clip


def test_preview_empty_click_plays_nothing(monkeypatch):
    played = []
    monkeypatch.setattr(C, "play_audio", lambda p: played.append(p))
    ipc = FakeIPC()
    r = _preview_reader(ipc)
    px, py, _pw, ph = r._preview_rect
    ipc.props["mouse-pos"] = {"hover": True, "x": px + 6, "y": py + ph - 6}  # empty body
    r.on_click()
    assert played == []


def test_preview_image_click_toggles_zoom():
    ipc = FakeIPC()
    r = _preview_reader(ipc)
    assert not r._preview_zoom
    _point_at(ipc, r._preview_image_rect)
    r.on_click()
    assert r._preview_zoom  # click screenshot → enlarge
    _point_at(ipc, r._preview_image_rect)  # the (bigger) image moved — re-read its rect
    r.on_click()
    assert not r._preview_zoom  # click again → back


def test_preview_close_button_dismisses():
    ipc = FakeIPC()
    r = _preview_reader(ipc)
    _point_at(ipc, r._preview_close_rect)
    r.on_click()
    assert r._preview_rect is None and r._last_preview is None


def test_new_cue_dismisses_preview(monkeypatch):
    ipc = FakeIPC()
    r = _preview_reader(ipc)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_subtitle("別の字幕")  # a new subtitle cue
    assert r._preview_rect is None


def test_mark_mined_flips_hovered_tooltip_to_check(monkeypatch):
    from overlay.app.lookup import card_for

    ipc = FakeIPC()
    r = _scan_reader(ipc)  # dict_set present
    r.anki = object()
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    assert r._tip_key.mined is False  # not mined yet → ⊕
    r._mark_mined(card_for(r.tokens[0]).expression)
    assert r._tip_key.mined is True  # tooltip rebuilt with ✓


def test_seed_mined_preloads_deck_expressions():
    # a word mined in a past session (already in the deck) should be pre-marked so ⊕ shows ✓
    class FakeAnki:
        def find_notes(self, query):
            return [11, 22]

        def notes_info(self, ids):
            return [
                {"fields": {"Expression": {"value": "奉書"}}},
                {"fields": {"Expression": {"value": "<b>通り</b>"}}},
            ]

    from overlay.app.anki import MineConfig

    r = Reader(FakeIPC(), anki=FakeAnki(), mine_cfg=MineConfig())
    r._seed_mined()
    assert r._mined == {"奉書", "通り"}  # HTML stripped; both pre-marked


# --- N4: auto-reveal the translation on hover (opt-in) ---------------------------------------------


def _auto_trans_reader(ipc):
    from overlay.app.subtitles import WordBox
    from overlay.app.tokenize import Token

    ipc.props["secondary-sub-text"] = "I want you to read this."
    r = Reader(ipc, dict_set=_FakeDS(), auto_translate=True)
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 100, 40, 40)]
    return r


def test_auto_translate_shows_on_hover_and_hides_on_leave(monkeypatch):
    ipc = FakeIPC()
    r = _auto_trans_reader(ipc)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    shown = []
    monkeypatch.setattr(r.ov, "show", lambda img, x, y, oid: shown.append(oid))
    hidden = []
    monkeypatch.setattr(r.ov, "hide", lambda oid: hidden.append(oid))
    r.set_hover(0)
    assert C.TRANS_ID in shown  # hovering a word auto-revealed the translation
    assert r._trans_text == "I want you to read this."
    r.set_hover(-1)
    assert C.TRANS_ID in hidden  # leaving the word hid it again


def test_no_auto_translate_without_the_flag(monkeypatch):
    ipc = FakeIPC()
    ipc.props["secondary-sub-text"] = "hidden"
    from overlay.app.subtitles import WordBox
    from overlay.app.tokenize import Token

    r = Reader(ipc, dict_set=_FakeDS())  # flag off (default)
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 100, 40, 40)]
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    shown = []
    monkeypatch.setattr(r.ov, "show", lambda img, x, y, oid: shown.append(oid))
    r.set_hover(0)
    assert C.TRANS_ID not in shown  # translation stays on the manual `t` key


def test_manual_toggle_overrides_auto_and_persists(monkeypatch):
    ipc = FakeIPC()
    r = _auto_trans_reader(ipc)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.toggle_translation()  # force it ON with `t`
    assert r._translate_on and r._translation_visible()
    r.set_hover(-1)  # …and it stays even with nothing hovered
    assert r._translation_visible()


# --- JLPT pill on the tooltip (same signal as the subtitle underline) ------------------------------


def _jlpt_scorer(mapping):
    from overlay.app.scoring import Scorer
    from overlay.app.wordlists import JlptDict, KnownWords

    return Scorer(known=KnownWords.from_set([]), jlpt=JlptDict(dict(mapping)))


def test_jlpt_pill_matches_underline_color():
    from overlay.app.scoring import Palette
    from overlay.app.tokenize import Token

    r = Reader(FakeIPC(), dict_set=_FakeDS(), scorer=_jlpt_scorer({"本命": "N2", "ほんめい": "N2"}))
    tok = Token("本命", "本命", "ほんめい", "名詞", 0, 2)
    pill = r._jlpt_pill(tok)
    assert pill is not None and pill.name == "JLPT" and pill.value == "N2"
    assert pill.color == r._darken(Palette().jlpt["N2"])  # hue tied to the underline level color


def test_jlpt_pill_leads_the_frequency_row():
    from overlay.app.tokenize import Token

    r = Reader(FakeIPC(), dict_set=_FakeDS(), scorer=_jlpt_scorer({"本命": "N2"}))
    entry = r._entry_for(Token("本命", "本命", "ほんめい", "名詞", 0, 2), None)
    assert entry.freqs and entry.freqs[0].name == "JLPT" and entry.freqs[0].value == "N2"


def test_no_jlpt_pill_without_level_or_scorer():
    from overlay.app.tokenize import Token

    tok = Token("犬", "犬", "いぬ", "名詞", 0, 1)
    # word not in the JLPT dict → no pill, frequency row untouched
    r = Reader(FakeIPC(), dict_set=_FakeDS(), scorer=_jlpt_scorer({"本命": "N2"}))
    assert r._jlpt_pill(tok) is None
    assert r._entry_for(tok, None).freqs == []
    # no scorer at all → no pill (coloring is optional)
    assert Reader(FakeIPC(), dict_set=_FakeDS())._jlpt_pill(tok) is None


def test_jlpt_pill_suppressed_when_disabled():
    from overlay.app.tokenize import Token

    sc = _jlpt_scorer({"本命": "N2"})
    sc.enable_jlpt = False
    r = Reader(FakeIPC(), dict_set=_FakeDS(), scorer=sc)
    assert r._jlpt_pill(Token("本命", "本命", "ほんめい", "名詞", 0, 2)) is None


# --- mined-card metadata: hierarchical tags + structured MiscInfo (rearrange-friendly) -------------

VIDEO = "/x/[Erai-raws] Nippon Sangoku - 10 [1080p AMZN WEBRip HEVC EAC3][MultiSub][189B848D].mkv"


def test_tag_slug_is_anki_safe():
    assert Reader._tag_slug("Nippon Sangoku") == "Nippon_Sangoku"  # no spaces in Anki tags
    assert Reader._tag_slug("  a b  c ") == "a_b_c"


def test_mine_tags_carry_source_and_episode():
    r = Reader(FakeIPC())
    tags = r._mine_tags(VIDEO)
    assert tags == ["saitenka::mined", "saitenka::source::Nippon_Sangoku", "saitenka::ep::10"]
    assert r._mine_tags(None) == ["saitenka::mined"]  # no video → just the origin tag


# --- Stage 3: hygiene batch -----------------------------------------------------------------------


def test_bottom_margin_no_dead_code():
    """bottom_margin must not have unreachable code — verify it returns correctly."""
    r = Reader(FakeIPC())
    r.osd = (1280, 720)
    result = r.bottom_margin
    assert isinstance(result, int)
    assert result == round(720 * r.bottom_margin_frac)


def test_panel_cache_lru_eviction_not_wholesale_clear():
    """_panel_cache must evict the OLDEST entry (LRU) at its limit, not clear everything.
    After overflow, the most-recently-used entry must still be present."""
    from overlay.app.tokenize import Token
    from overlay.panel import Definition, Entry

    class _CountDS:
        def __init__(self):
            self.calls = 0

        def entry_for(self, tok, inflected=None):
            self.calls += 1
            return Entry(headword=tok.surface, defs=[Definition("D", ["x"])])

    r = Reader(FakeIPC(), dict_set=_CountDS())
    r.osd = (1280, 720)
    r.sub_origin = (0, 0)

    from overlay.app.subtitles import WordBox

    # Fill the cache to exactly the limit + 1 to trigger eviction.
    # We'll manually insert sentinel keys to test LRU behaviour.
    sentinel = object()
    for i in range(49):
        r._panel_cache[f"key_{i}"] = sentinel
    # Cache now has 49 entries. One more should evict the oldest (key_0), not clear all.
    tok = Token("本命", "本命", "ほんめい", "名詞", 0, 2)
    r.boxes = [WordBox(0, 100, 100, 40, 40)]
    r.tokens = [tok]
    r._show_tooltip(0)
    # key_48 (most-recently inserted before the overflow) should survive; key_0 should not.
    assert "key_48" in r._panel_cache, "LRU eviction removed recently-used entry"
    assert "key_0" not in r._panel_cache, "LRU eviction should have removed oldest entry"


def test_close_cleans_up_tmp_dir():
    """Reader.close() must remove the mkdtemp directory it created."""
    r = Reader(FakeIPC())
    tmp = r._tmp
    assert tmp.exists()
    r.close()
    assert not tmp.exists(), f"tmp dir {tmp} not cleaned up by close()"


def test_capture_media_failure_shows_toast(monkeypatch):
    """If both screenshot and audio fail, _capture_media must show a warn toast, not be silent."""
    ipc = FakeIPC()
    r = _reader_with_word(ipc)
    r.sub_text = "本命"

    # Patch screenshot and clip_audio to always raise (capture lives in app/miner.py since 8d).
    import overlay.app.miner as _M

    monkeypatch.setattr(
        _M, "screenshot", lambda ipc, p: (_ for _ in ()).throw(OSError("snap failed"))
    )
    monkeypatch.setattr(
        _M, "clip_audio", lambda v, s, p: (_ for _ in ()).throw(OSError("clip failed"))
    )
    toasts = []
    monkeypatch.setattr(
        r, "_toast", lambda text, kind="ok", seconds=2.8: toasts.append((text, kind))
    )

    pic, audio = r._capture_media("test_base", "/fake/video.mkv")
    assert pic == "" and audio == ""
    assert any(kind == "warn" for _, kind in toasts), f"no warn toast shown; got {toasts}"


def test_provenance_is_clean_anime_episode_timestamp():
    ipc = FakeIPC()
    ipc.props["time-pos"] = 607
    r = Reader(ipc)
    assert r._provenance(VIDEO) == "Nippon Sangoku · ep10 · 10:07"  # not the raw filename


# --- Stage 1: cue change while hovered leaves stale tooltip / stuck pause ----------------------


def test_cue_change_while_hovered_hides_tooltip_and_resets_state(monkeypatch):
    """When a new subtitle cue arrives while a tooltip is shown, set_subtitle must tear down the
    hover stack (TIP_ID hidden, _tip_rect/state/key cleared) so the next ⊕ click can't mine the
    old word, and so pause_on_tooltip does not stay stuck."""
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)  # open a tooltip on the first subtitle
    assert r._tip_rect is not None  # tooltip is shown
    assert r.hover == 0

    # simulate a cue change while the tooltip is visible
    hidden = []
    monkeypatch.setattr(r.ov, "hide", lambda oid: hidden.append(oid))
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_subtitle("別の字幕")

    assert C.TIP_ID in hidden  # tooltip was hidden
    assert r._tip_rect is None  # _tip_rect reset
    assert r._tip_state is None  # _tip_state reset
    assert r._tip_key is None  # _tip_key reset
    assert r.hover == -1  # hover index reset


def test_cue_change_while_paused_by_tip_resumes_mpv(monkeypatch):
    """If pause_on_tooltip paused mpv when the tooltip opened, a cue change must resume it."""
    ipc = FakeIPC()
    ipc.props["pause"] = False
    r = _reader_with_word(ipc)  # pause_on_tooltip=True
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r._show_tooltip(0)  # opens tooltip and pauses mpv
    assert r._paused_by_tip

    # new cue arrives
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_subtitle("別の字幕")

    assert not r._paused_by_tip
    assert ("set_property", "pause", False) in ipc.commands


# --- Stage 2: P2 trio fixes -----------------------------------------------------------------------


def test_entry_for_does_not_mutate_cached_entry_jlpt_pill_dedup(monkeypatch):
    """_entry_for must not mutate the lru_cached Entry returned by entry_for / dict_set.entry_for.
    Two calls with a JLPT-level token must yield exactly ONE pill each time, not accumulate.
    Uses a dict_set whose entry_for IS lru_cached (same object returned each call) to expose mutation."""
    from overlay.app.tokenize import Token

    # A dict_set backed by a real lru_cache so the same Entry object is returned on repeated calls.
    from overlay.panel import Definition, Entry as _Entry

    class _CachedDS:
        @functools.cache  # noqa: B019 — the test NEEDS a same-object cache to expose Entry mutation
        def entry_for(self, surface, inflected=None):
            return _Entry(headword=surface, defs=[Definition("D", ["x"])], freqs=[])

    r = Reader(
        FakeIPC(), dict_set=_CachedDS(), scorer=_jlpt_scorer({"本命": "N2", "ほんめい": "N2"})
    )
    tok = Token("本命", "本命", "ほんめい", "名詞", 0, 2)
    # Call entry_for twice directly via _entry_for so the lru_cache is hit on the second call.
    e1 = r._entry_for(tok, None)
    e2 = r._entry_for(tok, None)
    jlpt_pills_1 = [f for f in e1.freqs if f.name == "JLPT"]
    jlpt_pills_2 = [f for f in e2.freqs if f.name == "JLPT"]
    assert len(jlpt_pills_1) == 1, f"first call: {len(jlpt_pills_1)} JLPT pills, want 1"
    assert len(jlpt_pills_2) == 1, f"second call: {len(jlpt_pills_2)} JLPT pills, want 1"


def test_tip_panel_finish_does_not_block_render_head():
    """_TipPanel.finish() must not hold the lock during the entire tail render so that a concurrent
    render_head() call from the main thread can fast-path without waiting for the worker."""
    import threading
    from overlay.app.controller import _TipPanel

    # Use a TallDS entry so finish() actually has work to do (lazy panel with deferred tail).
    from overlay.app.subtitles import WordBox
    from overlay.app.tokenize import Token

    ipc = FakeIPC()
    r = _tall_reader(ipc)
    r.osd = (1280, 720)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    # Build the panel but only render head (leave tail deferred).
    from overlay.panel import LazyPanel, panel_rows

    entry = _TallDS().entry_for(r.tokens[0])
    lazy = LazyPanel(panel_rows(entry, r.tip_width, add_button=False), r.tip_width)
    st = _TipPanel(lazy, "ほんめい")
    st.render_head(min_h=r._tip_cap())
    initial_bgra = st.bgra

    blocked_for: list[float] = []

    def do_finish():
        st.finish()

    def do_render_head():
        t0 = time.monotonic()
        st.render_head(min_h=r._tip_cap())  # should fast-path (image already set)
        blocked_for.append(time.monotonic() - t0)

    # Run finish() concurrently with render_head(); measure how long render_head blocks.
    finish_thread = threading.Thread(target=do_finish)
    finish_thread.start()
    # Give finish a moment to acquire the lock (if it still holds it during render)
    time.sleep(0.01)
    do_render_head()
    finish_thread.join()

    # render_head should have fast-pathed and returned in microseconds, not waited for finish
    assert blocked_for[0] < 0.05, (
        f"render_head blocked for {blocked_for[0]:.3f}s — lock convoy detected"
    )
    # The top of the final bgra must match what render_head produced. Since Stage 6 the head's
    # BOUNDARY row may be a partial strip that finish() re-renders fully, and the head's bottom
    # margin (16px) is replaced by row content — so compare only the region safely above both
    # (bottom margin + one ~48px line of slack).
    head_h = initial_bgra.shape[0]
    stable_h = head_h - 64
    assert (st.bgra[:stable_h] == initial_bgra[:stable_h]).all()


def test_prefetch_worker_receives_mined_flag_not_calls_card_for(monkeypatch):
    """_update_prefetch must pass the mined flag from the main thread so that prefetch workers
    never call _is_mined → card_for → jamdict from a worker thread."""
    ipc = FakeIPC()
    ipc.props["pause"] = True
    r = _reader_with_word(ipc)
    r.sub_text = "本命"
    is_mined_calls_from_workers: list[str] = []

    original_is_mined = r._is_mined

    def tracked_is_mined(tok):
        import threading

        if threading.current_thread().name.startswith("saitenka-prefetch"):
            is_mined_calls_from_workers.append(tok.surface)
        return original_is_mined(tok)

    monkeypatch.setattr(r, "_is_mined", tracked_is_mined)
    r._update_prefetch()
    # Drain the queue — read what was queued
    items = []
    while not r._prefetch_q.empty():
        items.append(r._prefetch_q.get())
    assert items, "nothing was queued"
    # Each queued item must carry the main-thread-evaluated mined flag (typed since Stage 8b)
    assert isinstance(items[0].mined, bool), f"queue item lacks the mined flag: {items[0]}"


def test_cue_change_nested_also_cleared(monkeypatch):
    """A cue change with a nested popup open must also clear NESTED_ID and _nest state."""
    ipc = FakeIPC()
    r = _scan_reader(ipc)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_hover(0)
    _hover_first_scan_cell(r, ipc)
    r._update_hover()  # open the nested popup
    assert r._nest.state is not None

    hidden = []
    monkeypatch.setattr(r.ov, "hide", lambda oid: hidden.append(oid))
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.set_subtitle("別の字幕")

    assert C.NESTED_ID in hidden or r._nest.state is None  # nested cleared


# --- Stage 7c: event-driven property reads (observe_property instead of per-tick get_property) ----


def test_fakeipc_in_util_emits_property_change_events():
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    ipc.set_prop("sub-text", "本を読む")
    evs = ipc.drain_events()
    assert {"event": "property-change", "name": "sub-text", "data": "本を読む"} in [
        {k: e.get(k) for k in ("event", "name", "data")} for e in evs
    ]
    assert ipc.drain_events() == []  # drained


def test_start_observing_registers_and_seeds_initial_state():
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    ipc.props["pause"] = True
    ipc.props["sub-text"] = "字幕"
    r = Reader(ipc)
    r.start_observing()
    observed = {c[2] for c in ipc.commands if c and c[0] == "observe_property"}
    assert {"sub-text", "mouse-pos", "osd-dimensions", "pause", "secondary-sub-text"} <= observed
    # initial state was read once at startup
    assert r._prop("pause") is True
    assert r._prop("sub-text") == "字幕"


def test_poll_tick_does_no_property_round_trips_once_observing(monkeypatch):
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    ipc.props["sub-text"] = ""
    r = Reader(ipc, prefetch=False)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.start_observing()
    ipc.commands.clear()
    r.poll_once()
    gets = [
        c
        for c in ipc.commands
        if c
        and c[0] == "get_property"
        and c[1] in ("sub-text", "mouse-pos", "osd-dimensions", "pause", "secondary-sub-text")
    ]
    assert gets == [], f"steady-state tick still does blocking property reads: {gets}"


def test_property_change_event_drives_subtitle_update(monkeypatch):
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    r = Reader(ipc, prefetch=False)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r.start_observing()
    seen = []
    monkeypatch.setattr(r, "set_subtitle", lambda t: seen.append(t))
    ipc.set_prop("sub-text", "新しい字幕")
    r.poll_once()
    assert seen == ["新しい字幕"]  # the buffered event drove the update, no get_property


def test_property_change_event_drives_hover(monkeypatch):
    from util import FakeIPC as EventIPC

    ipc = EventIPC()
    r = Reader(ipc, prefetch=False)
    r.tokens = ["x"]
    seen = []
    monkeypatch.setattr(r, "set_hover", lambda i: (seen.append(i), setattr(r, "hover", i)))
    monkeypatch.setattr(r, "_hit", lambda x, y: 0 if (x < 10 and y < 10) else -1)
    r.start_observing()
    ipc.set_prop("mouse-pos", {"hover": True, "x": 5, "y": 5})
    for ev in ipc.drain_events():  # what poll_once's drain loop does
        if ev.get("event") == "property-change":
            r._on_property_change(ev)
    r._update_hover()
    assert seen == [0]  # hover driven purely by the observed event state


# --- Stage 8b: grouped options object (de-kwarg) + typed queue items ------------------------------


def test_reader_accepts_grouped_options_object():
    from overlay.app.config import KeyOptions, ReaderOptions, TooltipOptions

    opts = ReaderOptions(
        keys=KeyOptions(mine_key="Ctrl+x", sub_prev_key="Alt+a"),
        tooltip=TooltipOptions(tip_max_frac=0.5, pause_on_tooltip=True),
        prefetch=False,
    )
    r = Reader(FakeIPC(), options=opts)
    assert r.mine_key == "Ctrl+x"
    assert r.sub_prev_key == "Alt+a"
    assert r.tip_max_frac == 0.5
    assert r.pause_on_tooltip is True
    assert r.prefetch is False


def test_reader_kwargs_still_work_and_map_onto_groups():
    # legacy exploded kwargs stay accepted (they build the options object internally)
    r = Reader(FakeIPC(), mine_key="Ctrl+z", tip_max_frac=0.4, auto_translate=True)
    assert r.mine_key == "Ctrl+z" and r.tip_max_frac == 0.4 and r.auto_translate is True
    with pytest.raises(TypeError):
        Reader(FakeIPC(), not_a_knob=1)  # typo detection preserved


def test_prefetch_queue_items_are_typed_dataclasses():
    from overlay.app.prefetch import PrefetchItem

    ipc = FakeIPC()
    ipc.props["pause"] = True
    r = _reader_with_word(ipc)
    r.sub_text = "本命"
    r._update_prefetch()
    item = r._prefetch_q.get()
    assert isinstance(item, PrefetchItem)
    assert item.token.surface == "本命"
    assert item.inflected == "本命"
    assert isinstance(item.gen, int) and isinstance(item.mined, bool)


def test_finish_queue_items_are_typed_dataclasses(monkeypatch):
    from overlay.app.prefetch import FinishItem

    r = _tall_reader(FakeIPC())
    monkeypatch.setattr(r, "_finish_available", lambda: True)
    monkeypatch.setattr(r, "_draw_subtitle", lambda: None)
    r._show_tooltip(0)
    item = r._finish_q.get()
    assert isinstance(item, FinishItem)
    assert item.panel is r._tip_state and item.key == r._tip_key


# --- Stage 8d: controller split — popups.py (PopupView/TipPanel) + miner.py (Miner) ---------------


def test_popups_module_unifies_popup_view_state():
    from overlay.app.popups import PopupView, TipPanel

    pv = PopupView()
    # the unified per-popup view state (used by the nested popup today; base tip later)
    assert pv.state is None and pv.scroll == 0 and pv.rect is None and pv.hide_at == 0.0
    # controller keeps aliases so existing internals/tests stay valid
    assert C._TipPanel is TipPanel
    assert C._Nested is PopupView
    r = Reader(FakeIPC())
    assert isinstance(r._nest, PopupView)
    assert isinstance(TipPanel.__init__, object)  # class exists and is constructible via LazyPanel


def test_miner_module_owns_the_mining_flow(monkeypatch):
    from overlay.app.miner import Miner, tag_slug

    assert tag_slug("Nippon Sangoku") == "Nippon_Sangoku"
    ipc = FakeIPC()
    r = _reader_with_word(ipc)
    assert isinstance(r._miner, Miner)
    # Reader's mining API delegates to the Miner (behaviour preserved)
    mined = []
    monkeypatch.setattr(r._miner, "mine_token", lambda tok: mined.append(tok.surface))
    r.anki = object()
    r.mine_cfg = object()
    r.hover = 0
    r.mine_current()
    assert mined == ["本命"]

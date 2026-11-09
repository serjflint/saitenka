"""L1 interaction driver — script moves / clicks / wheel / keys through the REAL input path.

The overlay never sees OS events: a MOVE is mpv's ``mouse-pos`` property (read by ``_update_hover``),
and a CLICK / WHEEL / key is a ``keybind → script-message`` client-message (dispatched by ``_handle`` /
``on_click``). This wraps a :class:`SessionController` + ``FakeIPC`` so controller tests read as interaction
scripts — ``ui.move_to_word(0).click()`` — AND exercise the real hit-testing (``_hit`` / ``on_click``
map screen coords → word/button), instead of poking ``set_hover`` / ``_show_tooltip`` directly.
"""

from __future__ import annotations

#: The dwells `instant=True` zeroes. The two *hide* deadlines are deliberately absent: they run on
#: `hide_delay`, which instant mode never zeroed, so a linger stays pending exactly as before.
_INSTANT_DWELLS = ("lifecycle:hover-switch", "lifecycle:scan-open")


class Driver:
    def __init__(self, reader, *, instant: bool = True):
        self.session = reader
        self.r = reader.graph
        self.ipc = self.r.ipc
        self._instant = instant
        if instant:  # deterministic tests: no hover-switch or scan dwell to wait out
            self.r.tooltip.configure_delays(switch=0.0)
            self.r.tooltip.configure_delays(scan=0.0)

    # --- moves (mouse-pos property → _update_hover) ------------------------------------------------
    def move(self, x: float, y: float, *, hover: bool = True) -> Driver:
        """Move the cursor to screen ``(x, y)`` and let the reader react (hover / scan / linger)."""
        self.ipc.set_prop("mouse-pos", {"hover": hover, "x": x, "y": y})
        self.session.pump()
        if self._instant:
            # A zero delay used to mean the next clock comparison passed. A dwell is a deadline
            # now, so the equivalent is delivering it — zero-delay is still a timer.
            for timer in _INSTANT_DWELLS:
                self.ipc.fire_runtime_timer(timer)
        self.session.pump()
        return self

    def leave(self) -> Driver:
        """Cursor leaves the video window (nothing hovered)."""
        return self.move(-1, -1, hover=False)

    def word_center(self, index: int) -> tuple[float, float]:
        """Screen coords of subtitle word ``index`` (its box + the subtitle origin) — what a real
        cursor over that word would report."""
        b = next(b for b in self.r.subtitle_presentation.cue.current.boxes if b.index == index)
        ox, oy = self.r.subtitle_presentation.cue.current.origin
        return (ox + b.x + b.w / 2, oy + b.y + b.h / 2)

    def move_to_word(self, index: int) -> Driver:
        return self.move(*self.word_center(index))

    def move_into_tip(self, dx: float = 0.5, dy: float = 0.5) -> Driver:
        """Move to a point inside the shown tooltip (fractions of its rect) — e.g. to scan an inner
        word or hit a body region."""
        x, y, w, h = self.r.tooltip.surface_state().view.rect
        return self.move(x + w * dx, y + h * dy)

    # --- clicks / wheel / keys (client-message path) ----------------------------------------------
    def click(self) -> Driver:
        """Left-click at the current cursor (the ``MBTN_LEFT`` → ``saitenka-click`` path)."""
        self.r.interaction.route_click()
        self.session.pump()
        return self

    def right_click(self) -> Driver:
        """Right-click at the current cursor (copies the word under it)."""
        self.r.tooltip.copy_click()
        return self

    def wheel(self, steps: int) -> Driver:
        """Scroll the popup under the cursor by ``steps`` notches (down positive)."""
        from saitenka.app.session import surfaces

        self.r.tooltip.scroll_tip(surfaces.tip_wheel_pixels(self.r.tooltip.scale().ref_h, steps))
        return self

    def key(self, msg: str) -> Driver:
        """Dispatch a tooltip client-message (e.g. ``controller.MINE_MSG``)."""
        self.session.command(msg)
        return self

    # --- observed state ---------------------------------------------------------------------------
    @property
    def hover(self) -> int:
        return self.r.tooltip.observation().selected

    @property
    def tip_shown(self) -> bool:
        return self.r.tooltip.surface_state().view.rect is not None

    @property
    def nested_shown(self) -> bool:
        return self.r.tooltip.surface_state().nest.state is not None

    def subtitle(self, text):
        self.ipc.set_prop("sub-text", text)
        self.session.pump()
        return self

    def press(self, key):
        from util import press

        press(self.session, self.ipc, key)
        return self

    def hover_character(self, offset):
        cue = self.r.subtitle_presentation.cue.current
        index = next(i for i, token in enumerate(cue.tokens) if token.start <= offset < token.end)
        box = next(box for box in cue.boxes if box.index == index)
        local = offset - cue.tokens[index].start
        char = next((char for char in box.characters if char.offset == local), None)
        rect = char.bounds if char is not None else box
        width, height = (rect.width, rect.height) if char is not None else (rect.w, rect.h)
        return self.move(cue.origin[0] + rect.x + width / 2, cue.origin[1] + rect.y + height / 2)

    def finish_hover_delay(self):
        for timer in ("lifecycle:nested-hide", "lifecycle:tooltip-hide"):
            self.ipc.fire_runtime_timer(timer)
        self.session.pump()
        return self

    def resize(self, width, height):
        self.ipc.set_prop("osd-dimensions", {"w": width, "h": height})
        self.session.pump()
        return self

    @property
    def dictionary_images(self):
        return [frame[0] for frame in self.dictionary_frames]

    @property
    def dictionary_frames(self):
        from util import overlay_frame

        from saitenka.app.overlay_ids import OverlayId

        return [
            frame
            for oid in (OverlayId.TIP, OverlayId.NESTED)
            if (frame := overlay_frame(self.ipc, oid)) is not None
        ]

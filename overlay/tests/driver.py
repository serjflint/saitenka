"""L1 interaction driver — script moves / clicks / wheel / keys through the REAL input path.

The overlay never sees OS events: a MOVE is mpv's ``mouse-pos`` property (read by ``_update_hover``),
and a CLICK / WHEEL / key is a ``keybind → script-message`` client-message (dispatched by ``_handle`` /
``on_click``). This wraps a :class:`Reader` + ``FakeIPC`` so controller tests read as interaction
scripts — ``ui.move_to_word(0).click()`` — AND exercise the real hit-testing (``_hit`` / ``on_click``
map screen coords → word/button), instead of poking ``set_hover`` / ``_show_tooltip`` directly.
"""

from __future__ import annotations


class Driver:
    def __init__(self, reader, *, instant: bool = True):
        self.r = reader
        self.ipc = reader.ipc
        if instant:  # deterministic tests: no hover-switch or scan dwell to wait out
            self.r.hover_switch_delay = 0.0
            self.r.scan_delay = 0.0

    # --- moves (mouse-pos property → _update_hover) ------------------------------------------------
    def move(self, x: float, y: float, *, hover: bool = True) -> Driver:
        """Move the cursor to screen ``(x, y)`` and let the reader react (hover / scan / linger)."""
        self.ipc.props["mouse-pos"] = {"hover": hover, "x": x, "y": y}
        self.r._update_hover()
        return self

    def leave(self) -> Driver:
        """Cursor leaves the video window (nothing hovered)."""
        return self.move(-1, -1, hover=False)

    def word_center(self, index: int) -> tuple[float, float]:
        """Screen coords of subtitle word ``index`` (its box + the subtitle origin) — what a real
        cursor over that word would report."""
        b = next(b for b in self.r.boxes if b.index == index)
        ox, oy = self.r.sub_origin
        return (ox + b.x + b.w / 2, oy + b.y + b.h / 2)

    def move_to_word(self, index: int) -> Driver:
        return self.move(*self.word_center(index))

    def move_into_tip(self, dx: float = 0.5, dy: float = 0.5) -> Driver:
        """Move to a point inside the shown tooltip (fractions of its rect) — e.g. to scan an inner
        word or hit a body region."""
        x, y, w, h = self.r._tip_rect
        return self.move(x + w * dx, y + h * dy)

    # --- clicks / wheel / keys (client-message path) ----------------------------------------------
    def click(self) -> Driver:
        """Left-click at the current cursor (the ``MBTN_LEFT`` → ``saitenka-click`` path)."""
        self.r.on_click()
        return self

    def right_click(self) -> Driver:
        """Right-click at the current cursor (copies the word under it)."""
        self.r.copy_click()
        return self

    def wheel(self, steps: int) -> Driver:
        """Scroll the popup under the cursor by ``steps`` notches (down positive)."""
        self.r._scroll_tip(steps * round(self.r.osd[1] * 0.12))
        return self

    def key(self, msg: str) -> Driver:
        """Dispatch a tooltip client-message (e.g. ``controller.MINE_MSG``)."""
        self.r._handle(msg)
        return self

    # --- observed state ---------------------------------------------------------------------------
    @property
    def hover(self) -> int:
        return self.r.hover

    @property
    def tip_shown(self) -> bool:
        return self.r._tip_rect is not None

    @property
    def nested_shown(self) -> bool:
        return self.r._nest.state is not None

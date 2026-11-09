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

    def _popup(self):
        tip = self.r.tooltip.surface_state()
        return tip.nest if tip.nest.rect is not None else tip.view

    def scan_text(self, text):
        popup = self._popup()
        cell = next(
            cell
            for cell in popup.state.windowed.scan_boxes()
            if cell.text.startswith(text)
            and popup.scroll <= cell.y
            and cell.y + cell.h <= popup.scroll + popup.view_h
        )
        scale = self.r.tooltip.scale().raster
        return self.move(
            popup.xy[0] + (cell.x + cell.w / 2) * scale,
            popup.xy[1] + (cell.y + cell.h / 2 - popup.scroll) * scale,
        )

    def add_entry(self, entry=0):
        for _ in range(100):
            popup = self._popup()
            links = popup.state.windowed.link_boxes()
            target = next(
                (
                    link
                    for link in links
                    if link.query == f"mine:{entry}"
                    and popup.scroll <= link.y
                    and link.y + link.h <= popup.scroll + popup.view_h
                ),
                None,
            )
            if target is not None:
                scale = self.r.tooltip.scale().raster
                self.move(
                    popup.xy[0] + (target.x + target.w / 2) * scale,
                    popup.xy[1] + (target.y + target.h / 2 - popup.scroll) * scale,
                )
                return self.press("MBTN_LEFT")
            if entry == 0 and not any(link.query.startswith("mine:") for link in links):
                from saitenka.panel import header_add_rect

                x, y, width, height = header_add_rect(
                    self.r.tooltip.scale().width, speak_button=False
                )
                scale = self.r.tooltip.scale().raster
                self.move(
                    popup.xy[0] + (x + width / 2) * scale,
                    popup.xy[1] + (y + height / 2 - popup.scroll) * scale,
                )
                return self.press("MBTN_LEFT")
            self.press("DOWN")
        raise AssertionError("dictionary entry has no reachable add button")

    def read_popup(self):
        images = [self.dictionary_images[-1]]
        for _ in range(100):
            self.press("DOWN")
            image = self.dictionary_images[-1]
            if image.tobytes() == images[-1].tobytes():
                return images
            images.append(image)
        raise AssertionError("popup never reached its end")

    def click_link(self, query):
        popup = self._popup()
        link = next(
            link
            for link in popup.state.windowed.link_boxes()
            if link.query == query
            and popup.scroll <= link.y
            and link.y + link.h <= popup.scroll + popup.view_h
        )
        scale = self.r.tooltip.scale().raster
        self.move(
            popup.xy[0] + (link.x + link.w / 2) * scale,
            popup.xy[1] + (link.y + link.h / 2 - popup.scroll) * scale,
        )
        return self.press("MBTN_LEFT")

    @property
    def preview_frame(self):
        from util import overlay_frame

        from saitenka.app.overlay_ids import OverlayId

        return overlay_frame(self.ipc, OverlayId.PREVIEW)

    @property
    def help_visible(self):
        from util import overlay_frame

        from saitenka.app.overlay_ids import OverlayId

        return overlay_frame(self.ipc, OverlayId.HELP) is not None

    @property
    def preview_close_bounds(self):
        """Locate the close button in the published preview image."""
        import numpy as np

        image, x, y = self.preview_frame
        pixels = np.asarray(image)[: image.height // 3, image.width // 2 :]
        mask = np.all(pixels == (225, 225, 225, 255), axis=2)
        # Filled rows exclude antialiased text and divider pixels.
        ys = np.flatnonzero(mask.sum(axis=1) >= 8)
        runs = np.split(ys, np.flatnonzero(np.diff(ys) > 1) + 1)
        row = next((run for run in runs if len(run) >= 8), None)
        if row is None:
            raise AssertionError("preview has no visible close button")
        xs = np.flatnonzero(mask[row].sum(axis=0) >= len(row) / 2)
        return (
            x + image.width // 2 + int(xs.min()),
            y + int(row[0]),
            int(xs.max() - xs.min() + 1),
            int(row[-1] - row[0] + 1),
        )

    def close_preview(self):
        x, y, width, height = self.preview_close_bounds
        self.move(x + width * 0.85, y + height * 0.85)
        return self.press("MBTN_LEFT")

    def wait_for_preview(self):
        from util import await_ready

        await_ready(
            lambda: self.preview_frame is not None, "preview did not appear", pump=self.session.pump
        )
        return self

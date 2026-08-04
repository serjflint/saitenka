"""Shared tooltip-level fakes: a linking dict set + a hi-dpi reader, for the seam / cache tests.

Kept out of ``util`` (imported by ~everything) because it pulls in the controller — an opt-in import
for the handful of tests that drive the real tooltip pipeline through ``FakeIPC``.
"""

from __future__ import annotations

import util

from overlay.app.controller import Reader


class LinkingDS:
    """A dict set whose entries carry BOTH CJK scan cells and an inline cross-reference link, so a
    tooltip built from it exercises scan-hit AND link-hit. ``search`` backs a wildcard navigation."""

    def entry_for(self, _tok, _inflected=None):
        return util.cjk_links_entry(4)

    def search(self, _pattern):
        return util.cjk_links_entry(2)


def hidpi_reader(scale: float) -> Reader:
    """A headless reader whose OSD pins ``_tip_display_scale`` to ~``scale`` (osd_h / REF_H(1080)),
    one content token shown, crisp enabled — the fixture the crisp/native path needs."""
    from overlay.app.subtitles import WordBox
    from overlay.app.tokenize import Token

    r = Reader(util.FakeIPC(), dict_set=LinkingDS(), scan_delay=0.0)
    r.osd = (round(1920 * scale), round(1080 * scale))
    r.sub_origin = (0, 0)
    r.tokens = [Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    r.boxes = [WordBox(0, 100, 300, 40, 40)]
    r._crisp_on = True
    return r

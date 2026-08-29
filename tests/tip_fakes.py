"""Shared tooltip-level fakes: a linking dict set + a hi-dpi reader, for the seam / cache tests.

Kept out of ``util`` (imported by ~everything) because it pulls in the controller — an opt-in import
for the handful of tests that drive the real tooltip pipeline through ``FakeIPC``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import util
from session_builder import build_session

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionServices

if TYPE_CHECKING:
    from saitenka.app.session.controller import SessionController


class LinkingDS:
    """A dict set whose entries carry BOTH CJK scan cells and an inline cross-reference link, so a
    tooltip built from it exercises scan-hit AND link-hit. ``search`` backs a wildcard navigation."""

    def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
        return util.cjk_links_entry(4)

    def search(self, _pattern):
        return util.cjk_links_entry(2)


def hidpi_reader(scale: float) -> SessionController:
    """A headless reader whose OSD pins ``tip_scale.display`` to ~``scale`` (osd_h / REF_H(1080)),
    one content token shown, crisp enabled — the fixture the crisp/native path needs."""
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token

    r = build_session(
        util.FakeIPC(),
        services=SessionServices(
            dictionaries=LinkingDS(),
        ),
        options=ReaderOptions().with_overrides(scan_delay=0.0),
    )
    r.turn.screen.osd = (round(1920 * scale), round(1080 * scale))
    r.turn.subtitle_presentation.cue.replace_geometry(origin=(0, 0))
    r.turn.subtitle_presentation.cue.replace_tokenized(
        tokens=[Token("本命", "本命", "ほんめい", "名詞", 0, 2)]
    )
    r.turn.subtitle_presentation.cue.replace_geometry(boxes=[WordBox(0, 100, 300, 40, 40)])
    r.turn.tooltip_controller.visual.crisp = True
    return r

"""Panel placement and build output contracts."""

from __future__ import annotations

import pytest

from saitenka.app.features.tooltip.tooltip_panel import place_tip


class _View:
    def __init__(self) -> None:
        self.scroll = 7
        self.desired_scroll = 7
        self.view_h = 0
        self.xy = None


@pytest.mark.parametrize(
    ("height", "anchor", "expected_height"),
    [(900, (10, 20, 30), 400), (120, (10, 20, 30), 120), (200, (1900, 1060, 30), 200)],
    ids=["tall", "short", "corner"],
)
def test_placement_lands_inside_the_screen(height, anchor, expected_height) -> None:
    view = _View()
    place_tip(view, 300, height, 400, anchor, scale=1.0, osd=(1920, 1080))

    assert (view.scroll, view.desired_scroll, view.view_h) == (0, 0, expected_height)
    x, y = view.xy
    assert 0 <= x <= 1920 - 300
    assert 0 <= y <= 1080 - view.view_h


def test_a_panel_build_uses_the_supplied_width() -> None:
    from saitenka_tokenize.japanese import Token

    from saitenka.app.features.tooltip.tooltip_panel import PanelKey, PanelStyle, _build_panel
    from saitenka.panel import Definition, Entry

    class _DictSet:
        def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
            return Entry(headword=tok.surface, defs=[Definition("D", ["x"])])

        def rareness_rank(self, _token):  # protocol shape
            return None

    style = PanelStyle(
        width=420,
        band_cache_max=4,
        raw_band_ceiling=8,
        layout_backend=None,
        layout_engine="taffy",
        add_button=False,
        speak_button=False,
        dict_set=_DictSet(),
    )
    token = Token(surface="猫", lemma="猫", reading="ねこ", pos="名詞", start=0, end=1)
    key = PanelKey(
        lemma="猫",
        surface="猫",
        reading="ねこ",
        inflected="猫",
        width=420,
        anki_ok=False,
        mined=False,
        tts_ok=False,
        group_mined=(),
        phrase_terms=(),
    )

    panel = _build_panel(style, key, token, "猫", mined=False)

    assert panel.width == 420

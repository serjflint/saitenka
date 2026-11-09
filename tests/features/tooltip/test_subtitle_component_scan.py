"""Forced clipping and deferred worker schedules."""

import threading
from dataclasses import replace

import pytest
from driver import Driver
from util import FakeIPC

from saitenka.app.features.tooltip import hover_metadata, nested_popup, tooltip_panel
from saitenka.app.session.factory import SessionServices
from saitenka.panel import Definition, Entry


class Dictionary:
    def entry_for(self, tok, _inflected=None, *, extra_terms=()):
        del extra_terms
        return Entry(headword=[tok.lemma], reading="", defs=[Definition("test", ["meaning"])])

    def rareness_rank(self, _token):
        return None


def component_session(make_session, dictionary=None):
    ipc = FakeIPC()
    reader = make_session(ipc, services=SessionServices(dictionaries=dictionary or Dictionary()))
    ipc.set_prop("sub-text", "やり遂げたい")
    reader.pump()
    return reader


def char_center(reader, offset):
    cue = reader.graph.subtitle_presentation.cue.current
    cell = next(char for box in cue.boxes for char in box.characters if char.offset == offset)
    return (
        cue.origin[0] + cell.bounds.x + cell.bounds.width / 2,
        cue.origin[1] + cell.bounds.y + cell.bounds.height / 2,
    )


def test_offscreen_retained_links_do_not_block_a_subtitle_component(make_session):
    from tip_fakes import LinkingDS

    from saitenka.app.features.tooltip import tooltip

    reader = component_session(make_session, LinkingDS())
    Driver(reader).move(*char_center(reader, 0))
    x, y = char_center(reader, 2)
    reader.graph.ipc.set_prop("mouse-pos", {"hover": True, "x": x, "y": y})
    reader.pump()
    tip = reader.graph.tooltip.surface_state()
    raster = reader.graph.tooltip.scale().raster
    panel, scale, scroll = tooltip_panel.hit_target(
        tip.nest, tip.view.state, tip.view.scroll, raster, nested=False
    )
    panel.windowed.viewport(0, 1_000_000)
    link = panel.windowed.link_boxes()[0]
    sx = x - (link.x + link.w / 2) * scale
    sy = y - (link.y + link.h / 2 - scroll) * scale
    tip.view.xy = (sx, sy)
    tip.view.rect = (sx, sy, 100, 1)
    assert tooltip_panel.link_hit_at(tip, raster, x, y, nested=False) is not None

    observed, _targets = tooltip.observe_hover(
        reader.graph.tooltip.tip_ports, reader.graph.tooltip.hover_inputs, x, y, inside=True
    )

    assert observed.scan is not None and observed.scan.text == "遂げたい"


@pytest.mark.parametrize("occlusion", ["base", "nested", "outside"])
def test_visible_scan_does_not_see_through_popups_or_outside_the_window(make_session, occlusion):
    reader = component_session(make_session)
    Driver(reader).move(*char_center(reader, 0))
    x, y = char_center(reader, 2)
    reader.graph.ipc.set_prop("mouse-pos", {"hover": occlusion != "outside", "x": x, "y": y})
    reader.pump()
    tip = reader.graph.tooltip.surface_state()
    if occlusion == "base":
        tip.view.rect = (x - 5, y - 5, 10, 10)
        tip.view.xy = (x - 5, y - 5)
    elif occlusion == "nested":
        tip.nest.rect = (x - 5, y - 5, 10, 10)

    hit = tooltip_panel.visible_scan_hit(
        tip, reader.graph.tooltip.scale().raster, x, y, subtitle=reader.graph.tooltip.hover_inputs
    )

    assert hit is None


@pytest.mark.parametrize("move", ["stay", "leave", "cue"])
def test_deferred_component_lookup_rechecks_the_pointer(make_session, move):
    reader = component_session(make_session)
    ui = Driver(reader)
    ui.move(*char_center(reader, 0))
    x, y = char_center(reader, 2)
    reader.graph.ipc.set_prop("mouse-pos", {"hover": True, "x": x, "y": y})
    reader.pump()
    tip = reader.graph.tooltip.surface_state()
    tip.last_mouse = (x, y)
    lookup = reader.graph.tooltip.word_lookup
    cell = tooltip_panel.visible_scan_hit(
        tip, reader.graph.tooltip.scale().raster, x, y, subtitle=lookup.subtitle
    )
    requests = []
    deferred = replace(
        lookup, deferred=True, submit=lambda request: requests.append(request) or True
    )
    nested_popup.show_nested(
        reader.graph.tooltip.tip_ports, reader.graph.tooltip.panel_ports, deferred, cell
    )
    result = hover_metadata.run_metadata(requests[0], threading.Event())
    if move == "leave":
        ui.leave()
    elif move == "cue":
        reader.graph.ipc.set_prop("sub-text", "猫を見る")
        reader.pump()

    nested_popup.apply_nested_metadata(
        reader.graph.tooltip.tip_ports,
        reader.graph.tooltip.panel_ports,
        reader.graph.tooltip.word_lookup,
        result,
    )

    nested = reader.graph.tooltip.surface_state().nest
    assert (nested.state is not None) is (move == "stay")
    if move == "stay":
        assert nested.token.surface == "遂げたい"

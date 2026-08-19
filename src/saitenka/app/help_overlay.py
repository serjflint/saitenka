"""Playback-neutral orchestration for the in-player shortcut reference."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.bindings import active_bindings
from saitenka.app.overlay_ids import OverlayId
from saitenka.render.help import HelpEntry, build_document, render_page

if TYPE_CHECKING:
    from saitenka.app.bindings import ActiveBinding
    from saitenka.app.controller import Reader


@dataclass
class HelpState:
    """In-player shortcut-reference overlay: whether it is showing, and which page."""

    open: bool = False
    page: int = 0


def _entry(binding: ActiveBinding) -> HelpEntry:
    spec = binding.spec
    return HelpEntry(spec.section, binding.key, spec.label, spec.context, spec.source)


def document_for(reader: Reader):
    visible = active_bindings(reader, "global", "tooltip", "mpv")
    entries = tuple(_entry(binding) for binding in visible if binding.spec.show_in_help)
    footer = f"{reader.help_key} / Esc close  ·  PgUp/PgDn or wheel"
    return build_document(entries, osd=reader.osd, footer=footer, scale=reader.chrome_scale)


def redraw(reader: Reader) -> None:
    if not reader._help_open:
        return
    document = document_for(reader)
    reader._help_page = min(reader._help_page, len(document.pages) - 1)
    page = document.pages[reader._help_page]
    image = render_page(
        page,
        width=document.width,
        height=document.height,
        index=reader._help_page,
        total=len(document.pages),
        scale=document.scale,
    )
    x = (reader.osd[0] - document.width) // 2
    y = (reader.osd[1] - document.height) // 2
    reader.lifecycle_surfaces.present(image, x, y, oid=OverlayId.HELP)


def _bind_help_keys(reader: Reader) -> None:
    for binding in active_bindings(reader, "help"):
        message = binding.spec.message
        if message is not None:
            reader.ipc.command("keybind", binding.key, f"script-message {message}")


def _restore_context_keys(reader: Reader) -> None:
    tooltip_by_key = {
        binding.key: binding.spec.message for binding in active_bindings(reader, "tooltip")
    }
    for binding in active_bindings(reader, "help"):
        message = tooltip_by_key.get(binding.key) if reader._tip_keys_bound else None
        command = f"script-message {message}" if message else "ignore"
        reader.ipc.command("keybind", binding.key, command)


def open_help(reader: Reader) -> None:
    if reader._help_open:
        return
    reader._help_open = True
    reader._help_page = 0
    _bind_help_keys(reader)
    redraw(reader)


def close_help(reader: Reader) -> None:
    if not reader._help_open:
        return
    reader._help_open = False
    reader.lifecycle_surfaces.remove(OverlayId.HELP)
    _restore_context_keys(reader)
    reader._help_page = 0


def toggle(reader: Reader) -> None:
    close_help(reader) if reader._help_open else open_help(reader)


def step(reader: Reader, delta: int) -> None:
    if not reader._help_open:
        return
    document = document_for(reader)
    target = max(0, min(len(document.pages) - 1, reader._help_page + delta))
    if target != reader._help_page:
        reader._help_page = target
        redraw(reader)


def scroll(reader: Reader, steps: int) -> bool:
    if not reader._help_open:
        return False
    if steps:
        step(reader, 1 if steps > 0 else -1)
    return True


def suppress_hover(reader: Reader) -> bool:
    return reader._help_open

"""Card preview UI: verify a mined (or already-in-deck) card's expression/reading/image/audio/glosses
before or after mining.

Mining itself (Anki note creation, media capture, provenance/tags) lives in the mining feature.
This module renders the preview owner's panel and handles its clicks (dismiss / zoom / play). The ⊕→✓ feedback is
the session's mined-feedback conjunction; it writes a session fact and redraws the popups, which this
surface is not one of.

Two ports: `PreviewPorts` is the surface (what it draws on and what a click can do), `CardSource`
is immutable target metadata plus named retrieval acts and cue facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from PIL import Image

from saitenka.app.anki import strip_field_html
from saitenka.app.bindings import TIP_CLOSE_MSG, active_bindings
from saitenka.app.features.preview.card_preview import PreviewData, render_card_preview
from saitenka.app.media import audio_duration, play_audio
from saitenka.app.mpv_egress import send_correlated
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.procutil import kill_process_tree
from saitenka.model import in_rect
from saitenka.runtime import Owner, events

if TYPE_CHECKING:
    from collections.abc import Callable

    from PIL.Image import Image as PILImage

    from saitenka.app.config import KeyOptions
    from saitenka.app.features.preview.card_preview import PreviewPanel
    from saitenka.app.features.preview.preview_controller import PreviewController
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.tokenize import Token


@dataclass(frozen=True, slots=True)
class PreviewPorts:
    """The preview owner, physical sinks, and per-turn facts used by its UI operations."""

    preview: PreviewController
    help_open: bool
    tip_keys_bound: bool
    surfaces: LifecycleSurfaces
    osd: tuple[int, int]
    #: The tooltip's width, which the preview matches so the two read as one card at one size.
    tip_width: int
    ipc: object
    keys: KeyOptions
    add_duplicate: Callable[[], None]
    play_audio: bool


@dataclass(frozen=True, slots=True)
class CardSource:
    """Where a preview's content comes from: the deck it may already be in, and the cue it was mined
    from. Separate from `PreviewPorts` because a preview can be re-rendered — zoomed, replayed —
    without any of this, and the surface must not be able to reach Anki to do it.
    """

    deck: str | None
    model: str | None
    fields: tuple[tuple[str, str], ...]
    note_info: Callable[[int], dict | None]
    fetch_image: Callable[[str], PILImage | None]
    fetch_media: Callable[[str], Path | None]
    #: The cue's tokenized lines — `sentence_lines` rejoins them for the card's sentence field.
    lines: list[list[Token]]
    provenance: Callable[[object], str]
    video_path: Callable[[], object]
    toast: Callable[[str, str], None]


def duplicate_token(panel: PreviewPanel) -> Token | None:
    """Expose the preview-owned token selected by the duplicate affordance."""
    return panel.dup_tok


def _html_lines(html: str) -> list[str]:
    parts = re.split(r"<br\s*/?>", html or "")
    return [t for t in (strip_field_html(p) for p in parts) if t]


def _html_items(html: str) -> list[str]:
    return [strip_field_html(m) for m in re.findall(r"<li>(.*?)</li>", html or "", re.DOTALL)]


def _media_name(field_html: str, pattern: str) -> str:
    m = re.search(pattern, field_html or "")
    return m.group(1) if m else ""


def sentence_lines(lines) -> list[str]:
    """The cue's tokenized lines rejoined into plain text, one string per line."""
    return ["".join(token.surface for token in line) for line in lines]


def footer(deck: str | None, model: str | None, provenance: str) -> str:
    """Where the card went and where its media came from — the preview's one line of provenance."""
    assert deck is not None and model is not None  # previews only exist after a mine
    return f"{deck} · {model} · {provenance}"


def preview_mined(
    ports: PreviewPorts, source: CardSource, card, tok, video, status: str = "mined"
) -> None:
    panel = ports.preview.panel
    img = None
    if panel.last_jpg and Path(panel.last_jpg).exists():
        img = Image.open(panel.last_jpg)
    secs = audio_duration(panel.last_audio) if panel.last_audio else None
    pv = PreviewData(
        status,
        card.expression,
        card.reading,
        sentence_lines(source.lines),
        tok.surface,
        list(card.glosses),
        img,
        secs,
        footer(source.deck, source.model, source.provenance(video)),
    )
    show_preview(ports, pv, panel.last_audio)


def preview_existing(
    ports: PreviewPorts, source: CardSource, note_id: int, card, status: str
) -> None:
    info = source.note_info(note_id)
    if info is None:
        source.toast(f"already have {card.expression}", "warn")
        return
    f, fld = info["fields"], dict(source.fields)

    def val(logical):
        return f.get(fld.get(logical, ""), {}).get("value", "")

    img = source.fetch_image(_media_name(val("picture"), r'src="([^"]+)"'))
    mp3 = source.fetch_media(_media_name(val("audio"), r"\[sound:([^\]]+)\]"))
    secs = audio_duration(mp3) if mp3 else None
    pv = PreviewData(
        status,
        val("expression") or card.expression,
        val("reading") or card.reading,
        _html_lines(val("sentence")),
        val("expression") or card.expression,
        _html_items(val("glossary")) or list(card.glosses),
        img,
        secs,
        footer(source.deck, source.model, source.provenance(source.video_path())),
    )
    show_preview(ports, pv, mp3)


def _grab_preview_keys(ipc, bindings) -> None:
    """Route the preview-scoped keys (Esc → close) to the preview while it's on screen."""
    for b in bindings:
        send_correlated(
            ipc,
            f"preview-keybind:{b.key}",
            "keybind",
            b.key,
            f"script-message {b.spec.message}",
            owner=Owner.INTERACTION,
        )


def _release_preview_keys(ipc, bindings, *, help_open: bool, tip_keys_bound: bool) -> None:
    """Hand the preview's keys back when it closes. Esc is shared with the tooltip, so return it there
    if a tooltip is still up (the help overlay, if open, owns Esc itself — leave it alone)."""
    if help_open:
        return
    for b in bindings:
        command = (
            f"script-message {TIP_CLOSE_MSG}" if b.key == "ESC" and tip_keys_bound else "ignore"
        )
        send_correlated(
            ipc,
            f"preview-keybind-release:{b.key}",
            "keybind",
            b.key,
            command,
            owner=Owner.INTERACTION,
        )


def _stop_preview_audio(preview: PreviewPanel) -> None:
    """Kill the ▶ clip's player if one is running (afplay / mpv --no-video / ffplay) — the fire-and-
    forget Popen outlives the panel otherwise, so a dismiss left the clip playing (#251). Idempotent."""
    proc, preview.audio_proc = preview.audio_proc, None
    kill_process_tree(proc)  # no-op on None / an already-exited process


def show_preview(ports: PreviewPorts, pv: PreviewData, audio_path) -> None:
    # A fresh preview starts un-zoomed; audio no longer autoplays — click the ▶ button to hear it.
    # replay (P) / a new mine silences any clip still playing
    _stop_preview_audio(ports.preview.panel)
    ports.preview.store.dispatch(events.PreviewShown(pv, audio_path))
    render_preview(ports.preview, ports.surfaces, ports.osd, ports.tip_width)
    _grab_preview_keys(ports.ipc, active_bindings(ports.keys, "preview"))


def render_preview(
    preview: PreviewController, surfaces, osd: tuple[int, int], tip_width: int
) -> None:
    """Blit the card preview and record where each of its buttons landed.

    The slice says what to draw and at what magnification; the panel is where the answer goes, and
    it is the object rather than a snapshot because the rects have to survive the call.
    """
    shown = preview.state
    if shown.content is None:
        return
    pv = cast("PreviewData", shown.content)
    panel = preview.panel
    pr = render_card_preview(pv, width=max(440, tip_width), zoom=shown.zoom)
    px, py = round(osd[0] * 0.03), round(osd[1] * 0.06)
    surfaces.present(pr.image, px, py, oid=OverlayId.PREVIEW)
    panel.rect = (px, py, pr.image.width, pr.image.height)

    def _screen(r):
        return (px + r[0], py + r[1], r[2], r[3]) if r else None

    panel.close_rect = _screen(pr.close_rect)
    panel.audio_rect = _screen(pr.audio_rect)
    panel.image_rect = _screen(pr.image_rect)
    panel.dup_rect = _screen(pr.dup_rect)


def hide_preview(ports: PreviewPorts) -> None:
    # every dismiss path (✕ / Esc / new-cue) funnels here
    _stop_preview_audio(ports.preview.panel)
    ports.surfaces.remove(OverlayId.PREVIEW)
    ports.preview.store.dispatch(events.PreviewDismissed())
    ports.preview.panel.clear()
    _release_preview_keys(
        ports.ipc,
        active_bindings(ports.keys, "preview"),
        help_open=ports.help_open,
        tip_keys_bound=ports.tip_keys_bound,
    )


def click_preview(ports: PreviewPorts, x: float, y: float) -> bool:
    """Handle a click on the card preview: ✕ dismiss, screenshot → toggle enlarge, ▶ → play audio.
    An empty click does nothing. Returns True if the click landed on the preview."""
    panel = ports.preview.panel
    if panel.rect is None or not in_rect(panel.rect, x, y):
        return False
    if panel.close_rect and in_rect(panel.close_rect, x, y):
        hide_preview(ports)
    elif panel.dup_rect and in_rect(panel.dup_rect, x, y):
        ports.add_duplicate()  # ＋ add anyway → mine a second card for this scene
    elif panel.image_rect and in_rect(panel.image_rect, x, y):
        # enlarge to verify the frame / shrink back
        ports.preview.store.dispatch(events.PreviewZoomToggled())
        render_preview(ports.preview, ports.surfaces, ports.osd, ports.tip_width)
    elif (
        panel.audio_rect
        and in_rect(panel.audio_rect, x, y)
        and ports.play_audio
        and ports.preview.state.audio
    ):
        _stop_preview_audio(panel)  # a second ▶ press replaces the clip, never stacks two
        clip = cast("str | Path", ports.preview.state.audio)
        panel.audio_proc = play_audio(clip)  # ▶ → play on demand
    return True


def replay_preview(ports: PreviewPorts) -> None:
    shown = ports.preview.state
    if shown.content is not None:
        show_preview(ports, cast("PreviewData", shown.content), shown.audio)

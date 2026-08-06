"""Card preview UI: verify a mined (or already-in-deck) card's expression/reading/image/audio/glosses
before or after mining, plus the mined-state feedback that flips a tooltip's ⊕ to ✓.

Mining itself (Anki note creation, media capture, provenance/tags) lives in :class:`~overlay.app.miner.Miner`
— this module is the Reader-side glue: rendering the preview panel, handling clicks on it (dismiss /
zoom / play), and re-rendering an already-open tooltip or nested popup once a word gets mined. Takes
``reader: Reader`` (the AGENTS.md seam pattern) with thin delegating methods on Reader itself.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from overlay.app.anki import AnkiError
from overlay.app.bindings import TIP_CLOSE_MSG, active_bindings
from overlay.app.card_preview import PreviewData, render_card_preview
from overlay.app.media import audio_duration, play_audio
from overlay.app.overlay_ids import OverlayId

if TYPE_CHECKING:
    from overlay.app.controller import Reader


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s).strip()


def _html_lines(html: str) -> list[str]:
    parts = re.split(r"<br\s*/?>", html or "")
    return [t for t in (_strip_tags(p) for p in parts) if t]


def _html_items(html: str) -> list[str]:
    return [_strip_tags(m) for m in re.findall(r"<li>(.*?)</li>", html or "", re.DOTALL)]


def _media_name(field_html: str, pattern: str) -> str:
    m = re.search(pattern, field_html or "")
    return m.group(1) if m else ""


def mark_mined(reader: Reader, expression: str) -> None:
    """Record a word as in-deck and refresh the shown popups so their ⊕ flips to ✓ immediately."""
    if not expression:
        return
    reader._mined.add(expression)
    if reader.hover >= 0 and reader._tip_state is not None:
        reader._show_tooltip(reader.hover)  # rebuild the base tooltip (✓ if it's this word)
    if reader._nest.state is not None and reader._nest.token is not None:
        rerender_nested(reader)  # and the nested popup


def rerender_nested(reader: Reader) -> None:
    """Rebuild the nested popup in place with the current mined-state, keeping its position."""
    tok = reader._nest.token
    if tok is None:
        return
    mined = reader._is_mined(tok)
    st = reader._panel_for(tok, tok.surface, min_h=reader._tip_cap(), mined=mined)
    reader._nest.state = st
    reader._nest.key = reader._panel_key(tok, tok.surface, mined=mined)
    reader._render_nested_view()


def sentence_lines(reader: Reader) -> list[str]:
    return ["".join(t.surface for t in line) for line in reader.lines]


def footer(reader: Reader, video) -> str:
    assert reader.mine_cfg is not None  # previews only exist after a mine
    return f"{reader.mine_cfg.deck} · {reader.mine_cfg.model} · {reader._provenance(video)}"


def preview_mined(reader: Reader, card, tok, video, status: str = "mined") -> None:
    img = None
    if reader.preview.last_jpg and Path(reader.preview.last_jpg).exists():
        img = Image.open(reader.preview.last_jpg)
    secs = audio_duration(reader.preview.last_audio) if reader.preview.last_audio else None
    pv = PreviewData(
        status,
        card.expression,
        card.reading,
        sentence_lines(reader),
        tok.surface,
        list(card.glosses),
        img,
        secs,
        footer(reader, video),
    )
    show_preview(reader, pv, reader.preview.last_audio)


def preview_existing(reader: Reader, note_id: int, card, status: str) -> None:
    from overlay.app.anki import AnkiError

    assert reader.anki is not None and reader.mine_cfg is not None  # duplicate path = mining on
    try:
        info = reader.anki.notes_info([note_id])
    except AnkiError:
        info = []
    if not info:
        reader._toast(f"already have {card.expression}", "warn")
        return
    f, fld = info[0]["fields"], reader.mine_cfg.fields

    def val(logical):
        return f.get(fld.get(logical, ""), {}).get("value", "")

    img = media_image(reader, _media_name(val("picture"), r'src="([^"]+)"'))
    mp3 = media_tempfile(reader, _media_name(val("audio"), r"\[sound:([^\]]+)\]"))
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
        footer(reader, reader._get("path")),
    )
    show_preview(reader, pv, mp3)


def media_image(reader: Reader, name):
    if not name or reader.anki is None:
        return None
    try:
        data = reader.anki.retrieve_media(name)
        return Image.open(io.BytesIO(data)) if data else None
    except (OSError, AnkiError, json.JSONDecodeError):
        return None


def media_tempfile(reader: Reader, name):
    if not name or reader.anki is None:
        return None
    try:
        data = reader.anki.retrieve_media(name)
        if not data:
            return None
        p = reader._tmp / name
        p.write_bytes(data)
        return p
    except (OSError, AnkiError, json.JSONDecodeError):
        return None


def _grab_preview_keys(reader: Reader) -> None:
    """Route the preview-scoped keys (Esc → close) to the preview while it's on screen."""
    for b in active_bindings(reader, "preview"):
        reader.ipc.command("keybind", b.key, f"script-message {b.spec.message}")


def _release_preview_keys(reader: Reader) -> None:
    """Hand the preview's keys back when it closes. Esc is shared with the tooltip, so return it there
    if a tooltip is still up (the help overlay, if open, owns Esc itself — leave it alone)."""
    if reader._help_open:
        return
    for b in active_bindings(reader, "preview"):
        if b.key == "ESC" and reader._tip_keys_bound:
            reader.ipc.command("keybind", b.key, f"script-message {TIP_CLOSE_MSG}")
        else:
            reader.ipc.command("keybind", b.key, "ignore")


def show_preview(reader: Reader, pv: PreviewData, audio_path) -> None:
    # A fresh preview starts un-zoomed; audio no longer autoplays — click the ▶ button to hear it.
    reader.preview.last_preview, reader.preview.last_audio = pv, audio_path
    reader.preview.zoom = False
    render_preview(reader)
    _grab_preview_keys(reader)


def render_preview(reader: Reader) -> None:
    pv = reader.preview.last_preview
    if pv is None:
        return
    pr = render_card_preview(pv, width=max(440, reader.tip_width), zoom=reader.preview.zoom)
    px, py = round(reader.osd[0] * 0.03), round(reader.osd[1] * 0.06)
    reader.ov.show(pr.image, px, py, oid=OverlayId.PREVIEW)
    reader.preview.rect = (px, py, pr.image.width, pr.image.height)

    def _screen(r):
        return (px + r[0], py + r[1], r[2], r[3]) if r else None

    reader.preview.close_rect = _screen(pr.close_rect)
    reader.preview.audio_rect = _screen(pr.audio_rect)
    reader.preview.image_rect = _screen(pr.image_rect)
    reader.preview.dup_rect = _screen(pr.dup_rect)


def hide_preview(reader: Reader) -> None:
    reader.ov.hide(OverlayId.PREVIEW)
    reader.preview.clear()
    _release_preview_keys(reader)


def click_preview(reader: Reader, x: float, y: float) -> bool:
    """Handle a click on the card preview: ✕ dismiss, screenshot → toggle enlarge, ▶ → play audio.
    An empty click does nothing. Returns True if the click landed on the preview."""
    if reader.preview.rect is None or not reader._in_rect(reader.preview.rect, x, y):
        return False
    if reader.preview.close_rect and reader._in_rect(reader.preview.close_rect, x, y):
        hide_preview(reader)
    elif reader.preview.dup_rect and reader._in_rect(reader.preview.dup_rect, x, y):
        reader._add_duplicate()  # ＋ add anyway → mine a second card for this scene
    elif reader.preview.image_rect and reader._in_rect(reader.preview.image_rect, x, y):
        reader.preview.zoom = not reader.preview.zoom
        render_preview(reader)  # enlarge to verify the frame / shrink back
    elif (
        reader.preview.audio_rect
        and reader._in_rect(reader.preview.audio_rect, x, y)
        and reader.play_audio
        and reader.preview.last_audio
    ):
        play_audio(reader.preview.last_audio)  # ▶ → play the mined clip on demand
    return True


def replay_preview(reader: Reader) -> None:
    if reader.preview.last_preview:
        show_preview(reader, reader.preview.last_preview, reader.preview.last_audio)

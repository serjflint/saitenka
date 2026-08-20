"""Card preview UI: verify a mined (or already-in-deck) card's expression/reading/image/audio/glosses
before or after mining.

Mining itself (Anki note creation, media capture, provenance/tags) lives in :class:`~saitenka.app.miner.Miner`
— this module is the Reader-side glue for one INTERACTION surface: rendering the preview panel and
handling clicks on it (dismiss / zoom / play). The ⊕→✓ feedback is
:mod:`~saitenka.app.mined_feedback`; it writes a session fact and redraws the popups, which this
surface is not one of.

Two ports: `PreviewPorts` is the surface (what it draws on and what a click can do), `CardSource`
is where a preview's content comes from. They are separate because the second is Anki and the cue,
neither of which the surface has any business reaching.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from saitenka.app.anki import AnkiError
from saitenka.app.bindings import TIP_CLOSE_MSG, active_bindings
from saitenka.app.card_preview import PreviewData, render_card_preview
from saitenka.app.media import audio_duration, play_audio
from saitenka.app.mpv_egress import send_correlated
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.procutil import kill_process_tree
from saitenka.model import in_rect
from saitenka.runtime import Owner

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path as PathType

    from saitenka.app.anki import Anki, MineConfig
    from saitenka.app.card_preview import PreviewState
    from saitenka.app.config import KeyOptions
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.reader_context import InteractionContext
    from saitenka.app.tokenize import Token


@dataclass(frozen=True, slots=True)
class PreviewPorts:
    """What the card-preview surface draws on, and what a click on it can do.

    Cut by owner like the surface registry's ports: the INTERACTION state (the preview itself, and
    the help/tooltip whose key ownership a dismiss has to respect), the display it blits to, and
    the one act a button performs.
    """

    interaction: InteractionContext
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

    anki: Anki | None
    mine_cfg: MineConfig | None
    #: The cue's tokenized lines — `sentence_lines` rejoins them for the card's sentence field.
    lines: list[list[Token]]
    provenance: Callable[[object], str]
    video_path: Callable[[], object]
    tmp: PathType
    toast: Callable[..., None]


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


def sentence_lines(lines) -> list[str]:
    """The cue's tokenized lines rejoined into plain text, one string per line."""
    return ["".join(token.surface for token in line) for line in lines]


def footer(mine_cfg, provenance: str) -> str:
    """Where the card went and where its media came from — the preview's one line of provenance."""
    assert mine_cfg is not None  # previews only exist after a mine
    return f"{mine_cfg.deck} · {mine_cfg.model} · {provenance}"


def preview_mined(
    ports: PreviewPorts, source: CardSource, card, tok, video, status: str = "mined"
) -> None:
    preview = ports.interaction.preview
    img = None
    if preview.last_jpg and Path(preview.last_jpg).exists():
        img = Image.open(preview.last_jpg)
    secs = audio_duration(preview.last_audio) if preview.last_audio else None
    pv = PreviewData(
        status,
        card.expression,
        card.reading,
        sentence_lines(source.lines),
        tok.surface,
        list(card.glosses),
        img,
        secs,
        footer(source.mine_cfg, source.provenance(video)),
    )
    show_preview(ports, pv, preview.last_audio)


def preview_existing(
    ports: PreviewPorts, source: CardSource, note_id: int, card, status: str
) -> None:
    from saitenka.app.anki import AnkiError

    assert source.anki is not None and source.mine_cfg is not None  # duplicate path = mining on
    try:
        info = source.anki.notes_info([note_id])
    except AnkiError:
        info = []
    if not info:
        source.toast(f"already have {card.expression}", "warn")
        return
    f, fld = info[0]["fields"], source.mine_cfg.fields

    def val(logical):
        return f.get(fld.get(logical, ""), {}).get("value", "")

    img = media_image(source.anki, _media_name(val("picture"), r'src="([^"]+)"'))
    mp3 = media_tempfile(source.anki, _media_name(val("audio"), r"\[sound:([^\]]+)\]"), source.tmp)
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
        footer(source.mine_cfg, source.provenance(source.video_path())),
    )
    show_preview(ports, pv, mp3)


def media_image(anki, name):
    """Fetch a media file from Anki as an image, or None.

    Every failure is None on purpose: a preview missing its screenshot is a preview, and Anki being
    down is an ordinary state for an optional integration rather than something to raise through a
    keypress.
    """
    if not name or anki is None:
        return None
    try:
        data = anki.retrieve_media(name)
        return Image.open(io.BytesIO(data)) if data else None
    except (OSError, AnkiError, json.JSONDecodeError):
        return None


def media_tempfile(anki, name, tmp_dir):
    """Fetch a media file from Anki onto disk under ``tmp_dir``, or None. Fails soft, as above."""
    if not name or anki is None:
        return None
    try:
        data = anki.retrieve_media(name)
        if not data:
            return None
        path = tmp_dir / name
        path.write_bytes(data)
        return path
    except (OSError, AnkiError, json.JSONDecodeError):
        return None


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


def _stop_preview_audio(preview) -> None:
    """Kill the ▶ clip's player if one is running (afplay / mpv --no-video / ffplay) — the fire-and-
    forget Popen outlives the panel otherwise, so a dismiss left the clip playing (#251). Idempotent."""
    proc, preview.audio_proc = preview.audio_proc, None
    kill_process_tree(proc)  # no-op on None / an already-exited process


def show_preview(ports: PreviewPorts, pv: PreviewData, audio_path) -> None:
    # A fresh preview starts un-zoomed; audio no longer autoplays — click the ▶ button to hear it.
    preview = ports.interaction.preview
    _stop_preview_audio(preview)  # replay (P) / a new mine silences any clip still playing
    preview.last_preview, preview.last_audio = pv, audio_path
    preview.zoom = False
    render_preview(preview, ports.surfaces, ports.osd, ports.tip_width)
    _grab_preview_keys(ports.ipc, active_bindings(ports.keys, "preview"))


def render_preview(preview: PreviewState, surfaces, osd: tuple[int, int], tip_width: int) -> None:
    """Blit the card preview and record where each of its buttons landed.

    Takes the four facts. `preview` is both the input (what to draw, zoomed or not) and the output
    (the screen rects the click handler tests against) — which is why it is the state object and not
    a snapshot: the rects have to survive the call.
    """
    pv = preview.last_preview
    if pv is None:
        return
    pr = render_card_preview(pv, width=max(440, tip_width), zoom=preview.zoom)
    px, py = round(osd[0] * 0.03), round(osd[1] * 0.06)
    surfaces.present(pr.image, px, py, oid=OverlayId.PREVIEW)
    preview.rect = (px, py, pr.image.width, pr.image.height)

    def _screen(r):
        return (px + r[0], py + r[1], r[2], r[3]) if r else None

    preview.close_rect = _screen(pr.close_rect)
    preview.audio_rect = _screen(pr.audio_rect)
    preview.image_rect = _screen(pr.image_rect)
    preview.dup_rect = _screen(pr.dup_rect)


def hide_preview(ports: PreviewPorts) -> None:
    preview = ports.interaction.preview
    _stop_preview_audio(preview)  # every dismiss path (✕ / Esc / new-cue) funnels here
    ports.surfaces.remove(OverlayId.PREVIEW)
    preview.clear()
    _release_preview_keys(
        ports.ipc,
        active_bindings(ports.keys, "preview"),
        help_open=ports.interaction.help.open,
        tip_keys_bound=ports.interaction.tip.tip_keys_bound,
    )


def click_preview(ports: PreviewPorts, x: float, y: float) -> bool:
    """Handle a click on the card preview: ✕ dismiss, screenshot → toggle enlarge, ▶ → play audio.
    An empty click does nothing. Returns True if the click landed on the preview."""
    preview = ports.interaction.preview
    if preview.rect is None or not in_rect(preview.rect, x, y):
        return False
    if preview.close_rect and in_rect(preview.close_rect, x, y):
        hide_preview(ports)
    elif preview.dup_rect and in_rect(preview.dup_rect, x, y):
        ports.add_duplicate()  # ＋ add anyway → mine a second card for this scene
    elif preview.image_rect and in_rect(preview.image_rect, x, y):
        preview.zoom = not preview.zoom
        # enlarge to verify the frame / shrink back
        render_preview(preview, ports.surfaces, ports.osd, ports.tip_width)
    elif (
        preview.audio_rect
        and in_rect(preview.audio_rect, x, y)
        and ports.play_audio
        and preview.last_audio
    ):
        _stop_preview_audio(preview)  # a second ▶ press replaces the clip, never stacks two
        preview.audio_proc = play_audio(preview.last_audio)  # ▶ → play on demand
    return True


def replay_preview(ports: PreviewPorts) -> None:
    preview = ports.interaction.preview
    if preview.last_preview:
        show_preview(ports, preview.last_preview, preview.last_audio)

"""One manual Kiku merge; the field update is the irreversible commit point."""

from __future__ import annotations

import html
import logging

from saitenka_card import bold_word
from saitenka_card.merge import merge_scene, scene_ids

from saitenka.app.features.mining import miner
from saitenka.app.features.mining.merge_offer import MergeOffer, note_fields
from saitenka.app.media import source_screenshot

log = logging.getLogger(__name__)


def _current_fields(p: miner.MiningTransaction, offer: MergeOffer) -> dict[str, str]:
    rows = p.anki.notes_info([offer.note_id])
    if not rows or rows[0].get("noteId") != offer.note_id:
        raise ValueError("retained note is missing")
    if rows[0].get("modelName") != p.mine_cfg.model:
        raise ValueError("retained note model changed")
    return note_fields(rows[0])


def _unchanged(values: dict[str, str], offer: MergeOffer) -> None:
    if values != dict(offer.fields):
        raise ValueError("retained note changed; reopen its preview before merging")


def _prepare_merge(
    p: miner.MiningTransaction, offer: MergeOffer, base: str
) -> dict[str, str] | None:
    current = _current_fields(p, offer)
    if offer.group_id in scene_ids(current):
        if f"<!--saitenka-encounter:{offer.identity}-->" not in current["MiscInfo"]:
            raise ValueError("Kiku scene identity collision")
        p.apply.toast("scene already attached", "ok")
        return None
    _unchanged(current, offer)
    still = source_screenshot(
        str(p.encounter.media_path), p.encounter.playhead, p.scratch / f"{base}.jpg"
    )
    capture = miner.PreparedCapture(
        base,
        p.encounter.span,
        miner.media_needs(p.mine_cfg),
        still,
        None,
        p.mine_cfg.animated.enabled,
    )
    picture, picture_error = miner._capture_image(p, p.encounter.media_path, capture)
    audio, audio_error = miner._capture_audio(p, base, p.encounter.span)
    if p.cancelled():
        p.apply.toast("merge cancelled before update", "warn")
        return None
    if picture_error or audio_error or not picture or not audio:
        raise ValueError("scene media preparation failed; retained note unchanged")
    sentence = bold_word(p.encounter.sentence_html, offer.surface)
    scene = {
        "Sentence": sentence,
        "SentenceFurigana": sentence,
        "SentenceAudio": f"[sound:{audio}]",
        "Picture": f'<img src="{picture}">',
        "MiscInfo": html.escape(miner.provenance(p.encounter.playhead, p.encounter.media_path))
        + f"<!--saitenka-encounter:{offer.identity}-->",
    }
    updated = merge_scene(dict(offer.fields), scene, offer.note_id, offer.group_id)
    _unchanged(_current_fields(p, offer), offer)
    return updated


def commit_merge(p: miner.MiningTransaction, offer: MergeOffer, base: str) -> None:
    dispatched = False
    try:
        if p.cancelled():
            return
        updated = _prepare_merge(p, offer, base)
        if updated is None:
            return
        if p.cancelled():
            p.apply.toast("merge cancelled before update", "warn")
            return
        dispatched = True
        p.anki.update_note_fields(offer.note_id, updated)
        p.apply.toast("scene attached to retained note", "ok")
    except Exception as error:
        log.debug("merge failed", exc_info=True)
        message = (
            "merge outcome unknown; check Anki before retrying"
            if dispatched
            else f"merge failed: {error}"
        )
        p.apply.toast(message, "err")

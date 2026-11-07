"""A preview-bound snapshot of a declared retained-note merge."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka_card.merge import scene_ids, supports_kiku

from saitenka.app.anki import AnkiError

if TYPE_CHECKING:
    from saitenka_tokenize.japanese import Token

    from saitenka.app.features.mining import miner

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MergeOffer:
    note_id: int
    surface: str
    encounter: miner.MiningEncounter
    fields: tuple[tuple[str, str], ...]
    group_id: str
    identity: str


def note_fields(info: dict) -> dict[str, str]:
    return {name: field["value"] for name, field in info["fields"].items()}


def offer_merge(p: miner.MiningTransaction, note_id: int, token: Token) -> MergeOffer | None:
    if not supports_kiku(p.mine_cfg) or p.encounter.span is None or not p.encounter.media_path:
        return None
    try:
        rows = p.anki.notes_info([note_id])
        if (
            not rows
            or rows[0].get("noteId") != note_id
            or rows[0].get("modelName") != p.mine_cfg.model
        ):
            return None
        values = note_fields(rows[0])
        scene_ids(values)
        span = p.encounter.authored_span or p.encounter.span
        identity = json.dumps(
            [note_id, str(p.encounter.media_path), span.start, span.end], ensure_ascii=False
        )
        digest = hashlib.sha256(identity.encode()).hexdigest()
        group_id = str(int(digest[:12], 16) + 1)
        return MergeOffer(
            note_id, token.surface, p.encounter, tuple(values.items()), group_id, digest
        )
    except (OSError, ValueError, KeyError, TypeError, AnkiError):
        log.debug("retained note has no supported merge contract", exc_info=True)
        return None

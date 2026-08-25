"""Best-effort Anki media retrieval for mining-backed previews."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

from PIL import Image

from saitenka.app.anki import AnkiError

if TYPE_CHECKING:
    from pathlib import Path


def media_image(anki, name: str):
    """Fetch an Anki media file as an image, or return ``None`` when unavailable."""
    if not name or anki is None:
        return None
    try:
        data = anki.retrieve_media(name)
        return Image.open(io.BytesIO(data)) if data else None
    except (OSError, AnkiError, json.JSONDecodeError):
        return None


def media_tempfile(anki, name: str, tmp_dir: Path) -> Path | None:
    """Fetch Anki media into ``tmp_dir``, or return ``None`` when unavailable."""
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

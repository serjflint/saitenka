"""Explicit local attachments; exported bundles are user-owned and never auto-expire."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

MAX_ATTACHMENTS = 4
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".ass", ".srt", ".json", ".txt"})


def collect_attachments(paths: tuple[Path, ...]) -> dict[str, bytes]:
    if len(paths) > MAX_ATTACHMENTS:
        raise ValueError("at most four explicit diagnostic attachments")
    members: dict[str, bytes] = {}
    entries = []
    for index, path in enumerate(paths, 1):
        suffix = path.suffix.lower()
        if suffix not in SUFFIXES or not path.is_file():
            raise ValueError("unsupported diagnostic attachment")
        with path.open("rb") as stream:
            raw = stream.read(MAX_ATTACHMENT_BYTES + 1)
        if len(raw) > MAX_ATTACHMENT_BYTES:
            raise ValueError("diagnostic attachment exceeds 8 MiB limit")
        name = f"attachments/{index:02}{suffix}"
        members[name] = raw
        entries.append({"member": name, "bytes": len(raw), "privacy": "unredacted-user-attachment"})
    if entries:
        members["attachments/manifest.json"] = json.dumps(
            {
                "schema": 1,
                "files": entries,
                "expires": "never",
                "uploads": "none",
                "warning": "May contain readable private text and pixels. Review before sharing.",
            }
        ).encode()
    return members

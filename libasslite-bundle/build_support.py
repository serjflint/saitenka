from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def validate_payload(root: Path) -> None:
    package = root / "src" / "libasslite_bundle"
    manifest = json.loads((package / "native-manifest.json").read_text(encoding="utf-8"))
    library = manifest.get("library")
    if not isinstance(library, str) or "PAYLOAD_NOT_BUILT" in library:
        raise RuntimeError("bundle wheels require a staged native payload")
    if not package.joinpath(library).is_file():
        raise RuntimeError(f"bundle primary library is missing: {library}")
    if (
        "This source tree contains no native binaries"
        in (root / "THIRD_PARTY_LICENSES").read_text()
    ):
        raise RuntimeError("bundle wheels require generated dependency notices")

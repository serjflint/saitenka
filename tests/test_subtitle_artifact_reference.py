"""External and embedded track artifacts plus extraction arguments."""

import json
from pathlib import Path

import pytest

from saitenka.app.subtitle_artifact import (
    ArtifactUnavailable,
    EmbeddedArtifact,
    ExternalArtifact,
    extract_spec,
    resolve,
)

DATA = json.loads((Path(__file__).parent / "fixtures/subtitle_reference.json").read_text())


@pytest.mark.parametrize("record", DATA["artifacts"], ids=lambda r: r["id"])
def test_track_artifact_reference(record):
    v = record["input"]
    artifact = resolve(v["track"], media_path=v["media_path"])
    if isinstance(artifact, ExternalArtifact):
        assert record["expected"] == {"kind": "external", "path": artifact.path}
    elif isinstance(artifact, EmbeddedArtifact):
        assert record["expected"] == {
            "kind": "embedded",
            "media_path": artifact.media_path,
            "stream_index": artifact.ff_index,
            "codec": artifact.codec,
        }
    else:
        assert isinstance(artifact, ArtifactUnavailable)
        assert record["expected"] == {"unavailable": artifact.value}


@pytest.mark.parametrize("record", DATA["extract"], ids=lambda r: r["id"])
def test_extraction_arguments_reference(record):
    suffix, args = extract_spec(record["input"]["codec"])
    assert {"suffix": suffix, "args": list(args)} == record["expected"]

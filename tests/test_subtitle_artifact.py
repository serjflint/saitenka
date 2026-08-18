"""WP4.2: an unresolvable subtitle artifact is a named outcome, not an exception."""

from __future__ import annotations

import pytest

from saitenka.app.subtitle_artifact import (
    ArtifactUnavailable,
    EmbeddedArtifact,
    ExternalArtifact,
    resolve,
)


def test_an_external_track_resolves_to_its_file() -> None:
    track = {"external": True, "external-filename": "/subs/ep1.ass"}

    assert resolve(track, media_path="/media/ep1.mkv") == ExternalArtifact("/subs/ep1.ass")


def test_an_external_track_needs_no_media_path() -> None:
    track = {"external": True, "external-filename": "/subs/ep1.ass"}

    assert resolve(track, media_path=None) == ExternalArtifact("/subs/ep1.ass")


def test_an_embedded_track_resolves_to_a_stream_in_the_media() -> None:
    track = {"external": False, "ff-index": 3}

    assert resolve(track, media_path="/media/ep1.mkv") == EmbeddedArtifact("/media/ep1.mkv", 3)


@pytest.mark.parametrize(
    ("track", "media_path", "reason"),
    [
        (None, "/media/ep1.mkv", ArtifactUnavailable.NO_TRACK_SELECTED),
        (
            {"external": True, "external-filename": None},
            "/media/ep1.mkv",
            ArtifactUnavailable.EXTERNAL_PATH_MISSING,
        ),
        (
            {"external": True},
            "/media/ep1.mkv",
            ArtifactUnavailable.EXTERNAL_PATH_MISSING,
        ),
        (
            {"external": False},
            "/media/ep1.mkv",
            ArtifactUnavailable.EMBEDDED_STREAM_UNIDENTIFIED,
        ),
        (
            {"external": False, "ff-index": 3},
            None,
            ArtifactUnavailable.MEDIA_PATH_MISSING,
        ),
        (
            {"external": False, "ff-index": 3},
            "",
            ArtifactUnavailable.MEDIA_PATH_MISSING,
        ),
    ],
)
def test_an_unresolvable_artifact_names_its_reason(
    track: dict | None, media_path: object, reason: ArtifactUnavailable
) -> None:
    assert resolve(track, media_path=media_path) is reason


def test_resolution_never_raises_on_a_malformed_track() -> None:
    """mpv's track-list is untrusted input; a surprising entry degrades lookahead, nothing more."""
    for track in ({}, {"external": 0}, {"external": None, "ff-index": 0}):
        assert resolve(track, media_path="/media/ep1.mkv") is not None

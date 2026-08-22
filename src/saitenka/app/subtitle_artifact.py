"""Pure resolution of the authored subtitle artifact behind mpv's selected track.

Deciding *which* artifact backs the current track — an external file already on disk, or an
embedded stream that has to be extracted — is separate from loading it. The decision is a function
of the track-list entry and the media path; an artifact that cannot be resolved is a named,
bounded outcome, never an exception and never a reason to change who owns the visible pixels.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ArtifactUnavailable(StrEnum):
    """Why no artifact could be resolved. Each disables lookahead only."""

    NO_TRACK_SELECTED = "no-track-selected"
    #: An external track whose `external-filename` mpv did not report.
    EXTERNAL_PATH_MISSING = "external-path-missing"
    #: An embedded track with no `ff-index`, so nothing identifies the stream to extract.
    EMBEDDED_STREAM_UNIDENTIFIED = "embedded-stream-unidentified"
    #: An embedded track with no media path to extract from.
    MEDIA_PATH_MISSING = "media-path-missing"


@dataclass(frozen=True, slots=True)
class ExternalArtifact:
    """An authored subtitle file already on disk; load it directly."""

    path: str


@dataclass(frozen=True, slots=True)
class EmbeddedArtifact:
    """A subtitle stream inside the container; extract `ff_index` from `media_path` once."""

    media_path: str
    ff_index: int
    codec: str = ""


type Artifact = ExternalArtifact | EmbeddedArtifact
type ArtifactResolution = Artifact | ArtifactUnavailable


def resolve(track: dict | None, *, media_path: object) -> ArtifactResolution:
    """Decide which artifact backs `track`, or why none can be."""
    if track is None:
        return ArtifactUnavailable.NO_TRACK_SELECTED
    if track.get("external"):
        external = track.get("external-filename")
        if not external:
            return ArtifactUnavailable.EXTERNAL_PATH_MISSING
        return ExternalArtifact(str(external))
    ff_index = track.get("ff-index")
    if ff_index is None:
        return ArtifactUnavailable.EMBEDDED_STREAM_UNIDENTIFIED
    if not media_path:
        return ArtifactUnavailable.MEDIA_PATH_MISSING
    return EmbeddedArtifact(str(media_path), int(ff_index), str(track.get("codec") or ""))


def extract_spec(codec: str) -> tuple[str, tuple[str, ...]]:
    """(file suffix, ffmpeg ``-c:s`` args) for extracting an embedded sub of *codec*.

    ASS/SSA are COPIED to a native ``.ass``, never transcoded, for two independent reasons: ffmpeg's
    srt conversion injects ``<font>``/``<b>`` tags a strict SubRip parser rejects (live: ep02's
    ``<b>Edição</b>`` → ``alass-cli`` exit 1 → subs left several seconds late), and native-visible
    geometry only accepts an authored ASS document — a transcode makes the track noninteractive.
    subrip is copied verbatim; anything else (mov_text/webvtt) converts to srt, clean for those.
    """
    if codec in {"ass", "ssa"}:
        return ".ass", ("-c:s", "copy")
    if codec in {"subrip", "srt"}:
        return ".srt", ("-c:s", "copy")
    return ".srt", ("-c:s", "srt")

"""Provider-neutral subtitle geometry contracts."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field, replace
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from saitenka.subtitles.document import SubtitleEventId, SubtitleFrameId, SubtitleTrackId

MAX_FRAME_PIXELS = 16_777_216
MAX_GEOMETRY_TOKENS = 4_096
MAX_BITMAP_BYTES = 16 * 1_024 * 1_024


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


class FontProvider(IntEnum):
    """libass's `ASS_DefaultFontProvider`, named here so the contract stays provider-neutral."""

    NONE = 0
    AUTODETECT = 1
    CORETEXT = 2
    FONTCONFIG = 3
    DIRECTWRITE = 4


@dataclass(frozen=True, slots=True)
class FontSetup:
    """Font lookup a renderer must be given before it can lay a cue out.

    Separate from ``attachments`` because these are settings and those are payload, and because a
    backend applies them at different points: the directory and the extraction flag before the
    document is parsed, the lookup defaults after the renderer exists.
    """

    fonts_dir: str | None = None
    extract_fonts: bool = False
    default_font: str | None = None
    default_family: str | None = None
    fontconfig_config: str | None = None
    font_provider: FontProvider = FontProvider.AUTODETECT


@dataclass(frozen=True, slots=True)
class RendererState:
    """Renderer state a host sets per frame, which a measuring renderer has to set identically.

    `font_scale` is not decoration: on a track the host converted it is a letterbox-dependent
    multiplier, not 1, so a renderer that leaves it alone lays every box out at the wrong size —
    uniformly, which is the hardest kind of wrong to notice.

    `features` are `ASS_Feature` flags applied to the track before its first render, as
    `(feature, enabled)` pairs; they change how a run's advances accumulate.

    `blur` and `justify` are here rather than in the document because a V4+ `Style:` row has no
    field for either — mpv writes both onto the libass style struct directly, so the only way to
    reproduce them is a selective style override on the renderer. `justify` decides where every
    line of a multi-line cue starts, which is a box position, not a decoration.
    """

    font_scale: float = 1.0
    features: tuple[tuple[int, bool], ...] = ()
    blur: float = 0.0
    justify: int = 0


@dataclass(frozen=True, slots=True)
class TokenGeometry:
    """Geometry for one semantic token; ``bounds`` is the stable UI anchor."""

    event_id: SubtitleEventId
    token_index: int
    bounds: Rect
    regions: tuple[Rect, ...] = ()
    #: Copied from the palette entry that produced this token — see `GeometryPaletteEntry`.
    font_name: str = ""
    font_size: float = 0.0
    #: The token's own anti-aliased coverage, row-major over `bounds`, one byte per pixel — the
    #: measuring render's output kept instead of thrown away once the extent was read off it.
    #: This is what lets the color be painted as a raster when the text device cannot draw the
    #: face; tinting a mask IS the raster, so the second device costs no second render. Empty when
    #: the backend was not asked to keep it.
    coverage: bytes = b""


class GeometryVariant(StrEnum):
    NATIVE = "native"
    STYLED = "styled"
    HIT_MAP = "geometry-map"


@dataclass(frozen=True, slots=True, order=True)
class GeometryPaletteEntry:
    event_id: SubtitleEventId
    token_index: int
    rgb: int
    #: The face and size this token is laid out in, in the FRAME's units rather than the document's
    #: script units — so an overprint can draw the same glyph at the same size without redoing
    #: libass's scaling. Empty when the document did not resolve one; the overprint then leaves that
    #: token uncolored rather than drawing it at a guess.
    font_name: str = ""
    font_size: float = 0.0

    def __post_init__(self) -> None:
        if self.token_index < 0 or isinstance(self.rgb, bool) or not 0 < self.rgb <= 0xFFFFFF:
            raise ValueError("geometry palette entries require a token index and 24-bit RGB")


def _validate_render_space(request: GeometryRequest) -> None:
    if request.timestamp_ms < 0:
        raise ValueError("geometry timestamp must be non-negative")
    if any(value <= 0 for size in (request.frame_size, request.storage_size) for value in size):
        raise ValueError("geometry frame and storage sizes must be positive")
    if request.frame_size[0] * request.frame_size[1] > MAX_FRAME_PIXELS:
        raise ValueError("geometry frame pixel limit exceeded")
    if not math.isfinite(request.pixel_aspect) or request.pixel_aspect <= 0:
        raise ValueError("geometry pixel aspect must be finite and positive")
    if any(isinstance(value, bool) or value < 0 for value in request.margins):
        raise ValueError("geometry margins must be non-negative integers")
    top, bottom, left, right = request.margins
    if top + bottom >= request.frame_size[1] or left + right >= request.frame_size[0]:
        raise ValueError("geometry margins must leave a positive video rectangle")
    if not isinstance(request.use_margins, bool):
        raise TypeError("geometry use_margins must be a bool")


def _validate_palette(request: GeometryRequest) -> None:
    if len({entry.rgb for entry in request.palette}) != len(request.palette):
        raise ValueError("geometry palette colors must be unique")
    identities = {(entry.event_id, entry.token_index) for entry in request.palette}
    if len(identities) != len(request.palette):
        raise ValueError("geometry palette token identities must be unique")
    if len(request.palette) > MAX_GEOMETRY_TOKENS:
        raise ValueError("geometry palette entry limit exceeded")
    active = set(request.frame_id.active_event_ids)
    if any(entry.event_id not in active for entry in request.palette):
        raise ValueError("geometry palette entries must belong to the requested frame")
    reserved = set(request.reserved_rgb)
    if any(isinstance(color, bool) or not 0 <= color <= 0xFFFFFF for color in reserved):
        raise ValueError("reserved geometry colors must be 24-bit RGB")
    if reserved & {entry.rgb for entry in request.palette}:
        raise ValueError("geometry palette colors must not be reserved")


def _validate_attachments(request: GeometryRequest) -> None:
    names = [name for name, _data in request.attachments]
    if any(not name or "\x00" in name for name in names) or len(names) != len(set(names)):
        raise ValueError("geometry attachment names must be non-empty and unique")
    profile_names = [name for name, _value in request.render_profile]
    if any(not name for name in profile_names) or len(profile_names) != len(set(profile_names)):
        raise ValueError("geometry render profile names must be non-empty and unique")


@dataclass(frozen=True, slots=True)
class GeometryRequest:
    generation: int
    track_id: SubtitleTrackId
    frame_id: SubtitleFrameId
    timestamp_ms: int
    frame_size: tuple[int, int]
    storage_size: tuple[int, int]
    ass: bytes
    variant: GeometryVariant = GeometryVariant.HIT_MAP
    pixel_aspect: float = 1.0
    margins: tuple[int, int, int, int] = (0, 0, 0, 0)
    use_margins: bool = False
    palette: tuple[GeometryPaletteEntry, ...] = ()
    reserved_rgb: tuple[int, ...] = ()
    attachments: tuple[tuple[str, bytes], ...] = ()
    font_setup: FontSetup = field(default_factory=FontSetup)
    renderer_state: RendererState = field(default_factory=RendererState)
    render_profile: tuple[tuple[str, str], ...] = ()
    #: Keep each token's coverage mask, for the raster device. Asked for per frame rather than
    #: always, because a cue whose color the text device can draw has no use for the bytes.
    keep_coverage: bool = False

    def __post_init__(self) -> None:
        _validate_render_space(self)
        _validate_palette(self)
        _validate_attachments(self)

    def cache_key(self) -> str:
        """Stable identity for render inputs; generation is deliberately excluded.

        `frame_size` is load-bearing beyond cache freshness, and not obviously so. A snapshot's
        coverage masks are rasterised at this frame's pixels and uploaded to mpv as a bitmap, which
        — unlike an `osd-overlay` text payload — mpv does not rescale with its OSD surface. Drop the
        frame from this key and a resize serves the old masks, painting the previous window's
        colors over the new window's glyphs.
        """
        digest = hashlib.sha256()
        for value in (
            str(self.track_id),
            repr(self.frame_id),
            str(self.timestamp_ms),
            repr(self.frame_size),
            repr(self.storage_size),
            self.variant.value,
            repr(self.pixel_aspect),
            repr(self.margins),
            repr(self.use_margins),
            repr(self.palette),
            repr(self.reserved_rgb),
            repr(self.font_setup),
            repr(self.renderer_state),
            repr(self.render_profile),
            # It changes what the snapshot CONTAINS, not just how it was made: a maskless hit
            # served to a caller that asked for coverage drops the whole cue to the plainest
            # color device, silently. Free while the only producer derives it from the palette
            # hashed above — and this is what keeps that from being the thing holding it true.
            repr(self.keep_coverage),
        ):
            digest.update(value.encode())
            digest.update(b"\0")
        digest.update(self.ass)
        for name, data in self.attachments:
            digest.update(name.encode())
            digest.update(b"\0")
            digest.update(data)
        return digest.hexdigest()

    def renderer_key(self) -> str:
        """Identity of the libass *renderer* this request needs — a different question from
        `cache_key`, which is the identity of the SNAPSHOT.

        libass has three handles with three lifetimes: the library and its font set, the renderer
        holding the glyph cache built for them, and the track, which is the only per-cue one. A
        renderer is therefore identified by the font environment alone. Keying it on `cache_key`
        instead meant hashing the timestamp, the palette and the document — all of which change
        every cue — so the renderer cache could never hit: it rebuilt libass, rescanned the font
        directory, and discarded the glyph cache once per cue, at every frame size.

        Deliberately absent: the frame geometry and the render style, which `render` pushes onto
        the renderer per call; `renderer_state.features`, which libass stores on the TRACK and
        which travel with the document; and the document itself.
        """
        digest = hashlib.sha256()
        digest.update(repr(self.font_setup).encode())
        digest.update(b"\0")
        # Names and sizes, not the bytes: a font's content is what makes a renderer expensive to
        # build, and re-hashing megabytes of it per cue would pay that cost back a second time.
        # Attachments only change when the track does, which also changes a name or a length.
        for name, data in self.attachments:
            digest.update(f"{name}:{len(data)}".encode())
            digest.update(b"\0")
        return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class GeometrySnapshot:
    generation: int
    track_id: SubtitleTrackId
    frame_id: SubtitleFrameId
    timestamp_ms: int
    variant: GeometryVariant
    tokens: tuple[TokenGeometry, ...]

    @property
    def coverage_bytes(self) -> int:
        """Retained alpha, in bytes — the only part of a snapshot whose size is not bounded by the
        token count. A full-screen sign's masks are megabytes."""
        return sum(len(token.coverage) for token in self.tokens)

    def without_coverage(self) -> GeometrySnapshot:
        """The same hit boxes with the masks dropped.

        Still a complete, correct answer: coverage feeds only the raster color device, so a
        stripped snapshot costs those tokens a plainer mark and nothing else. That is what makes it
        the right thing to evict under memory pressure — evicting the entry would cost a re-render.
        """
        return replace(self, tokens=tuple(replace(token, coverage=b"") for token in self.tokens))


class GeometryBackend(Protocol):
    """Synchronous worker-side geometry provider; orchestration lives in the app."""

    def render(self, request: GeometryRequest) -> GeometrySnapshot: ...

    def close(self) -> None: ...

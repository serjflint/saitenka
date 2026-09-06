"""Opt-in native-visible subtitle geometry orchestration."""

from __future__ import annotations

import ast
import logging
import math
import time
import unicodedata
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

from saitenka_subtitles import (
    MAX_ASS_SOURCE_BYTES,
    GeometryPaletteEntry,
    GeometryRequest,
    RendererState,
    SubtitleTrackId,
    TokenAnnotation,
    authored_ass_rows_at,
    canonical_active_ass_rows,
    converted,
    decode_ass_event,
    font_names,
    parse_ass_event_line,
    prepare_ass_hit_map_frame,
    subrip,
)

from saitenka import otel_metrics
from saitenka.app import subtitle_fonts
from saitenka.app.subtitle_geometry_diagnostics import (
    GeometryCacheReason,
    GeometryOutcome,
    geometry_error_code,
    geometry_failure_reason,
)
from saitenka.app.subtitles import WordBox

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path
    from typing import SupportsFloat

    from saitenka_subtitles import Cue, CueIndex
    from saitenka_subtitles.ass_geometry import PreparedAssFrame
    from saitenka_subtitles.geometry import GeometrySnapshot
    from saitenka_tokenize.japanese import Token

    from saitenka.app.subtitle_geometry_job import SubtitleGeometryWorker
    from saitenka.app.subtitle_pipeline import SubtitleModeCoordinator
    from saitenka.app.token_cache import TokenizedCue

log = logging.getLogger(__name__)

_FALLBACK_REASONS = frozenset(
    {
        "geometry-provider-failed",
        "geometry-token-identity-invalid",
        "mpv-sub-visibility-rejected",
        "subtitle-render-input-unsupported",
        "subtitle-source-encoding-unsupported",
        "subtitle-source-not-authored-ass",
        "subtitle-source-conversion-unreproduced",
        "subtitle-source-too-large",
        "subtitle-source-unavailable",
        "subtitle-timing-unavailable",
        "subtitle-token-annotation-invalid",
        "subtitle-ass-full-unavailable",
        "subtitle-ass-full-unsupported",
        "subtitle-geometry-cache-miss",
        "subtitle-observation-pending",
        "subtitle-frame-unsupported",
        "subtitle-font-environment-stale",
    }
)
_PENDING_REASONS = frozenset(
    {
        "subtitle-ass-full-unavailable",
        "subtitle-geometry-cache-miss",
        "subtitle-observation-pending",
        "subtitle-timing-unavailable",
    }
)
# Sources geometry can never accept, however long it waits — wrong format, too large, not UTF-8.
# `subtitle-source-unavailable` is deliberately absent: `set_source(None)` is also the reset every
# track load runs, so switching on it would flap the renderer twice per episode.
#: `ASS_Feature` ordinals, in the header's declaration order. Named here rather than imported from
#: the wrapper because the wrapper is an optional extra and this module loads without it.
_ASS_FEATURE_BIDI_BRACKETS = 1
_ASS_FEATURE_WHOLE_TEXT_LAYOUT = 2
_ASS_FEATURE_WRAP_UNICODE = 3

_UNSUPPORTED_SOURCE_REASONS = frozenset(
    {
        "subtitle-source-encoding-unsupported",
        "subtitle-source-not-authored-ass",
        "subtitle-source-conversion-unreproduced",
        "subtitle-source-too-large",
    }
)

#: Refusals the user is NOT told about. `subtitle-source-unavailable` is the reset every track load
#: runs (see `set_source`), so announcing it would toast twice an episode for nothing.
_UNANNOUNCED_REASONS = frozenset({"subtitle-source-unavailable"})

#: What the user can actually do about a refusal, by reason. Absent here → the generic notice: the
#: point is that scanning is off, which is true whether or not there is an action to offer.
_FALLBACK_ACTIONS = {
    "subtitle-render-input-unsupported": "mpv is configured in a way the overlay cannot follow",
    "subtitle-source-not-authored-ass": "this track is not an authored .ass",
    "subtitle-source-too-large": "the subtitle file is too large to measure",
    "subtitle-source-encoding-unsupported": "the subtitle file is not UTF-8",
    "subtitle-ass-full-unsupported": "this mpv does not report sub-text/ass-full",
    "subtitle-font-environment-stale": "mpv's fonts changed mid-track — reselect the track",
}


def _fallback_notice(reason: str, diagnostic: str | None) -> str:
    """One line naming the loss, the cause, and — when mpv named the options — which ones.

    The detail is what makes it actionable: the reason alone sends a user to the source, while the
    same line carrying the option names mpv reported sends them to their own mpv.conf.
    """
    cause = _FALLBACK_ACTIONS.get(reason, reason)
    detail = f" ({diagnostic})" if diagnostic else ""
    return f"no word scanning: {cause}{detail}"


#: The demuxer codecs whose libavcodec-to-ASS conversion `subtitles.converted` reproduces. Only
#: SubRip: every other text codec sd_ass converts (`mov_text`, `webvtt`, `microdvd`, ...) is decoded
#: by a DIFFERENT libavcodec decoder, which writes its own header and its own styles, and mpv's
#: converted branch leaves margins, ScaleX/Y and any non-default style standing. Our own extraction
#: hides the difference by transcoding all of them to `.srt` (`subtitle_artifact.extract_spec`) —
#: the file is not what mpv is decoding, so the suffix cannot be the test. Widen this only against a
#: measurement, never against a reading of the decoder.
CONVERTED_CODECS = frozenset({"subrip"})

#: The components of `_key`, in order, so a miss can name which one diverged rather than only
#: that the whole key did.
_KEY_FIELDS = (
    "path",
    "text",
    "rows",
    "frame_size",
    "storage_size",
    "pixel_aspect",
    "margins",
    "use_margins",
    "render_profile",
)

#: The mpv options a geometry request is derived from — reproduced in the measuring render, or
#: refused. Every one must also be observed and counted a render-space input, or `_render_inputs`
#: reads it with a blocking round trip twice per cue and a mid-episode change never invalidates the
#: boxes it moved. `tests/test_native_subtitles.py` binds the three lists together.
GATE_OPTIONS = (
    "sub-ass-override",
    "sub-ass-scale-with-window",
    "sub-scale",
    "sub-pos",
    "sub-use-margins",
    "sub-ass-force-margins",
    "sub-ass-video-aspect-override",
    "sub-ass-use-video-data",
    "sub-ass-style-overrides",
    "sub-scale-with-window",
    "sub-scale-by-window",
    "blend-subtitles",
    "sub-filter-sdh",
    "video-crop",
    "video-rotate",
    # Read, never refused. `sub-shaper` runs on every branch (`sd_ass.c:570`), so a mismatch shapes
    # mpv's runs with different advances than ours. The rest are read only on the
    # `sub-ass-override` branches that set them (`sd_ass.c:552-558,577`).
    "sub-shaper",
    "sub-ass-justify",
    "sub-line-spacing",
    "sub-hinting",
    "sub-scale-signs",
    *subtitle_fonts.FONT_OPTIONS,
    # Read, never refused: mpv applies these to a CONVERTED track only, and `converted.style_of`
    # reproduces each of them. Refusing one would cost a user with a custom `--sub-font-size` every
    # authored track too, where mpv does not read it at all.
    *converted.STYLE_OPTIONS,
)


class SourceKind(StrEnum):
    """Which document the boxes are measured against.

    `CONVERTED` is not "an SRT": mpv never renders one. libavcodec turns it into ASS and mpv renders
    *that*, through a branch of `configure_ass` it applies to no authored track. The document is
    therefore reconstructed per cue rather than read from disk — see `subtitles.converted`.
    """

    NONE = "none"
    AUTHORED = "authored-ass"
    CONVERTED = "converted"


class NativeFormats(StrEnum):
    """Which track formats the native path will take."""

    AUTHORED_ASS = "authored-ass"
    ALL = "all"


def native_formats(configured: object) -> NativeFormats:
    """Read the config value, defaulting to the tested envelope rather than raising.

    A typo must not take the session down, and it must not silently widen the envelope either:
    anything unrecognised means the authored-ASS path, and says so.
    """
    try:
        return NativeFormats(str(configured))
    except ValueError:
        log.warning(
            "unknown native_formats %r; using %s", configured, NativeFormats.AUTHORED_ASS.value
        )
        return NativeFormats.AUTHORED_ASS


class AssFullCapability(StrEnum):
    UNKNOWN = "unknown"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class AnnotationSelection:
    annotations: tuple[TokenAnnotation, ...]
    skipped_whitespace: int
    skipped_tokenizer: int
    skipped_unpaintable: int


def _annotation_for_token(
    token: Token,
    token_index: int,
    line: str,
    offset: int,
    is_skippable: Callable[[Token], bool],
) -> tuple[TokenAnnotation | None, str | None]:
    if not 0 <= token.start < token.end <= len(line):
        raise ValueError("token span exceeds subtitle line")
    surface = line[token.start : token.end]
    if surface != token.surface:
        raise ValueError("token surface does not match subtitle text")
    if not surface.strip():
        return None, "whitespace"
    if is_skippable(token):
        return None, "tokenizer"
    if not any(unicodedata.category(char)[0] not in {"C", "Z"} for char in surface):
        return None, "unpaintable"
    return TokenAnnotation(token_index, offset + token.start, offset + token.end), None


def _short_repr(value: object, *, limit: int = 80) -> str:
    rendered = repr(value)
    return rendered if len(rendered) <= limit else f"{rendered[: limit - 3]}..."


def _frame_margins(osd: Mapping[str, object]) -> tuple[int, int, int, int]:
    return cast(
        "tuple[int, int, int, int]",
        tuple(
            int(cast("int | float | str", osd.get(name) or 0)) for name in ("mt", "mb", "ml", "mr")
        ),
    )


def _requested_family(font_name: str) -> str:
    """The family an ASS `Fontname` asks for, as the unreachable set spells it."""
    return font_names.key(font_name)


def connect_drift_sink(renderer: object, geometry: NativeSubtitleGeometry) -> None:
    """Let a measured drift verdict reach the geometry side.

    The renderer measures it and applies it where it draws, which is the only place a late answer
    can still reach the cue on screen. This side needs to hear it so the next build keeps coverage
    masks for those families. A renderer without the seam simply never reports one.
    """
    sink = getattr(renderer, "set_drift_sink", None)
    if sink is not None:
        sink(geometry.record_drifting_families)


def _palette_in_frame_units(
    prepared: PreparedAssFrame,
    frame_height: int,
    font_scale: float,
    *,
    unreachable: subtitle_fonts.OsdReach,
) -> tuple[GeometryPaletteEntry, ...]:
    """Restate each token's font size in the frame's pixels rather than the document's script units.

    libass scales a style's `Fontsize` by `frame_height / PlayResY`, then by whatever
    `ass_set_font_scale` holds. Doing that here — once, where both numbers are known — is what lets
    an overprint draw the token at the size it was actually laid out at, instead of redoing libass's
    arithmetic at the point of drawing where `PlayResY` is no longer in scope.

    A document that declares no `PlayResY` yields a size of zero, which the overprint reads as "do
    not draw this token" rather than as a size. So does a token whose family the OSD library cannot
    reach: its box is still right, and only its color stands down.
    """
    if prepared.play_res_y <= 0:
        return tuple(replace(entry, font_name="", font_size=0.0) for entry in prepared.palette)
    scale = frame_height / prepared.play_res_y * font_scale
    demoted = [
        entry
        for entry in prepared.palette
        if unreachable.blocks(_requested_family(entry.font_name))
    ]
    if demoted and otel_metrics.subtitle_overprint_demotions is not None:
        otel_metrics.subtitle_overprint_demotions.add(
            len(demoted), {"reason": "font-not-on-osd-library"}
        )
    blanked = {(entry.event_id, entry.token_index) for entry in demoted}
    return tuple(
        replace(entry, font_name="", font_size=0.0)
        if (entry.event_id, entry.token_index) in blanked
        else replace(entry, font_size=entry.font_size * scale)
        for entry in prepared.palette
    )


#: `--sub-hinting` to `ASS_Hinting`, in libass's declaration order (`ass.h`). mpv passes the ordinal
#: straight through (`sd_ass.c:555`), so the names have to line up here rather than be counted.
_HINTING = {"none": 0, "light": 1, "normal": 2, "native": 3}


@dataclass(frozen=True, slots=True)
class _ScaleOverride:
    """What `--sub-ass-override=scale` sets on the libass renderer, and nothing else.

    One value rather than six loose fields because they are one decision: mpv reads all of them on
    the same branch (`sd_ass.c:552-558`) and none of them on the other, so a caller that has one
    has all of them. `active` is what says which branch produced this — every other field is at
    libass's own default when it is false, so passing them on is a no-op rather than a claim.
    """

    active: bool = False
    font_scale: float = 1.0
    line_position: float = 0.0
    line_spacing: float = 0.0
    hinting: int = 0
    #: `--sub-scale-signs`. Inverted into `SELECTIVE_FONT_SCALE`, which mpv sets when it is OFF
    #: (`sd_ass.c:577`): the bit CONFINES the scale to dialogue, so setting it is how signs escape.
    scale_signs: bool = False


def _scaled_renderer_state(scale: _ScaleOverride) -> RendererState:
    """The authored track's renderer state: libass defaults, or the `scale` branch's four values.

    `selective_font_scale` is the INVERSE of `--sub-scale-signs`, because the bit confines the
    scale to dialogue and mpv sets it exactly when the user did NOT ask for signs to be scaled
    (`sd_ass.c:577`). Reading it the other way scales every sign in the episode.
    """
    if not scale.active:
        return RendererState()
    return RendererState(
        font_scale=scale.font_scale,
        line_position=scale.line_position,
        line_spacing=scale.line_spacing,
        hinting=scale.hinting,
        selective_font_scale=not scale.scale_signs,
    )


def _scales_authored_styles(settings: Mapping[str, object], *, authored: bool) -> bool:
    """Whether mpv is on the `--sub-ass-override=scale` branch, which we reproduce.

    `authored` is not a nicety. `converted` is the FIRST disjunct of both branch conditions
    (`sd_ass.c:544,553`), so a converted track applies `sub-scale` and `100 - sub-pos` whatever the
    override says — and `_renderer_state` reproduces neither there. Widening on the override alone
    would accept a letterboxed SubRip track that mpv draws at a different size and height, which is
    a silently misplaced box rather than a refusal.
    """
    return authored and settings["sub-ass-override"] == "scale"


def _unsupported_render_inputs(
    settings: Mapping[str, object], *, authored: bool
) -> tuple[str, ...]:
    """Which of mpv's render options put the cue somewhere our layout cannot follow.

    The font options are absent on purpose: `subtitle_fonts.resolve` now reproduces each of them
    instead of refusing it, so `--embeddedfonts`, `--sub-fonts-dir`, `--sub-font-provider` and
    `--sub-font` are inputs to the measuring renderer rather than reasons to give up on a track.
    They are still read, so a change to one still invalidates the cache and is still checked
    against the resolved environment.
    """
    scaled = _scales_authored_styles(settings, authored=authored)
    supported = {
        # `yes`/`force` substitute mpv's own style into every event (`sd_ass.c:572-581`), making
        # every `converted.STYLE_OPTIONS` entry an authored-track layout input. `scale` only sets
        # renderer state, which `_renderer_state` reproduces.
        "sub-ass-override": settings["sub-ass-override"] in {False, "no", "scale"},
        "sub-ass-scale-with-window": settings["sub-ass-scale-with-window"] is False,
        # Read only on the branches `scale` and `force` take (`sd_ass.c:552-558`); under `no` mpv
        # leaves the renderer at 1 and 0 and never looks at either. So they are refused exactly
        # when they would move the text AND we are not reproducing the branch that moves it.
        "sub-scale": scaled or settings["sub-scale"] == 1.0,
        "sub-pos": scaled or settings["sub-pos"] == 100.0,
        # Only the converted branch reads this (`sd_ass.c:545`); the authored one reads
        # `sub-ass-force-margins` below, gated separately and accepted at either value.
        "sub-use-margins": settings["sub-use-margins"] is True,
        # Reachable on an authored track only once the override is on (`sd_ass.c:589-591`), and not
        # reproduced: it decides where every line of a wrapped cue starts, which is a box position.
        "sub-ass-justify": not scaled or settings["sub-ass-justify"] in {False, "no", None},
        "sub-ass-force-margins": isinstance(settings["sub-ass-force-margins"], bool),
        "sub-ass-video-aspect-override": settings["sub-ass-video-aspect-override"]
        in {
            None,
            0,
        },
        "sub-ass-use-video-data": settings["sub-ass-use-video-data"] == "all",
        "sub-ass-style-overrides": settings["sub-ass-style-overrides"] in (None, "", (), [], [""]),
        # `--sub-ass-vsfilter-aspect-compat` is NOT here, and must not return as an is-None check:
        # mpv's default is `yes`, so a present bool option never reads `None` and that row refused
        # every track on every mpv that still had the option. 0.41 removed it in favour of
        # `sub-ass-use-video-data`, gated above and forced by `mpvio.launch`.
        #
        # `--sub-scale-with-window` and `--sub-scale-by-window` are NOT here. mpv reads them only on
        # `configure_ass`'s forced-override branch, which a CONVERTED track takes — and there
        # `converted.font_scale` reproduces both, so they are inputs to the measurement rather than
        # reasons to give up on a track. On the authored branch mpv reads `sub-ass-scale-with-window`
        # (gated above) and never reads `sub-scale-by-window` at all, so refusing either there would
        # cost an episode's interaction over an option with no effect on it.
        #
        # `--blend-subtitles` does not move our overlay: it is still drawn last and at screen
        # resolution, in the `OSD_DRAW_OSD_ONLY` pass onto the screen (`video/out/gpu/video.c:3650`).
        # What moves is where MPV's glyphs are — it builds a different `mp_osd_res` for the blend
        # pass (`video.c:3227-3273`), and the boxes have to be laid out against that surface and
        # then offset onto the screen.
        #
        # `=yes` IS reproduced — see `_blend_space`, which recreates that `mp_osd_res` from
        # `osd-dimensions`. `=video` is not: it lays the subtitle out on `texture_w/h` AFTER the
        # user's shader hooks, which a `--glsl-shader` can resize and nothing reports.
        "blend-subtitles": settings["blend-subtitles"] in {False, "no", True, "yes"},
        # Only meaningful for the blend pass, and only there are they refused. `_blend_space`
        # derives the blend rect from the premise that the src rect is the whole image, which a
        # crop breaks outright and a rotation re-orients (`mp_get_src_dst_rects`,
        # `aspect.c:156-163`). Outside blending they change nothing this gate reads.
        "video-crop": not _blends_into_video(settings) or not str(settings["video-crop"] or ""),
        "video-rotate": not _blends_into_video(settings)
        or settings["video-rotate"] in {0, None, "0", "no"},
        # The SDH filter REWRITES an event's text before `ass_process_chunk` sees it
        # (`filter_and_add`, `sd_ass.c:370-385`), so what mpv is drawing is not what the file says
        # and our match back to the authored source is against a document that no longer describes
        # the screen. It runs on every track sd_ass handles — `.codec` is hardcoded `"ass"`
        # (`sd_ass.c:221`), so a converted track is filtered too.
        #
        # `--sub-filter-regex` / `--sub-filter-jsre` are NOT here: they only DROP a whole packet,
        # never rewrite one. A dropped cue reaches us as an empty `sub-text`, which already means
        # "nothing to lay out" — the events that do arrive are still the file's own.
        "sub-filter-sdh": settings["sub-filter-sdh"] in {False, "no", None},
    }
    return tuple(name for name, accepted in supported.items() if not accepted)


@dataclass(frozen=True, slots=True)
class _BlendSpace:
    """The surface mpv lays a blended subtitle out on, and where it sits on the screen."""

    frame_size: tuple[int, int]
    pixel_aspect: float
    origin: tuple[int, int]


def _blends_into_video(settings: Mapping[str, object]) -> bool:
    return settings["blend-subtitles"] in {True, "yes"}


def _blend_space(
    frame_size: tuple[int, int],
    margins: tuple[int, int, int, int],
    video: Mapping[str, object],
) -> _BlendSpace | None:
    """`--blend-subtitles=yes`: the OSD resolution mpv builds for the blend pass, or `None`.

    mpv draws the subtitle into the video texture before scaling, on a `mp_osd_res` it recreates
    from the src/dst rects (`video/out/gpu/video.c:3249-3263`): the video's on-screen rectangle,
    with margins `-src.x0`, `src.x1 - image_w` (scaled), and `display_par = 1.0`.

    Derivable from `osd-dimensions` alone in the cases this accepts. `mp_get_src_dst_rects`
    (`video/out/aspect.c:76-114`) sets `osd.ml = dst.x0` and `osd.mr = dst_size - dst.x1` BEFORE it
    clips, so the video rectangle is exactly the surface inset by those margins. And when the source
    is uncropped and unrotated the src rect is the whole image, which makes every blend-pass margin
    zero. Both premises are guarded: a crop or a rotation is refused above, and any case where mpv
    had to clip (pan, zoom or panscan pushing the video past the window) leaves a NEGATIVE osd
    margin, which `_validate_frame` already rejects.

    The consequence for the boxes is an offset, not a scale: the blend surface has the same pixel
    pitch as the screen, so a token measured at `(x, y)` there is drawn at `(x + ml, y + mt)`.
    """
    top, bottom, left, right = margins
    width = frame_size[0] - left - right
    height = frame_size[1] - top - bottom
    if width <= 0 or height <= 0:
        return None
    # `display_par` is 1.0 on the blend rect, so the screen's OSD aspect drops out and only the
    # video's own remains.
    return _BlendSpace((width, height), _pixel_aspect({"par": 1.0}, video), (left, top))


def _validate_frame(frame_size: tuple[int, int], margins: tuple[int, int, int, int]) -> None:
    if any(value < 0 for value in margins):
        raise ValueError(f"osd-margins={_short_repr(margins)}")
    if margins[0] + margins[1] >= frame_size[1] or margins[2] + margins[3] >= frame_size[0]:
        raise ValueError(f"osd-margins={_short_repr(margins)}")


def _pixel_aspect(osd: Mapping[str, object], video: Mapping[str, object]) -> float:
    osd_par = cast("int | float | str", osd.get("par") or 1.0)
    video_par = cast("int | float | str", video.get("par") or 1.0)
    value = float(osd_par) * float(video_par)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            "pixel-aspect=" + _short_repr({"osd": osd.get("par"), "video": video.get("par")})
        )
    return value


def render_inputs_of(
    osd: Mapping[str, object],
    video: Mapping[str, object],
    settings: Mapping[str, object],
    *,
    frame_size: tuple[int, int],
    authored: bool,
) -> _RenderInputs:
    """Whether mpv's current render configuration lets us key geometry off it, and the frame it
    implies. Raises `ValueError` naming what disqualified it.

    This is the gate on native geometry, and everything it rejects has the same consequence: our
    boxes would be computed against a frame mpv is not drawing into, so hit regions land beside the
    words. `sub-scale`, `sub-pos` and the margin flags all move the text; the font settings change
    which glyphs get laid out; a non-finite or non-positive pixel aspect makes the mapping
    meaningless.

    `frame_size` is the host's OSD surface, passed in rather than re-derived from the `osd` mapping
    here. Both were reads of `osd-dimensions`, and two reads of one property are two values: the
    boxes were laid out against one and drawn onto the other, with the same scale-and-offset error
    on every box and nothing to say so. Margins and pixel aspect still come from the mapping — the
    host does not carry those.
    """

    def _dim(source: Mapping[str, object], key: str, default: int) -> int:
        return int(cast("int | float | str", source.get(key) or default))

    storage_size = (_dim(video, "w", frame_size[0]), _dim(video, "h", frame_size[1]))
    margins = _frame_margins(osd)
    unsupported = _unsupported_render_inputs(settings, authored=authored)
    if unsupported:
        raise ValueError(", ".join(f"{name}={_short_repr(settings[name])}" for name in unsupported))
    _validate_frame(frame_size, margins)
    override = _scale_override_inputs(settings, authored=authored)
    blended = _blend_space(frame_size, margins, video) if _blends_into_video(settings) else None
    if blended is not None:
        return _RenderInputs(
            blended.frame_size,
            storage_size,
            blended.pixel_aspect,
            (0, 0, 0, 0),
            cast("bool", settings["sub-ass-force-margins"]),
            tuple(sorted((name, repr(value)) for name, value in settings.items())),
            subtitle_fonts.option_snapshot(settings),
            settings["sub-scale-with-window"] is not False,
            settings["sub-scale-by-window"] is not False,
            blended.origin,
            converted.style_of(settings),
            override,
        )
    return _RenderInputs(
        frame_size,
        storage_size,
        _pixel_aspect(osd, video),
        margins,
        cast("bool", settings["sub-ass-force-margins"]),
        tuple(sorted((name, repr(value)) for name, value in settings.items())),
        subtitle_fonts.option_snapshot(settings),
        settings["sub-scale-with-window"] is not False,
        settings["sub-scale-by-window"] is not False,
        style=converted.style_of(settings),
        scale=override,
    )


def _scale_override_inputs(settings: Mapping[str, object], *, authored: bool) -> _ScaleOverride:
    """The renderer values `--sub-ass-override=scale` puts in play, or the `no` defaults.

    Defaults rather than zeroes under `no` because `configure_ass` assigns none of them there
    (`sd_ass.c:552,557`) — libass keeps its own, and writing values out would claim we read
    something mpv did not.
    """
    if not _scales_authored_styles(settings, authored=authored):
        return _ScaleOverride()
    return _ScaleOverride(
        active=True,
        font_scale=_as_float(settings["sub-scale"], 1.0),
        # mpv hands libass the complement, not the property (`sd_ass.c:553`).
        line_position=100.0 - _as_float(settings["sub-pos"], 100.0),
        line_spacing=_as_float(settings["sub-line-spacing"], 0.0),
        hinting=_HINTING.get(str(settings["sub-hinting"]), 0),
        scale_signs=settings["sub-scale-signs"] is True,
    )


def _as_float(value: object, default: float) -> float:
    try:
        return float(cast("SupportsFloat", value))
    except (TypeError, ValueError):
        return default


def _subtitle_clock(
    raw_time: SupportsFloat | None, raw_delay: SupportsFloat | None, fallback: float | None
) -> tuple[float, float, float, int]:
    """Video time, `sub-delay`, and the delay-adjusted subtitle time (plus its ms key) from mpv's two
    raw properties. Rejects a clock the geometry can't be keyed off — non-finite or negative — rather
    than let a garbage timestamp reach the cache; `fallback` is `sub-start`, used when mpv has no
    `time-pos` yet."""
    sub_delay = 0.0 if raw_delay is None else float(raw_delay)
    if raw_time is None:
        if fallback is None:
            raise ValueError("subtitle clock is unavailable")
        subtitle_time = fallback
        video_time = subtitle_time + sub_delay
    else:
        video_time = float(raw_time)
        subtitle_time = video_time - sub_delay
    if not all(math.isfinite(value) for value in (video_time, sub_delay, subtitle_time)):
        raise ValueError("subtitle clock must be finite")
    if subtitle_time < 0:
        raise ValueError("subtitle clock must be non-negative")
    return video_time, sub_delay, subtitle_time, round(subtitle_time * 1_000)


@dataclass(frozen=True, slots=True)
class NativeSubtitleStatus:
    enabled: bool
    source: str | None
    fallback_reason: str | None
    geometry_ready: bool
    ass_full_capability: AssFullCapability
    eligible_tokens: int
    skipped_tokens: int
    owner: str
    outcome: GeometryOutcome | None
    last_transition: str | None
    last_recovery: str | None
    source_epoch: int


@dataclass(frozen=True, slots=True)
class _RenderInputs:
    frame_size: tuple[int, int]
    storage_size: tuple[int, int]
    pixel_aspect: float
    margins: tuple[int, int, int, int]
    use_margins: bool
    profile: tuple[tuple[str, str], ...]
    #: Just the font options, so a change to one can be told from a change to the render space.
    font_options: tuple[tuple[str, str], ...] = ()
    #: The two `--sub-scale-*` switches mpv reads on its forced-override branch. Reproduced rather
    #: than refused, so they travel to `converted.font_scale` as values.
    scale_with_window: bool = True
    scale_by_window: bool = True
    #: Where the frame the boxes were laid out in sits on the screen. Non-zero only under
    #: `--blend-subtitles=yes`, where mpv lays the cue out on the video rectangle rather than on the
    #: OSD surface, and the boxes are drawn onto the screen — see `_blend_space`.
    box_origin: tuple[int, int] = (0, 0)
    #: The `--sub-*` style mpv applies to a converted track. Unread on an authored one, where
    #: `--sub-ass-override=no` leaves the file's own styles standing.
    style: converted.SubStyle = field(default_factory=converted.SubStyle)
    #: What `--sub-ass-override=scale` puts on the renderer. Its default IS what libass is left at
    #: under `no`, where `configure_ass` assigns none of them (`sd_ass.c:552,557`).
    scale: _ScaleOverride = field(default_factory=lambda: _ScaleOverride())


@dataclass(frozen=True, slots=True)
class _CueInputs:
    start_ms: int
    end_ms: int
    timestamp_ms: int
    text: str
    active_rows: str
    frame_size: tuple[int, int]
    storage_size: tuple[int, int]
    pixel_aspect: float
    margins: tuple[int, int, int, int]
    use_margins: bool
    render_profile: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _ScheduleInputs:
    path: Path
    source: bytes
    track_id: SubtitleTrackId
    generation: int
    render: _RenderInputs
    cue: _CueInputs
    key: str
    observation_key: str | None


@dataclass(frozen=True, slots=True)
class GeometryPorts:
    """What the geometry owner asks the session to do, and the pipeline it defers to.

    Bound once, at construction, rather than reached for per call: the geometry owner is a
    session-lived collaborator, so a port that could change between calls would mean a different
    session, not a different frame. That is what separates it from `SubtitleTarget`, whose members
    are live facts and must be snapshotted per operation.
    """

    pipeline: SubtitleModeCoordinator
    degrade: Callable[[], None]
    clear_interaction: Callable[[], None]
    use_native: Callable[[], bool]
    ownership_undecided: Callable[[], bool]
    redraw: Callable[[], None]
    #: Ask for a fresh geometry build. Distinct from `redraw`, which re-draws what is already
    #: measured: a verdict that changes what the measurement must CONTAIN — coverage masks, say —
    #: needs the request rebuilt, and redrawing the old snapshot cannot produce it.
    reschedule: Callable[[], None]
    #: Hand the hit boxes (and, when the cue is installed, its origin) to whoever presents them.
    #: The geometry owner IS the publisher of these — unlike a renderer, which returns them so a
    #: superseded cue cannot write over a live one. Here the generation fence is what orders them.
    publish: Callable[[list[WordBox], tuple[int, int] | None], None]
    #: Tokenize a cue that is not on screen, for the lookahead. Session-lived, like the rest here;
    #: the *active* tokenizer is not, because a profile switch replaces it.
    tokenize_lookahead: Callable[[str], TokenizedCue]
    #: Surface a refusal to the user. Degrading is silent otherwise: the episode simply has no
    #: scanning and no overpaint, with nothing on screen to say why or that it was a decision.
    notify: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class GeometryObservation:
    """The live facts one geometry decision is made from, snapshotted per operation.

    Cut by contract: mpv-read (`prop`), presentation (`osd`), cue identity and nav (`text`,
    `index`, `normalise`, `nav_index`, `cue_hint`, `cue_revision`), and the rendered cue
    (`tokens`, `lines`). `is_skippable` is the dictionary's, and it travels here rather than in
    the ports because a profile switch replaces the tokenizer mid-session.

    Eleven members. That is a feature value, the size the sidebar's is — and the count is what
    says this module is one feature and `tooltip`, at ninety-two, is not yet.
    """

    prop: Callable[[str], Any]
    #: The OSD surface mpv composites onto, and therefore the frame the boxes are laid out in.
    #: One value for both drawing and layout — reading `osd-dimensions` separately for the layout
    #: is two surfaces whenever the two reads disagree.
    osd: tuple[int, int]
    text: str
    tokens: list[Token]
    lines: list[list[Token]]
    index: CueIndex | None
    normalise: Callable[[str], str]
    nav_index: int
    cue_hint: Cue | None
    cue_revision: int
    is_skippable: Callable[[Token], bool]


class NativeSubtitleGeometry:
    def __init__(
        self,
        worker: SubtitleGeometryWorker,
        ports: GeometryPorts,
        *,
        lookahead: int = 2,
        formats: NativeFormats = NativeFormats.AUTHORED_ASS,
    ) -> None:
        if lookahead < 0:
            raise ValueError("subtitle geometry lookahead must be non-negative")
        self.worker = worker
        self._ports = ports
        self.lookahead = lookahead
        self.formats = formats
        self.source_kind = SourceKind.NONE
        self.source_path: Path | None = None
        self._source_bytes: bytes | None = None
        self.fallback_reason: str | None = "subtitle-source-unavailable"
        self._last_snapshot: object | None = None
        self.ass_full_capability = AssFullCapability.UNKNOWN
        self._last_selection = AnnotationSelection((), 0, 0, 0)
        self._eligible_tokens = 0
        self._last_decision: tuple[GeometryOutcome, str] | None = None
        self._owner = "unknown"
        self._submitted_at: tuple[int, float] | None = None
        self._pending_key: tuple[int, str] | None = None
        self._published_key: str | None = None
        self._source_epoch = 0
        self._last_transition: str | None = None
        self._last_recovery: str | None = None
        self._last_render_inputs: _RenderInputs | None = None
        self._failure_diagnostic: tuple[str, str | None] | None = None
        #: Last reason announced on screen. Separate from `_failure_diagnostic`, which suppresses a
        #: repeated LOG line for FAILED only — a toast must not repeat for any outcome, and must
        #: fire again if the user fixes one cause and hits a different one.
        self._announced_reason: str | None = None
        self._fonts = subtitle_fonts.FontEnvironment()
        self._in_document_families: frozenset[str] = frozenset()
        self._measured_unsafe: frozenset[str] = frozenset()
        #: A converted track's cue markup, by millisecond span — see `_adopt_converted`.
        self._converted_markup: dict[tuple[int, int], str] = {}
        self._track_codec = ""

    def record_drifting_families(self, families: frozenset[str]) -> None:
        """A measured verdict that mpv's OSD renderer does not lay these families out as we do.

        The renderer has already stopped drawing them as text — it applies the verdict where it
        draws, which is the only place a *late* answer can still reach the cue on screen. What is
        left for this side is the consequence for the next build: those tokens now need coverage
        masks, so the raster device can color them instead of the rule device marking them.
        """
        if families <= self._measured_unsafe:
            return
        self._measured_unsafe |= families
        self.invalidate(cause=GeometryCacheReason.RENDER_INPUT_CHANGED)
        # Both, and they do different work. `redraw` takes the drifting face off the cue on screen
        # now; `reschedule` builds the request that will carry coverage masks for it, which is the
        # only way the raster device can pick the color back up. Invalidating alone does neither —
        # nothing re-observes a cue that has not changed, so the rebuild never happens and the
        # family stays uncolored for as long as it is shown.
        self._ports.reschedule()
        self._ports.redraw()

    def set_track_codec(self, codec: str) -> None:
        """Which decoder mpv is running for the selected track, from `track-list`.

        The codec, not the file suffix: our own extraction transcodes `mov_text` and `webvtt` to
        `.srt` (`subtitle_artifact.extract_spec`), so the artifact on disk says SubRip while mpv is
        decoding something else entirely — two conversions of one source, and only one of them is
        the one on screen. Must be set before the source, which is where it is read.
        """
        self._track_codec = codec.strip().casefold()

    @property
    def unreachable_families(self) -> subtitle_fonts.OsdReach:
        """The families the overprint must stand down on — see `FontEnvironment.osd_unreachable`.

        Three halves, arriving independently: the container's attachments come with the font
        environment, the document's own ``[Fonts]`` section with the source, and a measured drift
        verdict from the renderer at any time after either. Combining them on read means no arrival
        order leaves one out.
        """
        return self._fonts.osd_unreachable(self._in_document_families, self._measured_unsafe)

    def _skipped_tokens(self) -> int:
        return (
            self._last_selection.skipped_whitespace
            + self._last_selection.skipped_tokenizer
            + self._last_selection.skipped_unpaintable
        )

    def sync_pixel_owner(self, renderer) -> None:
        ownership = getattr(renderer, "ownership_state", None)
        owner = getattr(getattr(ownership, "owner", None), "value", self._owner)
        if owner != self._owner:
            self._transition_owner(owner, self._last_decision)

    def _record_decision_metrics(
        self, outcome: GeometryOutcome, reason: str, active_events: int
    ) -> None:
        if otel_metrics.subtitle_geometry_decisions is not None:
            otel_metrics.subtitle_geometry_decisions.add(1, {"outcome": outcome, "reason": reason})
        if otel_metrics.subtitle_geometry_active_events is not None:
            otel_metrics.subtitle_geometry_active_events.record(active_events)
        if otel_metrics.subtitle_geometry_eligible_tokens is not None:
            otel_metrics.subtitle_geometry_eligible_tokens.record(self._eligible_tokens)
        if otel_metrics.subtitle_geometry_skipped_tokens is not None:
            otel_metrics.subtitle_geometry_skipped_tokens.record(self._skipped_tokens())

    def _trace_decision(
        self,
        outcome: GeometryOutcome,
        reason: str,
        target_owner: str,
        error_code: str | None,
        active_events: int,
    ) -> None:
        with otel_metrics.traced("subtitle_geometry_decision") as span:
            span.set("outcome", outcome)
            span.set("reason", reason)
            span.set("generation", self.worker.generation)
            span.set("source_epoch", self._source_epoch)
            span.set("source_class", self.source_kind)
            span.set("ass_full_capability", self.ass_full_capability)
            span.set("active_events", active_events)
            span.set("eligible_tokens", self._eligible_tokens)
            span.set("skipped_whitespace", self._last_selection.skipped_whitespace)
            span.set("skipped_tokenizer", self._last_selection.skipped_tokenizer)
            span.set("skipped_unpaintable", self._last_selection.skipped_unpaintable)
            if error_code is not None:
                span.set("error_code", error_code)
            if self._last_render_inputs is not None:
                render = self._last_render_inputs
                span.set("frame_width", render.frame_size[0])
                span.set("frame_height", render.frame_size[1])
                span.set("storage_width", render.storage_size[0])
                span.set("storage_height", render.storage_size[1])
                span.set("pixel_aspect", render.pixel_aspect)
                span.set("margins", repr(render.margins))
            if target_owner != self._owner:
                span.set("owner_transition", f"{self._owner}_to_{target_owner}")

    def _transition_owner(
        self,
        target_owner: str,
        previous: tuple[GeometryOutcome, str] | None,
    ) -> None:
        if target_owner == self._owner:
            return
        transition = f"{self._owner}_to_{target_owner}"
        self._last_transition = transition
        if otel_metrics.subtitle_geometry_owner_transitions is not None:
            otel_metrics.subtitle_geometry_owner_transitions.add(1, {"transition": transition})
        recovery_reason = (
            previous[1]
            if target_owner == "native"
            and previous is not None
            and previous[0] != GeometryOutcome.EMPTY
            else None
        )
        if recovery_reason is not None and otel_metrics.subtitle_geometry_recoveries is not None:
            otel_metrics.subtitle_geometry_recoveries.add(1, {"reason": recovery_reason})
        if recovery_reason is not None:
            self._last_recovery = recovery_reason
        self._owner = target_owner

    def _set_decision(
        self,
        outcome: GeometryOutcome,
        reason: str,
        *,
        error_code: str | None = None,
        active_events: int = 0,
        owner: str | None = None,
    ) -> bool:
        decision = (outcome, reason)
        target_owner = owner or self._owner
        if decision == self._last_decision and target_owner == self._owner:
            return False
        previous = self._last_decision
        self._last_decision = decision
        self._record_decision_metrics(outcome, reason, active_events)
        self._trace_decision(outcome, reason, target_owner, error_code, active_events)
        self._transition_owner(target_owner, previous)
        return True

    def _set_fallback(
        self,
        reason: str | None,
        *,
        error_code: str | None = None,
        log_detail: str | None = None,
    ) -> None:
        if reason is None:
            self.fallback_reason = None
            self._set_decision(GeometryOutcome.READY, "ready")
            return
        if reason not in _FALLBACK_REASONS:
            reason = "geometry-provider-failed"
        outcome = self._fallback_outcome(reason)
        if self._preserves_latched_failure(outcome):
            return
        self.fallback_reason = reason
        if not self._set_decision(outcome, reason, error_code=error_code):
            return
        self._emit_fallback(outcome, reason, error_code=error_code, log_detail=log_detail)

    @staticmethod
    def _fallback_outcome(reason: str) -> GeometryOutcome:
        if reason in _PENDING_REASONS:
            return GeometryOutcome.PENDING
        if reason.startswith("geometry-") or reason == "mpv-sub-visibility-rejected":
            return GeometryOutcome.FAILED
        return GeometryOutcome.UNSUPPORTED

    def _preserves_latched_failure(self, outcome: GeometryOutcome) -> bool:
        return bool(
            outcome == GeometryOutcome.PENDING
            and self._last_decision is not None
            and self._last_decision[0] == GeometryOutcome.FAILED
        )

    def _emit_fallback(
        self,
        outcome: GeometryOutcome,
        reason: str,
        *,
        error_code: str | None,
        log_detail: str | None,
    ) -> None:
        diagnostic_key = (reason, error_code)
        if outcome == GeometryOutcome.FAILED and diagnostic_key == self._failure_diagnostic:
            return
        if outcome == GeometryOutcome.FAILED:
            self._failure_diagnostic = diagnostic_key
        if (
            outcome == GeometryOutcome.FAILED
            and otel_metrics.subtitle_geometry_failures is not None
        ):
            otel_metrics.subtitle_geometry_failures.add(1, {"reason": reason})
        level = logging.WARNING if outcome == GeometryOutcome.FAILED else logging.INFO
        diagnostic = error_code or log_detail
        log.log(
            level,
            "native subtitle interaction unavailable: %s%s",
            reason,
            f" detail={diagnostic}" if diagnostic else "",
        )
        self._announce_fallback(outcome, reason, diagnostic)

    def _announce_fallback(
        self, outcome: GeometryOutcome, reason: str, diagnostic: str | None
    ) -> None:
        """Say on screen that scanning is off, once per distinct cause.

        UNSUPPORTED only, because that is the class the user can act on: a config or a track the
        measurement refuses the same way for the whole episode. FAILED is provider-level and
        routinely transient — a cue queried while `sub-delay` is moving can find no active event and
        recover on the next frame — so it would spend the user's attention on something already
        fixed. PENDING says "not yet" and fires on every ordinary track load;
        `subtitle-source-unavailable` is the reset each load runs, for the same reason.
        """
        if outcome != GeometryOutcome.UNSUPPORTED or reason in _UNANNOUNCED_REASONS:
            return
        if reason == self._announced_reason:
            return
        self._announced_reason = reason
        self._ports.notify(_fallback_notice(reason, diagnostic), "warn")

    def _set_ready(self, *, active_events: int = 0) -> None:
        self._failure_diagnostic = None
        self.fallback_reason = None
        self._set_decision(
            GeometryOutcome.READY,
            "ready",
            active_events=active_events,
            owner="native",
        )

    def _set_pending_with_current_geometry(self, *, active_events: int) -> None:
        self.fallback_reason = "subtitle-observation-pending"
        self._set_decision(
            GeometryOutcome.PENDING,
            "subtitle-observation-pending",
            active_events=active_events,
            owner=self._owner,
        )

    def _degrade_geometry(self, reason: str, *, error_code: str | None = None) -> None:
        self._ports.degrade()
        renderer = self._ports.pipeline.renderer
        ownership = getattr(renderer, "ownership_state", None)
        owner = getattr(getattr(ownership, "owner", None), "value", self._owner)
        self._set_fallback(reason, error_code=error_code)
        if owner != self._owner:
            self._transition_owner(owner, self._last_decision)

    @property
    def status(self) -> NativeSubtitleStatus:
        return NativeSubtitleStatus(
            enabled=True,
            source=str(self.source_path) if self.source_path is not None else None,
            fallback_reason=self.fallback_reason,
            geometry_ready=self._last_snapshot is not None,
            ass_full_capability=self.ass_full_capability,
            eligible_tokens=self._eligible_tokens,
            skipped_tokens=self._skipped_tokens(),
            owner=self._owner,
            outcome=self._last_decision[0] if self._last_decision is not None else None,
            last_transition=self._last_transition,
            last_recovery=self._last_recovery,
            source_epoch=self._source_epoch,
        )

    @property
    def source_unsupported(self) -> bool:
        """Whether this track can never produce geometry, so legacy should own its pixels.

        A stable property of the *source*, not a geometry outcome — which is what lets it select a
        renderer without breaking the rule that geometry availability may not. It moves only when
        the source does, so the switch happens once per track and never between cues.
        """
        return self.fallback_reason in _UNSUPPORTED_SOURCE_REASONS

    def observe_ass_full_reply(self, reply: Mapping[str, object]) -> None:
        error = reply.get("error")
        if error == "success":
            self.ass_full_capability = AssFullCapability.SUPPORTED
        elif error in {"property unavailable", "property is unavailable"}:
            self.ass_full_capability = AssFullCapability.UNKNOWN
        elif error in {"property not found", "unknown property"}:
            self.ass_full_capability = AssFullCapability.UNSUPPORTED

    def set_fonts(self, environment: subtitle_fonts.FontEnvironment) -> None:
        """Adopt the font set mpv holds for this track, resolved by whoever loaded it.

        Not read from mpv here: it costs an ffmpeg dump and two `expand-path` round trips, and gate
        6 keeps that off the interaction loop. Arriving late is safe — it invalidates, so the next
        cue re-measures; arriving never leaves the environment empty and the check below refuses
        the frame rather than laying it out in whatever the system happens to offer.
        """
        if environment == self._fonts:
            return
        self._fonts = environment
        log.info(
            "subtitle font sources: %s (%d attachment(s), fonts-dir %s)",
            "+".join(environment.sources) or "none",
            len(environment.attachments),
            environment.setup.fonts_dir or "-",
        )
        if otel_metrics.subtitle_geometry_font_sources is not None:
            otel_metrics.subtitle_geometry_font_sources.add(
                1, {"sources": "+".join(environment.sources) or "none"}
            )
        self.invalidate(cause=GeometryCacheReason.RENDER_INPUT_CHANGED)

    def _fonts_are_current(self, render: _RenderInputs) -> bool:
        """Whether the resolved environment still describes what mpv is doing.

        mpv treats every one of these options as `UPDATE_SUB_HARD` — it rebuilds its own subtitle
        decoder — so a change between track loads means our faces and its faces have diverged.
        """
        return self._fonts.options == render.font_options

    def set_source(self, path: Path | None, *, live: bool = False) -> None:
        """Point at a new source.

        `live` says a running session is switching, so whatever the old source left on screen has
        to be retired. It used to be spelled by passing the host and testing it for `None` — a
        parameter whose only use was its own presence, which reads as a dependency and is a flag.
        """
        if live:
            self._consume_failure()
        self._source_epoch += 1
        self.worker.invalidate(cause=GeometryCacheReason.SOURCE_CHANGED)
        self.source_path = None
        self.source_kind = SourceKind.NONE
        self._source_bytes = None
        self._in_document_families = frozenset()
        self._converted_markup = {}
        self._last_snapshot = None
        self._submitted_at = None
        self._pending_key = None
        self._published_key = None
        self._last_selection = AnnotationSelection((), 0, 0, 0)
        self._eligible_tokens = 0
        self._last_render_inputs = None
        self._failure_diagnostic = None
        if live:
            self._ports.clear_interaction()
            self._ports.degrade()
        if path is None:
            self._set_fallback("subtitle-source-unavailable")
        elif path.suffix.casefold() == ".ass":
            self._adopt_authored(path)
        elif self.formats is not NativeFormats.ALL:
            self._set_fallback("subtitle-source-not-authored-ass")
        elif self._track_codec not in CONVERTED_CODECS:
            self._set_fallback("subtitle-source-conversion-unreproduced")
        else:
            self._adopt_converted(path)

    def _adopt_authored(self, path: Path) -> None:
        try:
            with path.open("rb") as source_file:
                source = source_file.read(MAX_ASS_SOURCE_BYTES + 1)
        except OSError:
            self._set_fallback("subtitle-source-unavailable")
            return
        if len(source) > MAX_ASS_SOURCE_BYTES:
            self._set_fallback("subtitle-source-too-large")
            return
        self._source_bytes = source
        # Once per track, not per cue: an `[Fonts]` section decodes megabytes, and this is already
        # the call that reads the whole file off disk.
        self._in_document_families = font_names.in_document(source)
        self.source_path = path
        self.source_kind = SourceKind.AUTHORED
        self.fallback_reason = None

    def _adopt_converted(self, path: Path) -> None:
        """Take a track mpv is rendering from libavcodec's conversion rather than from this file.

        The file is still read, but not as the document: mpv is not drawing it. It is read for the
        cue markup, which the cue index has already thrown away — `parse_srt` keeps plain text — and
        which is the only thing `subtitles.subrip` needs to predict the events mpv will report. That
        prediction is what gives a converted track a lookahead window at all.
        """
        self.source_path = path
        self.source_kind = SourceKind.CONVERTED
        self.fallback_reason = None
        self._converted_markup = {}
        try:
            with path.open("rb") as source_file:
                raw = source_file.read(MAX_ASS_SOURCE_BYTES + 1)
        except OSError:
            return  # only the lookahead is lost; the per-cue path never reads this file
        if len(raw) > MAX_ASS_SOURCE_BYTES:
            return
        try:
            self._converted_markup = subrip.markup_by_cue(raw.decode("utf-8"))
        except UnicodeDecodeError:
            log.debug("converted subtitle source is not UTF-8; no cue lookahead for this track")

    def invalidate(
        self,
        *,
        live: bool = False,
        cause: GeometryCacheReason = GeometryCacheReason.RENDER_INPUT_CHANGED,
    ) -> None:
        """Drop the cached geometry. `live` retires the interaction it was backing — see
        `set_source`."""
        if live:
            self._consume_failure()
        self._last_snapshot = None
        self._submitted_at = None
        self._pending_key = None
        self._published_key = None
        self.worker.invalidate(cause=cause)
        if live:
            self._ports.clear_interaction()

    def refresh(self, seen: GeometryObservation) -> None:
        identity = self._observation_key(seen)
        snapshot = self._ports.pipeline.current
        if (
            identity is not None
            and identity == self._published_key
            and snapshot is not None
            and snapshot is self._last_snapshot
        ):
            active_events = len(snapshot.frame_id.active_event_ids)
            if seen.prop("sub-start") is None or seen.prop("sub-end") is None:
                self._set_pending_with_current_geometry(active_events=active_events)
            else:
                self._set_ready(active_events=active_events)
            return
        self.invalidate(live=True)
        if seen.text.strip():
            self.schedule(seen)

    @staticmethod
    def record_clock_change(prop) -> None:
        with otel_metrics.traced("subtitle_geometry_clock") as span:
            try:
                raw_start = prop("sub-start")
                video_time, sub_delay, subtitle_time, _timestamp_ms = _subtitle_clock(
                    prop("time-pos"),
                    prop("sub-delay"),
                    None if raw_start is None else float(raw_start),
                )
            except (TypeError, ValueError):
                span.set("outcome", "invalid")
                log.info("mpv sub-delay changed; subtitle clock is temporarily unavailable")
                return
            span.set("outcome", "ready")
            span.set("video_time_ms", round(video_time * 1_000))
            span.set("sub_delay_ms", round(sub_delay * 1_000))
            span.set("subtitle_time_ms", round(subtitle_time * 1_000))
            log.info(
                "mpv sub-delay changed: video=%+.3fs delay=%+.3fs subtitle=%+.3fs",
                video_time,
                sub_delay,
                subtitle_time,
            )

    def mark_empty(self) -> None:
        self._last_snapshot = None
        self._pending_key = None
        self._published_key = None
        self._submitted_at = None
        self._last_selection = AnnotationSelection((), 0, 0, 0)
        self._eligible_tokens = 0
        self.fallback_reason = None
        self._set_decision(GeometryOutcome.EMPTY, "empty", owner=self._owner)

    @staticmethod
    def _annotations(
        text: str,
        lines,
        tokens,
        is_skippable: Callable[[Token], bool],
    ) -> AnnotationSelection:
        source_lines = text.replace("\\N", "\n").replace("\r", "").split("\n")
        annotated: list[TokenAnnotation] = []
        skipped: list[str] = []
        token_index = 0
        offset = 0
        line_index = 0
        for line_tokens in lines:
            while line_index < len(source_lines) and not source_lines[line_index].strip():
                offset += len(source_lines[line_index]) + 1
                line_index += 1
            if line_index >= len(source_lines):
                raise ValueError("token lines exceed subtitle semantic text")
            line = source_lines[line_index]
            for token in line_tokens:
                annotation, reason = _annotation_for_token(
                    token, token_index, line, offset, is_skippable
                )
                if annotation is not None:
                    annotated.append(annotation)
                if reason is not None:
                    skipped.append(reason)
                token_index += 1
            offset += len(line) + 1
            line_index += 1
        if token_index != len(tokens):
            raise ValueError("token lines do not cover the flattened subtitle tokens")
        return AnnotationSelection(
            tuple(annotated),
            skipped.count("whitespace"),
            skipped.count("tokenizer"),
            skipped.count("unpaintable"),
        )

    @staticmethod
    def _key(path: Path, cue: _CueInputs) -> str:
        return repr(
            (
                str(path.resolve()),
                cue.text,
                canonical_active_ass_rows(cue.active_rows),
                cue.frame_size,
                cue.storage_size,
                cue.pixel_aspect,
                cue.margins,
                cue.use_margins,
                cue.render_profile,
            )
        )

    @staticmethod
    def _key_divergence(live: str, filed: Iterable[str]) -> str:
        """Which components of the nearest filed key the live one disagrees with.

        A converted track predicts its own events from the `.srt` rather than reading them off a
        document, so a lookahead that files a key mpv never reproduces misses *every* cue and
        reports the same `first-seen` as a track with no lookahead at all. Naming the field says
        which of the two it is, and the components are the key's own — the tuple is written from
        literals, so it reads back.
        """
        try:
            here = ast.literal_eval(live)
        except (SyntaxError, ValueError):
            return "unreadable"
        best: tuple[str, ...] | None = None
        for candidate in filed:
            try:
                there = ast.literal_eval(candidate)
            except (SyntaxError, ValueError):
                continue
            differing = tuple(
                name
                for name, mine, theirs in zip(_KEY_FIELDS, here, there, strict=False)
                if mine != theirs
            )
            if best is None or len(differing) < len(best):
                best = differing
        if best is None:
            return "nothing-filed"
        return ",".join(best) or "none"

    def _observation_key(self, seen: GeometryObservation) -> str | None:
        path = self.source_path
        active_rows = seen.prop("sub-text/ass-full")
        if path is None or not isinstance(active_rows, str) or not active_rows.strip():
            return None
        try:
            render = self._render_inputs(seen.prop, seen.osd)
            cue = _CueInputs(
                0,
                1,
                0,
                seen.text,
                active_rows,
                render.frame_size,
                render.storage_size,
                render.pixel_aspect,
                render.margins,
                render.use_margins,
                render.profile,
            )
            token_identity = tuple((token.surface, token.start, token.end) for token in seen.tokens)
            return repr((self._key(path, cue), token_identity))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _build(
        source: bytes,
        track_id: SubtitleTrackId,
        generation: int,
        cue: _CueInputs,
        annotations: tuple[TokenAnnotation, ...],
        fonts: subtitle_fonts.FontEnvironment,
        renderer_state: RendererState,
        unreachable: subtitle_fonts.OsdReach,
    ) -> GeometryRequest:
        with otel_metrics.traced("subtitle_geometry_prepare") as span:
            span.set("observed_rows", len(cue.active_rows.splitlines()))
            span.set("eligible_tokens", len(annotations))
            span.set("timestamp_ms", cue.timestamp_ms)
            started = time.perf_counter_ns()
            try:
                prepared = prepare_ass_hit_map_frame(
                    source,
                    track_id,
                    active_rows=cue.active_rows,
                    text=cue.text,
                    tokens=annotations,
                )
            except Exception as error:
                span.set("outcome", "failed")
                span.set("error_code", geometry_error_code(error))
                raise
            prepare_ms = (time.perf_counter_ns() - started) / 1_000_000
            span.set("outcome", "ready")
            span.set("matched_events", len(prepared.events))
            span.set("prepare_ms", prepare_ms)
            if otel_metrics.subtitle_geometry_prepare_ms is not None:
                otel_metrics.subtitle_geometry_prepare_ms.record(prepare_ms)
        palette = _palette_in_frame_units(
            prepared, cue.frame_size[1], renderer_state.font_scale, unreachable=unreachable
        )
        return GeometryRequest(
            generation,
            track_id,
            prepared.frame_id,
            cue.timestamp_ms,
            cue.frame_size,
            cue.storage_size,
            prepared.ass,
            pixel_aspect=cue.pixel_aspect,
            margins=cue.margins,
            use_margins=cue.use_margins,
            render_profile=cue.render_profile,
            palette=palette,
            reserved_rgb=prepared.reserved_rgb,
            attachments=fonts.attachments,
            font_setup=fonts.setup,
            renderer_state=renderer_state,
            # Exactly the frames the text device cannot color: no resolved face, a family only the
            # subtitle renderer holds, or a document with no `PlayResY` to scale from. The raster
            # device needs none of those — it tints what the measurement already drew.
            keep_coverage=prepared.requires_coverage
            or any(entry.font_size <= 0 for entry in palette),
        )

    def _render_inputs(self, prop: Callable[[str], Any], osd: tuple[int, int]) -> _RenderInputs:
        """Read the mpv properties native geometry depends on, then decide.

        Takes the property reader and the OSD surface rather than the host: those two are all it
        ever wanted, and a shim that takes the whole `SessionController` cannot be driven by the session
        runtime. The surface is the host's, and is the frame — see `render_inputs_of`.
        """
        return render_inputs_of(
            prop("osd-dimensions") or {},
            prop("video-out-params") or {},
            {name: prop(f"options/{name}") for name in GATE_OPTIONS},
            frame_size=osd,
            authored=self.source_kind is not SourceKind.CONVERTED,
        )

    def _lookahead_cue(
        self,
        track_id: SubtitleTrackId,
        timestamp_ms: int,
        render: _RenderInputs,
        index: CueIndex,
    ) -> _CueInputs | None:
        """The cue at `timestamp_ms`, or `None` when there is nothing to read ahead for.

        Two ways to get one, because the two track kinds hold their events in different places. An
        authored track is read out of the document mpv is drawing. A converted one has no such
        document, so the event is *predicted* from the `.srt` — and a wrong prediction cannot
        become a wrong box, because the cache key carries the rows and a mispredicted one simply
        never matches the observation it was meant to serve.
        """
        rows = (
            self._converted_rows(timestamp_ms, index)
            if self.source_kind is SourceKind.CONVERTED
            else self._authored_rows(track_id, timestamp_ms)
        )
        if rows is None:
            return None
        active_rows, text = rows
        if not active_rows.strip() or not text.strip():
            return None
        return _CueInputs(
            timestamp_ms,
            timestamp_ms + 1,
            timestamp_ms,
            text,
            active_rows,
            render.frame_size,
            render.storage_size,
            render.pixel_aspect,
            render.margins,
            render.use_margins,
            render.profile,
        )

    def _authored_rows(
        self, track_id: SubtitleTrackId, timestamp_ms: int
    ) -> tuple[str, str] | None:
        source = self._source_bytes
        if source is None:
            return None
        try:
            return authored_ass_rows_at(source, track_id, timestamp_ms)
        except (TypeError, ValueError):
            return None

    def _converted_rows(self, timestamp_ms: int, index: CueIndex) -> tuple[str, str] | None:
        """The event libavcodec will hand mpv for the cue at `timestamp_ms`, and its plain text.

        `None` whenever anything is less than certain — no cue there, no markup recovered for it,
        or a cue whose SubRip markup `subtitles.subrip` declines to predict. Declining costs the
        lookahead for that one cue, which is exactly what a converted track had for all of them.
        """
        active = index.active_at(timestamp_ms / 1_000)
        if not active.located:
            return None
        cue = index.cues[active.position]
        markup = self._converted_markup.get((round(cue.start * 1_000), round(cue.end * 1_000)))
        if markup is None:
            return None
        row = subrip.dialogue_row(cue, markup)
        if row is None:
            return None
        try:
            decoded = decode_ass_event(parse_ass_event_line(row, SubtitleTrackId("lookahead"), 0))
        except (TypeError, ValueError):
            return None
        return row, decoded.text

    def _prefetch(
        self,
        seen: GeometryObservation,
        path: Path,
        track_id: SubtitleTrackId,
        generation: int,
        render_inputs: _RenderInputs,
        after_ms: int,
    ) -> None:
        index = seen.index
        if index is None or self.lookahead == 0:
            return
        if self.source_kind is SourceKind.NONE:
            return
        queued_keys: set[str] = set()
        for boundary in index.boundaries_after(after_ms / 1_000):
            timestamp_ms = round(boundary * 1_000) + 1
            inputs = self._lookahead_cue(track_id, timestamp_ms, render_inputs, index)
            if inputs is None:
                continue
            key = self._key(path, inputs)
            if key in queued_keys:
                continue
            queued_keys.add(key)
            # Per cue, not once for the track: a converted document is rebuilt around this cue's own
            # rows, so the two kinds cannot share one captured source.
            document = self._document_for(inputs.active_rows, render_inputs)
            state = self._renderer_state(render_inputs)

            def build(
                inputs: _CueInputs = inputs,
                document: bytes = document,
                state: RendererState = state,
                fonts: subtitle_fonts.FontEnvironment = self._fonts,
                unreachable: subtitle_fonts.OsdReach = self.unreachable_families,
            ) -> GeometryRequest:
                tokenized = self._ports.tokenize_lookahead(inputs.text)
                selection = self._annotations(
                    inputs.text,
                    tokenized.lines,
                    tokenized.tokens,
                    seen.is_skippable,
                )
                if not selection.annotations:
                    raise ValueError("prefetched frame has no interaction-eligible tokens")
                return self._build(
                    document,
                    track_id,
                    generation,
                    inputs,
                    selection.annotations,
                    fonts,
                    state,
                    unreachable,
                )

            self.worker.prefetch(key, generation, build)
            if len(queued_keys) >= self.lookahead:
                break

    def schedule(self, seen: GeometryObservation) -> bool:
        try:
            return self._schedule(seen)
        except Exception as error:  # noqa: BLE001  # optional provider must fail interaction closed
            self.worker.mark_not_ready()
            reason, code = geometry_failure_reason(error)
            self._degrade_geometry(
                reason,
                error_code=code,
            )
            return False

    def _active_observation(
        self,
        seen: GeometryObservation,
        source: bytes | None,
        track_id: SubtitleTrackId,
        start: float,
        end: float,
        active_rows: object,
    ) -> tuple[float, float, int, str] | None:
        """Where in the track this cue is, and the rows that make it up.

        `source` is `None` for a converted track — mpv is not rendering a file there, so there is no
        document to index into and the rows have to come from mpv itself.
        """
        hint = seen.cue_hint
        if hint is not None and self.source_kind is SourceKind.CONVERTED:
            # The hint means "we navigated and mpv has not caught up". On an authored track the
            # document answers for the target cue; on a converted one there is nothing to ask until
            # mpv reports the rows, so wait for it rather than measure the cue we are leaving.
            self._degrade_geometry("subtitle-observation-pending")
            return None
        if hint is not None and source is not None:
            timestamp_ms = round(hint.start * 1_000) + 1
            rows, semantic_text = authored_ass_rows_at(source, track_id, timestamp_ms)
            if semantic_text != seen.normalise(seen.text):
                self._degrade_geometry("subtitle-observation-pending")
                return None
            return hint.start, hint.end, timestamp_ms, rows
        if not isinstance(active_rows, str) or not active_rows.strip():
            self._degrade_geometry("subtitle-ass-full-unavailable")
            return None
        try:
            _video_time, _sub_delay, subtitle_time, timestamp_ms = _subtitle_clock(
                seen.prop("time-pos"), seen.prop("sub-delay"), start
            )
        except (TypeError, ValueError):
            self._degrade_geometry("subtitle-timing-unavailable")
            return None
        if seen.index is not None:
            position = seen.index.locate(
                text=seen.text,
                sub_start=start,
                time_pos=subtitle_time,
                preferred=seen.nav_index,
            )
            if position >= 0:
                indexed = seen.index.cues[position]
                if indexed.text == seen.text:
                    start, end = indexed.start, indexed.end
        return start, end, timestamp_ms, active_rows

    def _render_space(self, render: _RenderInputs) -> converted.RenderSpace:
        return converted.RenderSpace(render.frame_size[0], render.frame_size[1], render.margins)

    def _converted_scale(self, render: _RenderInputs) -> float:
        """`ass_set_font_scale` on mpv's converted branch — the letterbox multiplier, not 1."""
        return converted.font_scale(
            self._render_space(render),
            use_margins=render.use_margins,
            scale_with_window=render.scale_with_window,
            scale_by_window=render.scale_by_window,
        )

    def _document_for(self, rows: str, render: _RenderInputs) -> bytes:
        """The document the boxes are measured against.

        Authored: the file, as mpv reads it. Converted: rebuilt around the rows mpv just reported,
        because mpv is rendering libavcodec's conversion rather than anything on disk, and
        `sub-ass-extradata` is *property unavailable* there so its header cannot be read back.
        """
        if self.source_kind is SourceKind.CONVERTED:
            return converted.document(
                rows,
                self._render_space(render),
                style=render.style,
                scale=self._converted_scale(render),
            )
        return self._source_bytes or b""

    def _renderer_state(self, render: _RenderInputs) -> RendererState:
        """What the measuring renderer must be set to for this track kind.

        For an authored track: libass's defaults under `--sub-ass-override=no`, where
        `configure_ass` assigns none of the renderer values — and the `scale` branch's four when it
        is on. For a converted one, the font scale and the three track features `configure_ass`
        turns on (`sd_ass.c:604-615`); each of them changes how a run's advances accumulate, so
        leaving them off measures a layout mpv is not drawing.
        """
        if self.source_kind is not SourceKind.CONVERTED:
            return _scaled_renderer_state(render.scale)
        return RendererState(
            font_scale=self._converted_scale(render),
            blur=render.style.blur,
            justify=render.style.justify,
            features=(
                (_ASS_FEATURE_WRAP_UNICODE, True),
                (_ASS_FEATURE_BIDI_BRACKETS, True),
                (_ASS_FEATURE_WHOLE_TEXT_LAYOUT, True),
            ),
        )

    def _trace_unscheduled(self, reason: str, cue_revision: int) -> None:
        """Name a geometry schedule that never started. Not a degrade: the inputs are not assembled
        yet, so pixels and ownership are untouched — only the silence is the bug. The revision is
        what ties a dropped schedule back to the observation that armed the refresh."""
        with otel_metrics.traced("subtitle_geometry_inputs") as span:
            span.set("reason", reason)
            span.set("cue_revision", cue_revision)
        log.debug("geometry schedule skipped: %s (cue revision %d)", reason, cue_revision)

    def _unassembled_input(self, *, cue_text: str, has_tokens: bool) -> str | None:
        """Which of the four schedule preconditions is not met yet, if any. These are "inputs not
        assembled", not a failure — name which and do not degrade."""
        if self.source_path is None or self.source_kind is SourceKind.NONE:
            return "no-source-path"
        if self.source_kind is SourceKind.AUTHORED and self._source_bytes is None:
            return "no-source-bytes"
        if not cue_text.strip():
            return "no-cue-text"
        if not has_tokens:
            return "no-tokens"
        return None

    def _resolve_schedule_inputs(self, seen: GeometryObservation) -> _ScheduleInputs | None:
        path = self.source_path
        # `None` for a converted track by design: mpv is not rendering a file there either, so the
        # document is rebuilt per cue from the rows it reports (`_document_for`).
        source = self._source_bytes
        unassembled = self._unassembled_input(cue_text=seen.text, has_tokens=bool(seen.tokens))
        if unassembled is not None or path is None:
            # Every other exit below records a reason; this one used to return a bare None, so a
            # geometry schedule that never ran was invisible in the logs and in telemetry.
            self._trace_unscheduled(unassembled or "no-source-path", seen.cue_revision)
            return None
        if self.ass_full_capability == AssFullCapability.UNSUPPORTED:
            self._degrade_geometry("subtitle-ass-full-unsupported")
            return None
        try:
            render = self._render_inputs(seen.prop, seen.osd)
        except (TypeError, ValueError) as error:
            self.worker.mark_not_ready()
            self._set_fallback("subtitle-render-input-unsupported", log_detail=str(error))
            self._ports.degrade()
            return None
        self._last_render_inputs = render
        if not self._fonts_are_current(render):
            self.worker.mark_not_ready()
            self._set_fallback(
                "subtitle-font-environment-stale",
                log_detail=f"{self._fonts.options} != {render.font_options}",
            )
            self._ports.degrade()
            return None
        active_rows = seen.prop("sub-text/ass-full")
        if isinstance(active_rows, str):
            self.ass_full_capability = AssFullCapability.SUPPORTED
        start = seen.prop("sub-start")
        end = seen.prop("sub-end")
        if start is None or end is None:
            self._degrade_geometry("subtitle-observation-pending")
            return None
        generation = self._ports.pipeline.generation
        track_id = SubtitleTrackId(f"sid:{seen.prop('sid')}:{path.resolve()}")
        active = self._active_observation(
            seen, source, track_id, float(start), float(end), active_rows
        )
        if active is None:
            return None
        start, end, timestamp_ms, rows = active
        cue = _CueInputs(
            round(start * 1_000),
            round(end * 1_000),
            timestamp_ms,
            seen.text,
            rows,
            render.frame_size,
            render.storage_size,
            render.pixel_aspect,
            render.margins,
            render.use_margins,
            render.profile,
        )
        return _ScheduleInputs(
            path,
            self._document_for(rows, render),
            track_id,
            generation,
            render,
            cue,
            self._key(path, cue),
            self._observation_key(seen),
        )

    def _publish_cached(self, seen: GeometryObservation, inputs: _ScheduleInputs) -> bool:
        cached = self.worker.publish_prefetched(inputs.key, inputs.generation)
        if cached is None:
            return False
        if inputs.observation_key is not None:
            self._pending_key = (inputs.generation, inputs.observation_key)
        self._eligible_tokens = len(cached.palette)
        self.worker.mark_presented(cached)
        if not self._ports.use_native():
            if self._ports.ownership_undecided():
                return True  # the assertion's terminal re-drives the refresh
            self._degrade_geometry("mpv-sub-visibility-rejected")
            return True
        self._set_ready(active_events=len(cached.frame_id.active_event_ids))
        self._prefetch(
            seen,
            inputs.path,
            inputs.track_id,
            inputs.generation,
            inputs.render,
            inputs.cue.timestamp_ms,
        )
        return True

    def _schedule(self, seen: GeometryObservation) -> bool:
        self._last_selection = AnnotationSelection((), 0, 0, 0)
        self._eligible_tokens = 0
        inputs = self._resolve_schedule_inputs(seen)
        if inputs is None:
            return False
        with otel_metrics.traced("subtitle_geometry_cache") as span:
            cache_hit = self._publish_cached(seen, inputs)
            stats = self.worker.stats
            span.set("outcome", "hit" if cache_hit else "miss")
            if not cache_hit:
                span.set("reason", self.worker.prefetch_miss_reason(inputs.key))
                span.set(
                    "key_divergence",
                    self._key_divergence(inputs.key, self.worker.filed_keys()),
                )
            span.set("cache_hits", stats.cache_hits)
            span.set("prefetch_dropped", stats.prefetch_dropped)
            span.set("prefetch_cache_entries", stats.prefetch_cache_entries)
            span.set("coverage_trimmed", stats.coverage_trimmed)
        if cache_hit:
            # A hit resolves inside this call, so there is no terminal to wait for: publish here, or
            # a cached cue would sit unapplied until some later miss happened to land.
            self.apply(seen)
            return True
        try:
            selection = self._annotations(
                seen.text,
                seen.lines,
                seen.tokens,
                seen.is_skippable,
            )
        except ValueError:
            self.worker.mark_not_ready()
            self._degrade_geometry("subtitle-token-annotation-invalid")
            return False
        self._last_selection = selection
        self._eligible_tokens = len(selection.annotations)
        if not selection.annotations:
            self._ports.publish([], None)
            self.worker.mark_not_ready()
            if self._ports.use_native():
                self._published_key = inputs.observation_key
                self._set_ready()
                return True
            if self._ports.ownership_undecided():
                return False  # the assertion's terminal re-drives the refresh
            self._degrade_geometry("mpv-sub-visibility-rejected")
            return False

        # Bound now, not read in the closure: the job runs on the worker, and by then the track may
        # have changed under it — the same reason `fonts` is bound here.
        renderer_state = self._renderer_state(inputs.render)

        def build(
            fonts: subtitle_fonts.FontEnvironment = self._fonts,
            state: RendererState = renderer_state,
            unreachable: subtitle_fonts.OsdReach = self.unreachable_families,
        ) -> GeometryRequest:
            return self._build(
                inputs.source,
                inputs.track_id,
                inputs.generation,
                inputs.cue,
                selection.annotations,
                fonts,
                state,
                unreachable,
            )

        def settled() -> None:
            """The lane terminal is what publishes the result — nothing polls for it."""
            self.apply(seen)

        # Everything the result will be judged against is established BEFORE the work is queued.
        # These used to run after, which reads fine while a completion is guaranteed to be later —
        # and silently wipes the result the moment one is not, because `_degrade_geometry` clears
        # the very boxes the completion just published.
        if inputs.observation_key is not None:
            self._pending_key = (inputs.generation, inputs.observation_key)
        self._submitted_at = (inputs.generation, time.perf_counter())
        self.worker.mark_not_ready()
        self._degrade_geometry("subtitle-geometry-cache-miss")
        accepted = self.worker.submit_job(
            inputs.generation, build, work_key=inputs.key, on_settled=settled
        )
        if accepted:
            self._prefetch(
                seen,
                inputs.path,
                inputs.track_id,
                inputs.generation,
                inputs.render,
                inputs.cue.timestamp_ms,
            )
        return accepted

    def apply(self, seen: GeometryObservation) -> bool:
        try:
            return self._apply(seen)
        except Exception as error:  # noqa: BLE001  # optional provider must fail interaction closed
            reason, code = geometry_failure_reason(error)
            self._degrade_geometry(
                reason,
                error_code=code,
            )
            return False

    def _consume_failure(self) -> None:
        error = self._ports.pipeline.consume_error()
        if error is None:
            return
        reason, code = geometry_failure_reason(error)
        if error.startswith("subtitle-source-"):
            reason = error
        self._degrade_geometry(
            reason,
            error_code=code if not reason.startswith("subtitle-source-") else None,
        )

    @staticmethod
    def _snapshot_identities_are_valid(snapshot: GeometrySnapshot, token_count: int) -> bool:
        """Whether a worker's geometry can be painted against the cue currently tokenized.

        Each guard rejects a different way the snapshot and the cue can disagree: a repeated token
        index would paint one box twice; an event the frame no longer lists is geometry for a cue
        that has gone; and an index past ``token_count`` is a box for a token this cue does not
        have. Any of them paints a hit region over the wrong word.

        There was a fourth, rejecting a repeated ``(event, token)`` pair. It could never fire:
        equal pairs have equal indices, so the index guard already rejected them.
        """
        identities = [(item.event_id, item.token_index) for item in snapshot.tokens]
        indices = [token_index for _event_id, token_index in identities]
        active_events = set(snapshot.frame_id.active_event_ids)
        return bool(
            len(indices) == len(set(indices))
            and all(event_id in active_events for event_id, _token_index in identities)
            and all(0 <= index < token_count for index in indices)
        )

    def _install_snapshot(self, snapshot: GeometrySnapshot) -> list[WordBox]:
        """Retain ``snapshot`` and return its boxes; the caller owns writing them onto the host.

        The origin is the caller's to set to (0, 0) alongside: these boxes are already in frame
        coordinates, so a leftover subtitle origin would offset every hit region by it.
        """
        self._last_snapshot = snapshot
        if self._pending_key is not None and self._pending_key[0] == snapshot.generation:
            self._published_key = self._pending_key[1]
        self._pending_key = None
        # Into screen coordinates here rather than through `publish`'s origin: the origin only
        # translates hit testing, and the focus rect, the overprint's `\pos` and the raster's upload
        # point all read the box directly. One translated set is one answer; a translated origin
        # beside untranslated boxes is two.
        origin = (0, 0) if self._last_render_inputs is None else self._last_render_inputs.box_origin
        return [
            WordBox(
                item.token_index,
                item.bounds.x + origin[0],
                item.bounds.y + origin[1],
                item.bounds.width,
                item.bounds.height,
                item.font_name,
                item.font_size,
                item.coverage,
            )
            for item in snapshot.tokens
        ]

    def _record_ready_latency(self, generation: int) -> None:
        if (
            self._submitted_at is not None
            and self._submitted_at[0] == generation
            and otel_metrics.subtitle_geometry_ready_ms is not None
        ):
            otel_metrics.subtitle_geometry_ready_ms.record(
                (time.perf_counter() - self._submitted_at[1]) * 1_000
            )
        self._submitted_at = None

    def _apply(self, seen: GeometryObservation) -> bool:
        snapshot = self._ports.pipeline.current
        if snapshot is None:
            self._consume_failure()
            return False
        if snapshot is self._last_snapshot:
            return False
        if not self._snapshot_identities_are_valid(snapshot, len(seen.tokens)):
            self._degrade_geometry("geometry-token-identity-invalid")
            return False
        self._ports.publish(self._install_snapshot(snapshot), (0, 0))
        self._record_ready_latency(snapshot.generation)
        if not self._ports.use_native():
            if self._ports.ownership_undecided():
                return False  # the assertion's terminal re-drives the refresh
            self._set_fallback("mpv-sub-visibility-rejected")
            return False
        self._set_ready(active_events=len(snapshot.frame_id.active_event_ids))
        self._ports.redraw()
        return True

    def close(self) -> None:
        self.worker.close()

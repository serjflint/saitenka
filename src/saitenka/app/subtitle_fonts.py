"""The font sources mpv hands libass, resolved the way mpv resolves them.

Four sources reach mpv's subtitle renderer: the system providers, one extra directory
(``--sub-fonts-dir``, or the config dir's ``fonts`` when that is empty), the container's font
attachments, and a ``[Fonts]`` section inside the document. The measuring renderer used to hold only
the first, so a track whose typesetting came from an attachment was laid out in a substitute face —
silently, because nothing compares the two layouts, and the boxes are simply the wrong width for the
whole episode.

Resolving these costs a subprocess and two IPC round trips, so it happens once per track beside the
artifact resolution (`embedded_subs`), never on the interaction loop.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka.subtitles import font_names
from saitenka.subtitles.geometry import FontProvider, FontSetup

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

log = logging.getLogger(__name__)

#: `sd_ass.c`'s `font_mimetypes`, verbatim — including the one it calls "probably incorrect",
#: because accepting a different set means loading a different font set than mpv did.
FONT_MIMETYPES = frozenset(
    {
        "application/x-truetype-font",
        "application/vnd.ms-opentype",
        "application/x-font-otf",
        "application/x-font-ttf",
        "application/x-font",
        "application/font-sfnt",
        "font/collection",
        "font/otf",
        "font/sfnt",
        "font/ttf",
    }
)
#: `sd_ass.c`'s `font_exts`. mpv compares the LAST FOUR CHARACTERS case-insensitively, so a name
#: shorter than five characters never matches and `x.OTF` does.
FONT_EXTENSIONS = frozenset({".ttf", ".ttc", ".otf", ".otc"})

#: The mpv options whose values decide the font environment. Read together with the render inputs so
#: a mid-session change is visible to the gate, and snapshotted into the resolved environment so a
#: change after resolution refuses the frame instead of measuring in the wrong faces.
FONT_OPTIONS = ("embeddedfonts", "sub-fonts-dir", "sub-font-provider", "sub-font")

_PROVIDERS = {
    "auto": FontProvider.AUTODETECT,
    "none": FontProvider.NONE,
    "fontconfig": FontProvider.FONTCONFIG,
}


def attachment_is_font(name: str | None, mimetype: str | None, size: int) -> bool:
    """`sd_ass.c`'s `attachment_is_font`: MIME match, then a last-four-character extension fallback.

    Mirrored rather than approximated. A broader test loads a face mpv ignored; a narrower one drops
    a face mpv used, and either way the two renderers lay the cue out differently.
    """
    if not name or not mimetype or size <= 0:
        return False
    if mimetype in FONT_MIMETYPES:
        return True
    return len(name) > 4 and name[-4:].casefold() in FONT_EXTENSIONS


@dataclass(frozen=True, slots=True)
class FontEnvironment:
    """What mpv gave libass for this track, plus the options it was derived from.

    The options travel with it so a live change to any of them is a refusal rather than a silent
    measurement in the wrong faces — `--embeddedfonts` and `--sub-fonts-dir` are `UPDATE_SUB_HARD`
    on mpv's side, and re-resolution here is a track load, not a frame.
    """

    setup: FontSetup = field(default_factory=FontSetup)
    attachments: tuple[tuple[str, bytes], ...] = ()
    options: tuple[tuple[str, str], ...] = ()
    attachment_families: frozenset[str] = frozenset()

    def osd_unreachable(self, in_document: frozenset[str] = frozenset()) -> frozenset[str]:
        """The families that reach mpv's subtitle renderer and never its **OSD** one.

        Its library is built from `osd_style` plus `mpv-osd-symbols` (`osd_libass.c:51-52`) and has
        no attachment path at all — so a family supplied by the container or by an in-file `[Fonts]`
        section is one an `osd-overlay` overprint would draw in a substitute face: right words, wrong
        glyph shapes, measured at −29px against the correct layout in the drift probe.

        Per family rather than per track, because a release whose dialogue is a system font and whose
        signs are attachment-only should lose the colour on its signs, not on the whole episode.
        """
        return self.attachment_families | (in_document if self.setup.extract_fonts else frozenset())

    @property
    def sources(self) -> tuple[str, ...]:
        """Which of the four sources are in play, for the counter and the report."""
        present = ["system"] if self.setup.font_provider != FontProvider.NONE else []
        if self.setup.fonts_dir:
            present.append("fonts-dir")
        if self.attachments:
            present.append("attachments")
        if self.setup.extract_fonts:
            present.append("in-file")
        return tuple(present)


def option_snapshot(settings: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((name, repr(settings.get(name))) for name in FONT_OPTIONS))


def _existing_path(expand: Callable[[str], str | None], placeholder: str) -> str | None:
    """Expand one of mpv's ``~~`` placeholders, keeping only what `mp_find_config_file` would.

    That function returns a path only when it exists, and `mp_get_user_path` falls back to joining
    the home config dir when it does not — which under `--no-config` is a bare relative name, since
    every platform path is NULL there. Requiring an absolute, existing path reproduces both.
    """
    expanded = expand(placeholder)
    if not expanded:
        return None
    path = Path(expanded)
    return str(path) if path.is_absolute() and path.exists() else None


def _fonts_dir(expand: Callable[[str], str | None], configured: object) -> str | None:
    """`mp_ass_init`: an explicit ``--sub-fonts-dir`` through `mp_get_user_path`, else the config
    dir's ``fonts`` through `mp_find_config_file`."""
    if isinstance(configured, str) and configured:
        expanded = expand(configured)
        return expanded if expanded and Path(expanded).is_dir() else None
    return _existing_path(expand, "~~/fonts")


def _font_provider(configured: object) -> FontProvider:
    return _PROVIDERS.get(str(configured), FontProvider.AUTODETECT)


def container_fonts(video: Path, *, cache_dir: Path) -> tuple[tuple[str, bytes], ...]:
    """The container's font attachments, dumped once per video and cached beside it.

    Fail-soft like the subtitle extraction next door: a missing ffmpeg or an unreadable container
    costs the attachment faces, and the frame is then refused by the environment check rather than
    measured in substitutes.
    """
    from saitenka.app.paths import sanitize_filename

    try:
        size = video.stat().st_size
    except OSError:
        return ()
    target = cache_dir / sanitize_filename(f"{video.stem}-{size}-fonts")
    manifest = target / "manifest.json"
    if not manifest.exists() and not _dump_container_fonts(video, target, manifest):
        return ()
    try:
        names = json.loads(manifest.read_text(encoding="utf-8"))
        return tuple((name, (target / name).read_bytes()) for name in names)
    except (OSError, ValueError, TypeError) as error:
        log.warning("container font cache for %s is unreadable: %s", video.name, error)
        return ()


def _attachment_streams(exe: str, video: Path) -> list[dict]:
    probe = subprocess.run(
        [
            exe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "t",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    streams = json.loads(probe.stdout or b"{}").get("streams")
    return streams if isinstance(streams, list) else []


def _font_attachments(streams: list[dict]) -> list[tuple[int, str]]:
    """The attachment ordinals mpv would load, paired with the filename to dump each under.

    Ordinals, because ffmpeg addresses attachments by their position among attachment streams
    (`-dump_attachment:t:N`), which is the order ffprobe lists them in.
    """
    wanted = []
    for ordinal, stream in enumerate(streams):
        tags = stream.get("tags") or {}
        name = tags.get("filename")
        # `extradata_size` is where ffprobe reports an attachment's payload length; mpv reads the
        # same bytes as `data_size` and refuses a zero-length one.
        if attachment_is_font(name, tags.get("mimetype"), int(stream.get("extradata_size") or 0)):
            wanted.append((ordinal, Path(str(name)).name))
    return wanted


def _dump_container_fonts(video: Path, target: Path, manifest: Path) -> bool:
    from saitenka.mpvio.discover import find_tool

    try:
        streams = _attachment_streams(find_tool("ffprobe") or "ffprobe", video)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        log.warning("could not list %s's attachments: %s", video.name, error)
        return False
    wanted = _font_attachments(streams)
    target.mkdir(parents=True, exist_ok=True)
    dumps = [
        argument
        for ordinal, name in wanted
        for argument in (f"-dump_attachment:t:{ordinal}", str(target / name))
    ]
    try:
        if dumps:
            subprocess.run(
                [
                    find_tool("ffmpeg") or "ffmpeg",
                    "-y",
                    *dumps,
                    "-i",
                    str(video),
                    "-f",
                    "null",
                    "-",
                ],
                check=True,
                capture_output=True,
            )
    except (OSError, subprocess.SubprocessError) as error:
        log.warning("could not dump %s's font attachments: %s", video.name, error)
        return False
    manifest.write_text(json.dumps([name for _ordinal, name in wanted]), encoding="utf-8")
    return True


def resolve(
    *,
    expand: Callable[[str], str | None],
    settings: Mapping[str, object],
    video: Path | None,
    cache_dir: Path,
) -> FontEnvironment:
    """Assemble the same font set mpv's subtitle renderer holds for this track."""
    embedded = settings.get("embeddedfonts") is True
    setup = FontSetup(
        fonts_dir=_fonts_dir(expand, settings.get("sub-fonts-dir")),
        # `sd_ass.c:255` passes `--embeddedfonts` straight to `ass_set_extract_fonts`, and gates
        # `add_subtitle_fonts` on the same flag — one option, both in-container sources.
        extract_fonts=embedded,
        default_font=_existing_path(expand, "~~/subfont.ttf"),
        default_family=str(settings.get("sub-font") or "") or None,
        fontconfig_config=_existing_path(expand, "~~/fonts.conf"),
        font_provider=_font_provider(settings.get("sub-font-provider")),
    )
    attachments = container_fonts(video, cache_dir=cache_dir) if embedded and video else ()
    return FontEnvironment(
        setup,
        attachments,
        option_snapshot(settings),
        frozenset().union(*(font_names.families(data) for _name, data in attachments))
        if attachments
        else frozenset(),
    )

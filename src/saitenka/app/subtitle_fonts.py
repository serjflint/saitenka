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
FONT_OPTIONS = (
    "embeddedfonts",
    "sub-fonts-dir",
    "sub-font-provider",
    "sub-font",
    # Read but never given to the measuring renderer: `mp_ass_init` and `mp_ass_configure_fonts`
    # take a style GROUP (`sub/osd.c:47-78` — `fonts-dir` and `font-provider` are per-group), so the
    # subtitle library gets these under `sub-` and the OSD library gets its own under `osd-`. When
    # the two disagree, a family the subtitle renderer resolves is one the OSD renderer may not, and
    # the text device has to stand down for it.
    "osd-fonts-dir",
    "osd-font-provider",
)

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
class OsdReach:
    """Which families an `osd-overlay` payload would be laid out in differently from the cue.

    Two shapes because there are two ways to lose: a *named* family the OSD library cannot load, and
    a configuration where its whole system lookup differs from the subtitle library's — at which
    point no family can be argued safe and the raster device takes the cue.
    """

    families: frozenset[str] = frozenset()
    all_unsafe: bool = False

    def blocks(self, family: str) -> bool:
        return self.all_unsafe or family in self.families


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
    #: Families supplied by `--sub-fonts-dir` and reachable from there ALONE, so they matter only
    #: when the OSD library reads a different directory.
    fonts_dir_families: frozenset[str] = frozenset()
    #: Whether the OSD library reads the same extra directory the subtitle library does. When it
    #: does not, only the families that live there are lost.
    osd_shares_fonts_dir: bool = True
    #: Whether the two libraries use the same font provider. When they do not, even a system family
    #: is looked up two ways and no family can be argued equal.
    osd_shares_provider: bool = True

    def osd_unreachable(self, in_document: frozenset[str] = frozenset()) -> OsdReach:
        """The families that reach mpv's subtitle renderer and never its **OSD** one.

        Three sources, all from `sub/osd_libass.c` and `sub/ass_mp.c`:

        * The OSD library is built from `osd_style` plus `mpv-osd-symbols` (`osd_libass.c:51-52`) and
          has no attachment path at all, so a container attachment or an in-file `[Fonts]` family is
          one it can never hold — right words, wrong glyph shapes, −29px in the drift probe.
        * `mp_ass_init` reads `fonts_dir` off the style group it is handed (`ass_mp.c:128-138`), and
          the OSD one is handed `osd_style`. So `--sub-fonts-dir` feeds the subtitle library and
          `--osd-fonts-dir` the OSD one; set only the first and its families are subtitle-only. With
          both unset they fall back to the same config directory, which is the common case and stays
          reachable.
        * `mp_ass_configure_fonts` takes the same group, so `--osd-font-provider` can differ from
          `--sub-font-provider`. Then even a system family is looked up two ways, and nothing here
          can argue any of them equal.

        Per family rather than per track, because a release whose dialogue is a system font and whose
        signs are attachment-only should lose the colour on its signs, not on the whole episode.
        """
        return OsdReach(
            self.attachment_families
            | (in_document if self.setup.extract_fonts else frozenset())
            | (frozenset() if self.osd_shares_fonts_dir else self.fonts_dir_families),
            all_unsafe=not self.osd_shares_provider,
        )

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


#: Enough for a typesetting release's whole font folder; a directory past it is not one a user
#: assembled, and reading all of it at track load would be the wrong place to find that out.
MAX_FONTS_DIR_FILES = 512


def _directory_families(fonts_dir: str | None) -> frozenset[str]:
    """Every family a `--sub-fonts-dir` supplies, for deciding which ones the OSD library lacks.

    Read from the files rather than from libass, which offers no way to enumerate what it loaded.
    Unreadable is empty, and empty means "no family is blamed on this directory" — the direction
    that keeps a colour rather than the one that invents a demotion.
    """
    if not fonts_dir:
        return frozenset()
    try:
        entries = sorted(Path(fonts_dir).iterdir())[:MAX_FONTS_DIR_FILES]
    except OSError as error:
        log.warning("could not list the subtitle fonts directory %s: %s", fonts_dir, error)
        return frozenset()
    found: set[str] = set()
    for entry in entries:
        try:
            found |= font_names.families(entry.read_bytes()) if entry.is_file() else frozenset()
        except OSError:
            continue
    return frozenset(found)


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
    osd_fonts_dir = _fonts_dir(expand, settings.get("osd-fonts-dir"))
    shares_dir = osd_fonts_dir == setup.fonts_dir
    return FontEnvironment(
        setup,
        attachments,
        option_snapshot(settings),
        frozenset().union(*(font_names.families(data) for _name, data in attachments))
        if attachments
        else frozenset(),
        # Only enumerated when it can matter: reading every face in a directory to name families
        # nothing will ask about is work for an answer already known.
        frozenset() if shares_dir else _directory_families(setup.fonts_dir),
        osd_shares_fonts_dir=shares_dir,
        osd_shares_provider=_font_provider(settings.get("osd-font-provider"))
        == setup.font_provider,
    )

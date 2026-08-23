"""The font sources are resolved the way mpv resolves them, or the boxes are the wrong width."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from saitenka.app import subtitle_fonts
from saitenka.subtitles import FontProvider

REPO_FONT = Path(__file__).resolve().parents[1] / "src/saitenka/assets/fonts/NotoSans.ttf"


def expander(config_dir: Path | None) -> object:
    """mpv's `expand-path`: only a `~`-prefixed path is touched, the rest come back verbatim."""

    def expand(path: str) -> str | None:
        if not path.startswith("~~"):
            return path
        stripped = path.removeprefix("~~").removeprefix("/")
        return stripped if config_dir is None else str(config_dir / stripped)

    return expand


@pytest.mark.parametrize(
    ("name", "mimetype", "size", "accepted"),
    [
        # Every MIME in mpv's list, including the one it calls "probably incorrect".
        ("sign.bin", "application/x-truetype-font", 12, True),
        ("sign.bin", "application/x-font", 12, True),
        ("sign.bin", "font/collection", 12, True),
        # The extension fallback: last four characters, case-insensitive.
        ("sign.OTF", "application/octet-stream", 12, True),
        ("sign.ttc", "application/octet-stream", 12, True),
        # mpv reads `name + strlen(name) - 4` only when the name is LONGER than four characters,
        # so a name that is exactly its own extension never matches.
        (".ttf", "application/octet-stream", 12, False),
        ("sign.png", "image/png", 12, False),
        # A zero-length or unnamed attachment is refused before either test.
        ("sign.ttf", "application/x-truetype-font", 0, False),
        (None, "application/x-truetype-font", 12, False),
        ("sign.ttf", None, 12, False),
    ],
)
def test_attachment_acceptance_mirrors_mpv(
    name: str | None, mimetype: str | None, size: int, *, accepted: bool
) -> None:
    assert subtitle_fonts.attachment_is_font(name, mimetype, size) is accepted


def test_an_empty_sub_fonts_dir_means_the_config_directory_not_no_fonts(tmp_path: Path) -> None:
    """The defect this replaced: the render-input gate read an empty `--sub-fonts-dir` as "no extra
    fonts", when mpv reads it as "use the config dir's `fonts`" — so a user with faces there got
    boxes measured without them, and nothing said so."""
    fonts = tmp_path / "fonts"
    fonts.mkdir()

    resolved = subtitle_fonts.resolve(
        expand=expander(tmp_path),
        settings={"sub-fonts-dir": "", "embeddedfonts": False, "sub-font-provider": "auto"},
        video=None,
        cache_dir=tmp_path / "cache",
    )

    assert resolved.setup.fonts_dir == str(fonts)
    assert resolved.sources == ("system", "fonts-dir")


def test_a_config_directory_without_a_fonts_folder_adds_nothing(tmp_path: Path) -> None:
    resolved = subtitle_fonts.resolve(
        expand=expander(tmp_path),
        settings={"sub-fonts-dir": "", "embeddedfonts": False, "sub-font-provider": "auto"},
        video=None,
        cache_dir=tmp_path / "cache",
    )

    assert resolved.setup.fonts_dir is None
    assert resolved.sources == ("system",)


def test_no_config_at_all_leaves_a_relative_expansion_unused(tmp_path: Path) -> None:
    """Under `--no-config` every platform path is NULL, so mpv's `~~` expansion returns a bare
    relative name and `mp_find_config_file` returns nothing. Taking the relative name would point
    libass at whatever `fonts` happens to sit in the working directory."""
    resolved = subtitle_fonts.resolve(
        expand=expander(None),
        settings={"sub-fonts-dir": "", "embeddedfonts": False, "sub-font-provider": "auto"},
        video=None,
        cache_dir=tmp_path / "cache",
    )

    assert resolved.setup.fonts_dir is None


def test_an_explicit_fonts_dir_wins_over_the_config_directory(tmp_path: Path) -> None:
    explicit = tmp_path / "typeset"
    explicit.mkdir()
    (tmp_path / "fonts").mkdir()

    resolved = subtitle_fonts.resolve(
        expand=expander(tmp_path),
        settings={
            "sub-fonts-dir": str(explicit),
            "embeddedfonts": False,
            "sub-font-provider": "auto",
        },
        video=None,
        cache_dir=tmp_path / "cache",
    )

    assert resolved.setup.fonts_dir == str(explicit)


def test_the_lookup_defaults_are_only_taken_when_the_files_exist(tmp_path: Path) -> None:
    (tmp_path / "subfont.ttf").write_bytes(b"font")

    resolved = subtitle_fonts.resolve(
        expand=expander(tmp_path),
        settings={"sub-font": "Symbola", "sub-font-provider": "fontconfig"},
        video=None,
        cache_dir=tmp_path / "cache",
    )

    assert resolved.setup.default_font == str(tmp_path / "subfont.ttf")
    assert resolved.setup.fontconfig_config is None
    assert resolved.setup.default_family == "Symbola"
    assert resolved.setup.font_provider == FontProvider.FONTCONFIG


def test_embeddedfonts_off_reaches_neither_in_container_source(tmp_path: Path) -> None:
    """One mpv option gates both: `sd_ass.c` passes it to `ass_set_extract_fonts` and guards
    `add_subtitle_fonts` with it, so an attachment and an in-file `[Fonts]` section stand or fall
    together."""
    resolved = subtitle_fonts.resolve(
        expand=expander(tmp_path),
        settings={"embeddedfonts": False, "sub-font-provider": "auto"},
        video=tmp_path / "episode.mkv",
        cache_dir=tmp_path / "cache",
    )

    assert resolved.setup.extract_fonts is False
    assert resolved.attachments == ()


def test_an_option_change_after_resolution_is_visible_in_the_snapshot(tmp_path: Path) -> None:
    settings = {"embeddedfonts": False, "sub-fonts-dir": "", "sub-font-provider": "auto"}
    resolved = subtitle_fonts.resolve(
        expand=expander(tmp_path), settings=settings, video=None, cache_dir=tmp_path / "cache"
    )

    assert resolved.options == subtitle_fonts.option_snapshot(settings)
    assert resolved.options != subtitle_fonts.option_snapshot({**settings, "embeddedfonts": True})


def test_an_unreadable_container_costs_the_faces_not_the_session(tmp_path: Path) -> None:
    resolved = subtitle_fonts.resolve(
        expand=expander(tmp_path),
        settings={"embeddedfonts": True, "sub-font-provider": "auto"},
        video=tmp_path / "missing.mkv",
        cache_dir=tmp_path / "cache",
    )

    assert resolved.attachments == ()
    assert resolved.setup.extract_fonts is True


@pytest.mark.integration
@pytest.mark.timeout(60)
def test_a_containers_font_attachment_is_dumped_and_cached(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("test requires ffmpeg and ffprobe")
    video = tmp_path / "episode.mkv"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            "-attach", str(REPO_FONT),
            "-metadata:s:t:0", "mimetype=application/x-truetype-font",
            "-metadata:s:t:0", f"filename={REPO_FONT.name}",
            "-c:v", "libx264", str(video),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    cache = tmp_path / "cache"

    dumped = subtitle_fonts.container_fonts(video, cache_dir=cache)
    # A second call must answer from the manifest; deleting the tool would break a re-dump.
    cached = subtitle_fonts.container_fonts(video, cache_dir=cache)

    assert dumped == cached
    assert [name for name, _data in dumped] == [REPO_FONT.name]
    assert dumped[0][1] == REPO_FONT.read_bytes()


@pytest.mark.integration
@pytest.mark.timeout(60)
def test_a_non_font_attachment_is_left_where_mpv_leaves_it(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("test requires ffmpeg and ffprobe")
    cover = tmp_path / "cover.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=8x8:d=1",
         "-frames:v", "1", str(cover)],
        check=True,
        capture_output=True,
    )  # fmt: skip
    video = tmp_path / "episode.mkv"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            "-attach", str(cover), "-metadata:s:t:0", "mimetype=image/png",
            "-c:v", "libx264", str(video),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip

    assert subtitle_fonts.container_fonts(video, cache_dir=tmp_path / "cache") == ()


def test_an_in_document_face_is_one_the_osd_library_can_never_load(tmp_path: Path) -> None:
    """mpv's OSD library is built from `osd_style` plus `mpv-osd-symbols` and has no attachment path
    at all. A family that came from the container or an in-file `[Fonts]` section therefore reaches
    the subtitle renderer and never the OSD one — which is what decides whether the overprint may
    draw that token through `osd-overlay` or has to leave it uncoloured."""
    resolved = subtitle_fonts.resolve(
        expand=expander(tmp_path),
        settings={"embeddedfonts": True, "sub-font-provider": "auto"},
        video=None,
        cache_dir=tmp_path / "cache",
    )

    assert resolved.osd_unreachable(frozenset({"signs"})) == frozenset({"signs"})


def test_a_document_face_is_reachable_again_once_mpv_stops_extracting_them(tmp_path: Path) -> None:
    """`--embeddedfonts=no` makes mpv ignore the `[Fonts]` section, so its subtitle renderer resolves
    that family the same way the OSD one does — from the system. Nothing to stand down for."""
    resolved = subtitle_fonts.resolve(
        expand=expander(tmp_path),
        settings={"embeddedfonts": False, "sub-font-provider": "auto"},
        video=None,
        cache_dir=tmp_path / "cache",
    )

    assert resolved.osd_unreachable(frozenset({"signs"})) == frozenset()


@pytest.mark.integration
@pytest.mark.timeout(60)
def test_an_attachments_own_family_name_is_what_the_overprint_stands_down_on(
    tmp_path: Path,
) -> None:
    """The whole point of naming families rather than counting sources: a release whose dialogue is
    a system font and whose signs are attachment-only must lose the colour on its signs only."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("test requires ffmpeg and ffprobe")
    video = tmp_path / "episode.mkv"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            "-attach", str(REPO_FONT),
            "-metadata:s:t:0", "mimetype=application/x-truetype-font",
            "-metadata:s:t:0", f"filename={REPO_FONT.name}",
            "-c:v", "libx264", str(video),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip

    resolved = subtitle_fonts.resolve(
        expand=expander(tmp_path),
        settings={"embeddedfonts": True, "sub-font-provider": "auto"},
        video=video,
        cache_dir=tmp_path / "cache",
    )

    assert "noto sans" in resolved.osd_unreachable()
    assert "helvetica" not in resolved.osd_unreachable()

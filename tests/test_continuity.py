from __future__ import annotations

import pytest

from saitenka.app import backlog
from saitenka.app.continuity import episode_identity, resolve_sibling


def _touch(directory, *names):
    for name in names:
        (directory / name).write_bytes(b"")


def test_episode_identity_agrees_with_backlog(tmp_path):
    path = "/anime/[Erai-raws] Nippon Sangoku - 10 [1080p].mkv"
    title, title_match, episode = episode_identity(path)

    assert (title, episode) == ("Nippon Sangoku", 10)
    # The match key must be exactly what backlog.media would store for the same file.
    store = backlog.BacklogStore(tmp_path / "backlog.sqlite")
    media = store.ensure_media(path)
    store.close()
    assert title_match == backlog.normalize_title(media.title)


@pytest.mark.parametrize(
    ("current", "sibling"),
    [
        ("Show S01E05.mkv", "Show S01E06.mkv"),
        ("Show 1x08.mkv", "Show 1x09.mkv"),
        ("[Grp] Show - 10 [1080p].mkv", "[Grp] Show - 11 [1080p].mkv"),
    ],
)
def test_resolve_next_across_filename_forms(tmp_path, current, sibling):
    _touch(tmp_path, current, sibling)

    assert resolve_sibling(tmp_path / current, 1) == tmp_path / sibling


def test_resolve_prev_episode(tmp_path):
    _touch(tmp_path, "Show 03.mkv", "Show 02.mkv")

    assert resolve_sibling(tmp_path / "Show 03.mkv", -1) == tmp_path / "Show 02.mkv"


def test_resolve_ignores_non_video_and_other_series(tmp_path):
    _touch(
        tmp_path,
        "Show 03.mkv",
        "Show 04.srt",  # right episode, wrong (non-video) suffix
        "Other 04.mkv",  # right episode, different title
    )

    assert resolve_sibling(tmp_path / "Show 03.mkv", 1) is None


def test_resolve_ambiguous_match_is_no_op(tmp_path):
    # Two files parse to the same (title_match, episode) — refuse rather than guess.
    _touch(tmp_path, "Show 03.mkv", "Show 04.mkv", "Show - 04 [repack].mkv")

    assert resolve_sibling(tmp_path / "Show 03.mkv", 1) is None


@pytest.mark.parametrize(
    ("current", "others", "delta"),
    [
        ("Show 03.mkv", (), 1),
        ("movie.mkv", ("another.mkv",), 1),
        ("Show 00.mkv", ("Show 01.mkv",), -1),
        ("Show 05.mkv", (), 0),
    ],
    ids=["missing", "unparseable", "out-of-range", "self"],
)
def test_resolve_invalid_target_is_no_op(tmp_path, current, others, delta):
    _touch(tmp_path, current, *others)

    assert resolve_sibling(tmp_path / current, delta) is None

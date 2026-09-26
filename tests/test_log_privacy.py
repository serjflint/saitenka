"""Which spellings of a registered media identity the file log scrubs, and which words it leaves."""

from __future__ import annotations

import pytest

from saitenka.app import log_privacy
from saitenka.app.jimaku import JimakuClient, JimakuError


@pytest.fixture(autouse=True)
def _empty_registry():
    log_privacy.reset()
    yield
    log_privacy.reset()


def test_an_oserror_quoting_a_registered_windows_path_loses_every_folder_name():
    path = "D:\\アニメ\\Zeta Show\\Zeta Show - 01.mkv"
    log_privacy.register_media(path)

    scrubbed = log_privacy.scrub(str(FileNotFoundError(2, "No such file or directory", path)))

    assert "アニメ" not in scrubbed
    assert "Zeta" not in scrubbed


def test_a_generic_stem_stays_readable_where_it_is_not_the_file():
    log_privacy.register_media("/subs/English.ass")

    assert log_privacy.scrub("subtitles announced: English (1/11)") == (
        "subtitles announced: English (1/11)"
    )
    assert "English.ass" not in log_privacy.scrub("sub index: 3 cues from English.ass")


def test_a_one_word_title_does_not_rewrite_the_same_word_elsewhere():
    log_privacy.register_media("/clips/video.mp4", title="video")

    line = "mpv sub-delay changed: video=+1.000s delay=+0.000s subtitle=+1.000s"

    assert log_privacy.scrub(line) == line


def test_a_jimaku_entry_name_is_scrubbed_from_its_no_files_error(monkeypatch):
    monkeypatch.setattr(
        JimakuClient, "search", lambda *_a: [{"id": 1, "name": "Sousou no Frieren"}]
    )
    monkeypatch.setattr(JimakuClient, "files", lambda *_a: [])

    with pytest.raises(JimakuError) as raised:
        JimakuClient("k" * 32).episode_files("Frieren", 7)

    assert "Sousou no Frieren" not in log_privacy.scrub(str(raised.value))


def test_a_japanese_title_is_scrubbed_where_japanese_text_surrounds_it():
    log_privacy.register_title("葬送のフリーレン")

    assert "フリーレン" not in log_privacy.scrub("字幕：葬送のフリーレン第1話")

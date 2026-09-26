"""Which spellings of a registered media identity the file log scrubs, and which words it leaves."""

from __future__ import annotations

import pytest

from saitenka.app import log_privacy


@pytest.fixture(autouse=True)
def _empty_registry(monkeypatch):
    monkeypatch.setattr(log_privacy, "_labels", {})
    monkeypatch.setattr(log_privacy, "_snapshot", None)


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

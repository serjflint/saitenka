from pathlib import Path

from overlay.app.subtitle_providers import fetch_first


def test_provider_chain_stops_at_first_success():
    calls: list[str] = []

    def miss():
        calls.append("jimaku")
        return None, "jimaku: miss"

    def hit():
        calls.append("tsukihime")
        return Path("episode.ja.ass"), "tsukihime: added"

    path, status = fetch_first((("jimaku", miss), ("tsukihime", hit)))

    assert calls == ["jimaku", "tsukihime"]
    assert path == Path("episode.ja.ass") and status == "tsukihime: added"


def test_provider_chain_does_not_call_later_provider_after_success():
    calls: list[str] = []

    def hit():
        calls.append("jimaku")
        return Path("episode.ja.srt"), "jimaku: added"

    def unexpected():
        calls.append("tsukihime")
        return None, "unexpected"

    path, _status = fetch_first((("jimaku", hit), ("tsukihime", unexpected)))

    assert path == Path("episode.ja.srt")
    assert calls == ["jimaku"]


def test_empty_provider_chain_performs_no_request():
    assert fetch_first(()) == (None, "no Japanese subtitle providers enabled")

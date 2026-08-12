"""TsukiHime matching, filtering, and bounded HTTP/XZ handling."""

from __future__ import annotations

import io
import lzma
import urllib.error

import pytest

from saitenka.app import tsukihime


class Response:
    def __init__(self, body: bytes, url: str):
        self._body = io.BytesIO(body)
        self._url = url

    def read(self, size=-1):
        return self._body.read(size)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[str] = []
        self.timeouts: list[float] = []

    def open(self, url, *, timeout):
        self.requests.append(url)
        self.timeouts.append(timeout)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _json(value) -> bytes:
    import json

    return json.dumps(value).encode()


def _search(*names: str, total: int | None = None):
    results = [{"id": index + 1, "name": name} for index, name in enumerate(names)]
    return {"total": len(results) if total is None else total, "results": results}


def _detail(*attachments):
    return {
        "files": [
            {
                "filename": "[Group] Show - 01.mkv",
                "attachments": list(attachments),
            }
        ]
    }


def _attachment(attachment_id=9, *, codec="ASS", lang="jpn", kind=1):
    return {
        "id": attachment_id,
        "type": kind,
        "info": {"codec": codec, "lang": lang},
    }


def test_unique_japanese_text_attachment_is_downloaded_and_decompressed(tmp_path):
    search_url = f"{tsukihime.API_BASE}/search/torrents"
    detail_url = f"{tsukihime.API_BASE}/torrents/1"
    download_url = "https://storage.tsukihime.org/attach/00000009/9.xz"
    opener = Opener(
        [
            Response(_json(_search("[Group] Show - 01.mkv")), search_url),
            Response(_json(_detail(_attachment())), detail_url),
            Response(lzma.compress(b"Japanese subtitle"), download_url),
        ]
    )

    path = tsukihime.TsukiHimeClient(opener=opener).fetch("Show", 1, tmp_path)

    assert path.read_bytes() == b"Japanese subtitle"
    assert path.name == "tsukihime-1-9.ass"
    assert opener.requests[0] == f"{search_url}?q=Show+1&limit=10"
    assert opener.requests[1:] == [detail_url, download_url]


@pytest.mark.parametrize(
    ("search", "message"),
    [
        (_search("[A] Show - 01.mkv", "[B] Show - 01.mkv"), "found 2"),
        (_search("[A] Other - 01.mkv"), "found 0"),
        (_search("[A] Show - 01.mkv", total=2), "truncated"),
    ],
)
def test_ambiguous_or_unproven_release_match_stops_before_detail(search, message, tmp_path):
    opener = Opener([Response(_json(search), f"{tsukihime.API_BASE}/search/torrents")])

    with pytest.raises(tsukihime.TsukiHimeError, match=message):
        tsukihime.TsukiHimeClient(opener=opener).fetch("Show", 1, tmp_path)

    assert len(opener.requests) == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "attachments",
    [
        [_attachment(codec="PGS"), _attachment(10, codec="VobSub")],
        [_attachment(lang="eng")],
        [_attachment(), _attachment(10, codec="SRT", lang="ja-JP")],
    ],
)
def test_non_unique_japanese_text_attachment_is_non_destructive(attachments, tmp_path):
    opener = Opener(
        [
            Response(_json(_search("Show - 01.mkv")), f"{tsukihime.API_BASE}/search/torrents"),
            Response(_json(_detail(*attachments)), f"{tsukihime.API_BASE}/torrents/1"),
        ]
    )

    with pytest.raises(tsukihime.TsukiHimeError, match="Japanese text attachments"):
        tsukihime.TsukiHimeClient(opener=opener).fetch("Show", 1, tmp_path)

    assert len(opener.requests) == 2
    assert not list(tmp_path.iterdir())


def test_episode_candidates_lists_every_release_without_the_uniqueness_guard():
    """The interactive picker's list: ambiguity that ``fetch`` rejects becomes a multi-row list here —
    a human disambiguates. One detail call per matching release."""
    search_url = f"{tsukihime.API_BASE}/search/torrents"
    opener = Opener(
        [
            Response(_json(_search("[A] Show - 01.mkv", "[B] Show - 01.mkv")), search_url),
            Response(_json(_detail(_attachment(9))), f"{tsukihime.API_BASE}/torrents/1"),
            Response(_json(_detail(_attachment(10))), f"{tsukihime.API_BASE}/torrents/2"),
        ]
    )

    pairs, truncated = tsukihime.TsukiHimeClient(opener=opener).episode_candidates("Show", 1)

    assert truncated is False
    assert [(release.name, attachment.id) for release, attachment in pairs] == [
        ("[A] Show - 01.mkv", 9),
        ("[B] Show - 01.mkv", 10),
    ]


def test_episode_candidates_flags_truncation_instead_of_raising():
    """Unlike ``fetch`` (which raises on a truncated search), the picker path returns ``truncated=True``
    so the caller can warn that fuzzy matching may have missed releases."""
    opener = Opener(
        [
            Response(
                _json(_search("[A] Show - 01.mkv", total=5)),
                f"{tsukihime.API_BASE}/search/torrents",
            ),
            Response(_json(_detail(_attachment(9))), f"{tsukihime.API_BASE}/torrents/1"),
        ]
    )

    pairs, truncated = tsukihime.TsukiHimeClient(opener=opener).episode_candidates("Show", 1)

    assert truncated is True
    assert len(pairs) == 1


def test_oversized_response_stops_before_json_decode(tmp_path):
    opener = Opener([Response(b"{}x", f"{tsukihime.API_BASE}/search/torrents")])
    client = tsukihime.TsukiHimeClient(opener=opener, max_response_bytes=2)

    with pytest.raises(tsukihime.TsukiHimeError, match="exceeds 2 bytes"):
        client.fetch("Show", 1, tmp_path)


def test_oversized_download_is_not_written(tmp_path):
    opener = Opener(
        [
            Response(_json(_search("Show - 01.mkv")), f"{tsukihime.API_BASE}/search/torrents"),
            Response(_json(_detail(_attachment())), f"{tsukihime.API_BASE}/torrents/1"),
            Response(b"abc", "https://storage.tsukihime.org/attach/00000009/9.xz"),
        ]
    )

    with pytest.raises(tsukihime.TsukiHimeError, match="exceeds 2 bytes"):
        tsukihime.TsukiHimeClient(opener=opener, max_download_bytes=2).fetch("Show", 1, tmp_path)

    assert not list(tmp_path.iterdir())


def test_redirected_download_to_untrusted_host_is_rejected(tmp_path):
    opener = Opener(
        [
            Response(_json(_search("Show - 01.mkv")), f"{tsukihime.API_BASE}/search/torrents"),
            Response(_json(_detail(_attachment())), f"{tsukihime.API_BASE}/torrents/1"),
            Response(b"subtitle", "https://example.com/stolen"),
        ]
    )

    with pytest.raises(tsukihime.TsukiHimeError, match="untrusted"):
        tsukihime.TsukiHimeClient(opener=opener).fetch("Show", 1, tmp_path)

    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("payload", [tsukihime.XZ_MAGIC + b"bad", lzma.compress(b"12345")])
def test_invalid_or_oversized_xz_is_non_destructive(payload, tmp_path):
    opener = Opener(
        [
            Response(_json(_search("Show - 01.mkv")), f"{tsukihime.API_BASE}/search/torrents"),
            Response(_json(_detail(_attachment())), f"{tsukihime.API_BASE}/torrents/1"),
            Response(payload, "https://storage.tsukihime.org/attach/00000009/9.xz"),
        ]
    )

    with pytest.raises(tsukihime.TsukiHimeError):
        tsukihime.TsukiHimeClient(opener=opener, max_subtitle_bytes=4).fetch("Show", 1, tmp_path)

    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("base", ["http://api.tsukihime.org/v1", "file:///tmp/api"])
def test_api_base_must_be_https(base):
    with pytest.raises(tsukihime.TsukiHimeError, match="untrusted"):
        tsukihime.TsukiHimeClient(api_base=base)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_timeout_must_be_finite_and_positive(timeout):
    with pytest.raises(tsukihime.TsukiHimeError, match="timeout"):
        tsukihime.TsukiHimeClient(timeout=timeout)


def test_network_errors_are_soft_provider_errors(tmp_path):
    opener = Opener([urllib.error.URLError("offline")])

    with pytest.raises(tsukihime.TsukiHimeError, match="offline"):
        tsukihime.TsukiHimeClient(opener=opener).fetch("Show", 1, tmp_path)


def test_timeout_and_result_cap_are_applied_to_requests(tmp_path):
    opener = Opener(
        [Response(_json(_search("Other - 01.mkv")), f"{tsukihime.API_BASE}/search/torrents")]
    )
    client = tsukihime.TsukiHimeClient(opener=opener, timeout=2.5, result_cap=3)

    with pytest.raises(tsukihime.TsukiHimeError, match="found 0"):
        client.fetch("Show", 1, tmp_path)

    assert opener.requests == [f"{tsukihime.API_BASE}/search/torrents?q=Show+1&limit=3"]
    assert opener.timeouts == [2.5]

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
    """Records the ``Request`` the client opens, not just its URL — the headers are part of the call
    the real opener makes, and a fake that only saw the URL could not observe a missing User-Agent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.timeouts: list[float] = []

    def open(self, request, *, timeout):
        self.requests.append(request.full_url)
        self.headers.append({k.lower(): v for k, v in request.header_items()})
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


def test_every_request_carries_a_saitenka_user_agent(tmp_path):
    """Cloudflare rule 1010 answers urllib's default ``Python-urllib/x.y`` UA with a 403, so an
    unbranded request fails against the live API on search, detail, and download alike."""
    opener = Opener(
        [
            Response(
                _json(_search("[Group] Show - 01.mkv")), f"{tsukihime.API_BASE}/search/torrents"
            ),
            Response(_json(_detail(_attachment())), f"{tsukihime.API_BASE}/torrents/1"),
            Response(lzma.compress(b"sub"), "https://storage.tsukihime.org/attach/00000009/9.xz"),
        ]
    )

    tsukihime.TsukiHimeClient(opener=opener).fetch("Show", 1, tmp_path)

    assert len(opener.headers) == 3
    for headers in opener.headers:
        assert headers["user-agent"] == tsukihime.USER_AGENT
        assert not headers["user-agent"].lower().startswith("python-urllib")


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
def test_non_unique_target_language_attachment_is_non_destructive(attachments, tmp_path):
    opener = Opener(
        [
            Response(_json(_search("Show - 01.mkv")), f"{tsukihime.API_BASE}/search/torrents"),
            Response(_json(_detail(*attachments)), f"{tsukihime.API_BASE}/torrents/1"),
        ]
    )

    with pytest.raises(tsukihime.TsukiHimeError, match="text attachments in jp"):
        tsukihime.TsukiHimeClient(opener=opener, language="jp").fetch("Show", 1, tmp_path)

    assert len(opener.requests) == 2
    assert not list(tmp_path.iterdir())


#: One release's attachment tags, in the spelling the live API returns. A census of 30 releases found
#: 30+ languages on a single title, French outnumbering Japanese — the shape `fetch` must survive.
_MULTILINGUAL = ("jpn", "en", "fr-FR", "zh-Hans", "es-419")


@pytest.mark.parametrize(
    ("language", "expected_id"),
    [("jp", 9), ("ja", 9), ("fr", 11), ("fr-FR", 11), ("zh", 12), ("es", 13)],
)
def test_fetch_picks_the_profile_language_out_of_a_multilingual_release(
    language, expected_id, tmp_path
):
    """A release carrying a dozen languages is not ambiguous — it holds exactly one per language. The
    filter must therefore run before the uniqueness guard, or every real release becomes an error."""
    attachments = [_attachment(9 + offset, lang=lang) for offset, lang in enumerate(_MULTILINGUAL)]
    opener = Opener(
        [
            Response(_json(_search("Show - 01.mkv")), f"{tsukihime.API_BASE}/search/torrents"),
            Response(_json(_detail(*attachments)), f"{tsukihime.API_BASE}/torrents/1"),
            Response(
                lzma.compress(b"chosen"),
                f"https://storage.tsukihime.org/attach/0000000{expected_id}/{expected_id}.xz",
            ),
        ]
    )

    path = tsukihime.TsukiHimeClient(opener=opener, language=language).fetch("Show", 1, tmp_path)

    assert path.name == f"tsukihime-1-{expected_id}.ass"


def test_fetch_will_not_silently_attach_an_untagged_attachment(tmp_path):
    """An untagged file is the one case a tag cannot settle. Auto-fetch is unattended, so it declines
    rather than guess; the picker still lists it for a human (see the boundary in ``list_candidates``)."""
    opener = Opener(
        [
            Response(_json(_search("Show - 01.mkv")), f"{tsukihime.API_BASE}/search/torrents"),
            Response(_json(_detail(_attachment(lang=None))), f"{tsukihime.API_BASE}/torrents/1"),
        ]
    )

    with pytest.raises(tsukihime.TsukiHimeError, match="0 text attachments in jp"):
        tsukihime.TsukiHimeClient(opener=opener, language="jp").fetch("Show", 1, tmp_path)

    assert not list(tmp_path.iterdir())


def test_attachments_retain_every_language_the_release_reports():
    """The picker's list is unfiltered — language is a tag on each row, not a gate here."""
    opener = Opener(
        [
            Response(_json(_search("Show - 01.mkv")), f"{tsukihime.API_BASE}/search/torrents"),
            Response(
                _json(_detail(*[_attachment(9 + i, lang=x) for i, x in enumerate(_MULTILINGUAL)])),
                f"{tsukihime.API_BASE}/torrents/1",
            ),
        ]
    )

    pairs, _truncated = tsukihime.TsukiHimeClient(opener=opener).episode_candidates("Show", 1)

    assert [attachment.language for _release, attachment in pairs] == list(_MULTILINGUAL)


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

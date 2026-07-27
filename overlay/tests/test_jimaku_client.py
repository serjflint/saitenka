"""JimakuClient HTTP layer: URL building, error/retry classification, and the fetch orchestration.

The key/keychain/cache surface is covered in test_jimaku_key.py; this file pins the network path —
`_get`'s transient-vs-client error split (the stamina retry contract), `_http_error_detail`'s JSON
unwrap, and `fetch`'s best-file scoring — all against a fake urlopen, no real network.
"""

from __future__ import annotations

import email.message
import io
import json
import urllib.error

import pytest
import stamina

from overlay.app import jimaku


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a) -> bool:
        return False


def _fake_urlopen(payload, seen: list | None = None):
    def _open(req, **_kwargs):
        if seen is not None:
            seen.append(req.full_url)
        return _FakeResp(json.dumps(payload).encode())

    return _open


def _raising_urlopen(exc: Exception):
    def _open(_req, **_kwargs):
        raise exc

    return _open


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://jimaku.cc/api/x", code, "Err", email.message.Message(), io.BytesIO(body)
    )


def _client() -> jimaku.JimakuClient:
    return jimaku.JimakuClient(api_key="testkey")


def _patch_urlopen(monkeypatch, fn) -> None:
    monkeypatch.setattr(jimaku.urllib.request, "urlopen", fn)


# --- _get: URL building + happy path -------------------------------------------------------------


def test_get_builds_url_and_drops_none_params(monkeypatch):
    seen: list[str] = []
    _patch_urlopen(monkeypatch, _fake_urlopen({"ok": 1}, seen))
    got = _client()._get("/entries/1/files", episode=None, page=2)
    assert got == {"ok": 1}
    assert seen == ["https://jimaku.cc/api/entries/1/files?page=2"]  # episode=None omitted


def test_get_sends_authorization_header(monkeypatch):
    captured: dict = {}

    def _open(req, **_kwargs):
        captured["auth"] = req.get_header("Authorization")
        return _FakeResp(b"{}")

    _patch_urlopen(monkeypatch, _open)
    _client()._get("/x")
    assert captured["auth"] == "testkey"


# --- _get: error classification (the stamina retry contract) -------------------------------------


@pytest.mark.parametrize("code", [400, 401, 404])
def test_get_client_error_raises_immediately_not_retried(monkeypatch, code):
    calls = {"n": 0}

    def _open(_req, **_kwargs):
        calls["n"] += 1
        raise _http_error(code, b'{"error":"nope"}')

    _patch_urlopen(monkeypatch, _open)
    with pytest.raises(jimaku.JimakuError) as ei:
        _client()._get("/x")
    assert not isinstance(ei.value, jimaku._JimakuRetryable)  # client errors are terminal
    assert calls["n"] == 1  # a single attempt — no backoff loop
    assert "nope" in str(ei.value)  # jimaku's own body is surfaced


def test_get_401_names_the_key_command(monkeypatch):
    _patch_urlopen(monkeypatch, _raising_urlopen(_http_error(401)))
    with pytest.raises(jimaku.JimakuError, match="set-jimaku-key"):
        _client()._get("/x")


@pytest.mark.parametrize("code", [429, 500, 503])
def test_get_transient_http_is_retried_then_raises_retryable(monkeypatch, code):
    calls = {"n": 0}

    def _open(_req, **_kwargs):
        calls["n"] += 1
        raise _http_error(code)

    _patch_urlopen(monkeypatch, _open)
    stamina.set_testing(True, attempts=3)
    try:
        with pytest.raises(jimaku._JimakuRetryable, match=str(code)):
            _client()._get("/x")
    finally:
        stamina.set_testing(False)
    assert calls["n"] == 3  # exhausted the (test-capped) retry budget


def test_get_network_error_is_retryable(monkeypatch):
    _patch_urlopen(monkeypatch, _raising_urlopen(urllib.error.URLError("dns dead")))
    stamina.set_testing(True, attempts=1)
    try:
        with pytest.raises(jimaku._JimakuRetryable, match="network error"):
            _client()._get("/x")
    finally:
        stamina.set_testing(False)


def test_get_bad_header_valueerror_is_terminal_and_hints_reset(monkeypatch):
    """A stray char in the key makes urllib reject the Authorization header — terminal, not retried."""
    _patch_urlopen(monkeypatch, _raising_urlopen(ValueError("Invalid header value")))
    with pytest.raises(jimaku.JimakuError, match="set-jimaku-key") as ei:
        _client()._get("/x")
    assert not isinstance(ei.value, jimaku._JimakuRetryable)


# --- _http_error_detail ---------------------------------------------------------------------------


def test_http_error_detail_unwraps_json_error_field():
    assert jimaku._http_error_detail(_http_error(400, b'{"error":"bad query"}')) == " — bad query"


def test_http_error_detail_passes_through_plain_body_and_truncates():
    long = b"x" * 500
    out = jimaku._http_error_detail(_http_error(400, long))
    assert out.startswith(" — ") and len(out) == len(" — ") + 300  # capped at 300 chars


def test_http_error_detail_empty_body_is_empty():
    assert jimaku._http_error_detail(_http_error(500, b"")) == ""


# --- search / files / download --------------------------------------------------------------------


def test_search_hits_the_search_endpoint(monkeypatch):
    seen: list[str] = []
    _patch_urlopen(monkeypatch, _fake_urlopen([{"id": 1}], seen))
    assert _client().search("Nichijou") == [{"id": 1}]
    assert "/entries/search?query=Nichijou&anime=true" in seen[0]


def test_files_maps_to_jimakufile(monkeypatch):
    payload = [
        {"name": "ep01.srt", "url": "https://j/1", "size": 42},
        {"name": "ep02.ass", "url": "https://j/2"},
    ]
    _patch_urlopen(monkeypatch, _fake_urlopen(payload))
    files = _client().files(7, episode=1)
    assert [(f.name, f.url, f.size, f.ext) for f in files] == [
        ("ep01.srt", "https://j/1", 42, ".srt"),
        ("ep02.ass", "https://j/2", 0, ".ass"),  # missing size defaults to 0
    ]


def test_download_writes_bytes_to_dest(monkeypatch, tmp_path):
    _patch_urlopen(monkeypatch, lambda _req, **_kwargs: _FakeResp(b"SRTDATA"))
    jf = jimaku.JimakuFile("show-01.srt", "https://j/file")
    dest = _client().download(jf, tmp_path)
    assert dest == tmp_path / "show-01.srt"
    assert dest.read_bytes() == b"SRTDATA"


# --- fetch orchestration + best-file scoring ------------------------------------------------------


def test_fetch_picks_episode_match_srt_over_ass(monkeypatch, tmp_path):
    """Best file = episode-in-name > .srt/.ass > .srt > largest. A huge off-episode file must lose to
    the small on-episode .srt."""
    files = [
        jimaku.JimakuFile("Show - 01.ass", "u-ass", 10),
        jimaku.JimakuFile("Show - 01.srt", "u-srt", 5),
        jimaku.JimakuFile("Show - 99.srt", "u-big", 9_999),
    ]
    picked: dict = {}
    monkeypatch.setattr(
        jimaku.JimakuClient, "search", lambda _self, title: [{"id": 3, "name": title}]
    )
    monkeypatch.setattr(jimaku.JimakuClient, "files", lambda _self, _eid, _ep: files)
    monkeypatch.setattr(
        jimaku.JimakuClient,
        "download",
        lambda _self, jf, _d: picked.setdefault("jf", jf) or tmp_path / jf.name,
    )
    _client().fetch("Show", 1, tmp_path)
    assert picked["jf"].url == "u-srt"


def test_fetch_no_entries_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(jimaku.JimakuClient, "search", lambda _self, _title: [])
    with pytest.raises(jimaku.JimakuError, match="no jimaku entry"):
        _client().fetch("Nope", 1, tmp_path)


def test_fetch_no_files_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(
        jimaku.JimakuClient, "search", lambda _self, _title: [{"id": 1, "name": "X"}]
    )
    monkeypatch.setattr(jimaku.JimakuClient, "files", lambda _self, _eid, _ep: [])
    with pytest.raises(jimaku.JimakuError, match="no files"):
        _client().fetch("X", 5, tmp_path)


# --- small helpers --------------------------------------------------------------------------------


def test_subs_cache_key_survives_unstattable_video(tmp_path):
    """A video whose size can't be read (gone/permission) falls back to size 0, still a stable key."""
    key = jimaku.subs_cache_key(tmp_path / "missing.mkv", "Show", 1)
    assert key.endswith("-0.srt")


def test_ssl_context_is_usable():
    import ssl

    assert isinstance(jimaku._ssl_context(), ssl.SSLContext)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # SxxExx / NxNN take precedence and yield the episode (E) part; the title is what precedes it.
        ("Show S01E01.mkv", ("Show", 1)),
        ("Show.S01E05.1080p.mkv", ("Show", 5)),  # dot-delimited, trailing tags dropped
        ("[Grp] Show - S2E03.mkv", ("Show", 3)),
        ("Show 1x08.mkv", ("Show", 8)),
        # real-world: SxxExx surrounded by dots, a repeated episode-subtitle after it, JP title.
        (
            "片田舎のおっさん、剣聖になる.S02E01.片田舎のおっさん、新たな職場に行く.WEBRip.Amazon.ja-jp[sdh].srt",
            ("片田舎のおっさん、剣聖になる", 1),
        ),
        # bare number, incl. an underscore-delimited 'ep05' (the (?!\d) boundary, not \b, catches it).
        ("[Erai-raws] Nippon Sangoku - 10 [1080p].mkv", ("Nippon Sangoku", 10)),
        ("Show - 12.mkv", ("Show", 12)),
        ("Show_ep05_1080p.mkv", ("Show", 5)),
        # a resolution must not be mistaken for a season×episode, and no number → None.
        ("Movie.1920x1080.mkv", ("Movie 1920x1080", None)),
        ("MovieWithNoNumber.mkv", ("MovieWithNoNumber", None)),
    ],
)
def test_parse_filename(filename, expected):
    """(title, episode) from a release-named file: strip [group]/(tag), prefer a SxxExx/NxNN season+
    episode over a bare trailing number, tidy the title. No number → episode None."""
    assert jimaku.parse_filename(filename) == expected

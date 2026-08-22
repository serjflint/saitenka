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
from hypothesis import given, settings
from hypothesis import strategies as st

from saitenka.app import jimaku


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


def test_fetch_picks_the_episode_match_and_prefers_ass(monkeypatch, tmp_path):
    """Best file = episode-in-name > .srt/.ass > .ass > largest. A huge off-episode file must lose to
    the small on-episode one, and the format tiebreak goes to ASS: native-visible geometry accepts
    nothing else, so auto-picking the .srt left the whole episode unscannable."""
    files = [
        jimaku.JimakuFile("Show - 01.ass", "u-ass", 5),
        jimaku.JimakuFile("Show - 01.srt", "u-srt", 10),
        jimaku.JimakuFile("Show - 99.ass", "u-big", 9_999),
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
    assert picked["jf"].url == "u-ass"  # smaller than the .srt, and off-episode size cannot win


def test_fetch_prefers_the_release_matching_the_video_resolution(monkeypatch, tmp_path):
    """A jimaku entry carries several sources whose cue timing differs by tens of seconds; the sub whose
    resolution matches THIS encode wins over a bigger off-release one (live: an AT-X 1440x1080 rip put a
    1080p WebRip episode 30s out of sync purely because its .srt was larger)."""
    files = [
        jimaku.JimakuFile(
            "[GroupB] Show - 03 (Broadcast 1440x1080 MPEG2 AAC).srt", "u-broadcast", 21_874
        ),
        jimaku.JimakuFile("[GroupA] Show - 03 (WebRip 1920x1080 x265 AAC).srt", "u-webrip", 20_689),
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
    _client().fetch("Show", 3, tmp_path, video="[Grp] Show - 03 [1080p CR WEBRip HEVC].mkv")
    assert (
        picked["jf"].url == "u-webrip"
    )  # 1920x1080 match beats the larger 1440x1080 broadcast file


def test_fetch_span_records_the_picked_release_and_resolution_match(monkeypatch, tmp_path):
    """The report signal for the "wrong release picked" class: one subtitle.fetch span per fetch names
    the winner and whether its resolution matched the video, so a bad pick is visible without the subs."""
    from contextlib import contextmanager

    from saitenka import otel_metrics

    files = [
        jimaku.JimakuFile("[GroupB] Show - 03 (Broadcast 1440x1080).srt", "u-broadcast", 21_874),
        jimaku.JimakuFile("[GroupA] Show - 03 (WebRip 1920x1080).srt", "u-webrip", 20_689),
    ]
    monkeypatch.setattr(jimaku.JimakuClient, "search", lambda _s, title: [{"id": 3, "name": title}])
    monkeypatch.setattr(jimaku.JimakuClient, "files", lambda _s, _e, _ep: files)
    monkeypatch.setattr(jimaku.JimakuClient, "download", lambda _s, jf, _d: tmp_path / jf.name)
    attrs: dict = {}

    @contextmanager
    def _traced(_name, **_kw):
        class _Span:
            def set(self, k, v):
                attrs[k] = v

        yield _Span()

    monkeypatch.setattr(otel_metrics, "traced", _traced)
    _client().fetch("Show", 3, tmp_path, video="[Grp] Show - 03 [1080p WEBRip].mkv")

    assert attrs["picked"] == "[GroupA] Show - 03 (WebRip 1920x1080).srt"
    assert attrs["resolution_match"] is True  # the 1920x1080 WebRip release matched the 1080p video
    assert attrs["episode"] == 3
    assert attrs["candidates"] == 2


def test_episode_files_lists_candidates_best_match_first(monkeypatch):
    """Window 1's source list: same ranking as fetch's auto-pick, best-first — so row 0 is exactly
    what fetch would have grabbed, and the user overrides a mistimed pick by choosing a lower row."""
    files = [
        jimaku.JimakuFile("[GroupB] Show - 03 (Broadcast 1440x1080).srt", "u-broadcast", 21_874),
        jimaku.JimakuFile("[GroupA] Show - 03 (WebRip 1920x1080).srt", "u-webrip", 20_689),
        jimaku.JimakuFile("Show - 99.srt", "u-off", 9_999_999),
    ]
    monkeypatch.setattr(jimaku.JimakuClient, "search", lambda _s, title: [{"id": 3, "name": title}])
    monkeypatch.setattr(jimaku.JimakuClient, "files", lambda _s, _e, _ep: files)
    ordered = _client().episode_files("Show", 3, video="[Grp] Show - 03 [1080p WEBRip].mkv")
    # 03-in-name beats the huge off-episode file; among the two ep-03 rips the 1920x1080 match wins.
    assert [f.url for f in ordered] == ["u-webrip", "u-broadcast", "u-off"]


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


# --- verify_key: the post-save / doctor liveness probe classification ----------------------------


def test_verify_key_ok_on_search_hit(monkeypatch):
    """A successful search → ("ok", "<n> entrie(s) …") so set-jimaku-key can confirm the key works."""
    _patch_urlopen(monkeypatch, _fake_urlopen([{"name": "Spy x Family"}]))
    status, msg = jimaku.verify_key("goodkey", "Spy x Family")
    assert status == "ok"
    assert "1 entrie(s)" in msg and "Spy x Family" in msg


def test_verify_key_bad_on_401(monkeypatch):
    """A full-length but WRONG key (401) → ("bad", …) — the class the length guard can't catch."""
    _patch_urlopen(monkeypatch, _raising_urlopen(_http_error(401)))
    status, msg = jimaku.verify_key("wrongkey")
    assert status == "bad"
    assert "set-jimaku-key" in msg  # the client's 401 hint is surfaced


def test_verify_key_unknown_on_network_error(monkeypatch):
    """A network/transient failure → ("unknown", …): can't tell, so a correct save is NOT failed."""
    _patch_urlopen(monkeypatch, _raising_urlopen(urllib.error.URLError("dns dead")))
    stamina.set_testing(True, attempts=1)
    try:
        status, msg = jimaku.verify_key("somekey")
    finally:
        stamina.set_testing(False)
    assert status == "unknown"
    assert "network" in msg.lower()


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


# --- fuzz: the release-name parsers must never raise on adversarial input -------------------------
# These consume unfiltered real-world filenames (jimaku file names + the played media name). A crash
# here aborts the whole subtitle fetch, so the contract is total: any str → a value, never an
# exception. (Complements sub_index.parse_srt's existing `poe fuzz` never-raise contract.)


@given(name=st.text())
@settings(max_examples=300, deadline=None)
def test_resolutions_never_raises(name):
    assert isinstance(jimaku._resolutions(name), set)


@given(video=st.text(), sub=st.text())
@settings(max_examples=300, deadline=None)
def test_resolution_match_never_raises(video, sub):
    assert isinstance(jimaku._resolution_match(video or None, sub), bool)


@given(name=st.text())
@settings(max_examples=300, deadline=None)
def test_parse_filename_never_raises(name):
    title, episode = jimaku.parse_filename(name)
    assert isinstance(title, str)
    assert episode is None or isinstance(episode, int)


# --- invariant: resolution-match dominates size in the picker (not just the one live example) ------

_STD_RES = [(1920, 1080), (1280, 720), (3840, 2160)]


@given(
    match_size=st.integers(0, 5_000),  # the matching release is deliberately the SMALLER file…
    other_size=st.integers(6_000, 999_999),  # …and the non-matching release the larger one
    res_idx=st.integers(0, len(_STD_RES) - 1),
)
@settings(max_examples=100, deadline=None)
def test_picker_resolution_match_dominates_size(match_size, other_size, res_idx):
    """The score tuple ranks `_resolution_match` ABOVE `size`, so the release whose resolution matches
    the video always wins over a bigger off-release file — the property behind the live AT-X-vs-EX bug,
    generalised past the single hand-picked pair."""
    w, h = _STD_RES[res_idx]
    other_w, other_h = _STD_RES[(res_idx + 1) % len(_STD_RES)]  # a different standard raster
    video = f"[Grp] Show - 03 [{h}p WEBRip HEVC].mkv"
    match = jimaku.JimakuFile(f"[A] Show - 03 ({w}x{h}).srt", "u-match", match_size)
    other = jimaku.JimakuFile(f"[B] Show - 03 ({other_w}x{other_h}).srt", "u-other", other_size)
    picked: dict = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(jimaku.JimakuClient, "search", lambda _s, title: [{"id": 1, "name": title}])
        mp.setattr(jimaku.JimakuClient, "files", lambda _s, _e, _ep: [other, match])
        mp.setattr(
            jimaku.JimakuClient,
            "download",
            lambda _s, jf, _d: picked.setdefault("jf", jf) or jimaku.Path(jf.name),
        )
        jimaku.JimakuClient(api_key="testkey").fetch("Show", 3, "/tmp", video=video)
    assert picked["jf"].url == "u-match"  # resolution match beats the larger non-matching file

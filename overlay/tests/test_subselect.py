"""attach/plugin-mode subtitle selection: pick the JP track over the user's English-first mpv, or
fetch jimaku when the file has no JP subs. A FakeIPC records commands and serves track-list/path."""

from __future__ import annotations

from overlay.app import subselect


class FakeIPC:
    def __init__(self, tracks=None, path=None):
        self._tracks = tracks or []
        self._path = path
        self.calls: list[tuple] = []

    def command(self, *args):
        self.calls.append(args)
        if args[:2] == ("get_property", "track-list"):
            return {"data": self._tracks}
        if args[:2] == ("get_property", "path"):
            return {"data": self._path}
        return {"data": None}

    def sets(self, prop):
        return [a[2] for a in self.calls if a[:2] == ("set_property", prop)]


JP = {"id": 2, "type": "sub", "lang": "jpn"}
EN = {"id": 1, "type": "sub", "lang": "eng"}


def test_select_prefers_japanese_over_english():
    ipc = FakeIPC(tracks=[EN, JP])
    sid = subselect.select_sub_track(ipc, "ja,jpn,jp")
    assert sid == 2
    assert ipc.sets("sid") == [2]


def test_select_returns_none_when_no_sub_tracks():
    ipc = FakeIPC(tracks=[{"id": 1, "type": "audio", "lang": "jpn"}])
    assert subselect.select_sub_track(ipc, "ja,jpn") is None
    assert ipc.sets("sid") == []


def test_lang_matches_two_and_three_letter_and_name():
    assert subselect._lang_matches("jpn", ["jpn"])
    assert subselect._lang_matches("ja", ["ja"])
    assert subselect._lang_matches("Japanese", ["ja"])
    assert not subselect._lang_matches("eng", ["ja", "jpn", "jp"])


def test_ensure_selects_jp_and_hides_native_subs():
    ipc = FakeIPC(tracks=[EN, JP])
    msg = subselect.ensure_jp_subs(ipc, slang="ja,jpn,jp")
    assert "sid=2" in msg
    assert ipc.sets("sub-visibility") == [False]  # overlay draws its own


def test_ensure_no_jp_without_jimaku_reports_gap():
    ipc = FakeIPC(tracks=[EN])
    msg = subselect.ensure_jp_subs(ipc, slang="ja,jpn,jp")
    assert "no Japanese subtitle track" in msg
    assert ipc.sets("sub-visibility") == []  # left mpv alone


def test_ensure_sub_file_is_added_and_selected(tmp_path):
    sub = tmp_path / "ep.ja.srt"
    sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n")
    ipc = FakeIPC(tracks=[EN])
    msg = subselect.ensure_jp_subs(ipc, sub_file=str(sub))
    assert "ep.ja.srt" in msg
    assert ("sub-add", str(sub), "select") in ipc.calls


def test_ensure_jimaku_fetches_when_no_jp_track(tmp_path, monkeypatch):
    fetched = tmp_path / "fetched.ja.srt"
    fetched.write_text("x")
    ipc = FakeIPC(tracks=[EN], path="/v/Nippon Sangoku - 09.mkv")

    monkeypatch.setattr(subselect, "_add_and_select", lambda ipc, p: ipc.command("sub-add", str(p)))

    class FakeClient:
        def __init__(self, key=None):
            pass

        def fetch(self, title, ep, dest):
            return fetched

    import overlay.app.jimaku as jm

    monkeypatch.setattr(jm, "JimakuClient", FakeClient)
    monkeypatch.setattr(jm, "parse_filename", lambda p: ("Nippon Sangoku", 9))
    # resync off so we don't shell out
    msg = subselect.ensure_jp_subs(ipc, jimaku=True, resync=False)
    assert "jimaku: added fetched.ja.srt" in msg and "ep 9" in msg
    assert ("sub-add", str(fetched)) in ipc.calls


def _stub_jimaku(monkeypatch, tmp_path, *, ok=True):
    fetched = tmp_path / "fetched.ja.srt"
    fetched.write_text("x")
    monkeypatch.setattr(subselect, "_add_and_select", lambda ipc, p: ipc.command("sub-add", str(p)))
    import overlay.app.jimaku as jm

    class FakeClient:
        def __init__(self, key=None):
            pass

        def fetch(self, title, ep, dest):
            if not ok:
                raise jm.JimakuError("not found")
            return fetched

    monkeypatch.setattr(jm, "JimakuClient", FakeClient)
    monkeypatch.setattr(jm, "parse_filename", lambda p: ("Nippon Sangoku", 9))
    return fetched


def test_jimaku_force_prefers_jimaku_over_embedded_jp_track(tmp_path, monkeypatch):
    fetched = _stub_jimaku(monkeypatch, tmp_path)
    ipc = FakeIPC(tracks=[EN, JP], path="/v/Nippon Sangoku - 09.mkv")
    msg = subselect.ensure_jp_subs(ipc, jimaku=True, jimaku_force=True, resync=False)
    assert "jimaku: added fetched.ja.srt" in msg
    assert ("sub-add", str(fetched)) in ipc.calls
    assert ipc.sets("sid") == []  # embedded JP track was NOT selected — jimaku won


def test_jimaku_force_falls_back_to_embedded_on_fetch_failure(tmp_path, monkeypatch):
    _stub_jimaku(monkeypatch, tmp_path, ok=False)
    ipc = FakeIPC(tracks=[EN, JP], path="/v/Nippon Sangoku - 09.mkv")
    msg = subselect.ensure_jp_subs(ipc, jimaku=True, jimaku_force=True, resync=False)
    assert "sid=2" in msg  # jimaku failed → embedded JP track selected as fallback
    assert ipc.sets("sid") == [2]

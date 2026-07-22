"""Post-mine card preview: baked-layout render, HTML/media parse, audio-play command."""

from PIL import Image

from overlay.app.card_preview import PreviewData, render_card_preview
from overlay.app.controller import _html_items, _html_lines, _media_name


def test_preview_renders_all_sections():
    frame = Image.new("RGBA", (320, 180), (40, 70, 90, 255))
    pv = PreviewData(
        "mined",
        "奉書",
        "ほうしょ",
        ["それに奉書の管轄は", "司宮府であり―"],
        "奉書",
        ["high-quality Japanese paper"],
        frame,
        2.4,
        "Saitenka::Mining · Lapis · ep10 · 10:16",
    )
    pr = render_card_preview(pv, width=470)
    assert pr.image.width == 470
    assert pr.image.height > 300
    assert pr.image.getextrema()[3][1] > 0  # not blank
    assert pr.close_rect and pr.audio_rect and pr.image_rect  # clickable regions exposed


def test_preview_without_media():
    pv = PreviewData("exists", "本", "ほん", ["本を読む"], "本", ["book"], None, None, "")
    pr = render_card_preview(pv, width=440)
    assert pr.image.width == 440
    assert pr.image_rect is None  # no screenshot → nothing to enlarge


def test_preview_zoom_enlarges_the_screenshot():
    frame = Image.new("RGBA", (320, 180), (40, 70, 90, 255))
    pv = PreviewData("mined", "本", "ほん", ["本を読む"], "本", ["book"], frame, 2.0, "")
    small = render_card_preview(pv, width=470)
    big = render_card_preview(pv, width=470, zoom=True)
    assert big.image_rect[2] > small.image_rect[2]  # the screenshot is wider when zoomed
    assert big.image.height > small.image.height


def test_html_lines_splits_br_strips_tags():
    assert _html_lines("それに<b>奉書</b>の管轄は<br>司宮府であり―") == [
        "それに奉書の管轄は",
        "司宮府であり―",
    ]


def test_html_items_parses_ol():
    assert _html_items("<ol><li>to read</li><li>to count</li></ol>") == ["to read", "to count"]


def test_media_name_extracts_filenames():
    assert _media_name('<img src="pic_123.jpg">', r'src="([^"]+)"') == "pic_123.jpg"
    assert _media_name("[sound:au_123.mp3]", r"\[sound:([^\]]+)\]") == "au_123.mp3"


def test_play_audio_builds_command(monkeypatch):
    import overlay.app.media as media

    calls = {}
    monkeypatch.setattr(media.subprocess, "Popen", lambda cmd, **kw: calls.__setitem__("cmd", cmd))
    monkeypatch.setattr(media.sys, "platform", "darwin")
    media.play_audio("/x.mp3")
    assert calls["cmd"] == ["afplay", "/x.mp3"]
    # non-mac prefers mpv (a guaranteed dep); ffplay is only the fallback when mpv isn't found
    monkeypatch.setattr(media.sys, "platform", "linux")
    monkeypatch.setattr("overlay.mpvio.discover.find_mpv", lambda _c: "/usr/bin/mpv")
    media.play_audio("/x.mp3")
    assert calls["cmd"][0] == "/usr/bin/mpv" and "/x.mp3" in calls["cmd"]
    monkeypatch.setattr("overlay.mpvio.discover.find_mpv", lambda _c: None)
    media.play_audio("/x.mp3")
    assert calls["cmd"][0] == "ffplay" and "/x.mp3" in calls["cmd"]

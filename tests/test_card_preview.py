"""Post-mine card preview: baked-layout render, HTML/media parse, audio-play command."""

from PIL import Image

from saitenka.app.card_preview import PreviewData, PreviewState, render_card_preview
from saitenka.app.miner_ui import _html_items, _html_lines, _media_name


def test_preview_state_clear_resets_panel_and_all_rects():
    """The dismiss path resets the shown preview + every clickable region in one move, so no stale
    rect can outlive the panel (the invariant hide_preview relies on)."""
    st = PreviewState()
    st.last_preview = PreviewData("mined", "本", "ほん", ["本"], "本", ["book"], None, None, "")
    st.rect = st.close_rect = st.audio_rect = st.image_rect = st.dup_rect = (1, 2, 3, 4)
    st.last_audio = "/tmp/a.mp3"  # media survives clear() — only the shown panel is dismissed
    st.clear()
    assert st.last_preview is None
    assert st.rect is st.close_rect is st.audio_rect is st.image_rect is st.dup_rect is None
    assert st.last_audio == "/tmp/a.mp3"  # last-mined media untouched (replay after dismiss)


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


def test_exists_preview_offers_add_duplicate_button():
    """A card already in the deck shows the ＋ "add anyway" affordance; a freshly mined card does not."""
    exists = PreviewData("exists", "本", "ほん", ["本を読む"], "本", ["book"], None, None, "")
    mined = PreviewData("mined", "本", "ほん", ["本を読む"], "本", ["book"], None, None, "")
    assert render_card_preview(exists, width=440).dup_rect is not None
    assert render_card_preview(mined, width=440).dup_rect is None


def test_preview_scales_with_window():
    # Matches the tooltip: the card preview's contents scale with the window (mpv model), so its
    # height and its clickable ✕ button scale together — same layout, just smaller on a small video.
    from saitenka.panel import Theme

    frame = Image.new("RGBA", (320, 180), (40, 70, 90, 255))
    pv = PreviewData(
        "mined", "門前", "もんぜん", ["門前の小僧"], "門前", ["temple gate"], frame, 1.2, "Deck"
    )
    full = render_card_preview(pv, width=640, theme=Theme(scale=1.0))
    half = render_card_preview(pv, width=320, theme=Theme(scale=0.5))
    assert (
        abs(full.image.height - 2 * half.image.height) <= 8
    )  # height scales ~linearly (px rounding)
    assert full.close_rect and half.close_rect
    assert abs(full.close_rect[3] - 2 * half.close_rect[3]) <= 1  # ✕ button size scales too


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
    from saitenka.app import media

    calls = {}
    monkeypatch.setattr(media.subprocess, "Popen", lambda cmd, **_kw: calls.__setitem__("cmd", cmd))
    monkeypatch.setattr(media.sys, "platform", "darwin")
    media.play_audio("/x.mp3")
    assert calls["cmd"] == ["afplay", "/x.mp3"]
    # non-mac prefers mpv (a guaranteed dep); ffplay is only the fallback when mpv isn't found
    monkeypatch.setattr(media.sys, "platform", "linux")
    monkeypatch.setattr("saitenka.mpvio.discover.find_mpv", lambda _c: "/usr/bin/mpv")
    media.play_audio("/x.mp3")
    assert calls["cmd"][0] == "/usr/bin/mpv" and "/x.mp3" in calls["cmd"]
    monkeypatch.setattr("saitenka.mpvio.discover.find_mpv", lambda _c: None)
    media.play_audio("/x.mp3")
    assert calls["cmd"][0] == "ffplay" and "/x.mp3" in calls["cmd"]

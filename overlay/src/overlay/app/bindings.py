"""Canonical runtime binding catalog shared by registration and session help."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from overlay.app.controller import Reader

MINE_MSG = "saitenka-mine"
MINE_VIDEO_MSG = "saitenka-mine-video"
MINE_ALL_MSG = "saitenka-mine-all"
TRANS_MSG = "saitenka-translate"
OVERLAY_TOGGLE_MSG = "saitenka-toggle-overlay"
SUBTITLE_LANGUAGE_MSG = "saitenka-toggle-subtitle-language"
SUBTITLE_RETRY_MSG = "saitenka-retry-subtitle-providers"
HOVER_PAUSE_MSG = "saitenka-toggle-hover-pause"
BOOKMARK_MSG = "saitenka-toggle-bookmark"
SIDEBAR_MSG = "saitenka-toggle-sidebar"
ANALYSIS_MSG = "saitenka-toggle-analysis"
ANNOTATION_MSG = "saitenka-toggle-annotations"
HELP_TOGGLE_MSG = "saitenka-toggle-help"
HELP_PREV_MSG = "saitenka-help-prev"
HELP_NEXT_MSG = "saitenka-help-next"
HELP_CLOSE_MSG = "saitenka-help-close"
PREVIEW_MSG = "saitenka-preview"
PREVIEW_CLOSE_MSG = "saitenka-preview-close"
SCROLL_UP_MSG = "saitenka-scroll-up"
SCROLL_DOWN_MSG = "saitenka-scroll-down"
SPEAK_MSG = "saitenka-speak"
COPY_MSG = "saitenka-copy"
COPY_LINE_MSG = "saitenka-copy-line"
COPY_CLICK_MSG = "saitenka-copy-click"
CLICK_MSG = "saitenka-click"
SUB_PREV_MSG = "saitenka-sub-prev"
SUB_NEXT_MSG = "saitenka-sub-next"
SUB_REPLAY_MSG = "saitenka-sub-replay"
SUB_DELAY_MINUS_MSG = "saitenka-sub-delay-minus"
SUB_DELAY_PLUS_MSG = "saitenka-sub-delay-plus"
SUB_DELAY_RESET_MSG = "saitenka-sub-delay-reset"
KANJI_MSG = "saitenka-kanji"
TIP_UP_MSG = "saitenka-tip-up"
TIP_DOWN_MSG = "saitenka-tip-down"
TIP_CLOSE_MSG = "saitenka-tip-close"

Scope = Literal["global", "tooltip", "help", "mpv", "preview", "mouse"]
Requirement = Literal["always", "anki", "tts"]

# "mouse"-scoped bindings live in this FORCED mpv input section, enabled only while a saitenka surface
# is up (controller._sync_mouse_capture) so clicks/wheel outrank other scripts' forced MBTN_LEFT.
MOUSE_SECTION = "saitenka-mouse"


@dataclass(frozen=True)
class BindingSpec:
    section: str
    label: str
    message: str | None
    scope: Scope = "global"
    key: str | None = None
    key_attr: str | None = None
    context: str | None = None
    source: Literal["saitenka", "mpv"] = "saitenka"
    requires: Requirement = "always"
    show_in_help: bool = True


@dataclass(frozen=True)
class ActiveBinding:
    key: str
    spec: BindingSpec


BINDINGS: tuple[BindingSpec, ...] = (
    BindingSpec(
        "Essentials & language", "Shortcut reference", HELP_TOGGLE_MSG, key_attr="help_key"
    ),
    BindingSpec(
        "Essentials & language", "Show English translation", TRANS_MSG, key_attr="translate_key"
    ),
    BindingSpec(
        "Essentials & language",
        "Hide / show Saitenka",
        OVERLAY_TOGGLE_MSG,
        key_attr="overlay_toggle_key",
    ),
    BindingSpec(
        "Essentials & language",
        "Switch Japanese / English",
        SUBTITLE_LANGUAGE_MSG,
        key_attr="subtitle_language_key",
    ),
    BindingSpec(
        "Essentials & language",
        "Retry Japanese subtitle providers",
        SUBTITLE_RETRY_MSG,
        key_attr="subtitle_retry_key",
    ),
    BindingSpec(
        "Essentials & language",
        "Toggle hover auto-pause",
        HOVER_PAUSE_MSG,
        key_attr="hover_pause_key",
    ),
    BindingSpec(
        "Essentials & language",
        "Toggle learning annotations",
        ANNOTATION_MSG,
        key_attr="annotation_key",
    ),
    BindingSpec(
        "Essentials & language", "Subtitle and backlog sidebar", SIDEBAR_MSG, key_attr="sidebar_key"
    ),
    BindingSpec("Essentials & language", "Episode analysis", ANALYSIS_MSG, key_attr="analysis_key"),
    BindingSpec("Subtitle navigation", "Previous subtitle", SUB_PREV_MSG, key_attr="sub_prev_key"),
    BindingSpec("Subtitle navigation", "Next subtitle", SUB_NEXT_MSG, key_attr="sub_next_key"),
    BindingSpec(
        "Subtitle navigation", "Replay current subtitle", SUB_REPLAY_MSG, key_attr="sub_replay_key"
    ),
    BindingSpec("Subtitle navigation", "Subtitle delay -0.1 s", SUB_DELAY_MINUS_MSG, key="z"),
    BindingSpec("Subtitle navigation", "Subtitle delay +0.1 s", SUB_DELAY_PLUS_MSG, key="Z"),
    BindingSpec("Subtitle navigation", "Reset subtitle delay", SUB_DELAY_RESET_MSG, key="x"),
    BindingSpec("Capture & mining", "Bookmark active cue", BOOKMARK_MSG, key_attr="bookmark_key"),
    BindingSpec(
        "Capture & mining",
        "Mine hovered word",
        MINE_MSG,
        key_attr="mine_key",
        context="tooltip",
        requires="anki",
    ),
    BindingSpec(
        "Capture & mining",
        "Mine hovered word with video clip",
        MINE_VIDEO_MSG,
        key_attr="mine_video_key",
        context="tooltip",
        requires="anki",
    ),
    BindingSpec(
        "Capture & mining",
        "Mine all words in cue",
        MINE_ALL_MSG,
        key_attr="mine_all_key",
        requires="anki",
    ),
    BindingSpec(
        "Capture & mining",
        "Replay card preview",
        PREVIEW_MSG,
        scope="tooltip",  # bound only while a tooltip is up, so global `p` keeps mpv's pause (Windows)
        key_attr="preview_key",
        context="card preview",
        requires="anki",
    ),
    BindingSpec(
        "Capture & mining",
        "Close card preview",
        PREVIEW_CLOSE_MSG,
        scope="preview",  # bound on show_preview, handed back on hide_preview
        key="ESC",
        context="card preview",
        requires="anki",
    ),
    BindingSpec(
        "Tooltip actions",
        "Scroll up",
        SCROLL_UP_MSG,
        scope="mouse",
        key="WHEEL_UP",
        context="tooltip / sidebar",
    ),
    BindingSpec(
        "Tooltip actions",
        "Scroll down",
        SCROLL_DOWN_MSG,
        scope="mouse",
        key="WHEEL_DOWN",
        context="tooltip / sidebar",
    ),
    BindingSpec(
        "Tooltip actions",
        "Speak hovered word",
        SPEAK_MSG,
        key="a",
        context="tooltip",
        requires="tts",
    ),
    BindingSpec("Tooltip actions", "Copy hovered word", COPY_MSG, key="c", context="tooltip"),
    BindingSpec("Tooltip actions", "Open or cycle kanji", KANJI_MSG, key="k", context="tooltip"),
    BindingSpec(
        "Tooltip actions", "Copy subtitle cue", COPY_LINE_MSG, key="Shift+c", context="subtitle"
    ),
    BindingSpec(
        "Tooltip actions",
        "Activate control",
        CLICK_MSG,
        scope="mouse",
        key="MBTN_LEFT",
        context="tooltip / sidebar",
    ),
    BindingSpec(
        "Tooltip actions",
        "Copy word under pointer",
        COPY_CLICK_MSG,
        scope="mouse",
        key="MBTN_RIGHT",
        context="tooltip",
    ),
    BindingSpec(
        "Tooltip actions",
        "Scroll tooltip up",
        TIP_UP_MSG,
        scope="tooltip",
        key="UP",
        context="tooltip only",
    ),
    BindingSpec(
        "Tooltip actions",
        "Scroll tooltip down",
        TIP_DOWN_MSG,
        scope="tooltip",
        key="DOWN",
        context="tooltip only",
    ),
    BindingSpec(
        "Tooltip actions",
        "Close tooltip",
        TIP_CLOSE_MSG,
        scope="tooltip",
        key="ESC",
        context="tooltip only",
    ),
    BindingSpec(
        "Useful mpv controls",
        "Pause / resume (SyncPlay)",
        None,
        scope="mpv",
        key="SPACE",
        source="mpv",
    ),
    BindingSpec(
        "Useful mpv controls", "Toggle fullscreen", None, scope="mpv", key="f", source="mpv"
    ),
    BindingSpec(
        "Useful mpv controls",
        "Seek backward / forward",
        None,
        scope="mpv",
        key="LEFT / RIGHT",
        source="mpv",
    ),
    BindingSpec(
        "Useful mpv controls",
        "Cycle primary subtitles",
        None,
        scope="mpv",
        key="j / Shift+J",
        source="mpv",
    ),
    BindingSpec(
        "Useful mpv controls",
        "Toggle native primary subtitles",
        None,
        scope="mpv",
        key="v",
        source="mpv",
    ),
    BindingSpec(
        "Useful mpv controls",
        "Toggle native secondary subtitles",
        None,
        scope="mpv",
        key="Alt+v",
        source="mpv",
    ),
    BindingSpec("Useful mpv controls", "Quit mpv", None, scope="mpv", key="q", source="mpv"),
    BindingSpec(
        "Help navigation",
        "Previous help page",
        HELP_PREV_MSG,
        scope="help",
        key="PGUP",
        show_in_help=False,
    ),
    BindingSpec(
        "Help navigation",
        "Next help page",
        HELP_NEXT_MSG,
        scope="help",
        key="PGDWN",
        show_in_help=False,
    ),
    BindingSpec(
        "Help navigation",
        "Close shortcut reference",
        HELP_CLOSE_MSG,
        scope="help",
        key="ESC",
        show_in_help=False,
    ),
)


def active_bindings(reader: Reader, *scopes: Scope) -> tuple[ActiveBinding, ...]:
    """Resolve configured keys and omit session actions whose dependency is unavailable."""
    wanted = frozenset(scopes)
    out: list[ActiveBinding] = []
    for spec in BINDINGS:
        if wanted and spec.scope not in wanted:
            continue
        if spec.requires == "anki" and reader.anki is None:
            continue
        if spec.requires == "tts" and not reader._tts_ok:
            continue
        key = getattr(reader, spec.key_attr) if spec.key_attr else spec.key
        if key:
            out.append(ActiveBinding(str(key), spec))
    return tuple(out)


HELP_MESSAGES = frozenset((HELP_TOGGLE_MSG, HELP_PREV_MSG, HELP_NEXT_MSG, HELP_CLOSE_MSG))

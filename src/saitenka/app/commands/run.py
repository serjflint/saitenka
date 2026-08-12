from __future__ import annotations

from typing import Annotated, Literal

import cyclopts

from saitenka.app.command_defaults import _mine_cfg
from saitenka.app.config import TooltipOptions

# _resolve_names/jimaku_should_fetch: re-exported — tests import them from here directly.
from saitenka.app.launch.run import _resolve_names as _resolve_names  # noqa: PLC0414  # re-export
from saitenka.app.launch.run import (
    jimaku_should_fetch as jimaku_should_fetch,  # noqa: PLC0414  # re-export
)
from saitenka.app.launch.run import run_impl


def run(  # noqa: PLR0913  # cyclopts CLI signature — flags are individual params for --help/parsing
    video: str | None = None,
    *,
    config: Annotated[
        str | None,
        cyclopts.Parameter(help="settings TOML (default: platform config dir, see `doctor`)"),
    ] = None,
    sub_file: str | None = None,
    slang: Annotated[
        str, cyclopts.Parameter(help="primary (JP) sub languages, priority order")
    ] = "ja,jpn,jp",
    dicts: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            name="--dict",
            negative=(),
            help="imported dictionary TITLE (repeatable; ordered — first = top of the tooltip)",
        ),
    ] = None,
    translate_key: Annotated[
        str, cyclopts.Parameter(help="mpv key to toggle the EN translation")
    ] = "t",
    start: Annotated[str, cyclopts.Parameter(help="mpv --start (seconds or hh:mm:ss)")] = "1",
    jimaku: Annotated[
        bool, cyclopts.Parameter(negative=(), help="fetch JP subs from jimaku.cc")
    ] = False,
    jimaku_key: Annotated[
        str | None, cyclopts.Parameter(help="jimaku.cc API key (else $JIMAKU_API_KEY)")
    ] = None,
    jimaku_title: Annotated[
        str | None, cyclopts.Parameter(help="override the title parsed from the filename")
    ] = None,
    resync: Annotated[
        bool,
        cyclopts.Parameter(
            negative="--no-resync",
            help="auto-resync jimaku-sourced subtitles via alass/ffsubsync (default: on)",
        ),
    ] = True,
    profile: Annotated[
        str | None,
        cyclopts.Parameter(help="active reading profile name ([profiles.<name>] in the config)"),
    ] = None,
    episode: Annotated[
        int | None, cyclopts.Parameter(help="override the episode parsed from the filename")
    ] = None,
    width: Annotated[int, cyclopts.Parameter(help="test-clip width (default 1080p)")] = 1920,
    height: int = 1080,
    fullscreen: Annotated[bool, cyclopts.Parameter(negative=())] = False,
    use_config: Annotated[bool, cyclopts.Parameter(negative=())] = False,
    demo_word: Annotated[
        str | None, cyclopts.Parameter(help="force-hover the first token containing this text")
    ] = None,
    demo_translate: Annotated[
        bool, cyclopts.Parameter(negative=(), help="reveal the EN translation (demo)")
    ] = False,
    demo_scroll: Annotated[int, cyclopts.Parameter(help="scroll the tooltip N steps (demo)")] = 0,
    bulk: Annotated[
        bool, cyclopts.Parameter(negative=(), help="in demo, bulk-mine the cue instead of one word")
    ] = False,
    screenshot: Annotated[
        str | None, cyclopts.Parameter(help="capture the composited window to this PNG, then quit")
    ] = None,
    seconds: float = 60.0,
    color: Annotated[
        bool, cyclopts.Parameter(negative=(), help="enable SubMiner-style word coloring")
    ] = False,
    known: Annotated[
        str, cyclopts.Parameter(help="comma-separated known words (lemmas/readings)")
    ] = "",
    anki_decks: Annotated[
        str | None,
        cyclopts.Parameter(help='JSON {"Deck": ["Field"]} to build known-set via AnkiConnect'),
    ] = None,
    freq: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            negative=(),
            help="imported frequency-dict TITLE (repeatable; green pills + coloring bands)",
        ),
    ] = None,
    pitch: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            negative=(), help="imported pitch-accent-dict TITLE (repeatable; purple pills)"
        ),
    ] = None,
    mine: Annotated[
        bool,
        cyclopts.Parameter(
            negative="--no-mine",
            help="one-key mining to Anki (default: on when [mine] is configured; --no-mine to disable)",
        ),
    ] = bool(_mine_cfg.get("enabled", bool(_mine_cfg))),
    # None = flag not passed → the active profile's (or runtime [mine]'s) deck/model is resolved at
    # runtime in run_impl (#254). This makes the default honor --config AND a profile, not the
    # import-time default-path config baked into a literal default. An explicit flag still wins.
    mine_deck: str | None = None,
    mine_model: str | None = None,
    mine_normalize_audio: Annotated[
        bool,
        cyclopts.Parameter(
            negative="--no-mine-normalize-audio",
            help="normalize mined clip loudness to −23 LUFS (EBU R128) so cards play at an even volume",
        ),
    ] = bool(_mine_cfg.get("normalize_audio", False)),
    mine_animated_screenshot: Annotated[
        bool,
        cyclopts.Parameter(
            negative="--no-mine-animated-screenshot",
            help="mine a short animated (motion) WebP clip of the scene instead of a still frame",
        ),
    ] = bool(_mine_cfg.get("animated_screenshot", False)),
    mine_key: Annotated[
        str, cyclopts.Parameter(help="mpv key that mines the hovered word")
    ] = _mine_cfg.get("key", "Ctrl+m"),
    mine_all_key: Annotated[
        str, cyclopts.Parameter(help="mpv key that bulk-mines the cue")
    ] = _mine_cfg.get("all_key", "Shift+m"),
    preview_key: Annotated[
        str, cyclopts.Parameter(help="mpv key to replay the last card preview + audio")
    ] = _mine_cfg.get("preview_key", "p"),
    no_audio_play: Annotated[
        bool, cyclopts.Parameter(negative=(), help="don't auto-play the mined clip")
    ] = False,
    mine_preview: Annotated[
        bool,
        cyclopts.Parameter(
            negative="--no-mine-preview",  # on by default → explicit off switch
            help="auto-pop the card-preview panel after a mine (--no-mine-preview mines silently "
            "with a toast instead)",
        ),
    ] = bool(_mine_cfg.get("preview", True)),
    tip_height: Annotated[
        float,
        cyclopts.Parameter(
            help=f"max BASE tooltip height as a fraction of the video height "
            f"(default {TooltipOptions().tip_max_frac})"
        ),
        # The default lives once, on TooltipOptions.tip_max_frac. cyclopts still layers
        # defaults < config < CLI (token-based), so sourcing the floor here changes nothing but DRY.
    ] = TooltipOptions().tip_max_frac,
    tip_scale: Annotated[
        float,
        cyclopts.Parameter(
            help="fixed tooltip crisp render scale (0 = auto from resolution; e.g. 1.5 renders "
            "crisp native glyph masks at 1.5× on any display — a cosmetic preference). Match "
            "`saitenka prewarm --scale` to preload native masks"
        ),
    ] = TooltipOptions().tip_scale,
    pause_on_tooltip: Annotated[
        bool,
        cyclopts.Parameter(
            negative="--no-pause-on-tooltip",  # on by default now → give an explicit off switch
            help="auto-pause playback while a tooltip is shown (resumes when it hides)",
        ),
    ] = True,
    prefetch: Annotated[
        bool,
        cyclopts.Parameter(
            name=(),  # only the negative form exists, exactly like the legacy --no-prefetch
            negative="--no-prefetch",
            help="disable background prefetch of the paused line's tooltips",
        ),
    ] = True,
    auto_translate: Annotated[
        bool,
        cyclopts.Parameter(
            negative=(),
            help="auto-reveal the EN translation while a tooltip is shown (else press the translate "
            "key). Anti-crutch: the EN only appears when you're looking a word up",
        ),
    ] = False,
    hover_switch_delay: Annotated[
        float,
        cyclopts.Parameter(
            help="seconds the cursor must rest on a NEW word before the tooltip switches to it "
            "(0 = instant)"
        ),
    ] = 0.15,
    layout_engine: Annotated[
        Literal["default", "taffy"],
        cyclopts.Parameter(
            help="tooltip block-geometry backend: 'default' (pure-Python) or 'taffy' (the optional "
            "taffylite Rust flexbox engine — needs `pip install 'saitenka[layout-engine]'`; "
            "byte-identical output, falls back to default if the wheel is absent)"
        ),
    ] = TooltipOptions().layout_engine,
    mpv_arg: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            negative=(),
            help="extra raw mpv flag (repeatable; SubMiner-style passthrough). Wins over our own "
            "defaults (force-window/keep-open/slang/sub-visibility/osd-level/start) — mpv is "
            "last-flag-wins — but never over --input-ipc-server/--log-file/the anti-duplicate "
            "script-opts marker, which we always set last",
        ),
    ] = None,
) -> int:  # pragma: no cover — launches real mpv/ffmpeg (parse layer covered by test_cli)
    """Play a video with Japanese subs; hover a word → Yomitan-like dictionary tooltip in mpv."""
    return run_impl(
        video,
        config=config,
        sub_file=sub_file,
        slang=slang,
        dicts=dicts,
        translate_key=translate_key,
        start=start,
        jimaku=jimaku,
        jimaku_key=jimaku_key,
        jimaku_title=jimaku_title,
        resync=resync,
        episode=episode,
        width=width,
        height=height,
        fullscreen=fullscreen,
        use_config=use_config,
        demo_word=demo_word,
        demo_translate=demo_translate,
        demo_scroll=demo_scroll,
        bulk=bulk,
        screenshot=screenshot,
        seconds=seconds,
        color=color,
        known=known,
        anki_decks=anki_decks,
        freq=freq,
        pitch=pitch,
        mine=mine,
        mine_deck=mine_deck,
        mine_model=mine_model,
        mine_normalize_audio=mine_normalize_audio,
        mine_animated_screenshot=mine_animated_screenshot,
        mine_key=mine_key,
        mine_all_key=mine_all_key,
        preview_key=preview_key,
        no_audio_play=no_audio_play,
        mine_preview=mine_preview,
        tip_height=tip_height,
        tip_scale=tip_scale,
        pause_on_tooltip=pause_on_tooltip,
        prefetch=prefetch,
        auto_translate=auto_translate,
        hover_switch_delay=hover_switch_delay,
        layout_engine=layout_engine,
        mpv_arg=mpv_arg,
        profile=profile,
    )


def register(app: cyclopts.App) -> None:
    app.command(run, name="run")
    app.default(run)

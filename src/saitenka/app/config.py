"""Persistent overlay settings — a small TOML file so you don't re-type ``--dict``/``--freq`` etc.

Lives in its **own** platform-native config dir (``paths.config_dir()`` →
``%LOCALAPPDATA%\\saitenka\\overlay.toml`` on Windows, ``~/.config/saitenka/overlay.toml`` on
macOS/Linux), separate from mpv's config and the animecards rig — the overlay is an independent tool
and shouldn't have its settings parsed by mpv's own config loader. Precedence: built-in defaults <
this file < explicit CLI flags. Point elsewhere with ``$SAITENKA_CONFIG`` or ``--config``.

``dicts`` / ``freq`` / ``pitch`` hold dictionary **titles**, resolved against the consolidated
:class:`~saitenka.app.dictdb.DictionaryDb` (``data_dir()/dictionaries.sqlite``) that ``saitenka
import`` builds once — not file paths.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Literal

from saitenka.app import paths

log = logging.getLogger(__name__)

CONFIG_HOME = paths.config_dir()
DEFAULT_PATH = CONFIG_HOME / "overlay.toml"


def config_path(override: str | os.PathLike | None = None) -> Path:
    """Resolved config path: explicit override > $SAITENKA_CONFIG > default."""
    p = override or os.environ.get("SAITENKA_CONFIG") or DEFAULT_PATH
    return Path(p).expanduser()


#: Keys a config may still carry that no longer do anything, and what replaced them. Warned about
#: rather than dropped in silence: a key that stops working without saying so reads as the setting
#: having no effect on this machine, which is the one diagnosis nothing in `doctor` can correct.
RETIRED_KEYS = {
    "poll_interval": (
        "the session blocks on its transport under the earliest armed timer; there is no tick"
    ),
}


def load_config(override: str | os.PathLike | None = None) -> dict:
    """Parse the TOML config, or return ``{}`` if it doesn't exist / can't be read."""
    p = config_path(override)
    if not p.exists():
        return {}
    try:
        with p.open("rb") as f:
            cfg = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    warn_retired(cfg)
    return cfg


def warn_retired(cfg: dict) -> list[str]:
    """Log every retired key the config still sets, and return their names."""
    found = [key for key in RETIRED_KEYS if _holds(cfg, key)]
    for key in found:
        log.warning("config key %r is retired and ignored: %s", key, RETIRED_KEYS[key])
    return found


def _holds(cfg: dict, key: str) -> bool:
    """Whether ``key`` is set at the top level or in any one section."""
    return key in cfg or any(key in v for v in cfg.values() if isinstance(v, dict))


def expand_paths(items) -> list[str]:
    """Expand ``~`` and env vars in a list of dictionary paths."""
    return [os.path.expandvars(str(Path(str(x)).expanduser())) for x in items or []]


# Default keybinds for subtitle navigation.  All can be overridden in overlay.toml.
SUB_NAV_DEFAULTS: dict[str, str] = {
    "sub_prev_key": "Alt+LEFT",  # jump to previous subtitle line
    "sub_next_key": "Alt+RIGHT",  # jump to next subtitle line
    "sub_replay_key": "Alt+DOWN",  # replay current subtitle line from its start
}


# --- SessionController options schema -----------------------------------------------------------------------
# The controller's knobs, grouped by concern. This IS the settings schema: a new knob is one field
# here (plus reading it in SessionController.__init__) — no more 22-parameter signatures. The CLI binds these
# via cyclopts; legacy exploded kwargs still route through ``ReaderOptions.with_overrides``.


@dataclass(frozen=True)
class KeyOptions:
    """mpv keybinds owned by the overlay."""

    mine_key: str = field(
        default="Ctrl+m", metadata={"help": "Mine the hovered word (still frame)."}
    )
    mine_video_key: str = field(
        default="Ctrl+Shift+m",
        metadata={"help": "Mine the hovered word with an animated (motion) clip."},
    )
    mine_all_key: str = field(
        default="Shift+m", metadata={"help": "Bulk-mine every word on the line."}
    )
    translate_key: str = field(default="t", metadata={"help": "Reveal the line's EN translation."})
    overlay_toggle_key: str = field(
        default="Alt+o", metadata={"help": "Toggle the overlay on/off."}
    )
    subtitle_language_key: str = field(
        default="Alt+t", metadata={"help": "Cycle the subtitle track / language."}
    )
    subtitle_mark_jp_key: str = field(
        default="Alt+j",
        metadata={"help": "Force the current track as JP (untagged/misdetected subs)."},
    )
    bookmark_key: str = field(default="Alt+b", metadata={"help": "Bookmark the current line."})
    sidebar_key: str = field(default="\\", metadata={"help": "Toggle the sidebar."})
    analysis_key: str = field(default="`", metadata={"help": "Open the episode-analysis panel."})
    annotation_key: str = field(default="Alt+a", metadata={"help": "Cycle the annotation mode."})
    help_key: str = field(default="F1", metadata={"help": "Show the keybind help overlay."})
    profile_cycle_key: str = field(
        default="Alt+Shift+p",
        metadata={"help": "Cycle the active reading profile (no-op with a single profile)."},
    )
    subtitle_retry_key: str = field(
        default="Ctrl+Shift+T", metadata={"help": "Retry subtitle fetch/resync."}
    )
    sub_picker_key: str = field(
        default="Ctrl+j", metadata={"help": "Open the jimaku subtitle-source download picker."}
    )
    legacy_renderer_key: str = field(
        default="Ctrl+Shift+L",
        metadata={"help": "Draw subtitles with the legacy renderer instead of mpv's."},
    )
    preview_key: str = field(default="p", metadata={"help": "Toggle the card-preview panel."})
    hover_pause_key: str = field(default="Alt+p", metadata={"help": "Toggle pause-on-hover."})
    sub_prev_key: str = field(
        default="Alt+LEFT", metadata={"help": "Jump to the previous subtitle line."}
    )
    sub_next_key: str = field(
        default="Alt+RIGHT", metadata={"help": "Jump to the next subtitle line."}
    )
    sub_replay_key: str = field(
        default="Alt+DOWN", metadata={"help": "Replay the current subtitle line from its start."}
    )


@dataclass(frozen=True)
class TooltipOptions:
    """Tooltip geometry + hover feel."""

    sub_size: int | None = field(
        default=None, metadata={"help": "Subtitle font size override (blank = scale to video)."}
    )
    # 0 = AUTO (osd_h/REF_H: 1.0 @1080p, 2.0 @4K — tracks the video viewport). A positive value FIXES it
    # regardless of resolution; also the scale `saitenka prewarm` builds the mask atlas at, so the crisp
    # upgrade lands from disk instead of paying getmask2 on first native raster.
    tip_scale: float = field(
        default=0.0,
        metadata={"help": "Crisp-render reference→display factor (0 = auto by resolution)."},
    )
    bottom_margin_frac: float = 0.06
    sub_background_opacity: int = field(
        default=150,
        metadata={"help": "Opacity (0–255) of the box behind subtitles; 0 = fully transparent."},
    )
    tip_max_frac: float = 0.4  # BASE tooltip viewport ≤ this fraction of the video height
    nested_max_frac: float = field(
        default=0.6, metadata={"help": "Nested (scan) popup height as a fraction of the video."}
    )
    pause_on_tooltip: bool = field(
        default=True,
        metadata={"help": "Freeze the frame the moment a tooltip opens (mining default)."},
    )
    annotation_mode: Literal["full", "hover"] = field(
        default="full",
        metadata={"help": "Annotate every word (full) or only the hovered one (hover)."},
    )
    kanji_stroke_order: bool = field(
        default=True,
        metadata={"help": "Draw the kanji panel's big headword in a numbered stroke-order font."},
    )
    scan_delay: float = field(
        default=1.0, metadata={"help": "Dwell (seconds) before a nested scan popup opens."}
    )
    hover_switch_delay: float = field(
        default=0.15,
        metadata={"help": "Dwell (seconds) before the tooltip switches to a new word."},
    )
    hide_delay: float = field(
        default=0.6,
        metadata={"help": "Seconds the tooltip lingers after the cursor leaves the word."},
    )
    flash_secs: float = field(
        default=0.22, metadata={"help": 'Duration of the "copied" highlight pulse on a popup.'}
    )
    panel_cache_max: int = field(
        default=128, metadata={"help": "LRU cap on cached (compressed) rendered tooltip panels."}
    )
    band_cache_max: int = 128  # LRU cap on retained 256px render BANDS *per* windowed panel — the
    # layer under panel_cache_max. Bounds a single tall tooltip's warm pixels to a scroll-back WINDOW,
    # not the whole block: 128 bands ≈ 32k px kept warm — well past the viewport so short scrolls back
    # hit the cache, but a bounded fraction of a worst-case ~87k-px entry (retaining all of that would
    # defeat the O(viewport) memory bound banding exists for). The visible window is always protected
    # regardless of the cap; raise it to widen the warm window (more RAM), lower it to shrink it.
    raw_band_ceiling_mb: float = (
        0.1  # keep a panel's render bands UNCOMPRESSED only below this estimated
    )
    # size in MB — effectively "always compress", since a real panel clears 0.1 MB immediately.
    # It was 100, which no entry reaches, so bands were always retained raw for a one-time inflate
    # saved on first scroll-reach. Compression now costs far less than that trade assumed: zstd
    # (3.14+) inflates a band in ~0.55ms against zlib's ~1.13 and stores 2.4x smaller, so the ceiling
    # buys a sub-millisecond win per band in exchange for ~11-27x the retained pixels.
    # 0 = compress unconditionally. The visible/warm window is bounded by band_cache_max either way.
    # "default" = always-available pure-Python backend; "taffy" = the optional taffylite Rust flexbox
    # engine (needs saitenka[layout-engine]); byte-identical geometry. A missing wheel falls back, logged.
    layout_engine: Literal["default", "taffy"] = field(
        default="default", metadata={"help": "Tooltip block-geometry backend."}
    )
    # OPT-OUT: when a `render-cache.sqlite` (built by `saitenka prewarm`) exists, a cold first hover on a
    # tall entry paints its precomposed first viewport straight from disk. No prebuilt cache → costs nothing.
    render_cache: bool = field(
        default=True, metadata={"help": "Use an on-disk render cache when one exists."}
    )
    # OPT-OUT: on a hi-dpi display composite the reference panel at NATIVE resolution (crisp glyph masks
    # over 1× geometry). No effect at 1080p (native == the upscale). False = paint only the soft 1× upscale.
    crisp_upscale: bool = field(
        default=True, metadata={"help": "Composite at native resolution on hi-dpi displays."}
    )
    # OPT-OUT: when a `mask-atlas.sqlite` (built by `saitenka prewarm`) exists, getmask2 alpha bitmaps load
    # from disk (~half the raster CPU); ~150 MB RAM bulk-loaded once at startup. No atlas → nothing loads.
    mask_atlas: bool = field(
        default=True, metadata={"help": "Use an on-disk glyph mask atlas when one exists."}
    )
    # LRU byte ceiling on the on-disk render cache. The ~32k popular-word set is ~900 MB; 2 GB holds it all
    # with headroom for runtime write-back. Lower it to spend less disk.
    render_cache_max_mb: int = field(
        default=2048, metadata={"help": "On-disk render-cache size ceiling (MiB)."}
    )
    # Eligibility gate (px): a shorter entry cold-renders within budget, so it's not worth a cache row.
    # EMPIRICALLY CALIBRATED — <512px ~8ms, 512–1024px ~18ms, 4096px ~31ms.
    render_cache_min_height: int = field(
        default=512, metadata={"help": "Minimum panel height (px) eligible for the render cache."}
    )


@dataclass(frozen=True)
class MiningOptions:
    """Mining-flow behaviour (the Anki client/deck config stays in anki.MineConfig)."""

    play_audio: bool = field(
        default=True, metadata={"help": "Play the sentence audio after a mine."}
    )
    show_preview: bool = field(
        default=True, metadata={"help": "Auto-pop the card-preview panel after a mine."}
    )
    max_bulk: int = field(
        default=12, metadata={"help": 'Cap on words mined in one "mine all" bulk action.'}
    )
    anki_ok_ttl: float = field(
        default=3.0, metadata={"help": "Seconds an AnkiConnect reachability check is cached for."}
    )
    anki_ping_timeout: float = field(
        default=0.4, metadata={"help": "Timeout (seconds) for the reachability ping."}
    )


@dataclass(frozen=True)
class TranslationOptions:
    """EN-translation reveal behaviour."""

    auto_translate: bool = field(
        default=False, metadata={"help": "Reveal the EN translation automatically on each line."}
    )


@dataclass(frozen=True)
class StatsOptions:
    """Local immersion history; explicit opt-in and independent of telemetry."""

    enabled: bool = field(
        default=False, metadata={"help": "Record local immersion-session history."}
    )
    summary: bool = field(
        default=True, metadata={"help": "Print a session summary when a file ends."}
    )


@dataclass(frozen=True)
class PanelOptions:
    """Scale for the help, sidebar, and episode-analysis utility surfaces."""

    scale: float = field(
        default=1.0, metadata={"help": "Scale for the help/sidebar/analysis panels."}
    )


@dataclass(frozen=True)
class PerfOptions:
    """Background-work tuning: prefetch parallelism and speculative line lookahead.

    No poll cadence: the session blocks on its transport under the earliest armed timer, so there is
    no tick left to tune. `[perf] poll_interval` is accepted and ignored — see `RETIRED_KEYS`.
    """

    # Tooltip-warming worker threads (persistent, whole session). Mostly a RAM knob: each holds its own
    # per-thread SQLite page cache (~[dictdb].cache_size_kib, 32 MiB default) + a per-thread FreeType
    # face cache + (free-threaded) its own allocator arena, so RSS scales ~linearly with the count.
    # 0 = auto (a flat 4 free-threaded where render parallelizes; 2 on a GIL build where extra workers
    # only contend); a positive value pins it explicitly on BOTH builds — lower it to cap RAM.
    prefetch_workers: int = field(
        default=0,
        metadata={"help": "Tooltip-warming worker threads (0 = auto). Mostly a RAM knob."},
    )
    # Each decodes+caches the next cue's glossaries during idle playback so the first hover after the line
    # advances is warm. Needs an external sub index — embedded/jimaku tracks have none (a no-op there).
    prefetch_lookahead: int = field(
        default=0,
        metadata={"help": "Subtitle cues to decode-warm ahead of the current line (0 = none)."},
    )

    # Speculatively renders the SAME viewport-capped head a real hover would (via the same
    # panel_for()/panel_cache path — a cache hit at hover time, no separate cache tier or key-matching
    # logic needed), for a WORTHWHILE subset of upcoming words — n+1 / forgotten / rare-frequency-band,
    # explicitly excluding already-known or already-mined words — instead of only decode-warming them.
    # Selectivity IS the RAM/CPU cap: most upcoming words never get a render job at all, only the ones
    # worth the extra cost over plain decode. Needs a sub index + a scorer (for the n+1/known/freq
    # signal); a no-op without either.
    # A SEPARATE, shallower knob than prefetch_lookahead — render jobs cost far more than decode-only warm
    # jobs, so this stays shorter-range even when lookahead is generous.
    head_prefetch_lookahead: int = field(
        default=1, metadata={"help": "Upcoming cues to consider for head pre-render (0 = off)."}
    )
    # panel_cache's LRU bounds RETAINED size, not concurrently-building PIL objects in flight.
    head_prefetch_queue_max: int = field(
        default=24,
        metadata={"help": "Queued head-render cap, 1–64 (transient-RSS bound)."},
    )
    # Sized for a whole episode's cues so a full-file tokenization prefetch never evicts a cue still needed.
    token_cache_max: int = field(
        default=2500,
        metadata={"help": "LRU cap on tokenized+scored cues (source line → TokenizedCue)."},
    )


@dataclass(frozen=True)
class SubtitleGeometryOptions:
    """Opt-in native-visible subtitle interaction backed by shadow libass geometry."""

    native_visible: bool = field(
        default=False,
        metadata={"help": "Keep mpv subtitles visible and derive hover geometry with libass."},
    )
    native_formats: str = field(
        default="authored-ass",
        metadata={
            "help": (
                "Which tracks the native path takes: authored-ass (only .ass) or all (also the "
                "SubRip tracks mpv converts). Ignored when native_visible is off."
            )
        },
    )
    library_path: str | None = field(
        default=None,
        metadata={"help": "Optional explicit path to the system libass library."},
    )
    cache_max: int = field(default=3, metadata={"help": "Current/lookahead geometry cache bound."})
    lookahead: int = field(default=2, metadata={"help": "Static cues to render ahead."})


# Flat legacy kwarg name -> the ReaderOptions group it belongs to (used by with_overrides).
_OPTION_GROUPS: dict[str, str] = {
    **{f.name: "keys" for f in fields(KeyOptions)},
    **{f.name: "tooltip" for f in fields(TooltipOptions)},
    **{f.name: "mining" for f in fields(MiningOptions)},
    **{f.name: "translation" for f in fields(TranslationOptions)},
    **{f.name: "panels" for f in fields(PanelOptions)},
    **{f.name: "perf" for f in fields(PerfOptions)},
    **{f.name: "subtitle_geometry" for f in fields(SubtitleGeometryOptions)},
}


@dataclass(frozen=True)
class ReaderOptions:
    """All SessionController knobs, grouped by concern."""

    keys: KeyOptions = KeyOptions()
    tooltip: TooltipOptions = TooltipOptions()
    mining: MiningOptions = MiningOptions()
    translation: TranslationOptions = TranslationOptions()
    stats: StatsOptions = StatsOptions()
    panels: PanelOptions = PanelOptions()
    perf: PerfOptions = PerfOptions()
    subtitle_geometry: SubtitleGeometryOptions = SubtitleGeometryOptions()
    prefetch: bool = True
    resync: bool = True  # auto-resync jimaku-sourced subs via alass/ffsubsync
    overlay_id_base: int = 1  # shift physical mpv overlay ids to coexist with other scripts

    def with_overrides(self, **kw) -> ReaderOptions:
        """Route flat legacy kwargs (``mine_key=…``, ``tip_max_frac=…``) onto the right group.
        Unknown names raise TypeError so typos stay loud."""
        keys, tooltip = self.keys, self.tooltip
        mining, translation, stats, panels = (
            self.mining,
            self.translation,
            self.stats,
            self.panels,
        )
        perf, subtitle_geometry = self.perf, self.subtitle_geometry
        prefetch, resync, overlay_id_base = self.prefetch, self.resync, self.overlay_id_base
        for name, value in kw.items():
            group = _OPTION_GROUPS.get(name)
            if name == "prefetch":
                prefetch = bool(value)
            elif name == "resync":
                resync = bool(value)
            elif name == "overlay_id_base":
                overlay_id_base = int(value)
            elif group == "keys":
                keys = replace(keys, **{name: value})
            elif group == "tooltip":
                tooltip = replace(tooltip, **{name: value})
            elif group == "mining":
                mining = replace(mining, **{name: value})
            elif group == "translation":
                translation = replace(translation, **{name: value})
            elif group == "panels":
                panels = replace(panels, **{name: value})
            elif group == "perf":
                perf = replace(perf, **{name: value})
            elif group == "subtitle_geometry":
                subtitle_geometry = replace(subtitle_geometry, **{name: value})
            else:
                raise TypeError(f"unknown SessionController option: {name!r}")
        return ReaderOptions(
            keys=keys,
            tooltip=tooltip,
            mining=mining,
            perf=perf,
            subtitle_geometry=subtitle_geometry,
            translation=translation,
            stats=stats,
            panels=panels,
            prefetch=prefetch,
            resync=resync,
            overlay_id_base=overlay_id_base,
        )


def subtitle_geometry_options(cfg: dict) -> SubtitleGeometryOptions:
    raw = cfg.get("subtitle_geometry")
    if raw is not None and not isinstance(raw, dict):
        raise TypeError("subtitle_geometry must be a table")
    values = raw or {}
    defaults = SubtitleGeometryOptions()
    native_visible = values.get("native_visible", defaults.native_visible)
    if not isinstance(native_visible, bool):
        raise TypeError("subtitle_geometry.native_visible must be a boolean")
    cache_max = values.get("cache_max", defaults.cache_max)
    if isinstance(cache_max, bool) or not isinstance(cache_max, int) or cache_max <= 0:
        raise ValueError("subtitle_geometry.cache_max must be a positive integer")
    library_path = values.get("library_path", defaults.library_path)
    if library_path is not None and not isinstance(library_path, str):
        raise ValueError("subtitle_geometry.library_path must be a string")
    lookahead = values.get("lookahead", defaults.lookahead)
    if isinstance(lookahead, bool) or not isinstance(lookahead, int) or lookahead < 0:
        raise ValueError("subtitle_geometry.lookahead must be a non-negative integer")
    native_formats = values.get("native_formats", defaults.native_formats)
    if not isinstance(native_formats, str):
        raise TypeError("subtitle_geometry.native_formats must be a string")
    return SubtitleGeometryOptions(
        native_visible=native_visible,
        native_formats=native_formats,
        library_path=library_path,
        cache_max=cache_max,
        lookahead=lookahead,
    )


def resolve_resync_timeout(cfg: dict | None = None) -> int:
    """Resync subprocess timeout (seconds) from top-level ``resync_timeout`` in ``overlay.toml``."""
    if cfg is None:
        cfg = load_config()
    return int(cfg.get("resync_timeout", 300))


def resolve_resync_split_penalty(cfg: dict | None = None) -> float | None:
    """alass ``--split-penalty`` from top-level ``resync_split_penalty`` in ``overlay.toml`` — its most
    impactful knob (0–1000; LOWER = more willing to split at a scene/OP boundary, so a source that's
    right after the OP but early before it can get its own segment offset). ``None`` (the default)
    passes no flag, leaving alass's built-in default. Ignored by the ffsubsync fallback (no such knob)."""
    if cfg is None:
        cfg = load_config()
    raw = cfg.get("resync_split_penalty")
    return None if raw is None else float(raw)


@dataclass(frozen=True)
class DictDbOptions:
    """Per-connection SQLite tuning for the consolidated dictionary DB (``dictdb.py``), plus the
    chunk size used when re-chunking a streamed dexie database export into Yomitan-format banks."""

    mmap_size: int = field(
        default=268_435_456, metadata={"help": "SQLite mmap window per read connection (bytes)."}
    )
    cache_size_kib: int = field(
        default=32_768, metadata={"help": "SQLite page cache per read connection (KiB)."}
    )
    dexie_chunk_size: int = field(
        default=10_000, metadata={"help": "Entries per bank file when importing a dexie export."}
    )
    entry_cache_max: int = field(
        default=256, metadata={"help": "LRU cap on decoded DictEntry objects, per Dictionary."}
    )
    # Opt-in: persist each term_bank entry's Yomitan `seq` (term_bank[6]) into `entries.seq`. For a
    # JMdict-derived dict (JMdict itself, Jitendex, …) `seq` == the Kanji Study deep-link id, so this
    # lets mining fill `card.idseq` offline, without the `jmdict` extra (#255). The column always exists
    # (additive migration); this flag only gates whether import writes values into it.
    persist_seq: bool = field(
        default=False,
        metadata={
            "help": "Persist each entry's Yomitan seq into entries.seq for offline deep-link IDs."
        },
    )


def resolve_dictdb(cfg: dict | None = None) -> DictDbOptions:
    """:class:`DictDbOptions` from the ``[dictdb]`` config table, defaulting to the stock tuning."""
    if cfg is None:
        cfg = load_config()
    raw = cfg.get("dictdb")
    d: dict = raw if isinstance(raw, dict) else {}
    defaults = DictDbOptions()
    return DictDbOptions(
        mmap_size=int(d.get("mmap_size", defaults.mmap_size)),
        cache_size_kib=int(d.get("cache_size_kib", defaults.cache_size_kib)),
        dexie_chunk_size=int(d.get("dexie_chunk_size", defaults.dexie_chunk_size)),
        entry_cache_max=int(d.get("entry_cache_max", defaults.entry_cache_max)),
        persist_seq=bool(d.get("persist_seq", defaults.persist_seq)),
    )


@dataclass(frozen=True)
class TelemetryOptions:
    """Runtime tracing/metrics — OFF by default even when the ``telemetry`` extra is installed;
    ``enabled`` is the actual opt-in switch. ``sample_hot_path``
    bounds the cost of instrumenting the per-tick hit-test path (0.0 = never sample it)."""

    enabled: bool = field(
        default=False, metadata={"help": "Master switch for runtime tracing/metrics."}
    )
    export_dir: str | None = field(
        default=None, metadata={"help": "Trace/metric export dir (blank = cache_dir()/telemetry)."}
    )
    sample_hot_path: float = field(
        default=0.0, metadata={"help": "Sampling rate [0.0–1.0] for the per-tick hit-test path."}
    )


@dataclass(frozen=True)
class ProfileOptions:
    """One reading profile's identity (#254): the main/second language CODES and the tokenizer strategy
    NAME. Edited inline as the ``[profile]`` default table by ``saitenka config``; named
    ``[profiles.<name>]`` tables (selected by top-level ``active_profile``) overlay it. The tokenizer is
    a separate, user-overridable field — NOT derived from the language — so one strategy serves a family
    (``unidic`` for ja; a future ``latin`` for fr/es/…). Resolved into a runtime ``Profile`` by
    :func:`saitenka.app.profiles.resolve_profile`."""

    language: str = field(
        default="jp", metadata={"help": "Main (target) language code — open, e.g. jp, fr, de-CH."}
    )
    tokenizer: str = field(
        default="unidic",
        metadata={"help": "Tokenizer strategy name (a registered tokenizer; jp uses unidic)."},
    )
    second: str = field(
        default="en", metadata={"help": "Second (known/translation) language code."}
    )


@dataclass(frozen=True)
class WordAudioOptions:
    """Opt-in word-pronunciation audio from a local yomichan/yomitan audio pack (#93) — additive to the
    mined sentence/scene clip, resolved offline from the expression + reading mining already knows
    (grounded — never synthesized). Edited as ``[mine]`` table keys by ``saitenka config``; merged into
    :class:`~saitenka.app.anki.MineConfig` (``word_audio_pack``/``word_audio_field``) by
    :func:`saitenka.app.mining_config.mine_config_from`."""

    word_audio_enabled: bool = field(
        default=False,
        metadata={"help": "Attach word-pronunciation audio from a local audio pack."},
    )
    word_audio_pack_dir: str | None = field(
        default=None,
        metadata={"help": "Local yomichan/yomitan audio-pack directory (blank = disabled)."},
    )
    word_audio_field: str = field(
        default="WordAudio",
        metadata={"help": "Note field the word-pronunciation audio is written to."},
    )


def resolve_telemetry(cfg: dict | None = None) -> TelemetryOptions:
    """:class:`TelemetryOptions` from the ``[telemetry]`` config table. ``$OTEL_SDK_DISABLED`` (the
    standard OTel env var) forces ``enabled=False`` even if the config table turns it on — it's the
    documented emergency kill switch, so it must win over a stale/mistaken config file."""
    if cfg is None:
        cfg = load_config()
    raw = cfg.get("telemetry")
    d: dict = raw if isinstance(raw, dict) else {}
    defaults = TelemetryOptions()
    enabled = bool(d.get("enabled", defaults.enabled))
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() in {"true", "1"}:
        enabled = False
    return TelemetryOptions(
        enabled=enabled,
        export_dir=d.get("export_dir", defaults.export_dir),
        sample_hot_path=float(d.get("sample_hot_path", defaults.sample_hot_path)),
    )

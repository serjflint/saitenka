"""Persistent overlay settings — a small TOML file so you don't re-type ``--dict``/``--freq`` etc.

Lives in its **own** platform-native config dir (``paths.config_dir()`` →
``%LOCALAPPDATA%\\saitenka\\overlay.toml`` on Windows, ``~/.config/saitenka/overlay.toml`` on
macOS/Linux), separate from mpv's config and the animecards rig — the overlay is an independent tool
and shouldn't have its settings parsed by mpv's own config loader. Precedence: built-in defaults <
this file < explicit CLI flags. Point elsewhere with ``$SAITENKA_CONFIG`` or ``--config``.

``dicts`` / ``freq`` / ``pitch`` hold dictionary **titles**, resolved against the consolidated
:class:`~overlay.app.dictdb.DictionaryDb` (``data_dir()/dictionaries.sqlite``) that ``saitenka
import`` builds once — not file paths.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Literal

from overlay.app import paths

CONFIG_HOME = paths.config_dir()
DEFAULT_PATH = CONFIG_HOME / "overlay.toml"


def config_path(override: str | os.PathLike | None = None) -> Path:
    """Resolved config path: explicit override > $SAITENKA_CONFIG > default."""
    p = override or os.environ.get("SAITENKA_CONFIG") or DEFAULT_PATH
    return Path(p).expanduser()


def load_config(override: str | os.PathLike | None = None) -> dict:
    """Parse the TOML config, or return ``{}`` if it doesn't exist / can't be read."""
    p = config_path(override)
    if not p.exists():
        return {}
    try:
        with p.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def expand_paths(items) -> list[str]:
    """Expand ``~`` and env vars in a list of dictionary paths."""
    return [os.path.expandvars(str(Path(str(x)).expanduser())) for x in items or []]


# Default keybinds for subtitle navigation.  All can be overridden in overlay.toml.
SUB_NAV_DEFAULTS: dict[str, str] = {
    "sub_prev_key": "Alt+LEFT",  # jump to previous subtitle line
    "sub_next_key": "Alt+RIGHT",  # jump to next subtitle line
    "sub_replay_key": "Alt+DOWN",  # replay current subtitle line from its start
}


# --- Reader options schema -----------------------------------------------------------------------
# The controller's knobs, grouped by concern. This IS the settings schema: a new knob is one field
# here (plus reading it in Reader.__init__) — no more 22-parameter signatures. The CLI binds these
# via cyclopts; legacy exploded kwargs still route through ``ReaderOptions.with_overrides``.


@dataclass(frozen=True)
class KeyOptions:
    """mpv keybinds owned by the overlay."""

    mine_key: str = "Ctrl+m"
    mine_video_key: str = "Ctrl+Shift+m"  # mine the hovered word with an animated (motion) clip
    mine_all_key: str = "Shift+m"
    translate_key: str = "t"
    overlay_toggle_key: str = "Alt+o"
    subtitle_language_key: str = "Alt+t"
    bookmark_key: str = "Alt+b"
    sidebar_key: str = "\\"
    analysis_key: str = "`"
    annotation_key: str = "Alt+a"
    help_key: str = "F1"
    subtitle_retry_key: str = "Ctrl+Shift+T"
    sub_picker_key: str = "Ctrl+j"  # Window 1: jimaku subtitle-source download picker
    preview_key: str = "p"
    hover_pause_key: str = "Alt+p"
    sub_prev_key: str = "Alt+LEFT"
    sub_next_key: str = "Alt+RIGHT"
    sub_replay_key: str = "Alt+DOWN"


@dataclass(frozen=True)
class TooltipOptions:
    """Tooltip geometry + hover feel."""

    sub_size: int | None = None  # subtitle font override (None = scale to video)
    tip_scale: float = 0.0  # reference→display factor for the tooltip crisp render. 0 = AUTO
    # (osd_h/REF_H: 1.0 @1080p, 2.0 @4K — tracks the video viewport). A positive value FIXES it
    # regardless of resolution — 1.5 renders crisp native glyph masks at 1.5× on any display, a
    # cosmetic preference independent of playback res. Also the scale `saitenka prewarm` builds the
    # mask atlas at, so the crisp upgrade lands from disk instead of paying getmask2 on first native raster.
    bottom_margin_frac: float = 0.06
    tip_max_frac: float = 0.4  # BASE tooltip viewport ≤ this fraction of the video height
    nested_max_frac: float = (
        0.6  # nested (scan) popup viewport ≤ this fraction — deliberately roomier
    )
    pause_on_tooltip: bool = (
        True  # freeze the frame the moment a tooltip opens — the mining default
    )
    annotation_mode: Literal["full", "hover"] = "full"
    scan_delay: float = 1.0  # dwell before a nested scan popup opens
    hover_switch_delay: float = 0.15  # dwell before the tooltip switches to a NEW word
    hide_delay: float = 0.6  # seconds the tooltip lingers after the cursor leaves the word
    flash_secs: float = 0.22  # how long the "copied" highlight border pulses on a popup
    panel_cache_max: int = 128  # LRU cap on cached (compressed) rendered tooltip panels
    band_cache_max: int = 128  # LRU cap on retained 256px render BANDS *per* windowed panel — the
    # layer under panel_cache_max. Bounds a single tall tooltip's warm pixels to a scroll-back WINDOW,
    # not the whole block: 128 bands ≈ 32k px kept warm — well past the viewport so short scrolls back
    # hit the cache, but a bounded fraction of a worst-case ~87k-px entry (retaining all of that would
    # defeat the O(viewport) memory bound banding exists for). The visible window is always protected
    # regardless of the cap; raise it to widen the warm window (more RAM), lower it to shrink it.
    raw_band_ceiling_mb: int = (
        100  # keep a panel's render bands UNCOMPRESSED (skips the one-time zlib
    )
    # decompress on the first scroll-reach of a band — measured ~9→4ms off the cold-band frame tail)
    # UNLESS the panel's estimated uncompressed size exceeds this many MB, when its bands compress so one
    # pathological entry can't blow the retained-pixel budget (raw is ~10× the zlib size). 0 = always
    # compress (the pre-1.3 behavior). The visible/warm window is bounded by band_cache_max either way.
    layout_engine: Literal["default", "taffy"] = (
        "default"  # tooltip block-geometry backend. "default"
    )
    # = the always-available pure-Python DefaultLayoutBackend. "taffy" = the optional taffylite Rust
    # flexbox engine (needs saitenka[layout-engine]); byte-identical geometry, chosen for a mature CSS
    # engine's robustness, not speed. An unset/missing wheel falls back to "default", logged, never fatal.
    render_cache: bool = True  # cross-session persistent render cache (#149), OPT-OUT: USED WHEN
    # AVAILABLE — if a `render-cache.sqlite` exists (built by `saitenka prewarm`), a cold first-ever hover
    # on a cost-gated (tall/pathological) entry paints its precomposed first viewport straight from disk
    # (copy+upload, skipping the build+raster) and live hovers extend it. No prebuilt cache → nothing is
    # created and it costs nothing. Set false to ignore an existing cache. Miss/resolution change → live.
    crisp_upscale: bool = True  # OPT-OUT: on a hi-dpi display the tooltip composites its ONE reference panel at NATIVE
    # resolution (crisp glyph masks over the 1× geometry — the scale-as-boundary arch), scroll-ahead
    # warming the next native bands off the main thread. Set false to paint only the soft 1× upscale. No
    # effect at 1080p (display scale 1.0, where native == the upscale).
    mask_atlas: bool = (
        True  # persistent glyph mask atlas (#149 Tier-1), OPT-OUT: USED WHEN AVAILABLE —
    )
    # if a `mask-atlas.sqlite` exists (built by `saitenka prewarm`), getmask2 alpha bitmaps load from disk
    # (~half the raster CPU) so cache-miss / scroll / post-paint builds skip re-rasterising. ~150 MB RAM
    # bulk-loaded once in the background at startup. No prebuilt atlas → nothing loads. Set false to ignore.
    render_cache_max_mb: int = (
        2048  # THE size bound: LRU byte ceiling on the on-disk render cache. Each
    )
    # stored head is the first viewport (height-capped at the tooltip cap) ≈ 32 KiB compressed, so the
    # whole ~32k popular-word set is ~900 MB — this 2 GB default holds it all with headroom for the
    # runtime write-back to add rarer hovered words on top; lower it to spend less disk.
    render_cache_min_height: int = (
        512  # eligibility gate (px), keeps the big render cache to NON-TRIVIAL panels: skip a head
    )
    # shorter than this. EMPIRICALLY CALIBRATED — a < 512px entry cold-renders (get + layout + first-
    # viewport raster) in ~8ms, already within budget, so it's not worth a render-cache row; 512–1024px is
    # ~18ms, 4096px ~31ms. (Glyph coverage for those trivial words still lands in the mask atlas, which is
    # per-glyph and covers the full population.) Cache SIZE is bounded by render_cache_max_mb + LRU too.


@dataclass(frozen=True)
class MiningOptions:
    """Mining-flow behaviour (the Anki client/deck config stays in anki.MineConfig)."""

    play_audio: bool = True
    show_preview: bool = (
        True  # auto-pop the card-preview panel after a mine (Esc/next-cue dismisses)
    )
    max_bulk: int = 12  # cap on words mined in one "mine all" bulk action
    anki_ok_ttl: float = 3.0  # seconds an AnkiConnect reachability check is cached for
    anki_ping_timeout: float = 0.4  # timeout for the reachability ping (hot hover path)


@dataclass(frozen=True)
class TranslationOptions:
    """EN-translation reveal behaviour."""

    auto_translate: bool = False


@dataclass(frozen=True)
class StatsOptions:
    """Local immersion history; explicit opt-in and independent of telemetry."""

    enabled: bool = False
    summary: bool = True


@dataclass(frozen=True)
class PanelOptions:
    """Scale for the help, sidebar, and episode-analysis utility surfaces."""

    scale: float = 1.0


@dataclass(frozen=True)
class PerfOptions:
    """Background-work tuning: poll cadence, prefetch parallelism, and speculative line lookahead."""

    poll_interval: float = 0.025  # main loop tick — trades CPU usage against input latency
    # Tooltip-warming worker threads (persistent, whole session). Mostly a RAM knob: each holds its own
    # per-thread SQLite page cache (~[dictdb].cache_size_kib, 32 MiB default) + a per-thread FreeType
    # face cache + (free-threaded) its own allocator arena, so RSS scales ~linearly with the count.
    # 0 = auto (a flat 4 free-threaded where render parallelizes; 2 on a GIL build where extra workers
    # only contend); a positive value pins it explicitly on BOTH builds — lower it to cap RAM.
    prefetch_workers: int = 0
    prefetch_lookahead: int = (
        0  # upcoming subtitle cues to WARM ahead of the current line (0 = only
    )
    # the current line). Each decodes+caches the next cue's dictionary glossaries during idle playback,
    # so the first hover after the line advances (or an Alt+→ nav) is already warm. Needs an external
    # sub index — embedded/jimaku tracks have none, so it's a no-op there.

    # Speculatively renders the SAME viewport-capped head a real hover would (via the same
    # panel_for()/panel_cache path — a cache hit at hover time, no separate cache tier or key-matching
    # logic needed), for a WORTHWHILE subset of upcoming words — n+1 / forgotten / rare-frequency-band,
    # explicitly excluding already-known or already-mined words — instead of only decode-warming them.
    # Selectivity IS the RAM/CPU cap: most upcoming words never get a render job at all, only the ones
    # worth the extra cost over plain decode. Needs a sub index + a scorer (for the n+1/known/freq
    # signal); a no-op without either.
    head_prefetch_lookahead: int = 1  # upcoming cues to consider for head pre-render (0 = off);
    # deliberately a SEPARATE, shallower knob than prefetch_lookahead — render jobs cost far more
    # than decode-only warm jobs, so this should stay shorter-range even when lookahead is generous
    head_prefetch_queue_max: int = (
        24  # bounds in-flight/queued render jobs — the transient-RSS cap:
    )
    # panel_cache's LRU bounds RETAINED size, not concurrently-building PIL objects in flight


# Flat legacy kwarg name -> the ReaderOptions group it belongs to (used by with_overrides).
_OPTION_GROUPS: dict[str, str] = {
    **{f.name: "keys" for f in fields(KeyOptions)},
    **{f.name: "tooltip" for f in fields(TooltipOptions)},
    **{f.name: "mining" for f in fields(MiningOptions)},
    **{f.name: "translation" for f in fields(TranslationOptions)},
    **{f.name: "panels" for f in fields(PanelOptions)},
    **{f.name: "perf" for f in fields(PerfOptions)},
}


@dataclass(frozen=True)
class ReaderOptions:
    """All Reader knobs, grouped by concern."""

    keys: KeyOptions = KeyOptions()
    tooltip: TooltipOptions = TooltipOptions()
    mining: MiningOptions = MiningOptions()
    translation: TranslationOptions = TranslationOptions()
    stats: StatsOptions = StatsOptions()
    panels: PanelOptions = PanelOptions()
    perf: PerfOptions = PerfOptions()
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
        perf = self.perf
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
            else:
                raise TypeError(f"unknown Reader option: {name!r}")
        return ReaderOptions(
            keys=keys,
            tooltip=tooltip,
            mining=mining,
            perf=perf,
            translation=translation,
            stats=stats,
            panels=panels,
            prefetch=prefetch,
            resync=resync,
            overlay_id_base=overlay_id_base,
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

    mmap_size: int = 268_435_456  # 256 MiB mmap window per read connection
    cache_size_kib: int = 32_768  # 32 MiB page cache per read connection
    dexie_chunk_size: int = 10_000  # entries per bank file when importing a dexie export
    entry_cache_max: int = 256  # LRU cap on decoded DictEntry objects, per Dictionary instance


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
    )


@dataclass(frozen=True)
class TelemetryOptions:
    """Runtime tracing/metrics — OFF by default even when the ``telemetry`` extra is installed;
    ``enabled`` is the actual opt-in switch. ``sample_hot_path``
    bounds the cost of instrumenting the per-tick hit-test path (0.0 = never sample it)."""

    enabled: bool = False
    export_dir: str | None = None  # None = paths.cache_dir() / "telemetry"
    sample_hot_path: float = 0.0  # [0.0, 1.0]


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
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() in ("true", "1"):
        enabled = False
    return TelemetryOptions(
        enabled=enabled,
        export_dir=d.get("export_dir", defaults.export_dir),
        sample_hot_path=float(d.get("sample_hot_path", defaults.sample_hot_path)),
    )

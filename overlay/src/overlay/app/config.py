"""Persistent overlay settings — a small TOML file so you don't re-type ``--dict``/``--freq`` etc.

Lives in its **own** platform-native config dir (``paths.config_dir()`` →
``%LOCALAPPDATA%\\saitenka\\overlay.toml`` on Windows, ``~/.config/saitenka/overlay.toml`` on
macOS/Linux), separate from mpv's config and the animecards rig — the overlay is an independent tool
and shouldn't have its settings parsed by mpv's own config loader. Precedence: built-in defaults <
this file < explicit CLI flags. Point elsewhere with ``$SAITENKA_CONFIG`` or ``--config``.

The dictionary **cache** (built SQLite indexes) is separate and already persists between runs under
``paths.cache_dir()/dicts/`` keyed by each zip's mtime+size — so dicts are indexed once and reused,
and only re-built when the zip changes.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path

from overlay.app import paths

CONFIG_HOME = paths.config_dir()
DEFAULT_PATH = CONFIG_HOME / "overlay.toml"
DATA_HOME = paths.data_dir()


def config_path(override: str | os.PathLike | None = None) -> Path:
    """Resolved config path: explicit override > $SAITENKA_CONFIG > default."""
    p = override or os.environ.get("SAITENKA_CONFIG") or DEFAULT_PATH
    return Path(p).expanduser()


def dicts_data_dir() -> Path:
    """Where relocated dictionaries live: ``paths.data_dir()/dicts`` (``%LOCALAPPDATA%\\saitenka\\dicts``
    on Windows, ``~/.local/share/saitenka/dicts`` on macOS/Linux) — NOT a TCC-protected folder, so a
    GUI-launched (plugin-mode) mpv can read them without a macOS consent prompt."""
    return DATA_HOME / "dicts"


def protected_dirs() -> list[Path]:
    """macOS TCC-protected user folders — access from a GUI app triggers a consent prompt."""
    home = Path.home()
    return [home / "Documents", home / "Desktop", home / "Downloads"]


def is_protected(path: str | os.PathLike) -> bool:
    """True if ``path`` is inside a TCC-protected folder (Documents/Desktop/Downloads)."""
    p = Path(str(path)).expanduser()
    return any(p == d or d in p.parents for d in protected_dirs())


def load_config(override: str | os.PathLike | None = None) -> dict:
    """Parse the TOML config, or return ``{}`` if it doesn't exist / can't be read."""
    p = config_path(override)
    if not p.exists():
        return {}
    try:
        with open(p, "rb") as f:
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
    mine_all_key: str = "Shift+m"
    translate_key: str = "t"
    preview_key: str = "p"
    sub_prev_key: str = "Alt+LEFT"
    sub_next_key: str = "Alt+RIGHT"
    sub_replay_key: str = "Alt+DOWN"


@dataclass(frozen=True)
class TooltipOptions:
    """Tooltip geometry + hover feel."""

    sub_size: int | None = None  # subtitle font override (None = scale to video)
    bottom_margin_frac: float = 0.06
    tip_max_frac: float = 0.6  # tooltip viewport ≤ this fraction of the video height
    pause_on_tooltip: bool = False
    scan_delay: float = 0.25  # dwell before a nested scan popup opens
    hover_switch_delay: float = 0.15  # dwell before the tooltip switches to a NEW word


@dataclass(frozen=True)
class MiningOptions:
    """Mining-flow behaviour (the Anki client/deck config stays in anki.MineConfig)."""

    play_audio: bool = True


@dataclass(frozen=True)
class TranslationOptions:
    """EN-translation reveal behaviour."""

    auto_translate: bool = False


@dataclass(frozen=True)
class ReaderOptions:
    """All Reader knobs, grouped by concern."""

    keys: KeyOptions = KeyOptions()
    tooltip: TooltipOptions = TooltipOptions()
    mining: MiningOptions = MiningOptions()
    translation: TranslationOptions = TranslationOptions()
    prefetch: bool = True
    resync: bool = True  # auto-resync jimaku-sourced subs via alass/ffsubsync
    overlay_id_base: int = 1  # shift physical mpv overlay ids to coexist with other scripts

    def with_overrides(self, **kw) -> ReaderOptions:
        """Route flat legacy kwargs (``mine_key=…``, ``tip_max_frac=…``) onto the right group.
        Unknown names raise TypeError so typos stay loud."""
        key_names = {f.name for f in fields(KeyOptions)}
        tip_names = {f.name for f in fields(TooltipOptions)}
        mine_names = {f.name for f in fields(MiningOptions)}
        trans_names = {f.name for f in fields(TranslationOptions)}
        keys, tooltip = self.keys, self.tooltip
        mining, translation = self.mining, self.translation
        prefetch = self.prefetch
        resync = self.resync
        overlay_id_base = self.overlay_id_base
        for name, value in kw.items():
            if name == "prefetch":
                prefetch = bool(value)
            elif name == "resync":
                resync = bool(value)
            elif name == "overlay_id_base":
                overlay_id_base = int(value)
            elif name in key_names:
                keys = replace(keys, **{name: value})
            elif name in tip_names:
                tooltip = replace(tooltip, **{name: value})
            elif name in mine_names:
                mining = replace(mining, **{name: value})
            elif name in trans_names:
                translation = replace(translation, **{name: value})
            else:
                raise TypeError(f"unknown Reader option: {name!r}")
        return ReaderOptions(
            keys=keys,
            tooltip=tooltip,
            mining=mining,
            translation=translation,
            prefetch=prefetch,
            resync=resync,
            overlay_id_base=overlay_id_base,
        )

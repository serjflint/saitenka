"""``saitenka config`` — interactive editor for ``overlay.toml``.

Wires three existing pieces and adds nothing new to the write path:

* **Schema (SSOT):** the frozen :mod:`~overlay.app.config` dataclasses. :func:`catalog` introspects them
  once — field type → prompt kind, built-in default, and the machine-readable ``help`` metadata — so a
  new knob is one field in ``config.py``, never a second list here. (This introspection is the seam #254
  profiles build on.)
* **Prompts:** :mod:`~overlay.app.prompt` (``confirm``/``select``/``text`` — questionary, isatty-gated,
  numbered fallback). This module only maps a field type onto the right primitive and coerces the reply.
* **Write:** :func:`~overlay.app.init_wizard.write_config` (tomlkit round-trip — preserves comments and
  every untouched key, timestamped ``.bak``, atomic). The editor builds a ``proposal`` dict and hands it over.

Default precedence per option: the **current ``overlay.toml`` value** (or an already-made edit this
session) wins over the built-in default — that's what's offered as the prompt default.
"""

from __future__ import annotations

import types
import typing
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any, Literal, Union, get_args, get_origin

from overlay.app import prompt
from overlay.app.config import (
    DictDbOptions,
    KeyOptions,
    MiningOptions,
    PanelOptions,
    PerfOptions,
    StatsOptions,
    TelemetryOptions,
    TooltipOptions,
    TranslationOptions,
    load_config,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

Kind = Literal["bool", "select", "int", "float", "str"]


@dataclass(frozen=True)
class OptionSpec:
    """One editable option: where it lives in the TOML doc, how to prompt for it, and its built-in default.

    ``toml_path`` is the dotted address the *runtime* reads/writes (e.g. ``("mine", "key")`` or
    ``("tip_height",)``) — deliberately NOT the dataclass field name, which can differ (``tip_height`` is
    ``TooltipOptions.tip_max_frac``; ``ui_scale`` is ``PanelOptions.scale``)."""

    section: str
    label: str
    toml_path: tuple[str, ...]
    kind: Kind
    default: object
    help: str = ""
    choices: tuple[str, ...] = ()
    optional: bool = False


def _classify(annotation: object) -> tuple[Kind, tuple[str, ...], bool]:
    """A resolved field annotation → (prompt kind, select-choices, is-optional). Unwraps ``X | None``
    to ``optional=True`` and reads ``Literal[...]`` choice sets via :func:`typing.get_args`."""
    optional = False
    origin = get_origin(annotation)
    if origin in {Union, types.UnionType}:
        args = [a for a in get_args(annotation) if a is not type(None)]
        optional = len(args) < len(get_args(annotation))
        annotation = args[0]
        origin = get_origin(annotation)
    if origin is Literal:
        return "select", tuple(str(a) for a in get_args(annotation)), optional
    if annotation is bool:
        return "bool", (), optional
    if annotation is int:
        return "int", (), optional
    if annotation is float:
        return "float", (), optional
    return "str", (), optional


def _spec(section: str, dc: Any, name: str, toml_path: tuple[str, ...]) -> OptionSpec:
    """Build a spec for dataclass field ``name`` — pulling type, built-in default, and ``help`` metadata
    straight from the frozen dataclass (the SSOT), bound to an explicit TOML address."""
    field = next(f for f in fields(dc) if f.name == name)
    annotation = typing.get_type_hints(dc)[name]
    kind, choices, optional = _classify(annotation)
    return OptionSpec(
        section=section,
        label=toml_path[-1],
        toml_path=toml_path,
        kind=kind,
        default=field.default,
        help=str(field.metadata.get("help", "")),
        choices=choices,
        optional=optional,
    )


# TOML addresses for the reader-option fields that DON'T map to their own name at top level. Everything
# else in a reader group is a flat top-level key named after the field.
_KEY_TOML = {
    "mine_key": ("mine", "key"),
    "mine_video_key": ("mine", "video_key"),
    "mine_all_key": ("mine", "all_key"),
    "preview_key": ("mine", "preview_key"),
}
_FLAT_KEYS = [
    "translate_key",
    "overlay_toggle_key",
    "hover_pause_key",
    "subtitle_language_key",
    "subtitle_mark_jp_key",
    "bookmark_key",
    "sidebar_key",
    "analysis_key",
    "annotation_key",
    "help_key",
    "subtitle_retry_key",
    "sub_picker_key",
    "sub_prev_key",
    "sub_next_key",
    "sub_replay_key",
]
_TOOLTIP_FIELDS = [
    "tip_scale",
    "nested_max_frac",
    "pause_on_tooltip",
    "annotation_mode",
    "scan_delay",
    "hover_switch_delay",
    "hide_delay",
    "flash_secs",
    "panel_cache_max",
    "layout_engine",
    "render_cache",
    "mask_atlas",
    "render_cache_max_mb",
    "render_cache_min_height",
]
_PERF_FIELDS = [
    "poll_interval",
    "prefetch_workers",
    "prefetch_lookahead",
    "head_prefetch_lookahead",
    "head_prefetch_queue_max",
]


def _general_specs() -> list[OptionSpec]:
    """Top-level scalars read ad-hoc from the TOML (outside the dataclasses) — folded into the schema so
    the editor covers them too. Defaults are pinned to the dataclass SSOT where one exists (no drift)."""
    return [
        OptionSpec(
            "general",
            "slang",
            ("slang",),
            "str",
            "ja,jpn,jp",
            "Subtitle language codes treated as Japanese (comma-separated, priority order).",
        ),
        OptionSpec(
            "general",
            "tip_height",
            ("tip_height",),
            "float",
            TooltipOptions().tip_max_frac,
            "Tooltip max height as a fraction of the video (0–1).",
        ),
        OptionSpec(
            "general",
            "ui_scale",
            ("ui_scale",),
            "float",
            PanelOptions().scale,
            "Scale for the help/sidebar/analysis panels.",
        ),
        OptionSpec(
            "general",
            "mpv_path",
            ("mpv_path",),
            "str",
            None,
            "Explicit path to the mpv binary (blank = auto-discover).",
            optional=True,
        ),
        OptionSpec(
            "general",
            "overlay_id_base",
            ("overlay_id_base",),
            "int",
            1,
            "Base mpv overlay id (shift to coexist with other scripts).",
        ),
        OptionSpec(
            "general",
            "resync_timeout",
            ("resync_timeout",),
            "int",
            300,
            "Subtitle-resync subprocess timeout (seconds).",
        ),
        OptionSpec(
            "general",
            "resync_split_penalty",
            ("resync_split_penalty",),
            "float",
            None,
            "alass --split-penalty (0–1000; lower = more willing to split). Blank = alass default.",
            optional=True,
        ),
    ]


def _build_catalog() -> list[OptionSpec]:
    specs = _general_specs()
    specs += [
        _spec("keys", KeyOptions, n, _KEY_TOML.get(n, (n,))) for n in [*_FLAT_KEYS, *_KEY_TOML]
    ]
    specs += [_spec("tooltip", TooltipOptions, n, (n,)) for n in _TOOLTIP_FIELDS]
    specs += [
        _spec("mining", MiningOptions, n, (n,))
        for n in ("max_bulk", "anki_ok_ttl", "anki_ping_timeout")
    ]
    specs.append(_spec("mining", MiningOptions, "show_preview", ("mine", "preview")))
    specs.append(_spec("translation", TranslationOptions, "auto_translate", ("auto_translate",)))
    specs += [_spec("perf", PerfOptions, n, (n,)) for n in _PERF_FIELDS]
    specs += [_spec("stats", StatsOptions, n, ("stats", n)) for n in ("enabled", "summary")]
    specs += [
        _spec("dictdb", DictDbOptions, n, ("dictdb", n))
        for n in ("mmap_size", "cache_size_kib", "dexie_chunk_size", "entry_cache_max")
    ]
    specs += [
        _spec("telemetry", TelemetryOptions, n, ("telemetry", n))
        for n in ("enabled", "export_dir", "sample_hot_path")
    ]
    return specs


_CATALOG: list[OptionSpec] | None = None


def catalog() -> list[OptionSpec]:
    """The full editable schema, derived once from the ``config.py`` dataclasses."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = _build_catalog()
    return _CATALOG


def sections() -> list[str]:
    """Section names in catalog order (deduped)."""
    seen: dict[str, None] = {}
    for spec in catalog():
        seen.setdefault(spec.section, None)
    return list(seen)


def options_in(section: str) -> list[OptionSpec]:
    return [s for s in catalog() if s.section == section]


# --- value plumbing (pure, unit-tested) ----------------------------------------------------------


def _get_path(doc: Mapping, path: tuple[str, ...]) -> object:
    cur: object = doc
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return _UNSET
        cur = cur[key]
    return cur


_UNSET = object()


def current_default(spec: OptionSpec, cfg: Mapping) -> object:
    """Default to offer: the value already in ``cfg`` (a prior edit or the on-disk config) if set, else
    the built-in default — the explicit precedence the feature asks for."""
    got = _get_path(cfg, spec.toml_path)
    return spec.default if got is _UNSET else got


def coerce(spec: OptionSpec, raw: str) -> object:
    """A raw prompt string → the typed value, validated. Raises ``ValueError`` on a bad number or an
    out-of-set ``select`` choice so the caller can re-offer instead of writing garbage."""
    text = raw.strip()
    if spec.optional and text == "":
        return None
    if spec.kind == "select":
        if text not in spec.choices:
            raise ValueError(f"{spec.label}: {raw!r} not one of {', '.join(spec.choices)}")
        return text
    if spec.kind == "int":
        return int(text)
    if spec.kind == "float":
        return float(text)
    return raw


def _to_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def apply_edit(proposal: dict, spec: OptionSpec, value: object) -> None:
    """Set ``value`` at ``spec.toml_path`` in the (nested) proposal, creating tables as needed."""
    table = proposal
    for key in spec.toml_path[:-1]:
        table = table.setdefault(key, {})
    table[spec.toml_path[-1]] = value


def prompt_value(spec: OptionSpec, current: object) -> object:
    """Ask for ``spec`` via the right :mod:`prompt` primitive, offering ``current`` as the default.
    Returns the coerced value; an invalid number / out-of-set choice keeps ``current`` (warned, no crash)."""
    message = f"{spec.label}" + (f" — {spec.help}" if spec.help else "")
    if spec.kind == "bool":
        return prompt.confirm(message, default=bool(current))
    if spec.kind == "select":
        picked = prompt.select(message, list(spec.choices), default=_to_str(current))
        try:
            return coerce(spec, picked)
        except ValueError as exc:
            print(f"  keeping {_to_str(current)} ({exc})")
            return current
    raw = prompt.text(message, default=_to_str(current))
    try:
        return coerce(spec, raw)
    except ValueError as exc:
        print(f"  keeping {_to_str(current)} ({exc})")
        return current


# --- interactive session -------------------------------------------------------------------------

_DONE = "· done (write changes)"
_BACK = "· back"


def run_editor(dest: Path | None = None) -> int:  # pragma: no cover — interactive loop, wired below
    """Interactive edit session: pick a section → an option → a typed value, repeat, then write."""
    from overlay.app.init_wizard import write_config

    cfg = dict(load_config(dest))
    proposal: dict = {}
    print("saitenka config — edit overlay.toml (Esc/blank to finish)")
    while True:
        section = prompt.select("Section", [*sections(), _DONE], default=_DONE)
        if section in {_DONE, ""}:
            break
        specs = options_in(section)
        label = prompt.select(section, [s.label for s in specs] + [_BACK], default=_BACK)
        if label == _BACK:
            continue
        spec = next(s for s in specs if s.label == label)
        merged = {**cfg, **proposal}
        value = prompt_value(spec, current_default(spec, merged))
        apply_edit(proposal, spec, value)
    if not proposal:
        print("No changes.")
        return 0
    backup = write_config(proposal, prompt.confirm, dest)
    if backup:
        print(f"backed up existing config → {backup}")
    return 0

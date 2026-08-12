"""``saitenka config`` editor (#257): the schema introspection, type coercion, default precedence, and
the tomlkit round-trip — driven through the ``prompt`` seam with a scripted fake, never a real TTY.
"""

import sys
from dataclasses import fields

import pytest

from saitenka.app import config_editor as ce
from saitenka.app import prompt
from saitenka.app.config import DictDbOptions, MiningOptions, TooltipOptions

# --- schema introspection (the #254 baton): types + choices + help come from config.py, not a 2nd list ---


def test_catalog_derives_kind_and_choices_from_the_dataclass_types():
    by = {s.label: s for s in ce.catalog()}
    assert by["layout_engine"].kind == "select"
    assert by["layout_engine"].choices == ("default", "taffy")
    assert by["annotation_mode"].choices == ("full", "hover")
    assert by["render_cache"].kind == "bool"
    assert by["max_bulk"].kind == "int"
    assert by["anki_ok_ttl"].kind == "float"


def test_catalog_defaults_track_the_config_ssot():
    by = {(*s.toml_path,): s for s in ce.catalog()}
    assert by["layout_engine",].default == TooltipOptions().layout_engine
    assert by["max_bulk",].default == MiningOptions().max_bulk
    assert by["dictdb", "mmap_size"].default == DictDbOptions().mmap_size


def test_catalog_carries_machine_readable_help_from_field_metadata():
    render_cache = next(s for s in ce.catalog() if s.label == "render_cache")
    assert render_cache.help  # non-empty — the editor surfaces it next to the prompt


def test_optional_field_is_detected():
    export_dir = next(s for s in ce.catalog() if s.toml_path == ("telemetry", "export_dir"))
    assert export_dir.optional is True


def test_renamed_and_tabled_toml_addresses():
    by = {s.label: s for s in ce.catalog()}
    # mine keybinds live under [mine], panel scale is `ui_scale`, tooltip height is `tip_height`
    assert by["key"].toml_path == ("mine", "key")
    assert by["ui_scale"].toml_path == ("ui_scale",)
    assert by["tip_height"].toml_path == ("tip_height",)


# --- default precedence: current value wins over the built-in default -----------------------------


def test_current_default_prefers_the_config_value_then_falls_back():
    spec = next(s for s in ce.catalog() if s.label == "tip_scale")
    assert ce.current_default(spec, {"tip_scale": 1.5}) == 1.5
    assert ce.current_default(spec, {}) == spec.default  # unset → built-in


def test_current_default_reads_a_nested_table_value():
    spec = next(s for s in ce.catalog() if s.toml_path == ("dictdb", "mmap_size"))
    assert ce.current_default(spec, {"dictdb": {"mmap_size": 42}}) == 42
    assert ce.current_default(spec, {"dictdb": {}}) == spec.default


# --- coercion: bool via confirm, Literal rejects out-of-set, numeric parse+validate ---------------


def test_coerce_numbers_and_optionals():
    max_bulk = next(s for s in ce.catalog() if s.label == "max_bulk")
    assert ce.coerce(max_bulk, "20") == 20
    ttl = next(s for s in ce.catalog() if s.label == "anki_ok_ttl")
    assert ce.coerce(ttl, "2.5") == 2.5
    mpv = next(s for s in ce.catalog() if s.label == "mpv_path")
    assert ce.coerce(mpv, "  ") is None  # optional + blank → unset


def test_coerce_select_rejects_out_of_set():
    layout = next(s for s in ce.catalog() if s.label == "layout_engine")
    with pytest.raises(ValueError, match="not one of"):
        ce.coerce(layout, "wgpu")
    assert ce.coerce(layout, "taffy") == "taffy"


def test_coerce_bad_int_raises():
    max_bulk = next(s for s in ce.catalog() if s.label == "max_bulk")
    with pytest.raises(ValueError):
        ce.coerce(max_bulk, "lots")


def test_apply_edit_builds_nested_tables():
    proposal: dict = {}
    ce.apply_edit(proposal, next(s for s in ce.catalog() if s.label == "key"), "Ctrl+n")
    ce.apply_edit(proposal, next(s for s in ce.catalog() if s.label == "tip_scale"), 2.0)
    assert proposal == {"mine": {"key": "Ctrl+n"}, "tip_scale": 2.0}


# --- prompt_value: right primitive per kind; an invalid reply keeps the current value -------------


def test_prompt_value_bool_uses_confirm(monkeypatch):
    spec = next(s for s in ce.catalog() if s.label == "render_cache")
    seen = {}

    def _confirm(_message, *, default):
        seen["default"] = default
        return False

    monkeypatch.setattr(prompt, "confirm", _confirm)
    assert ce.prompt_value(spec, current=True) is False
    assert seen["default"] is True  # current offered as the default


def test_prompt_value_select_returns_the_choice(monkeypatch):
    spec = next(s for s in ce.catalog() if s.label == "layout_engine")
    monkeypatch.setattr(prompt, "select", lambda *_a, **_k: "taffy")
    assert ce.prompt_value(spec, "default") == "taffy"


def test_prompt_value_keeps_current_on_bad_number(monkeypatch):
    spec = next(s for s in ce.catalog() if s.label == "max_bulk")
    monkeypatch.setattr(prompt, "text", lambda *_a, **_k: "not-a-number")
    assert ce.prompt_value(spec, 12) == 12  # rejected, unchanged


# --- end-to-end: a scripted session round-trips through write_config, preserving comments ---------


def _script(monkeypatch, *, selects: list[str], texts: list[str]) -> None:
    """Drive the interactive loop deterministically: each ``prompt.*`` pops its next scripted answer."""
    sel, txt = iter(selects), iter(texts)
    monkeypatch.setattr(prompt, "select", lambda *_a, **_k: next(sel))
    monkeypatch.setattr(prompt, "text", lambda *_a, **_k: next(txt))
    monkeypatch.setattr(prompt, "confirm", lambda *_a, **_k: True)


def test_run_editor_round_trips_and_preserves_untouched_keys_and_comments(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text(
        '# my hand-written config\nslang = "ja,jpn,jp"  # keep me\n\n[mine]\ndeck = "Saitenka::Mining"\n',
        encoding="utf-8",
    )
    _script(
        monkeypatch,
        selects=["tooltip", "tip_scale", "keys", "key", ce._DONE],
        texts=["2.5", "Ctrl+n"],
    )
    assert ce.run_editor(cfg) == 0

    import tomllib

    written = cfg.read_text(encoding="utf-8")
    doc = tomllib.loads(written)
    assert doc["tip_scale"] == 2.5  # the edit landed
    assert doc["mine"]["key"] == "Ctrl+n"
    assert doc["slang"] == "ja,jpn,jp" and doc["mine"]["deck"] == "Saitenka::Mining"  # untouched
    assert "# my hand-written config" in written and "# keep me" in written  # comments survive


def test_run_editor_writes_nothing_when_no_edits(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    _script(monkeypatch, selects=[ce._DONE], texts=[])
    assert ce.run_editor(cfg) == 0
    assert not cfg.exists()  # declined at the section menu → no file created


# --- drift guard: the catalog is derived from fields(); a new config.py knob can't go silently missing --


@pytest.mark.parametrize(("section", "dc", "_table"), ce._DC_SECTIONS)
def test_every_group_field_is_exposed_or_explicitly_exempt(section, dc, _table):
    """Completeness per group: every dataclass field is either editable in `saitenka config` or listed in
    the documented EXEMPT set — so adding a field to config.py without a decision fails the gate here."""
    covered = {s.field_name for s in ce.catalog() if s.section == section and s.field_name}
    exempt = ce._EXEMPT.get(section, frozenset())
    all_fields = {f.name for f in fields(dc)}
    assert covered | exempt == all_fields  # nothing unaccounted-for
    assert not (covered & exempt)  # a field is exposed XOR exempt, never both
    assert exempt <= all_fields  # no stale name lingering in EXEMPT after a rename


def test_newly_wired_toggles_are_reachable():
    """P1: `no_audio_play` / `prefetch` / `resync` are genuinely config-wired and must be editable."""
    labels = {s.label for s in ce.catalog()}
    assert {"no_audio_play", "prefetch", "resync"} <= labels


def test_non_config_wired_fields_stay_unexposed():
    """The runtime never reads these from overlay.toml (its dataclass is built without them), so exposing
    them would write a key the user mistakes for a change. They must remain absent from the catalog."""
    paths = {s.toml_path for s in ce.catalog()}
    assert ("sub_size",) not in paths
    assert ("crisp_upscale",) not in paths
    assert ("token_cache_max",) not in paths
    assert ("sub_picker_key",) not in paths


# --- P1: editing one key in a table must not revert its untouched siblings (deep-merge, not shallow) ---


def test_editing_one_table_key_keeps_siblings_on_disk_default():
    """Repro of the shallow-merge data-loss: after editing `[mine].key`, the sibling `[mine].preview`
    still defaults to its on-disk value, not the built-in — so accepting the shown default can't revert it."""
    key = next(s for s in ce.catalog() if s.toml_path == ("mine", "key"))
    preview = next(s for s in ce.catalog() if s.toml_path == ("mine", "preview"))
    cfg = {"mine": {"key": "Ctrl+m", "preview": False}}
    proposal: dict = {}
    ce.apply_edit(proposal, key, "Ctrl+n")
    merged = ce._deep_merge(cfg, proposal)
    assert ce.current_default(preview, merged) is False  # on-disk, NOT the built-in True
    assert ce.current_default(key, merged) == "Ctrl+n"  # the edit is visible too


def test_run_editor_edits_two_table_keys_and_preserves_the_third(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text(
        "[dictdb]\nmmap_size = 111\ncache_size_kib = 222\ndexie_chunk_size = 333\n",
        encoding="utf-8",
    )
    _script(
        monkeypatch,
        selects=["dictdb", "mmap_size", "dictdb", "cache_size_kib", ce._DONE],
        texts=["999", "888"],
    )
    assert ce.run_editor(cfg) == 0
    import tomllib

    doc = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert doc["dictdb"]["mmap_size"] == 999 and doc["dictdb"]["cache_size_kib"] == 888
    assert doc["dictdb"]["dexie_chunk_size"] == 333  # untouched sibling survives the two edits


def test_coerce_str_strips_surrounding_whitespace():
    slang = next(s for s in ce.catalog() if s.label == "slang")
    assert ce.coerce(slang, "  ja,en  ") == "ja,en"


def test_run_editor_non_tty_makes_no_changes(tmp_path, monkeypatch):
    """A console-less run: every prompt returns its default, so the section menu resolves to `done` and
    nothing is written — the degrade-don't-block contract, no real TTY needed."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    cfg = tmp_path / "overlay.toml"
    assert ce.run_editor(cfg) == 0
    assert not cfg.exists()

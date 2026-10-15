"""``saitenka config`` editor (#257): the schema introspection, type coercion, default precedence, and
the tomlkit round-trip — driven through the ``prompt`` seam with a scripted fake, never a real TTY.
"""

import sys
from dataclasses import fields

import pytest

from saitenka.app import config_editor as ce
from saitenka.app import prompt

# --- schema introspection (the #254 baton): types + choices + help come from config.py, not a 2nd list ---


# --- default precedence: current value wins over the built-in default -----------------------------


# --- coercion: bool via confirm, Literal rejects out-of-set, numeric parse+validate ---------------


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
    assert ce.prompt_value(spec, 37) == 37  # rejected, non-default config preserved


# --- end-to-end: a scripted session round-trips through write_config, preserving comments ---------


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


def test_run_editor_non_tty_makes_no_changes(tmp_path, monkeypatch):
    """A console-less run: every prompt returns its default, so the section menu resolves to `done` and
    nothing is written — the degrade-don't-block contract, no real TTY needed."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    cfg = tmp_path / "overlay.toml"
    assert ce.run_editor(cfg) == 0
    assert not cfg.exists()

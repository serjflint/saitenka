"""``saitenka config`` editor (#257): the schema introspection, type coercion, default precedence, and
the tomlkit round-trip — driven through the ``prompt`` seam with a scripted fake, never a real TTY.
"""

import sys
from dataclasses import fields

import pytest

from saitenka.app import config_editor as ce
from saitenka.app import prompt


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


def test_run_editor_accepts_a_sibling_current_default(tmp_path, monkeypatch):
    import tomllib

    cfg = tmp_path / "overlay.toml"
    cfg.write_text("[dictdb]\nmmap_size = 111\ncache_size_kib = 222\ndexie_chunk_size = 333\n")
    _script(
        monkeypatch, selects=["dictdb", "mmap_size", "dictdb", "cache_size_kib", ce._DONE], texts=[]
    )
    replies = iter(["999", None])

    def text(*_args, default, **_kwargs):
        answer = next(replies)
        return default if answer is None else answer

    monkeypatch.setattr(prompt, "text", text)

    assert ce.run_editor(cfg) == 0
    assert tomllib.loads(cfg.read_text()) == {
        "dictdb": {"mmap_size": 999, "cache_size_kib": 222, "dexie_chunk_size": 333}
    }


def test_run_editor_selects_confirms_and_rejects_bad_numbers(tmp_path, monkeypatch):
    import tomllib

    cfg = tmp_path / "overlay.toml"
    cfg.write_text('layout_engine = "default"\nrender_cache = true\nmax_bulk = 37\n')
    _script(
        monkeypatch,
        selects=[
            "tooltip",
            "layout_engine",
            "taffy",
            "tooltip",
            "render_cache",
            "mining",
            "max_bulk",
            ce._DONE,
        ],
        texts=["not-a-number"],
    )
    confirmations = iter([False, True])
    monkeypatch.setattr(prompt, "confirm", lambda *_args, **_kwargs: next(confirmations))

    assert ce.run_editor(cfg) == 0
    assert tomllib.loads(cfg.read_text()) == {
        "layout_engine": "taffy",
        "render_cache": False,
        "max_bulk": 37,
    }


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

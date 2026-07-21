"""Stage 15: `saitenka-overlay import-yomitan` — settings export → our config.

Reads a Yomitan SETTINGS export (a small file — NOT the multi-GB collection export): the enabled
dictionary list IN ORDER + per-dict enabled/priority, the mouse scan modifier, popup scale. Maps
onto our config: dict order → ``dicts``; matched zips are bucketed into ``freq`` / ``pitch`` by their
CONTENT (the term_meta mode), the way Yomitan classifies them — never by name. Zip locations come
ONLY from explicit ``--scan-dir DIR`` (opt-in, validated as Yomitan-format via ``index.json``);
titles matched against those dirs, unmatched titles reported. No personal-folder auto-scan.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from overlay.app import yomitan_import as yi


def _make_settings(dicts, *, scan_mod="alt", popup_scale=1.0):
    """Build a minimal Yomitan settings export around a list of (name, enabled, priority)."""
    return {
        "version": 1,
        "options": {
            "profiles": [
                {
                    "options": {
                        "dictionaries": [
                            {"name": n, "enabled": e, "priority": p} for (n, e, p) in dicts
                        ],
                        "scanning": {"inputs": [{"include": scan_mod, "types": {"mouse": True}}]},
                        "general": {"popupScale": popup_scale},
                    }
                }
            ]
        },
    }


def _write(tmp_path, obj):
    p = tmp_path / "yomitan-settings.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def _make_dict_zip(path, kind, *, title=None):
    """A minimal Yomitan dictionary zip. ``kind``: ``'dict'`` ships a term_bank glossary, ``'freq'``
    / ``'pitch'`` ship a term_meta bank whose entries carry that mode — exactly what the classifier
    inspects."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"format": 3, "title": title or path.stem}))
        if kind == "dict":
            zf.writestr("term_bank_1.json", json.dumps([["猫", "ねこ", "", "", 0, ["cat"], 1, ""]]))
        elif kind == "freq":
            zf.writestr("term_meta_bank_1.json", json.dumps([["猫", "freq", 123]]))
        elif kind == "pitch":
            zf.writestr(
                "term_meta_bank_1.json",
                json.dumps([["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 0}]}]]),
            )
    return path


def test_parse_orders_enabled_dicts_by_priority(tmp_path):
    obj = _make_settings(
        [("Bilingual", True, 10), ("MonoA", True, 20), ("disabled-dict", False, 99)]
    )
    settings = yi.parse_settings(_write(tmp_path, obj))
    names = [d.name for d in settings.dictionaries if d.enabled]
    # higher priority first (Yomitan convention), disabled excluded from the enabled view
    assert names == ["MonoA", "Bilingual"]
    assert "disabled-dict" not in names


def test_parse_reads_scan_modifier_and_scale(tmp_path):
    obj = _make_settings([("X", True, 0)], scan_mod="ctrl", popup_scale=1.5)
    settings = yi.parse_settings(_write(tmp_path, obj))
    assert settings.scan_modifier == "ctrl"
    assert settings.popup_scale == pytest.approx(1.5)


def test_classify_by_content(tmp_path):
    # classification looks at the banks, not the filename/title
    assert yi.classify_zip(_make_dict_zip(tmp_path / "a.zip", "dict")) == "dict"
    assert yi.classify_zip(_make_dict_zip(tmp_path / "b.zip", "freq")) == "freq"
    assert yi.classify_zip(_make_dict_zip(tmp_path / "c.zip", "pitch")) == "pitch"
    # unreadable / missing / non-dictionary zip → safe default
    assert yi.classify_zip(tmp_path / "missing.zip") == "dict"


def test_map_to_config_splits_lists_by_content(tmp_path):
    obj = _make_settings(
        [
            ("Bilingual", True, 30),
            ("MonoA", True, 20),
            ("FreqA", True, 10),
            ("PitchA", True, 5),
        ]
    )
    settings = yi.parse_settings(_write(tmp_path, obj))
    matches = {
        "Bilingual": str(_make_dict_zip(tmp_path / "bi.zip", "dict")),
        "MonoA": str(_make_dict_zip(tmp_path / "mono.zip", "dict")),
        "FreqA": str(_make_dict_zip(tmp_path / "freq.zip", "freq")),
        "PitchA": str(_make_dict_zip(tmp_path / "pitch.zip", "pitch")),
    }
    cfg = yi.to_config(settings, matches)
    assert cfg["dicts"] == [matches["Bilingual"], matches["MonoA"]]  # order preserved among defn dicts
    assert cfg["freq"] == [matches["FreqA"]]
    assert cfg["pitch"] == [matches["PitchA"]]


def test_unmatched_titles_default_to_dicts(tmp_path):
    # a name alone can't be typed → everything falls into ``dicts`` for the user to re-bucket
    obj = _make_settings([("MonoA", True, 20), ("FreqA", True, 10)])
    settings = yi.parse_settings(_write(tmp_path, obj))
    cfg = yi.to_config(settings, matches={})
    assert cfg["dicts"] == ["MonoA", "FreqA"]
    assert "freq" not in cfg and "pitch" not in cfg


def test_scan_dir_matches_titles_and_reports_missing(tmp_path):
    # one zip present in the scan dir, validated by index.json title; the other title unmatched
    scan = tmp_path / "zips"
    scan.mkdir()
    zpath = _make_dict_zip(scan / "some-file.zip", "dict", title="MonoB")
    matches, missing = yi.match_scan_dirs(["MonoB", "Bilingual"], [scan])
    assert matches["MonoB"] == str(zpath)
    assert missing == ["Bilingual"]


def test_scan_dir_ignores_non_yomitan_zip(tmp_path):
    scan = tmp_path / "zips"
    scan.mkdir()
    bad = scan / "random.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("hello.txt", "not a dictionary")
    matches, missing = yi.match_scan_dirs(["MonoB"], [scan])
    assert matches == {}
    assert missing == ["MonoB"]


def test_to_config_uses_matched_paths_when_available(tmp_path):
    obj = _make_settings([("MonoB", True, 20), ("FreqA", True, 10)])
    settings = yi.parse_settings(_write(tmp_path, obj))
    matches = {
        "MonoB": str(_make_dict_zip(tmp_path / "mono.zip", "dict")),
        "FreqA": str(_make_dict_zip(tmp_path / "freq.zip", "freq")),
    }
    cfg = yi.to_config(settings, matches)
    assert cfg["dicts"] == [matches["MonoB"]]
    assert cfg["freq"] == [matches["FreqA"]]


def test_parse_rejects_oversized_file(tmp_path, monkeypatch):
    p = tmp_path / "huge.json"
    p.write_text("{}")
    monkeypatch.setattr(yi, "MAX_SETTINGS_BYTES", 1)  # pretend it's over the cap
    with pytest.raises(yi.YomitanImportError):
        yi.parse_settings(p)


def test_real_export_parses_if_present():
    """Smoke: if a real export is on disk, it must parse without error (not a hard dep)."""
    from pathlib import Path

    candidates = sorted(
        Path.home().glob("Documents/Japanese/yomitan/yomitan-settings*.json"), reverse=True
    )
    if not candidates:
        pytest.skip("no real Yomitan export on this machine")
    settings = yi.parse_settings(candidates[0])
    assert settings.dictionaries  # at least one dict parsed

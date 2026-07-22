"""Stage 15: `saitenka-overlay import-settings` — settings export → our config.

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
        elif (
            kind == "pitch_with_headwords"
        ):  # e.g. NHK 2016: pitch term_meta AND headword term_bank
            zf.writestr("term_bank_1.json", json.dumps([["猫", "ねこ", "", "", 0, [], 1, ""]]))
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
    # a pitch dict that ALSO ships headword term_banks (NHK 2016) is still pitch — the term_meta mode
    # wins, so it lands in the `pitch` bucket and pitch accents render (regression: it was mis-filed as
    # a definition dict because it had a term_bank).
    assert yi.classify_zip(_make_dict_zip(tmp_path / "d.zip", "pitch_with_headwords")) == "pitch"
    # unreadable / missing / non-dictionary zip → safe default
    assert yi.classify_zip(tmp_path / "missing.zip") == "dict"


def test_classify_tolerates_wrong_crc_pitch(tmp_path, monkeypatch):
    """A pitch dict with a WRONG stored CRC-32 but intact deflate data — exactly what NHK 2016 ships —
    must still classify as ``pitch``. A CRC-strict read raises BadZipFile, which would mis-file it as a
    definition dict, so pitch accents never render (regression: NHK landed in ``dicts`` after import)."""
    import binascii

    real = binascii.crc32
    with monkeypatch.context() as m:  # force a bogus stored CRC on every entry as it's written
        m.setattr(zipfile, "crc32", lambda *a: (real(a[0]) ^ 0xFFFFFFFF) & 0xFFFFFFFF)
        z = _make_dict_zip(tmp_path / "nhk.zip", "pitch")
    # sanity: a strict read really does reject this zip (so the tolerant path is what saves it)
    with zipfile.ZipFile(z) as zf, pytest.raises(zipfile.BadZipFile):
        zf.read("term_meta_bank_1.json")
    assert yi.classify_zip(z) == "pitch"


def test_import_zips_buckets_titles_by_content_and_order(tmp_path):
    # importing builds into the consolidated DB and returns config TITLES bucketed by content, in order
    zips = [
        str(_make_dict_zip(tmp_path / "bi.zip", "dict", title="Bilingual")),
        str(_make_dict_zip(tmp_path / "mono.zip", "dict", title="MonoA")),
        str(_make_dict_zip(tmp_path / "freq.zip", "freq", title="FreqA")),
        str(_make_dict_zip(tmp_path / "pitch.zip", "pitch", title="PitchA")),
    ]
    cfg = yi.import_zips(zips, imported_at="2026-07-23T00:00:00")
    assert cfg["dicts"] == ["Bilingual", "MonoA"]  # order preserved among defn dicts
    assert cfg["freq"] == ["FreqA"]
    assert cfg["pitch"] == ["PitchA"]


def test_gather_yomitan_zips_expands_dirs_and_files(tmp_path):
    scan = tmp_path / "zips"
    scan.mkdir()
    a = _make_dict_zip(scan / "a.zip", "dict", title="A")
    _make_dict_zip(scan / "b.zip", "freq", title="B")
    with zipfile.ZipFile(scan / "junk.zip", "w") as zf:  # not a Yomitan dict → ignored
        zf.writestr("hello.txt", "x")
    loose = _make_dict_zip(tmp_path / "loose.zip", "pitch", title="C")
    found = yi.gather_yomitan_zips([str(scan), str(loose), str(a)])  # dir + file + dup
    assert str(scan / "junk.zip") not in found
    assert found.count(str(a)) == 1  # de-duplicated
    assert str(loose) in found


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

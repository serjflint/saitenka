"""Copy dicts out of TCC-protected folders and repoint the config by text substitution (so the
[known]/[mine]/[jimaku] tables and comments survive — there's no round-trip TOML writer)."""

from __future__ import annotations


from overlay.app import relocate
from overlay.app.config import is_protected


def test_is_protected_matches_documents(monkeypatch, tmp_path):
    home = tmp_path
    monkeypatch.setenv("HOME", str(home))
    assert is_protected("~/Documents/x/a.zip")
    assert is_protected(str(home / "Downloads" / "b.zip"))
    assert not is_protected("~/.local/share/saitenka/dicts/a.zip")


def test_repoint_text_preserves_tables_and_comments():
    text = 'dicts = [\n  "~/Documents/y/a.zip",\n]\n# a comment\n[mine]\ndeck = "D"\n'
    out = relocate.repoint_text(
        text, [("~/Documents/y/a.zip", "~/.local/share/saitenka/dicts/a.zip")]
    )
    assert "~/.local/share/saitenka/dicts/a.zip" in out
    assert "~/Documents/y/a.zip" not in out
    assert "# a comment" in out and "[mine]" in out  # untouched


def test_relocate_copies_protected_and_repoints(tmp_path, monkeypatch):
    home = tmp_path
    monkeypatch.setenv("HOME", str(home))
    # a protected source dict + a config pointing at it
    docs = home / "Documents" / "yomitan"
    docs.mkdir(parents=True)
    (docs / "a.zip").write_bytes(b"PKzipdata")
    cfg_dir = home / ".config" / "saitenka"
    cfg_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "overlay.toml"
    cfg_file.write_text('dicts = [\n  "~/Documents/yomitan/a.zip",\n]\n[mine]\ndeck = "D"\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg_file))

    dest = home / ".local" / "share" / "saitenka" / "dicts"
    mappings = relocate.relocate_dicts(dest_dir=dest)

    assert (dest / "a.zip").read_bytes() == b"PKzipdata"  # copied
    new_text = cfg_file.read_text()
    assert "~/.local/share/saitenka/dicts/a.zip" in new_text
    assert "~/Documents/yomitan/a.zip" not in new_text
    assert "[mine]" in new_text  # table preserved
    assert mappings == [("~/Documents/yomitan/a.zip", "~/.local/share/saitenka/dicts/a.zip")]


def test_relocate_noop_when_nothing_protected(tmp_path, monkeypatch):
    home = tmp_path
    monkeypatch.setenv("HOME", str(home))
    cfg_dir = home / ".config" / "saitenka"
    cfg_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "overlay.toml"
    cfg_file.write_text('dicts = [\n  "~/.local/share/saitenka/dicts/a.zip",\n]\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg_file))
    assert relocate.relocate_dicts(dest_dir=home / "d") == []


def test_import_from_dir_copies_and_configs(monkeypatch, tmp_path):
    """`copy-dicts <dir>`: Yomitan zips in a folder are copied into the data dir, classified, and
    added to the config; non-Yomitan zips are skipped."""
    import json
    import zipfile

    from overlay.app import relocate
    from overlay.app.config import load_config

    cfg = tmp_path / "overlay.toml"
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    data = tmp_path / "data" / "dicts"
    monkeypatch.setattr("overlay.app.relocate.dicts_data_dir", lambda: data)

    src = tmp_path / "src"
    src.mkdir()
    with zipfile.ZipFile(src / "MyDict.zip", "w") as zf:
        zf.writestr("index.json", json.dumps({"title": "MyDict", "format": 3}))
        zf.writestr("term_bank_1.json", json.dumps([["猫", "ねこ", "", "", 0, ["cat"], 1, ""]]))
    (src / "junk.zip").write_bytes(b"not a real zip")  # must be skipped

    added = relocate.import_from_dir(str(src), config=str(cfg))
    assert added == [(str(data / "MyDict.zip"), "dicts")]
    assert (data / "MyDict.zip").exists()
    assert any("MyDict.zip" in str(p) for p in load_config().get("dicts", []))


def test_import_from_dir_registers_data_dir_in_place(monkeypatch, tmp_path):
    """`copy-dicts` (no source) sweeps the data dir itself: a zip already sitting there but missing
    from the config gets registered, with NO re-copy (src == dest, same size — no SameFileError)."""
    import json
    import zipfile

    from overlay.app import relocate
    from overlay.app.config import load_config

    cfg = tmp_path / "overlay.toml"
    cfg.write_text('slang = "ja"\n')  # a config with no dicts (the stranded-copy situation)
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    data = tmp_path / "data" / "dicts"
    data.mkdir(parents=True)
    monkeypatch.setattr("overlay.app.relocate.dicts_data_dir", lambda: data)
    with zipfile.ZipFile(data / "Already.zip", "w") as zf:
        zf.writestr("index.json", json.dumps({"title": "Already", "format": 3}))
        zf.writestr("term_bank_1.json", json.dumps([["猫", "ねこ", "", "", 0, ["cat"], 1, ""]]))

    def _no_copy(*a, **k):  # copying onto itself must never be attempted
        raise AssertionError("should not re-copy a zip already in the data dir")

    added = relocate.import_from_dir(str(data), config=str(cfg), copy=_no_copy)
    assert added == [(str(data / "Already.zip"), "dicts")]
    assert any("Already.zip" in str(p) for p in load_config().get("dicts", []))


def test_import_from_dir_reclassifies_wrong_bucket(monkeypatch, tmp_path):
    """Re-running copy-dicts MOVES a zip filed under the wrong kind (a pitch dict an older classifier
    put in `dicts`) into its correct bucket, instead of double-listing it."""
    import json
    import zipfile

    from overlay.app import relocate
    from overlay.app.config import load_config

    data = tmp_path / "data" / "dicts"
    data.mkdir(parents=True)
    monkeypatch.setattr("overlay.app.relocate.dicts_data_dir", lambda: data)
    zp = data / "NHK.zip"  # a real pitch dict: pitch term_meta + headword term_bank (like NHK 2016)
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": "NHK", "format": 3}))
        zf.writestr("term_bank_1.json", json.dumps([["猫", "ねこ", "", "", 0, [], 1, ""]]))
        zf.writestr(
            "term_meta_bank_1.json",
            json.dumps([["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 0}]}]]),
        )
    raw = str(zp)  # tmp isn't under $HOME in tests → _new_raw yields this absolute path
    cfg = tmp_path / "overlay.toml"
    cfg.write_text(f'dicts = ["{raw}"]\n')  # mis-filed under dicts by an older classifier
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))

    relocate.import_from_dir(str(data), config=str(cfg), copy=lambda *a, **k: None)
    loaded = load_config()
    assert loaded.get("pitch") == [raw]  # moved to its correct bucket
    assert raw not in loaded.get("dicts", [])  # and removed from the wrong one

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

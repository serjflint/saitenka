"""Word-pronunciation audio resolution from a local yomichan/yomitan pack (#93) — pure, offline."""

import json

from saitenka.app.word_audio import load_index, resolve


def _write_pack(tmp_path, index: dict, *, files: tuple[str, ...] = ()):
    (tmp_path / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    for name in files:
        (tmp_path / name).write_bytes(b"RIFF-fake-audio")
    return tmp_path


def test_resolve_hits_the_indexed_file(tmp_path):
    pack = _write_pack(
        tmp_path,
        {"index": {"読む": {"よむ": ["yomu.opus"]}}},
        files=("yomu.opus",),
    )
    hit = resolve(pack, "読む", "よむ")
    assert hit is not None
    assert hit.filename == "yomu.opus"
    assert hit.path == pack / "yomu.opus"


def test_resolve_misses_on_unindexed_term(tmp_path):
    pack = _write_pack(tmp_path, {"index": {"読む": {"よむ": ["yomu.opus"]}}}, files=("yomu.opus",))
    assert resolve(pack, "書く", "かく") is None


def test_resolve_is_reading_specific_homograph_safe(tmp_path):
    """Same term, different reading (e.g. 上手 かみて vs じょうず) — only the resolved reading's file
    is returned; a homograph must never fall back to a different reading's audio."""
    pack = _write_pack(
        tmp_path,
        {"index": {"上手": {"じょうず": ["jouzu.opus"], "かみて": ["kamite.opus"]}}},
        files=("jouzu.opus", "kamite.opus"),
    )
    hit = resolve(pack, "上手", "かみて")
    assert hit is not None and hit.filename == "kamite.opus"


def test_resolve_misses_when_indexed_file_absent_on_disk(tmp_path):
    pack = _write_pack(tmp_path, {"index": {"読む": {"よむ": ["missing.opus"]}}})  # no file written
    assert resolve(pack, "読む", "よむ") is None


def test_resolve_misses_on_unconfigured_pack_dir(tmp_path):
    assert resolve(tmp_path / "does-not-exist", "読む", "よむ") is None


def test_load_index_degrades_cleanly_on_malformed_json(tmp_path):
    (tmp_path / "index.json").write_text("{not valid json", encoding="utf-8")
    assert load_index(tmp_path) == {}
    assert resolve(tmp_path, "読む", "よむ") is None


def test_load_index_degrades_cleanly_when_index_missing(tmp_path):
    assert load_index(tmp_path) == {}


def test_load_index_skips_malformed_entries_but_keeps_good_ones(tmp_path):
    """A malformed sibling entry (wrong shape) must not poison the whole index — the well-formed
    entries alongside it still resolve."""
    pack = _write_pack(
        tmp_path,
        {
            "index": {
                "読む": {"よむ": ["yomu.opus"]},
                "壊れる": "not-a-dict",  # malformed: readings must be a dict
                "空": {},  # malformed: no readings at all
                "無音": {"x": 42},  # malformed: entry not str/dict/list
            }
        },
        files=("yomu.opus",),
    )
    index = load_index(pack)
    assert index == {"読む": {"よむ": ["yomu.opus"]}}
    assert resolve(pack, "読む", "よむ") is not None


def test_load_index_accepts_a_flat_unwrapped_table(tmp_path):
    """A pack whose index.json IS the term->reading->files table, with no `index`/`media` wrapper."""
    pack = _write_pack(tmp_path, {"読む": {"よむ": ["yomu.opus"]}}, files=("yomu.opus",))
    assert resolve(pack, "読む", "よむ") is not None


def test_load_index_accepts_dict_entries_with_a_path_key(tmp_path):
    pack = _write_pack(
        tmp_path,
        {"index": {"読む": {"よむ": [{"path": "yomu.opus", "name": "NHK16"}]}}},
        files=("yomu.opus",),
    )
    hit = resolve(pack, "読む", "よむ")
    assert hit is not None and hit.filename == "yomu.opus"


def test_resolve_falls_back_to_next_candidate_when_first_file_is_missing(tmp_path):
    pack = _write_pack(
        tmp_path,
        {"index": {"読む": {"よむ": ["missing.opus", "yomu.opus"]}}},
        files=("yomu.opus",),
    )
    hit = resolve(pack, "読む", "よむ")
    assert hit is not None and hit.filename == "yomu.opus"


def test_resolve_empty_term_or_reading_is_a_clean_miss(tmp_path):
    pack = _write_pack(tmp_path, {"index": {"読む": {"よむ": ["yomu.opus"]}}}, files=("yomu.opus",))
    assert resolve(pack, "", "よむ") is None
    assert resolve(pack, "読む", "") is None


def test_resolve_rejects_parent_traversal_entry(tmp_path):
    """A poisoned pack index mapping a reading to a `../` path must NOT resolve — arbitrary local-file
    read + upload into Anki via store_media otherwise."""
    (tmp_path / "secret.txt").write_bytes(b"top-secret")
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "index.json").write_text(
        json.dumps({"index": {"読む": {"よむ": ["../secret.txt"]}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert resolve(pack, "読む", "よむ") is None


def test_resolve_rejects_absolute_path_entry(tmp_path):
    """An ABSOLUTE-path entry (`pack / "/abs"` discards the base in pathlib) must be rejected too."""
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside-the-pack")
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "index.json").write_text(
        json.dumps({"index": {"読む": {"よむ": [str(outside)]}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert resolve(pack, "読む", "よむ") is None

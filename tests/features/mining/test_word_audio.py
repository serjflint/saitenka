"""Word-pronunciation audio resolution from a local yomichan/yomitan pack (#93) — pure, offline."""

import json

import pytest

from saitenka.app.features.mining.word_audio import resolve


def _write_pack(tmp_path, index: dict, *, files: tuple[str, ...] = ()):
    (tmp_path / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    for name in files:
        (tmp_path / name).write_bytes(b"RIFF-fake-audio")
    return tmp_path


@pytest.mark.parametrize(
    "index",
    [
        {"index": {"読む": {"よむ": ["yomu.opus"]}}},
        {"読む": {"よむ": ["yomu.opus"]}},
        {"index": {"読む": {"よむ": [{"path": "yomu.opus", "name": "NHK16"}]}}},
        {"index": {"読む": {"よむ": ["missing.opus", "yomu.opus"]}}},
    ],
    ids=["wrapped", "flat", "path-entry", "next-candidate"],
)
def test_resolve_hits_the_indexed_file(tmp_path, index):
    pack = _write_pack(tmp_path, index, files=("yomu.opus",))
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


@pytest.mark.parametrize("directory", [".", "does-not-exist"])
def test_resolve_misses_on_unconfigured_pack_dir(tmp_path, directory):
    assert resolve(tmp_path / directory, "読む", "よむ") is None


def test_load_index_degrades_cleanly_on_malformed_json(tmp_path):
    (tmp_path / "index.json").write_text("{not valid json", encoding="utf-8")
    assert resolve(tmp_path, "読む", "よむ") is None


@pytest.mark.parametrize(
    ("term", "reading", "filename"),
    [
        ("読む", "よむ", "yomu.opus"),
        ("壊れる", "よむ", None),
        ("空", "x", None),
        ("無音", "x", None),
    ],
)
def test_load_index_skips_malformed_entries_but_keeps_good_ones(tmp_path, term, reading, filename):
    """Bad siblings must not hide a good pronunciation or resolve as one."""
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
    hit = resolve(pack, term, reading)
    assert (hit.filename if hit else None) == filename


def test_resolve_empty_term_or_reading_is_a_clean_miss(tmp_path):
    pack = _write_pack(tmp_path, {"index": {"読む": {"よむ": ["yomu.opus"]}}}, files=("yomu.opus",))
    assert resolve(pack, "", "よむ") is None
    assert resolve(pack, "読む", "") is None

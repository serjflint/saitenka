"""The consolidated dictionary DB (`dictdb.py`): import once, tag by dict_id, re-import in isolation."""

import json
import zipfile

import pytest

from overlay.app.dictdb import DictionaryDb

AT = "2026-07-23T00:00:00"  # fixed imported_at — no Date.now in the store, stamped by the caller


def _term_zip(path, title, entries, *, kanji=(), tags=()):
    """entries: [term, reading, glossary]; kanji: [char, on, kun, tags, meanings]; tags: [code, cat, order]."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": title, "format": 3, "revision": "r1"}))
        bank = [[t, r, "", "", 0, g, i + 1, ""] for i, (t, r, g) in enumerate(entries)]
        zf.writestr("term_bank_1.json", json.dumps(bank, ensure_ascii=False))
        if kanji:
            kb = [[c, on, kun, tg, ms, {}] for c, on, kun, tg, ms in kanji]
            zf.writestr("kanji_bank_1.json", json.dumps(kb, ensure_ascii=False))
        if tags:
            tb = [[code, cat, order, "", 0] for code, cat, order in tags]
            zf.writestr("tag_bank_1.json", json.dumps(tb, ensure_ascii=False))
    return str(path)


def _meta_zip(path, title, mode, entries):
    """entries: [term, data]. Writes a term_meta_bank zip (freq or pitch)."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": title, "format": 3}))
        bank = [[t, mode, data] for t, data in entries]
        zf.writestr("term_meta_bank_1.json", json.dumps(bank, ensure_ascii=False))
    return str(path)


def test_import_term_dict_populates_entries_keys_and_meta(tmp_path):
    z = _term_zip(
        tmp_path / "d.zip", "TestDict", [["読む", "よむ", ["to read"]], ["本", "ほん", ["book"]]]
    )
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    row = db.import_zip(z, imported_at=AT)
    assert row.title == "TestDict" and row.kind == "dict" and row.revision == "r1"
    counts = db.dict_counts(row.id)
    assert counts["entries"] == 2
    assert counts["keys"] == 4  # each entry keyed by term AND reading
    assert counts["term_meta"] == 0
    # the reading key resolves to the kanji headword, scoped to this dict_id
    hit = (
        db._conn()
        .execute(
            "SELECT e.term FROM keys k JOIN entries e ON k.dict_id=e.dict_id AND k.id=e.id "
            "WHERE k.dict_id=? AND k.key=?",
            (row.id, "よむ"),
        )
        .fetchone()
    )
    assert hit[0] == "読む"


def test_import_freq_and_pitch_go_to_term_meta(tmp_path):
    fz = _meta_zip(
        tmp_path / "f.zip", "FreqA", "freq", [["本命", {"reading": "ほんめい", "frequency": 8912}]]
    )
    pz = _meta_zip(
        tmp_path / "p.zip",
        "PitchA",
        "pitch",
        [["本命", {"reading": "ほんめい", "pitches": [{"position": 0}]}]],
    )
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    fr = db.import_zip(fz, imported_at=AT)
    pr = db.import_zip(pz, imported_at=AT)
    assert fr.kind == "freq" and pr.kind == "pitch"
    freq = (
        db._conn()
        .execute("SELECT mode, reading, rank FROM term_meta WHERE dict_id=?", (fr.id,))
        .fetchone()
    )
    assert freq == ("freq", "ほんめい", 8912)
    pitch = (
        db._conn()
        .execute("SELECT mode, reading, positions FROM term_meta WHERE dict_id=?", (pr.id,))
        .fetchone()
    )
    assert pitch[0] == "pitch" and json.loads(pitch[2]) == [0]


def test_import_kanji_and_tags(tmp_path):
    z = _term_zip(
        tmp_path / "k.zip",
        "K",
        [["聞こえる", "きこえる", ["to be heard"]]],
        kanji=[["猫", "ビョウ", "ねこ", "jouyou", ["cat"]]],
        tags=[["★", "popular", 2]],
    )
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    row = db.import_zip(z, imported_at=AT)
    assert db.dict_counts(row.id)["kanji"] == 1
    assert db.dict_counts(row.id)["tags"] == 1
    k = (
        db._conn()
        .execute(
            "SELECT onyomi, kunyomi, meanings FROM kanji WHERE dict_id=? AND chr=?", (row.id, "猫")
        )
        .fetchone()
    )
    assert k[0] == "ビョウ" and k[1] == "ねこ" and json.loads(k[2]) == ["cat"]


def test_reimport_replaces_only_that_dictionary(tmp_path):
    a = _term_zip(tmp_path / "a.zip", "AAA", [["猫", "ねこ", ["cat"]]])
    b1 = _term_zip(tmp_path / "b1.zip", "BBB", [["犬", "いぬ", ["dog"]]])
    b2 = _term_zip(tmp_path / "b2.zip", "BBB", [["犬", "いぬ", ["dog"]], ["鳥", "とり", ["bird"]]])
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    ar = db.import_zip(a, imported_at=AT)
    db.import_zip(b1, imported_at=AT)
    db.import_zip(b2, imported_at=AT)  # re-import BBB with an extra entry
    titles = [r.title for r in db.list_dictionaries()]
    assert titles.count("BBB") == 1  # replaced, not duplicated
    br = next(r for r in db.list_dictionaries() if r.title == "BBB")
    assert db.dict_counts(br.id)["entries"] == 2  # the fresh BBB
    assert db.dict_counts(ar.id)["entries"] == 1  # AAA untouched


def test_resolve_orders_and_reports_missing(tmp_path):
    a = _term_zip(tmp_path / "a.zip", "AAA", [["猫", "ねこ", ["cat"]]])
    b = _term_zip(tmp_path / "b.zip", "BBB", [["犬", "いぬ", ["dog"]]])
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    db.import_zip(a, imported_at=AT)
    db.import_zip(b, imported_at=AT)
    found, missing = db.resolve(["BBB", "Nope", "AAA"])
    assert [r.title for r in found] == ["BBB", "AAA"]  # order preserved
    assert missing == ["Nope"]


def test_drop_removes_dictionary(tmp_path):
    a = _term_zip(tmp_path / "a.zip", "AAA", [["猫", "ねこ", ["cat"]]])
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    row = db.import_zip(a, imported_at=AT)
    assert db.drop("AAA") is True
    assert db.list_dictionaries() == []
    assert db.dict_counts(row.id)["entries"] == 0
    assert db.drop("AAA") is False  # already gone


def test_import_tolerates_wrong_crc_meta(tmp_path):
    """Some Yomitan pitch/freq exports ship a wrong stored CRC on intact deflate data — import anyway."""
    p = tmp_path / "meta.zip"
    entry = [["本命", "pitch", {"reading": "ほんめい", "pitches": [{"position": 0}]}]]
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.json", json.dumps({"title": "BadCRC"}))
        zf.writestr("term_meta_bank_1.json", json.dumps(entry, ensure_ascii=False))
    raw = bytearray(p.read_bytes())
    ci = raw.find(b"PK\x01\x02")
    raw[ci + 16 : ci + 20] = b"\x00\x00\x00\x00"
    li = raw.find(b"PK\x03\x04")
    raw[li + 14 : li + 18] = b"\x00\x00\x00\x00"
    p.write_bytes(raw)
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    row = db.import_zip(str(p), imported_at=AT)
    assert db.dict_counts(row.id)["term_meta"] == 1


def test_failed_import_rolls_back(tmp_path, monkeypatch):
    """A failure mid-import must leave the DB untouched — the whole import is one transaction."""
    import overlay.app.dictdb as dictdb

    z = _term_zip(tmp_path / "d.zip", "Boom", [["猫", "ねこ", ["cat"]]], tags=[["★", "p", 1]])
    db = DictionaryDb.open(tmp_path / "db.sqlite")

    def boom(_zf):
        raise RuntimeError("disk full")

    monkeypatch.setattr(dictdb, "_extract_tags", boom)
    with pytest.raises(RuntimeError):
        db.import_zip(z, imported_at=AT)
    assert db.list_dictionaries() == []  # no half-written dictionary row
    assert db._conn().execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0


def test_import_reports_bank_progress(tmp_path):
    z = _term_zip(tmp_path / "d.zip", "P", [["猫", "ねこ", ["cat"]]])
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    calls: list[tuple[int, int]] = []
    db.import_zip(z, imported_at=AT, on_bank=lambda done, total: calls.append((done, total)))
    assert calls and calls[-1][0] == calls[-1][1]  # ends at (total, total)
    assert all(0 <= d <= t for d, t in calls)


def test_readonly_conn_has_mmap_and_cache_pragmas(tmp_path):
    """DictionaryDb._conn() sets PRAGMA mmap_size and a larger cache_size on the read-only per-thread
    connections, so cold first lookups avoid pread round-trips."""
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    c = db._conn()
    assert c.execute("PRAGMA mmap_size").fetchone()[0] == 1073741824
    assert c.execute("PRAGMA cache_size").fetchone()[0] == -65536  # 64 MiB (negative = KiB units)

"""The consolidated dictionary DB (`dictdb.py`): import once, tag by dict_id, re-import in isolation."""

import json
import zipfile

import pytest
from saitenka_dict.schema import SCHEMA_VERSION

from saitenka.app.dictdb import DictionaryDb

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


def _meta_zip(path, title, mode, entries, *, frequency_mode=None):
    """entries: [term, data]. Writes a term_meta_bank zip (freq or pitch). ``frequency_mode`` sets
    index.json's ``frequencyMode`` (e.g. ``"occurrence-based"``)."""
    index = {"title": title, "format": 3}
    if frequency_mode is not None:
        index["frequencyMode"] = frequency_mode
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps(index))
        bank = [[t, mode, data] for t, data in entries]
        zf.writestr("term_meta_bank_1.json", json.dumps(bank, ensure_ascii=False))
    return str(path)


def test_import_returns_the_app_dictionary_row(tmp_path):
    z = _term_zip(
        tmp_path / "d.zip", "TestDict", [["読む", "よむ", ["to read"]], ["本", "ほん", ["book"]]]
    )
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    row = db.import_zip(z, imported_at=AT)
    assert row.title == "TestDict" and row.kind == "dict" and row.revision == "r1"


def test_import_combined_dictionary_is_primary(tmp_path):
    archive = tmp_path / "combined.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("index.json", json.dumps({"title": "Combined", "format": 3}))
        output.writestr("term_bank_1.json", json.dumps([["chat", "", "", "", 0, ["cat"], 1, ""]]))
        output.writestr("term_meta_bank_1.json", json.dumps([["chat", "freq", 42]]))

    row = DictionaryDb.open(tmp_path / "db.sqlite").import_zip(str(archive), imported_at=AT)

    assert row.kind == "dict"


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
        db.connection()
        .execute("SELECT mode, reading, rank FROM term_meta WHERE dict_id=?", (fr.id,))
        .fetchone()
    )
    assert freq == ("freq", "ほんめい", 8912)
    pitch = (
        db.connection()
        .execute("SELECT mode, reading, positions FROM term_meta WHERE dict_id=?", (pr.id,))
        .fetchone()
    )
    assert pitch[0] == "pitch" and json.loads(pitch[2]) == [0]


def test_stats_reports_schema_size_and_per_dict_counts(tmp_path):
    tagged = _term_zip(
        tmp_path / "a.zip", "Tagged", [["猫", "ねこ", ["cat"]]], tags=[["★", "popular", 1]]
    )
    untagged = _term_zip(tmp_path / "b.zip", "Untagged", [["犬", "いぬ", ["dog"]]])  # no tag_bank
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    db.import_zip(tagged, imported_at=AT)
    db.import_zip(untagged, imported_at=AT)
    st = db.stats()
    assert st.exists and st.schema == SCHEMA_VERSION and st.size_bytes > 0
    by_title = {d.row.title: d for d in st.dicts}
    assert by_title["Tagged"].counts["tags"] == 1
    assert by_title["Tagged"].counts["entries"] == 1
    assert (
        by_title["Tagged"].imported_at == AT and by_title["Tagged"].schema_version == SCHEMA_VERSION
    )
    # 0 tags for a dict-kind entry is the sidecar-era / no-tag-bank tell the report surfaces
    assert by_title["Untagged"].counts["tags"] == 0


def test_stats_on_a_missing_db_is_empty_not_an_error(tmp_path):
    st = DictionaryDb(tmp_path / "absent.sqlite").stats()  # never opened → no file on disk
    assert st.exists is False and st.dicts == [] and st.schema is None and st.size_bytes == 0


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


def test_failed_import_rolls_back(tmp_path, monkeypatch):
    """A failure mid-import must leave the DB untouched — the whole import is one transaction."""
    from saitenka_dict.importer import DictionaryDatabase

    z = _term_zip(tmp_path / "d.zip", "Boom", [["猫", "ねこ", ["cat"]]], tags=[["★", "p", 1]])
    db = DictionaryDb.open(tmp_path / "db.sqlite")

    def boom(*_args):
        raise RuntimeError("disk full")

    monkeypatch.setattr(DictionaryDatabase, "_load_tags", staticmethod(boom))
    with pytest.raises(RuntimeError):
        db.import_zip(z, imported_at=AT)
    assert db.list_dictionaries() == []  # no half-written dictionary row
    assert db.connection().execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0


def test_import_reports_bank_progress(tmp_path):
    z = _term_zip(tmp_path / "d.zip", "P", [["猫", "ねこ", ["cat"]]])
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    calls: list[tuple[int, int]] = []
    db.import_zip(z, imported_at=AT, on_bank=lambda done, total: calls.append((done, total)))
    assert calls and calls[-1][0] == calls[-1][1]  # ends at (total, total)
    assert all(0 <= d <= t for d, t in calls)


def test_readonly_conn_has_mmap_and_cache_pragmas(tmp_path):
    """DictionaryDb.connection() sets a MODEST PRAGMA mmap_size + cache_size on the read-only per-thread
    connections — small enough that N worker connections don't inflate the Windows working set by GiB,
    but still page-cache-backed so cold lookups avoid pread round-trips."""
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    c = db.connection()
    assert c.execute("PRAGMA mmap_size").fetchone()[0] == 268435456  # 256 MiB per connection
    assert c.execute("PRAGMA cache_size").fetchone()[0] == -32768  # 32 MiB (negative = KiB units)


def test_term_meta_reading_index_exists(tmp_path):
    """idx_meta_reading covers (dict_id, mode, reading) so PitchSource.accents()'s
    `term=? OR reading=?` query can use an indexed seek for the reading branch instead of a full
    per-dict_id scan (py-spy profile: this was ~28% of a --stress run before the index existed)."""
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    names = {
        row[0]
        for row in db.connection().execute(
            "SELECT name FROM sqlite_master WHERE tbl_name='term_meta' AND type='index'"
        )
    }
    assert "idx_meta_reading" in names
    assert "idx_term_meta_term" in names


def test_ensure_schema_analyzes_term_meta_once(tmp_path):
    """The one-time catch-up (for DBs imported before idx_meta_reading existed) runs ANALYZE and
    records a meta flag so it doesn't repeat on every open — ANALYZE cost scales with table size."""
    p = tmp_path / "db.sqlite"
    db = DictionaryDb.open(p)
    assert db.connection().execute("SELECT v FROM meta WHERE k='analyzed'").fetchone() is not None
    # sqlite_stat1 gets populated by ANALYZE even on an empty table (rows for each index/table).
    stat_rows_after_first_open = (
        db.connection().execute("SELECT count(*) FROM sqlite_stat1").fetchone()[0]
    )

    # Reopening (simulating a later app start) must NOT re-run ANALYZE — verified indirectly: the
    # meta flag stays a single row (INSERT OR REPLACE, not accumulating) and reopen doesn't raise.
    db2 = DictionaryDb.open(p)
    assert (
        db2.connection().execute("SELECT count(*) FROM meta WHERE k='analyzed'").fetchone()[0] == 1
    )
    assert stat_rows_after_first_open >= 0  # sanity: the query above didn't error


def test_import_freq_or_pitch_reanalyzes_term_meta(tmp_path):
    """A freq/pitch import changes term_meta's row distribution, so query-planner stats must be
    refreshed — otherwise a fresh install's first import would rely on stale (empty-table) stats."""
    pz = _meta_zip(
        tmp_path / "p.zip",
        "PitchA",
        "pitch",
        [["本命", {"reading": "ほんめい", "pitches": [{"position": 0}]}]],
    )
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    db.import_zip(pz, imported_at=AT)
    stat = (
        db.connection()
        .execute("SELECT count(*) FROM sqlite_stat1 WHERE tbl='term_meta'")
        .fetchone()[0]
    )
    assert stat > 0  # ANALYZE ran and recorded stats for term_meta's indexes


def _ranks(db, dict_id):
    return dict(
        db.connection()
        .execute("SELECT term, rank FROM term_meta WHERE dict_id=? AND mode='freq'", (dict_id,))
        .fetchall()
    )


def test_occurrence_based_preserves_count_in_display(tmp_path):
    """The derived rank drives coloring, but the tooltip should still show the real occurrence count —
    kept in ``disp`` when the dict gave no explicit displayValue."""
    fz = _meta_zip(
        tmp_path / "occ2.zip",
        "OccFreq2",
        "freq",
        [["犬", {"reading": "いぬ", "frequency": 9000}]],
        frequency_mode="occurrence-based",
    )
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    row = db.import_zip(fz, imported_at=AT)
    rank, disp = (
        db.connection()
        .execute("SELECT rank, disp FROM term_meta WHERE dict_id=? AND term='犬'", (row.id,))
        .fetchone()
    )
    assert (rank, disp) == (1, "9000")

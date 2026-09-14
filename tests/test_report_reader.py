import json
import zipfile

import pytest

from saitenka.app import report_reader
from saitenka.app.subtitle_report import load_trace


@pytest.mark.parametrize(
    "name", ["../trace.json", "/trace.json", "C:/trace.json", "dir\\trace.json"]
)
def test_unsafe_archive_names_are_rejected_before_trace_read(tmp_path, name):
    source = tmp_path / "report.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(name, "[]")
    with pytest.raises(ValueError, match="unsafe"):
        load_trace(source)


def test_compressed_oversized_member_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(report_reader, "MAX_ARCHIVE_BYTES", 1000)
    source = tmp_path / "report.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("trace.json", " " * 10000)
    assert source.stat().st_size < 1000
    with pytest.raises(ValueError, match="expanded byte limit"):
        load_trace(source)


def test_suffix_ambiguity_cannot_select_an_arbitrary_trace(tmp_path):
    source = tmp_path / "report.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("a/trace.json", "[]")
        archive.writestr("b/trace.json", "[]")
    with pytest.raises(ValueError, match="ambiguous"):
        load_trace(source)


def test_directory_symlink_cannot_read_outside_report(tmp_path):
    source = tmp_path / "report"
    source.mkdir()
    private = tmp_path / "private.json"
    private.write_text("[]", encoding="utf-8")
    (source / "trace.json").symlink_to(private)
    with pytest.raises(ValueError, match="escapes"):
        load_trace(source)


def test_legacy_wrapped_report_preserves_events(tmp_path):
    source = tmp_path / "report.zip"
    events = [{"name": "cue_annotation", "ph": "X", "ts": 1, "dur": 2}]
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("report/telemetry/trace.json", json.dumps({"traceEvents": events}))
    assert load_trace(source) == events


def test_bounded_plain_file_read_has_teeth(tmp_path):
    source = tmp_path / "trace.json"
    source.write_text("x" * 11, encoding="utf-8")
    with pytest.raises(ValueError, match="byte limit"):
        report_reader.read_file(source, limit=10)

import json
import zipfile

import pytest

from saitenka.app import report_reader
from saitenka.app.report_reader import trace_evidence
from saitenka.app.subtitle_report import load_trace


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        (None, "missing"),
        ('{"traceEvents": []}', "readable"),
        ('{"traceEvents": [null]}', "invalid"),
        ('{"traceEvents": [{"name":"x","ph":"X","dur":NaN}]}', "invalid"),
        ('{"traceEvents": [{"name":"x","ph":"X","args":[]}]}', "invalid"),
        ('{"traceEvents": [{"name":"subtitle_draw","ph":"X"}]}', "invalid"),
        ('{"traceEvents": [{"name":"subtitle_draw","ph":"X","ts":0}]}', "invalid"),
        ('{"traceEvents": [{"name":"subtitle_draw","ph":"X","dur":1}]}', "invalid"),
        ('{"traceEvents":', "invalid"),
        ("{}", "invalid"),
    ],
)
def test_trace_reader_distinguishes_missing_empty_and_invalid_capture(tmp_path, raw, status):
    source = tmp_path / "report.zip"
    with zipfile.ZipFile(source, "w") as archive:
        if raw is not None:
            archive.writestr("telemetry/trace.json", raw)

    result = trace_evidence(source)

    assert result["status"] == status
    assert result["events"] == []
    if status == "readable":
        assert result["declared_events"] == 0


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


@pytest.mark.parametrize("version", [True, 3, "2", None])
def test_unknown_trace_schema_does_not_qualify_current_events(tmp_path, version):
    source = tmp_path / "trace.json"
    source.write_text(
        json.dumps(
            {
                "otherData": {"schema_version": version},
                "traceEvents": [{"name": "cue_annotation", "ph": "X"}],
            }
        ),
        encoding="utf-8",
    )

    result = trace_evidence(source)

    assert result == {"status": "invalid", "events": [], "reason": "unsupported-trace-schema"}


def test_partial_trace_keeps_valid_events_and_session_metadata(tmp_path):
    source = tmp_path / "trace.json"
    event = {"name": "cue_annotation", "ph": "X", "ts": 0, "dur": 3}
    metadata = {"session": "test-session", "schema_version": 2}
    source.write_text(
        json.dumps({"otherData": metadata, "traceEvents": [None, event]}), encoding="utf-8"
    )

    result = trace_evidence(source)

    assert result["status"] == "partial"
    assert result["declared_events"] == 2
    assert result["invalid_events"] == 1
    assert result["events"] == [{"ph": "M", "name": "session", "args": metadata}, event]


def test_subtitle_report_preserves_partial_capture_status(tmp_path, capsys):
    from saitenka.app.commands.diagnostics import subtitle_report

    source = tmp_path / "trace.json"
    source.write_text(
        json.dumps(
            {
                "traceEvents": [
                    None,
                    {
                        "name": "subtitle_geometry_render",
                        "ph": "X",
                        "ts": 0,
                        "dur": 1,
                        "args": {"outcome": "ready"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    code = subtitle_report(str(source), json_out=True)

    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["input_evidence"]["status"] == "partial"
    assert result["input_evidence"]["invalid_events"] == 1
    assert len(result["geometry"]) == 1

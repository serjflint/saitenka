import json
import zipfile

import pytest
from test_report_metadata import _environment

from saitenka.app.report import build_report_bundle
from saitenka.app.report_attachments import collect_attachments


def test_explicit_attachment_is_listed_without_its_original_filename(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    private = tmp_path / "PRIVATE.srt"
    private.write_text("PRIVATE subtitle", encoding="utf-8")

    bundle = build_report_bundle(tmp_path, timestamp="attached", attachments=(private,))

    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("attachments/manifest.json"))
        assert archive.read("attachments/01.srt") == b"PRIVATE subtitle"
        assert "PRIVATE.srt" not in " ".join(archive.namelist())
        assert manifest["files"][0]["privacy"] == "unredacted-user-attachment"
    assert private.read_text(encoding="utf-8") == "PRIVATE subtitle"


def test_attachment_limits_do_not_modify_the_source(tmp_path, monkeypatch):
    from saitenka.app import report_attachments

    monkeypatch.setattr(report_attachments, "MAX_ATTACHMENT_BYTES", 3)
    private = tmp_path / "subtitle.srt"
    private.write_bytes(b"1234")
    with pytest.raises(ValueError, match="exceeds"):
        collect_attachments((private,))
    assert private.read_bytes() == b"1234"


def test_no_attachments_produces_no_sensitive_members():
    assert collect_attachments(()) == {}

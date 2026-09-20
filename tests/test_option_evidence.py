import json
import zipfile
from dataclasses import fields

import pytest
from saitenka_tokenize.languages import ReaderLanguages
from session_builder import build_session
from test_render_evidence import _setup
from util import FakeIPC

from saitenka.app import option_evidence, report, telemetry
from saitenka.app.cli import create_app
from saitenka.app.cli_provenance import current_sources
from saitenka.app.commands import run
from saitenka.app.commands.attach import _build_attach_options
from saitenka.app.config import load_config
from saitenka.app.features.profiles.profile_controller import (
    ProfileAftermath,
    ProfileController,
    ProfileInvalidation,
    ProfileSubtitles,
)
from saitenka.app.launch.run import RunFlags, _build_run_options
from saitenka.app.profiles import DEFAULT_PROFILE, Profile


@pytest.mark.parametrize(
    "mode", ["legacy", "whole-cue-auto", "whole-cue-osd", "whole-cue-overpaint", "boxes-only"]
)
def test_session_evidence_preserves_coloring_mode(mode, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    options = _build_attach_options(
        {"subtitle_geometry": {"coloring": mode}}, mine={}, config_source="config-file"
    )
    option_evidence.record(options)
    fields = option_evidence.safe_snapshot(option_evidence.registry.snapshot())["owners"][0][
        "fields"
    ]
    assert fields["subtitle_geometry.coloring"] == {"value": mode, "origin": "config-file"}


@pytest.mark.parametrize(
    ("arguments", "expected"), [([], "config-file"), (["--tip-scale", "0"], "cli")]
)
def test_cli_origin_survives_construction_summary_and_zip(
    arguments, expected, monkeypatch, tmp_path
):
    config = _setup(monkeypatch, tmp_path)
    config.write_text("tip_scale = 0.0\nscan_delay = 0.5\n", encoding="utf-8")

    def launch(_video, **kwargs):
        flags = RunFlags(**{field.name: kwargs[field.name] for field in fields(RunFlags)})
        options = _build_run_options(load_config(), flags, config_source="config-file")
        session = build_session(FakeIPC(), options=options)
        session.close()
        return 0

    monkeypatch.setattr(run, "run_impl", launch)
    create_app()(["run", *arguments], result_action="return_value")
    telemetry.save_operation_summary()
    bundle = report.build_report_bundle(tmp_path / "reports", timestamp="origins")

    with zipfile.ZipFile(bundle) as archive:
        payload = json.loads(archive.read("diagnostics/envelope.json"))
    values = payload["session_configuration"]["owners"][0]["fields"]
    assert values["tooltip.tip_scale"] == {"value": 0.0, "origin": expected}
    assert values["tooltip.scan_delay"] == {"value": 0.5, "origin": "config-file"}
    assert values["tooltip.layout_engine"] == {"value": "default", "origin": "default"}
    assert current_sources() == ()


def test_programmatic_override_does_not_retain_the_original_file_origin(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    original = _build_attach_options({"tip_scale": 1.5}, mine={}, config_source="config-file")
    options = original.with_overrides(tip_scale=2.0)

    option_evidence.record(options)

    values = option_evidence.safe_snapshot(option_evidence.registry.snapshot())["owners"][0][
        "fields"
    ]
    assert values["tooltip.tip_scale"] == {"value": 2.0, "origin": "programmatic-override"}
    assert original.tooltip.tip_scale == 1.5


def test_option_export_rejects_private_freeform_values_and_origins():
    source = {
        "schema": 1,
        "owners": [
            {
                "owner": 1,
                "fields": {
                    "tooltip.tip_scale": {"value": "PRIVATE", "origin": "PRIVATE"},
                    "PRIVATE": {"value": "PRIVATE"},
                },
            }
        ],
    }

    result = option_evidence.safe_snapshot(source)

    assert "PRIVATE" not in json.dumps(result)
    assert result["owners"][0]["fields"]["tooltip.tip_scale"] == {
        "value": None,
        "origin": "unknown",
    }


@pytest.mark.parametrize("failure", ["none", "preflight", "aftermath"])
def test_profile_report_preserves_applied_state_across_switch_failures(
    failure, monkeypatch, tmp_path
):
    _setup(monkeypatch, tmp_path)

    def nothing():
        return None

    def warm():
        if failure == "aftermath":
            raise RuntimeError("PRIVATE")

    controller = ProfileController(
        DEFAULT_PROFILE,
        None,
        ProfileInvalidation(nothing, nothing, nothing),
        ProfileSubtitles(lambda: "fr", lambda _slang: True, lambda _slang: None, nothing),
        ProfileAftermath(warm, lambda _text, _kind: None),
    )
    controller.configure_cycle(
        [
            DEFAULT_PROFILE,
            Profile(
                "PRIVATE",
                ReaderLanguages("fr", "en"),
                "PRIVATE" if failure == "preflight" else "latin",
            ),
        ]
    )
    if failure == "aftermath":
        with pytest.raises(RuntimeError, match="PRIVATE"):
            controller.switch_to(1)
    else:
        controller.switch_to(1)
    telemetry.save_operation_summary()
    bundle = report.build_report_bundle(tmp_path / "reports", timestamp="profiles")

    with zipfile.ZipFile(bundle) as archive:
        payload = json.loads(archive.read("diagnostics/envelope.json"))
    evidence = payload["session_configuration"]["profiles"]["owners"][0]
    assert evidence["requested"]["main_language"] == "fr"
    assert evidence["applied"]["main_language"] == (
        DEFAULT_PROFILE.langs.main if failure == "preflight" else "fr"
    )
    assert (
        evidence["outcome"]
        == {"none": "committed", "preflight": "rejected", "aftermath": "pending"}[failure]
    )
    assert "PRIVATE" not in json.dumps(payload)

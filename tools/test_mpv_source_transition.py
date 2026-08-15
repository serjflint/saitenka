"""Locked Gate B source/transition contract tests for #354."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from mpv_source_transition import (
    FrameSample,
    _duplicate_layers_visible,
    _mask_coverage,
    _shadow_render,
    ass_hashes,
    assess_frames,
    build_embedded_delivery,
    collect_static_source_evidence,
    contract_hash,
    load_manifest,
    main,
    run_mpv_transition_probe,
    select_transition_contract,
    sha256,
    source_key_set_hash,
)

ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "mpv_source_transition.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
    shutil.copytree(MANIFEST_PATH.parent / "mpv_source_envelope", tmp_path / "mpv_source_envelope")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _relock(manifest: dict) -> None:
    manifest["denominator"]["source_count"] = len(manifest["sources"])
    manifest["denominator"]["source_key_set_sha256"] = source_key_set_hash(
        source["id"] for source in manifest["sources"]
    )
    manifest["denominator"]["contract_sha256"] = contract_hash(manifest)


def test_manifest_locks_complete_execution_contract_and_fixture_bytes() -> None:
    loaded = load_manifest(MANIFEST_PATH, repo_root=ROOT)

    assert loaded == MANIFEST
    assert contract_hash(loaded) == loaded["denominator"]["contract_sha256"]
    assert all(item["owner"] for item in loaded["render_inputs"])


def test_manifest_rejects_removed_source_even_if_contract_digest_is_reblessed(
    tmp_path: Path,
) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["sources"].pop()
    manifest["denominator"]["contract_sha256"] = contract_hash(manifest)

    with pytest.raises(ValueError, match="source count changed"):
        load_manifest(_write_manifest(tmp_path, manifest), repo_root=ROOT)


def test_manifest_rejects_changed_transition_contract(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["transition"]["selected_contract"] = "live-switch"

    with pytest.raises(ValueError, match="execution contract changed"):
        load_manifest(_write_manifest(tmp_path, manifest), repo_root=ROOT)


def test_manifest_rejects_invalid_duplicate_coverage_after_relock(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["transition"]["duplicate_minimum_coverage"] = 0
    _relock(manifest)

    with pytest.raises(ValueError, match="duplicate minimum coverage"):
        load_manifest(_write_manifest(tmp_path, manifest), repo_root=ROOT)


def test_manifest_rejects_boolean_duplicate_coverage_after_relock(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["transition"]["duplicate_minimum_coverage"] = True
    _relock(manifest)

    with pytest.raises(ValueError, match="duplicate minimum coverage"):
        load_manifest(_write_manifest(tmp_path, manifest), repo_root=ROOT)


def test_manifest_rejects_unowned_render_input_after_relock(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["render_inputs"][0]["owner"] = ""
    _relock(manifest)

    with pytest.raises(ValueError, match="render input needs an owner"):
        load_manifest(_write_manifest(tmp_path, manifest), repo_root=ROOT)


def test_manifest_rejects_removed_render_input_after_contract_relock(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["render_inputs"].pop()
    manifest["denominator"]["contract_sha256"] = contract_hash(manifest)

    with pytest.raises(ValueError, match="render input count changed"):
        load_manifest(_write_manifest(tmp_path, manifest), repo_root=ROOT)


def test_manifest_rejects_removed_required_control_after_contract_relock(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["transition"]["required_controls"].pop()
    manifest["denominator"]["contract_sha256"] = contract_hash(manifest)

    with pytest.raises(ValueError, match="required control count changed"):
        load_manifest(_write_manifest(tmp_path, manifest), repo_root=ROOT)


def test_manifest_rejects_fixture_content_mismatch_after_relock(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["sources"][0]["ass_sha256"] = sha256(b"different")
    _relock(manifest)

    with pytest.raises(ValueError, match="fixture hash changed"):
        load_manifest(_write_manifest(tmp_path, manifest), repo_root=ROOT)


def test_manifest_rejects_missing_attachment_after_relock(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    attachment = manifest["sources"][1]["attachments"][0]
    attachment["path"] = "src/saitenka/assets/fonts/missing.ttf"
    _relock(manifest)

    with pytest.raises(FileNotFoundError, match="fixture unavailable"):
        load_manifest(_write_manifest(tmp_path, manifest), repo_root=ROOT)


def test_ass_hashes_separate_style_extradata_from_authored_events() -> None:
    source = (MANIFEST_PATH.parent / "mpv_source_envelope" / "external.ass").read_bytes()
    style_changed = source.replace(b"Noto Sans JP Thin,48", b"Noto Sans JP Thin,49")
    event_changed = source.replace("原稿の字幕".encode(), "別の字幕".encode())

    baseline = ass_hashes(source)
    assert ass_hashes(style_changed).extradata != baseline.extradata
    assert ass_hashes(style_changed).events == baseline.events
    assert ass_hashes(event_changed).extradata == baseline.extradata
    assert ass_hashes(event_changed).events != baseline.events


def test_static_sources_deliver_the_same_bytes_to_mpv_and_shadow_libass() -> None:
    evidence = collect_static_source_evidence(MANIFEST_PATH, ROOT)
    supported = [item for item in evidence if item.decision == "supported"]

    assert {item.id for item in supported} == {"external-ass", "synthetic-ass-from-text"}
    assert all(item.mpv_input_sha256 == item.shadow_input_sha256 for item in supported)


def test_synthetic_source_rejects_semantically_different_paired_ass(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    path = _write_manifest(tmp_path, manifest)
    paired_ass = tmp_path / "mpv_source_envelope" / "synthetic.ass"
    paired_ass.write_bytes(
        paired_ass.read_bytes().replace("テキスト字幕".encode(), "別の字幕".encode())
    )
    manifest["sources"][2]["ass_sha256"] = sha256(paired_ass.read_bytes())
    _relock(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="paired ASS cues differ"):
        collect_static_source_evidence(path, ROOT)


def test_unavailable_remote_source_has_stable_native_visible_fallback() -> None:
    evidence = collect_static_source_evidence(MANIFEST_PATH, ROOT)
    remote = next(item for item in evidence if item.id == "remote-source-unavailable")

    assert remote.decision == "fallback"
    assert remote.fallback_reason == "authored-source-unavailable"
    assert remote.ass is None and remote.mpv_input_sha256 is None


def test_main_writes_structured_failure_report(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "failure.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mpv_source_transition.py",
            "--manifest",
            str(MANIFEST_PATH),
            "--output",
            str(output),
            "--repo-root",
            str(ROOT),
            "--ffmpeg",
            "missing-gate-b-ffmpeg",
        ],
    )

    with pytest.raises(FileNotFoundError):
        main()

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["gate_b_passed"] is False
    assert report["error_type"] == "FileNotFoundError"
    assert "missing-gate-b-ffmpeg" in report["error"]


def test_transition_oracle_accepts_single_owner_frames() -> None:
    result = assess_frames(
        [FrameSample(20, 0), FrameSample(10, 0), FrameSample(0, 12), FrameSample(0, 20)]
    )

    assert result.passed and result.blank_indices == () and result.duplicate_indices == ()


def test_transition_oracle_detects_injected_blank_frame() -> None:
    result = assess_frames([FrameSample(20, 0), FrameSample(0, 0), FrameSample(0, 20)])

    assert not result.passed and result.blank_indices == (1,) and result.duplicate_indices == ()


def test_transition_oracle_detects_injected_duplicate_layer() -> None:
    result = assess_frames([FrameSample(20, 0), FrameSample(20, 20), FrameSample(0, 20)])

    assert not result.passed and result.blank_indices == () and result.duplicate_indices == (1,)


def test_duplicate_layer_oracle_allows_small_compositing_edge_delta() -> None:
    native = set(range(100))
    generated = set(range(50, 150))
    duplicate = native | generated
    duplicate.remove(0)
    duplicate.remove(149)

    assert _mask_coverage(native, duplicate) == 0.99
    assert _mask_coverage(generated, duplicate) == 0.99
    assert _duplicate_layers_visible(native, generated, duplicate)


def test_duplicate_layer_oracle_rejects_native_only_sample() -> None:
    native = set(range(100))
    generated = set(range(50, 150))

    assert not _duplicate_layers_visible(native, generated, native)


def test_duplicate_layer_oracle_rejects_excessive_added_pixels() -> None:
    native = set(range(100))
    generated = set(range(50, 150))
    full_frame_artifact = set(range(1_000))

    assert not _duplicate_layers_visible(native, generated, full_frame_artifact)


def test_duplicate_layer_oracle_requires_independent_regions() -> None:
    native = set(range(100))

    assert not _duplicate_layers_visible(native, native, native)


def test_sampled_public_ipc_without_exact_frame_callback_selects_native_visible_contract() -> None:
    assert select_transition_contract(presented_frames_observed=False) == "native-visible"
    assert select_transition_contract(presented_frames_observed=True) == "paused-only-switch"


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_embedded_ass_and_attachment_are_extracted_as_owned_inputs(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required for the Gate B embedded-source probe")

    evidence, container, ass, attachments = build_embedded_delivery(
        MANIFEST_PATH, ROOT, tmp_path, ffmpeg=ffmpeg
    )
    locked_attachment = MANIFEST["sources"][1]["attachments"][0]

    assert evidence.decision == "supported"
    assert evidence.mpv_input_sha256 == evidence.shadow_input_sha256 == sha256(ass.read_bytes())
    assert evidence.container_sha256 == sha256(container.read_bytes())
    assert evidence.attachments == (("NotoSansJP.ttf", locked_attachment["sha256"]),)
    assert sha256(attachments[0].read_bytes()) == locked_attachment["sha256"]

    with_font = _shadow_render(evidence, ass, attachments)
    without_font = _shadow_render(evidence, ass, ())
    assert with_font["font_attachments"] == ["extracted-NotoSansJP.ttf"]
    assert without_font["font_attachments"] == []
    assert with_font["geometry_sha256"] != without_font["geometry_sha256"]


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_real_mpv_reports_track_identity_and_events_for_pause_and_playback(tmp_path: Path) -> None:
    mpv = shutil.which("mpv")
    ffmpeg = shutil.which("ffmpeg")
    if mpv is None or ffmpeg is None:
        pytest.skip("mpv and ffmpeg are required for the Gate B transition probe")

    embedded, container, _embedded_ass, _fonts = build_embedded_delivery(
        MANIFEST_PATH, ROOT, tmp_path, ffmpeg=ffmpeg
    )
    report = run_mpv_transition_probe(
        MANIFEST_PATH,
        ROOT,
        tmp_path,
        mpv=mpv,
        ffmpeg=ffmpeg,
        media_path=container,
        embedded_evidence=embedded,
    )
    without_font_report = run_mpv_transition_probe(
        MANIFEST_PATH,
        ROOT,
        tmp_path,
        mpv=mpv,
        ffmpeg=ffmpeg,
        media_path=container,
        embedded_evidence=embedded,
        embedded_fonts=False,
    )

    assert report["generated_sid"] != report["native_sid"]
    deliveries = {item["source_id"]: item for item in report["source_deliveries"]}
    supported = [source for source in MANIFEST["sources"] if source["kind"] != "remote-stream"]
    assert set(deliveries) == {source["id"] for source in supported}
    assert all(
        deliveries[source["id"]]["input_sha256"] == source["ass_sha256"]
        for source in supported
        if source["kind"] != "embedded-ass"
    )
    assert deliveries[embedded.id]["input_sha256"] == embedded.mpv_input_sha256
    assert deliveries[embedded.id]["container_sha256"] == embedded.container_sha256
    assert deliveries[embedded.id]["attachments"] == dict(embedded.attachments)
    assert all(
        item["track_added_event"]["name"] == "track-list"
        and item["track_removed_event"]["name"] == "track-list"
        for source_id, item in deliveries.items()
        if source_id == "synthetic-ass-from-text"
    )
    assert [phase["paused"] for phase in report["phases"]] == [True, False]
    assert all(
        phase["generated_sub_text_event"]["data"] == "生成した字幕"
        and "原稿の字幕" in phase["native_sub_text_event"]["data"]
        for phase in report["phases"]
    )
    assert report["generated_track_added_event"]["name"] == "track-list"
    assert report["generated_track_removed_event"]["name"] == "track-list"
    assert report["wrong_track_rejected"] and report["generated_track_removed"]
    assert report["final_sid"] == report["native_sid"]
    assert report["final_secondary_sid"] in {None, "no", False}
    assert (
        report["final_native_mask_sha256"] == report["public_frame_sampling"]["native_mask_sha256"]
    )
    assert report["native_visual_restored"]
    assert report["embedded_fonts_enabled"]
    assert not without_font_report["embedded_fonts_enabled"]
    assert (
        report["public_frame_sampling"]["native_mask_sha256"]
        != without_font_report["public_frame_sampling"]["native_mask_sha256"]
    )
    assert report["selected_contract"] == "native-visible"
    sampling = report["public_frame_sampling"]
    assert sampling["api"] == "screenshot-to-file"
    assert sampling["blank_pixels"] == 0 and sampling["blank_control_detected"]
    assert sampling["duplicate_native_overlap"] > 0
    assert sampling["duplicate_generated_overlap"] > 0
    assert sampling["duplicate_native_coverage"] >= 0.99
    assert sampling["duplicate_generated_coverage"] >= 0.99
    assert sampling["duplicate_union_precision"] >= 0.99
    assert sampling["duplicate_minimum_coverage"] == 0.99
    assert sampling["duplicate_layers_visible"]
    assert sampling["duplicate_control_detected"]
    assert sampling["native_restored_before_transition"]
    assert report["exact_presented_frame_callback"] == "unavailable-through-public-ipc"

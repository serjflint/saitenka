"""Negative controls for the same composition oracle used on installed-player reports."""

from __future__ import annotations

import pytest

from saitenka.app.frame_presentation import qualify


def capture(*, first=True, expired=False, owner="0x1", payload="abcdef0123456789", duplicate=False):
    lines = []
    for comp, pts in enumerate((0.0, 1.0, 1.5, 2.0), 1):
        native = 1 <= pts < 2
        color = (native and (first or pts > 1)) or (expired and pts == 2)
        prefix = f"{comp} {comp * 1000} {comp} "
        lines.extend(
            prefix + event
            for event in (
                f"draw_begin pts={pts}",
                "gpu_render ok=1",
                "gpu_submit ok=1",
                f"overlay_attached index=0 parts={int(native)}",
                f"timed_candidate id=2001 hash={payload} active={int(color)} start=1000 end=2000",
                f"external_candidate owner={owner} id=2001 hash={payload} images={int(color)} render_index=4",
                f"overlay_attached index=4 parts={int(color)}",
            )
        )
        if duplicate and native:
            lines.append(
                prefix
                + f"external_candidate owner={owner} id=1001 hash={payload} images=1 render_index=4"
            )
    return "\n".join([*lines, f"# health recorded={len(lines)} attempted={len(lines)} overflow=0"])


def demand():
    appearance = {
        "occurrence": 1,
        "text_hash": "a" * 32,
        "start_ms": 1000,
        "end_ms": 2000,
        "video_start_ms": 1000,
        "video_end_ms": 2000,
        "wall_start_ns": 0,
        "wall_end_ns": 5000,
        "epoch": 0,
        "connection_epoch": 0,
        "requested": 1,
        "required_warm": True,
        "owner": "0x1",
    }
    return {"schema": 1, "evidence_complete": True, "appearances": [appearance]}


def stages():
    return [
        {
            "event": "ack",
            "text_hash": "a" * 32,
            "start_ms": 1000,
            "end_ms": 2000,
            "video_start_ms": 1000,
            "video_end_ms": 2000,
            "epoch": 0,
            "connection_epoch": 0,
            "captured_ns": 1500,
            "slot": 2001,
            "payload_hash": "abcdef0123456789",
        }
    ]


def test_complete_independent_warm_population_qualifies():
    result = qualify(capture(), demand(), stages())
    assert result["qualified"]
    assert result["outcomes"] == {"complete": 1}


@pytest.mark.parametrize("mutation", ["first", "expired", "owner", "payload", "duplicate"])
def test_frame_regressions_cannot_pass_on_a_successful_ack(mutation):
    changes = {"first": False} if mutation == "first" else {mutation: True}
    if mutation in {"owner", "payload"}:
        changes = {mutation: "wrong"}
    assert not qualify(capture(**changes), demand(), stages())["qualified"]


@pytest.mark.parametrize(
    "failure", ["unstaged", "late", "missing", "loss", "truncated", "epoch", "cutover", "empty"]
)
def test_missing_readiness_or_evidence_never_qualifies(failure):
    manifest, ack, raw = demand(), stages(), capture()
    if failure == "unstaged":
        ack = []
    elif failure == "late":
        ack[0]["captured_ns"] = 2500
    elif failure == "missing":
        raw = ""
    elif failure == "loss":
        raw = raw.replace("overflow=0", "overflow=1")
    elif failure == "truncated":
        manifest["appearances"][0]["wall_end_ns"] = 3500
    elif failure == "epoch":
        ack[0]["epoch"] = 1
    elif failure == "cutover":
        manifest["cutovers"] = [
            {"composition": 3, "owner": "0x1", "slot": 2001, "payload_hash": ack[0]["payload_hash"]}
        ]
    else:
        manifest["appearances"] = []
    assert not qualify(raw, manifest, ack)["qualified"]


def test_repeated_visit_cannot_borrow_future_preparation():
    manifest = demand()
    manifest["appearances"].append(
        {**manifest["appearances"][0], "occurrence": 2, "wall_start_ns": 5000, "wall_end_ns": 9000}
    )
    result = qualify(capture(), manifest, stages())
    assert not result["qualified"]
    assert result["outcomes"] == {"complete": 1, "unknown": 1}


def test_missing_submission_outcome_is_unknown_not_a_discarded_first_frame():
    raw = capture(first=False).replace("2 2000 2 gpu_submit", "2 2000 2 missing_outcome")
    result = qualify(raw, demand(), stages())
    assert not result["qualified"]
    assert result["status"] == "unknown"


@pytest.mark.parametrize("event", ["invalidate", "remove", "removed"])
def test_retired_acknowledgment_cannot_certify_a_later_appearance(event):
    ack = [*stages(), {"event": event, "epoch": 1, "slot": 2001, "captured_ns": 1700}]
    assert not qualify(capture(), demand(), ack)["qualified"]


def test_capture_starting_mid_cue_cannot_certify_first_frame():
    lines = [line for line in capture().splitlines() if line.startswith(("3 ", "4 "))]
    raw = "\n".join([*lines, f"# health recorded={len(lines)} attempted={len(lines)} overflow=0"])
    assert not qualify(raw, demand(), stages())["qualified"]


@pytest.mark.parametrize("event", ["invalidate", "connection-replaced"])
def test_manifest_cannot_assert_an_unobserved_native_cutover(event):
    manifest = demand()
    manifest["cutovers"] = [
        {
            "epoch": 1,
            "composition": 99999,
            "owner": "0x1",
            "slot": 2001,
            "payload_hash": stages()[0]["payload_hash"],
        }
    ]
    records = [*stages(), {"event": event, "epoch": 1, "captured_ns": 2500}]
    result = qualify(capture(), manifest, records)
    assert not result["qualified"]
    assert result["status"] == "unknown"


@pytest.mark.parametrize(
    "broken", [None, "loss", "session", "trace-session", "binary", "source", "partial"]
)
def test_report_qualification_binds_health_session_and_source(tmp_path, broken):
    import json
    import zipfile

    from saitenka.app.frame_presentation import qualify_report

    manifest = demand()
    manifest["provenance"] = {
        "session": "run",
        "binary_sha256": "b" * 64,
        "source_sha256": "c" * 64,
    }
    receipt = {"session": "run", "binary_sha256": "b" * 64, "consumer_source": {"sha256": "c" * 64}}
    health = dict.fromkeys(
        (
            "lost_events",
            "queue_dropped",
            "write_failures",
            "history_losses",
            "sample_failures",
            "pending_admissions_omitted",
        ),
        0,
    )
    health.update(session="run", end="clean")
    if broken == "loss":
        health["lost_events"] = 1
    elif broken == "session":
        health["session"] = "other"
    elif broken == "binary":
        receipt["binary_sha256"] = "d" * 64
    elif broken == "source":
        receipt["consumer_source"]["sha256"] = "d" * 64
    trace = {
        "otherData": {"session": "other" if broken == "trace-session" else "run"},
        "traceEvents": [
            {"name": "subtitle_timed_osd", "ph": "X", "ts": 1.5, "dur": 0, "args": stages()[0]}
        ],
    }
    if broken == "partial":
        trace["traceEvents"].append({"ph": "X"})
    bundle = tmp_path / "report.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for name, document in (
            ("diagnostics/mpv-frame.json", receipt),
            ("telemetry/health.json", health),
            ("telemetry/trace.json", trace),
        ):
            archive.writestr(name, json.dumps(document))
        archive.writestr("diagnostics/mpv-frame.tsv", capture())
    assert qualify_report(bundle, manifest)["qualified"] is (broken is None)

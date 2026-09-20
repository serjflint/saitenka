from __future__ import annotations

import pytest
from saitenka_subtitles import Cue, CueIndex
from test_native_subtitles import FakeIPC

from saitenka.app.native_subtitles import GeometryObservation
from saitenka.app.timed_osd import MAX_ENTRIES, TimedOsd, timed_cue
from saitenka.runtime import EffectOutcome


def consumer(*, supported=True, cues=None, evidence=lambda _: None):
    ipc = FakeIPC()
    ipc.props.update(
        {
            "command-list": [{"name": "osd-overlay-timed"}] if supported else [],
            "options/sub-speed": 1.0,
            "options/sub-fps": 0.0,
            "options/play-direction": "forward",
            "sub-delay": -6.0,
            "time-pos": 0.0,
        }
    )
    seen = GeometryObservation(
        ipc.props.get,
        (1280, 720),
        "",
        [],
        [],
        CueIndex(cues or [Cue(16.12, 21.09, "猫を見る")]),
        str.strip,
        -1,
        None,
        0,
        lambda _: False,
    )
    return TimedOsd(ipc, lambda: seen, lambda: None, evidence=evidence), ipc


def test_prepared_cue_uses_authored_identity_and_shifted_video_interval():
    owner, ipc = consumer()
    owner.stage(16121, "colored cue", (1280, 720))
    assert ("osd-overlay-timed", 2001, 10120, 15090, "colored cue", 1280, 720, 1) in ipc.commands


def test_late_capability_discovery_publishes_prepared_cue():
    owner, ipc = consumer()
    ipc.correlate_commands = True
    owner.stage(16121, "colored cue", (1280, 720))

    ipc.deliver_runtime_mpv(match="command-list")

    assert any(
        command[:4] == ("osd-overlay-timed", 2001, 10120, 15090) for _, command, _ in ipc.submitted
    )


def test_pending_ack_does_not_duplicate_current_cue_publication():
    owner, ipc = consumer()
    owner.discover()
    ipc.correlate_commands = True
    entry = owner.stage(16121, "colored cue", (1280, 720))
    assert entry is not None

    result = owner.present((entry.cue.text_hash, 16120, 8), "colored cue", (1280, 720))

    assert result == (True, False)
    assert [command[0] for _, command, _ in ipc.submitted] == ["osd-overlay-timed"]


def test_backseek_reuses_prepared_entry_after_later_cue_is_staged():
    owner, ipc = consumer(cues=[Cue(10, 11, "猫"), Cue(12, 13, "犬")])
    entry = owner.stage(10001, "cat", (1280, 720))
    assert entry is not None
    ipc.props["time-pos"] = 6.1
    owner.stage(12001, "dog", (1280, 720))
    ipc.commands.clear()
    ipc.props["time-pos"] = 4.01

    result = owner.present((entry.cue.text_hash, 10000, 3), "cat", (1280, 720))

    assert result == (True, True)
    assert not ipc.commands


@pytest.mark.parametrize(
    ("prop", "value"),
    [
        ("sub-delay", None),
        ("options/sub-speed", 1.1),
        ("options/sub-fps", 24),
        ("options/play-direction", "backward"),
        ("options/blend-subtitles", True),
        ("options/sub-filter-regex", ["猫"]),
        ("options/sub-filter-jsre", ["猫"]),
    ],
)
def test_unknown_or_unsupported_clock_does_not_stage(prop, value):
    owner, ipc = consumer()
    ipc.props[prop] = value
    owner.stage(16121, "colored cue", (1280, 720))
    assert not any(command[0] == "osd-overlay-timed" for command in ipc.commands)


def test_stock_mpv_keeps_reactive_surface_owner():
    owner, ipc = consumer(supported=False)
    owner.discover()
    assert owner.present(None, "colored cue", (1280, 720)) == (False, False)
    assert not any(command[0] == "osd-overlay-timed" for command in ipc.commands)


def test_invalidation_clears_pending_stage_before_slot_can_be_reused():
    owner, ipc = consumer()
    owner.discover()
    ipc.correlate_commands = True
    owner.stage(16121, "old cue", (1280, 720))
    owner.invalidate("source-replaced")

    owner.stage(16121, "new cue", (1280, 720))

    assert [command[:3] for _, command, _ in ipc.submitted] == [
        ("osd-overlay-timed", 2001, 10120),
        ("osd-overlay", 2001, "none"),
    ]


def test_failed_clear_blocks_new_color_instead_of_reusing_uncertain_slot():
    owner, ipc = consumer()
    owner.stage(16121, "old cue", (1280, 720))
    ipc.correlate_commands = True
    owner.invalidate("source-replaced")
    ipc.deliver_runtime_mpv(match="osd-overlay", outcome=EffectOutcome.CANCELLED)
    ipc.commands.clear()

    owner.stage(16121, "new cue", (1280, 720))

    assert not ipc.submitted
    assert not ipc.commands


def test_pending_stages_are_bounded_before_ack():
    owner, ipc = consumer(cues=[Cue(10 + i * 2, 11 + i * 2, str(i)) for i in range(30)])
    owner.discover()
    ipc.correlate_commands = True

    for i in range(30):
        owner.stage(10001 + i * 2000, str(i), (1280, 720))

    assert sum(command[0] == "osd-overlay-timed" for _, command, _ in ipc.submitted) == MAX_ENTRIES
    assert len(ipc.submitted) <= MAX_ENTRIES + 1


def test_late_stage_ack_cannot_restore_retired_payload():
    owner, ipc = consumer()
    owner.discover()
    ipc.correlate_commands = True
    owner.stage(16121, "old cue", (1280, 720))
    owner.invalidate("source-replaced")
    ipc.deliver_runtime_mpv(match="osd-overlay-timed")
    ipc.deliver_runtime_mpv(match="osd-overlay")

    owner.stage(16121, "new cue", (1280, 720))

    assert [command for _, command, _ in ipc.submitted] == [
        ("osd-overlay-timed", 2001, 10120, 15090, "new cue", 1280, 720, 1)
    ]


def test_closed_consumer_does_not_publish_after_late_capability_reply():
    owner, ipc = consumer()
    ipc.correlate_commands = True
    owner.stage(16121, "colored cue", (1280, 720))
    owner.close()

    ipc.deliver_runtime_mpv(match="command-list")

    assert not ipc.submitted
    assert not any(c[0] == "osd-overlay-timed" for c in ipc.commands)


def test_delay_change_removes_old_interval_before_staging_replacement():
    owner, ipc = consumer()
    owner.stage(16121, "colored cue", (1280, 720))
    ipc.commands.clear()
    ipc.props["sub-delay"] = -5.0

    owner.stage(16121, "colored cue", (1280, 720))

    assert ipc.commands == [
        ("osd-overlay", 2001, "none", ""),
        ("osd-overlay-timed", 2001, 11120, 16090, "colored cue", 1280, 720, 1),
    ]


def test_overlap_later_in_event_declines_whole_interval():
    index = CueIndex([Cue(10, 15, "猫"), Cue(14, 16, "犬")])
    assert timed_cue(index, 10001, -6000) is None


def test_negative_start_interval_is_preserved():
    index = CueIndex([Cue(5, 8, "猫")])
    cue = timed_cue(index, 5001, -6000)
    assert cue is not None
    assert (cue.video_start_ms, cue.video_end_ms) == (-1000, 2000)


def test_co_timed_lines_share_the_whole_cue_interval():
    index = CueIndex([Cue(10, 15, "猫"), Cue(10, 15, "犬")])
    joined = CueIndex([Cue(10, 15, "猫\n犬")])
    assert timed_cue(index, 10001, -6000) == timed_cue(joined, 10001, -6000)


def test_rejected_removal_cannot_admit_replacement_color():
    owner, ipc = consumer()
    owner.stage(16121, "old cue", (1280, 720))
    ipc.refused_identities = ("'osd-overlay',",)
    ipc.commands.clear()

    owner.stage(16121, "new cue", (1280, 720))

    assert not ipc.commands


def test_capability_replay_stops_when_a_stage_is_rejected():
    owner, ipc = consumer(cues=[Cue(10, 11, "猫"), Cue(12, 13, "犬")])
    ipc.correlate_commands = True
    owner.stage(10001, "猫", (1280, 720))
    owner.stage(12001, "犬", (1280, 720))
    ipc.refused_identities = ("('osd-overlay-timed', 2001)",)

    ipc.deliver_runtime_mpv(match="command-list")

    assert [command[0] for _, command, _ in ipc.submitted] == ["osd-overlay"]


def test_backward_seek_evicts_distant_cue_then_reuses_acknowledged_slot():
    owner, ipc = consumer(cues=[Cue(10 + i * 2, 11 + i * 2, str(i)) for i in range(30)])
    for i in range(MAX_ENTRIES, 0, -1):
        ipc.props["time-pos"] = 4 + i * 2
        owner.stage(10001 + i * 2000, str(i), (1280, 720))
    ipc.props["time-pos"] = 4
    ipc.correlate_commands = True
    owner.stage(10001, "0", (1280, 720))
    ipc.deliver_runtime_mpv(match="osd-overlay")

    owner.stage(10001, "0", (1280, 720))

    assert [command for _, command, _ in ipc.submitted] == [
        ("osd-overlay-timed", 2001, 4000, 5000, "0", 1280, 720, 1)
    ]


@pytest.mark.parametrize("old_reply_first", [False, True])
def test_connection_replacement_fences_late_stage_reply(old_reply_first):
    records = []
    owner, ipc = consumer(evidence=records.append)
    owner.discover()
    ipc.correlate_commands = True
    owner.stage(16121, "old", (1280, 720))
    owner.connection_replaced()
    if old_reply_first:
        ipc.deliver_runtime_mpv(match="osd-overlay-timed")
    owner.stage(16121, "new", (1280, 720))
    ipc.deliver_runtime_mpv(match="command-list")
    if not old_reply_first:
        ipc.deliver_runtime_mpv(match="osd-overlay-timed")
    ipc.deliver_runtime_mpv(match="osd-overlay-timed")
    acknowledgments = [r for r in records if r["event"] == "ack"]
    assert len(acknowledgments) == 1
    assert acknowledgments[0]["connection_epoch"] == 1


@pytest.mark.usefixtures("enabled_telemetry")
def test_runtime_clock_and_entry_retirement_survive_trace_export(monkeypatch):
    from saitenka.app import render_evidence
    from saitenka.mpvio.diagnostics import payload_hash

    monkeypatch.setattr(render_evidence, "registry", render_evidence.EvidenceRegistry())
    evidence = render_evidence.GeometryEvidence()
    owner, ipc = consumer(evidence=evidence.timed_osd)
    owner.stage(16121, "private subtitle", (1280, 720))
    ipc.props["sub-delay"] = -5
    owner.stage(16121, "private subtitle", (1280, 720))
    snapshot = render_evidence.safe_runtime_configuration(render_evidence.registry.snapshot())
    timed = snapshot["owners"][0]["timed_osd"]
    assert [r["delay_ms"] for r in timed["history"] if r["event"] == "context"] == [-6000, -5000]
    acknowledgments = [r for r in timed["history"] if r["event"] == "ack"]
    assert [r["video_start_ms"] for r in acknowledgments] == [10120, 11120]
    assert all(r["payload_hash"] == payload_hash("private subtitle") for r in acknowledgments)
    assert timed["counts"]["removed"] == 1
    assert timed["display_counters"] is None
    assert "private subtitle" not in str(snapshot)


@pytest.mark.usefixtures("enabled_telemetry")
def test_history_eviction_remains_visible_in_report(monkeypatch):
    from saitenka.app import render_evidence
    from saitenka.app.timed_osd_evidence import LIMIT

    monkeypatch.setattr(render_evidence, "registry", render_evidence.EvidenceRegistry())
    evidence = render_evidence.GeometryEvidence()
    for epoch in range(LIMIT + 2):
        evidence.timed_osd({"event": "invalidate", "epoch": epoch})
    snapshot = render_evidence.safe_runtime_configuration(render_evidence.registry.snapshot())
    timed = snapshot["owners"][0]["timed_osd"]
    assert len(timed["history"]) == LIMIT
    assert timed["evicted"] == 2
    assert timed["counts"]["invalidate"] == LIMIT + 2

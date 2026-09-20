"""Occurrence accounting independent of real rendering speed and callback ordering."""

import pytest

from saitenka.app.color_accounting import ColorAccounting


def test_late_eligibility_expansion_has_one_final_complete_time():
    cue = ColorAccounting(1, "navigation", 0, 16)
    cue.qualify(frozenset({0, 1}), frozenset({0}), 0)
    cue.settle(cue.submit("overprint", frozenset({0})), 0.005, accepted=True)
    cue.qualify(frozenset({0, 1}), frozenset({0, 1}), 0.020)
    cue.settle(cue.submit("overpaint", frozenset({1})), 0.040, accepted=True)

    outcome = cue.retire(0.050, "replaced")

    assert outcome is not None
    assert (outcome.status, outcome.acknowledged, outcome.late) == ("complete", 2, True)
    assert (outcome.first_ms, outcome.complete_ms) == pytest.approx((5, 40))
    assert cue.retire(0.060, "shutdown") is None


@pytest.mark.parametrize(("seconds", "late"), [(0.016, False), (0.017, True)])
def test_never_acknowledged_color_counts_a_deadline_without_a_completion(seconds, late):
    cue = ColorAccounting(1, "natural", 0, 16)
    cue.qualify(frozenset({0}), frozenset({0}), 0)

    discovered = cue.check_deadline(seconds)
    outcome = cue.retire(seconds, "replaced")

    assert discovered is late
    assert outcome is not None
    assert (outcome.status, outcome.first_ms, outcome.complete_ms, outcome.late) == (
        "no-acknowledgment",
        None,
        None,
        late,
    )


def test_other_occurrence_and_superseded_writes_cannot_acknowledge_current_color():
    old = ColorAccounting(1, "navigation", 0, 16)
    old.qualify(frozenset({0}), frozenset({0}), 0)
    stale = old.submit("overprint", frozenset({0}))
    cue = ColorAccounting(2, "navigation", 0.010, 16)
    cue.qualify(frozenset({0}), frozenset({0}), 0.010)
    overtaken = cue.submit("overprint", frozenset({0}))
    current = cue.submit("overprint", frozenset({0}))

    assert not cue.settle(stale, 0.011, accepted=True)
    assert not cue.settle(overtaken, 0.012, accepted=True)
    assert cue.settle(current, 0.013, accepted=True)
    assert not cue.settle(current, 0.014, accepted=True)
    outcome = cue.retire(0.020, "replaced")
    assert outcome is not None and outcome.first_ms == pytest.approx(3)


def test_suppression_does_not_erase_demand_or_an_elapsed_deadline():
    cue = ColorAccounting(1, "natural", 0, 16)
    cue.qualify(frozenset({0}), frozenset({0}), 0)

    cue.qualify(frozenset({0}), frozenset(), 0.020)
    outcome = cue.retire(0.030, "replaced")

    assert outcome is not None
    assert (outcome.requested, outcome.permitted, outcome.acknowledged, outcome.late) == (
        1,
        1,
        0,
        True,
    )


def test_raster_and_vector_coverage_are_a_union_and_withdrawal_survives_success():
    cue = ColorAccounting(1, "natural", 0, 16)
    cue.qualify(frozenset({0, 1}), frozenset({0, 1}), 0)
    cue.settle(cue.submit("overprint", frozenset({0})), 0.001, accepted=True)
    cue.settle(cue.submit("overpaint", frozenset({0, 1})), 0.002, accepted=True)
    cue.settle(cue.submit("overprint", frozenset()), 0.003, accepted=True)
    assert cue.coverage == frozenset({0, 1})

    cue.settle(cue.submit("overpaint", frozenset()), 0.004, accepted=True)
    outcome = cue.retire(0.005, "replaced")

    assert outcome is not None
    assert (outcome.status, outcome.acknowledged, outcome.withdrawals) == ("complete", 2, 1)


@pytest.mark.parametrize(
    ("requested", "permitted", "status"),
    [
        (None, None, "unknown"),
        (frozenset(), frozenset(), "no-color-requested"),
        (frozenset({0}), frozenset(), "policy-suppressed"),
    ],
)
def test_nonpaint_outcomes_remain_distinct(requested, permitted, status):
    cue = ColorAccounting(1, "navigation", 0, 16)
    if requested is not None:
        cue.qualify(requested, permitted, 0)

    outcome = cue.retire(0.010, "unresolved-replacement")

    assert outcome is not None
    assert (outcome.status, outcome.late, outcome.complete_ms) == (status, False, None)


@pytest.mark.parametrize("retry", [False, True])
def test_rejected_write_cannot_count_as_acknowledged_but_retry_can(retry):
    cue = ColorAccounting(1, "natural", 0, 16)
    cue.qualify(frozenset({0}), frozenset({0}), 0)
    cue.settle(cue.submit("overpaint", frozenset({0})), 0.001, accepted=False)
    if retry:
        cue.settle(cue.submit("overpaint", frozenset({0})), 0.002, accepted=True)

    outcome = cue.retire(0.003, "replaced")

    assert outcome is not None
    assert outcome.status == ("complete" if retry else "failed")
    assert outcome.acknowledged == int(retry)


def test_withdrawal_before_ack_is_distinct_from_an_upload_that_never_settled():
    cue = ColorAccounting(1, "natural", 0, 16)
    cue.qualify(frozenset({0, 1}), frozenset({0, 1}), 0)
    cue.settle(cue.submit("overprint", frozenset({0})), 0.005, accepted=True)
    pending = cue.submit("overpaint", frozenset({1}))
    cue.qualify(frozenset({0, 1}), frozenset({0}), 0.010)

    outcome = cue.retire(0.020, "replaced")

    assert outcome is not None
    assert (outcome.status, outcome.eligibility_withdrawn, outcome.withdrawals) == ("partial", 1, 0)
    assert not cue.settle(pending, 0.021, accepted=True)
    assert outcome.complete_ms is None

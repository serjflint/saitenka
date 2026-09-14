"""Mining effects retain their meaning across the worker journal boundary."""

from dataclasses import fields, make_dataclass
from pathlib import Path

import pytest

from saitenka.app.features.mining import miner, mining_operation


@pytest.mark.parametrize("reverse_fields", [False, True])
def test_journal_preserves_callback_names_payloads_and_order(monkeypatch, reverse_fields):
    layout = [(field.name, field.type) for field in fields(miner.MiningApply)]
    if reverse_fields:
        monkeypatch.setattr(miner, "MiningApply", make_dataclass("MiningApply", layout[::-1]))
    expected = (
        mining_operation.MiningAction("toast", ("done", "info")),
        mining_operation.MiningAction("reset_capture", ()),
        mining_operation.MiningAction("captured_image", (Path("image.png"),)),
        mining_operation.MiningAction("captured_audio", (Path("audio.mp3"),)),
        mining_operation.MiningAction("mark_mined", ("猫",)),
        mining_operation.MiningAction("mined_here", ()),
        mining_operation.MiningAction("remember_duplicate", (object(),)),
        mining_operation.MiningAction("preview_existing", (42, object(), "duplicate")),
        mining_operation.MiningAction("preview_mined", (object(), object(), "video")),
        mining_operation.MiningAction("record_mined", (2,)),
        mining_operation.MiningAction("record_link", (object(),)),
        mining_operation.MiningAction("commit_mined", (object(),)),
    )
    journal = mining_operation.MiningActionJournal()
    callbacks = mining_operation._recording_apply(journal)

    for action in expected:
        getattr(callbacks, action.name)(*action.args)

    assert journal.drain() == expected
    assert journal.drain() == ()
    assert {action.name for action in expected} == {name for name, _ in layout}


def test_outcome_dispatches_by_name_in_journal_order():
    received = []

    def capture(name):
        return lambda *args: received.append((name, args))

    callbacks = miner.MiningApply(
        **{field.name: capture(field.name) for field in fields(miner.MiningApply)}
    )
    actions = tuple(
        mining_operation.MiningAction(field.name, (field.name,))
        for field in reversed(fields(miner.MiningApply))
    )

    mining_operation.apply_outcome(mining_operation.MiningOutcome(actions), callbacks)

    assert received == [(action.name, action.args) for action in actions]

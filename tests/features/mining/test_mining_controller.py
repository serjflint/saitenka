"""Mining-owner state, identity, and admission contracts."""

from __future__ import annotations

from dataclasses import dataclass

from saitenka_card import MineConfig
from saitenka_tokenize.japanese import Token

from saitenka.app.config import MiningOptions
from saitenka.app.features.mining import miner, mining_operation
from saitenka.app.features.mining.mining_controller import (
    MiningController,
    MiningIdentity,
    MiningLifecycle,
    MiningSpec,
    MiningTarget,
    SeedStatus,
)
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner


class _Tokenizer:
    def is_content(self, _token) -> bool:
        return True


class _Submitter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return True


class _RejectingSubmitter(_Submitter):
    def __call__(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return False


@dataclass
class _EncounterSource:
    samples: int = 0

    def __call__(self) -> miner.MiningEncounter:
        self.samples += 1
        token = Token("猫", "猫", "ネコ", "名詞", 0, 1)
        return miner.MiningEncounter(
            miner.MineCue([token], None, 0, _Tokenizer(), 20),
            None,
            object(),
            "/video.mkv",
            float(self.samples),
            miner.Timespan(10.0, 12.0),
            "猫",
            (),
        )


def _apply() -> miner.MiningApply:
    return miner.MiningApply(
        lambda *_args, **_kwargs: None,
        lambda: None,
        lambda _path: None,
        lambda _path: None,
        lambda _expression: None,
        lambda: None,
        lambda _token: None,
        lambda *_args: None,
        lambda *_args: None,
        lambda _count: None,
        lambda *_args: None,
    )


def _controller(tmp_path, monkeypatch, *, operation_submit=None):
    from saitenka.app.features.mining import mined_store

    monkeypatch.setattr(mined_store, "_DB_PATH_OVERRIDE", tmp_path / "mined.sqlite")
    seed = _Submitter()
    identity = MiningIdentity("a", 0)
    spec = MiningSpec(identity, {"deck": "Deck A", "model": "Lapis"})
    encounters = _EncounterSource()
    controller = MiningController(
        spec,
        MiningLifecycle(
            seed,
            _Submitter(),
            lambda _delay, _due: True,
            lambda: None,
            lambda: False,
            operation_submit,
        ),
        settings=MiningOptions(max_bulk=20, anki_ok_ttl=5, anki_ping_timeout=0.1),
        encounter=encounters,
        apply=_apply,
    )
    assert controller.publish_mining_target(
        MiningTarget(identity, object(), MineConfig(deck="Deck A"))
    )
    return controller, seed, encounters


def _finish(call: dict, values: set[str]) -> None:
    call["on_finished"](
        EffectFinished(
            EffectId(1),
            Owner.SESSION,
            call["identity"],
            EffectOutcome.SUCCEEDED,
            result=values,
        )
    )


def test_selecting_a_new_spec_hides_old_target_state_before_return(tmp_path, monkeypatch) -> None:
    controller, _seed, _encounters = _controller(tmp_path, monkeypatch)
    controller.record_mined_expression("old")
    old_target = controller.active_target
    assert old_target is not None

    identity = MiningIdentity("b", 1)
    controller.select_mining_spec(MiningSpec(identity, {"deck": "Deck B", "model": "Lapis"}))

    snapshot = controller.index_snapshot()
    assert controller.active_target is None
    assert snapshot.values == set() and snapshot.seed_status is SeedStatus.EMPTY
    assert controller.publish_mining_target(old_target) is False
    controller.close()


def test_prepared_target_must_match_the_selected_deck_and_model(tmp_path, monkeypatch) -> None:
    controller, _seed, _encounters = _controller(tmp_path, monkeypatch)
    identity = MiningIdentity("b", 1)
    controller.select_mining_spec(MiningSpec(identity, {"deck": "Deck B", "model": "Lapis"}))

    assert (
        controller.publish_mining_target(
            MiningTarget(identity, object(), MineConfig(deck="Wrong Deck"))
        )
        is False
    )
    assert controller.active_target is None
    controller.close()


def test_seed_for_current_target_merges_local_mines_and_rejects_old_completion(
    tmp_path, monkeypatch
) -> None:
    controller, seed, _encounters = _controller(tmp_path, monkeypatch)
    controller.request_seed()
    old = seed.calls[-1]

    identity = MiningIdentity("b", 1)
    config = MineConfig(deck="Deck B")
    controller.select_mining_spec(
        MiningSpec(identity, {"deck": config.deck, "model": config.model})
    )
    assert controller.publish_mining_target(MiningTarget(identity, object(), config))
    controller.request_seed()
    current = seed.calls[-1]
    controller.record_mined_expression("local")

    _finish(old, {"old"})
    assert controller.index_snapshot().values == {"local"}
    _finish(current, {"seed"})
    assert controller.index_snapshot().values == {"local", "seed"}
    controller.close()


def test_each_public_operation_samples_a_fresh_encounter(tmp_path, monkeypatch) -> None:
    controller, _seed, encounters = _controller(tmp_path, monkeypatch)
    observed: list[float] = []
    monkeypatch.setattr(
        miner,
        "preflight_token",
        lambda transaction, _token, **_kwargs: observed.append(transaction.encounter.playhead),
    )

    controller.mine_index(0)
    controller.mine_index(0)

    assert observed == [1.0, 2.0]
    assert encounters.samples == 2
    controller.close()


def test_profile_switch_refuses_an_old_mining_completion(tmp_path, monkeypatch) -> None:
    operations = _Submitter()
    controller, _seed, _encounters = _controller(tmp_path, monkeypatch, operation_submit=operations)
    monkeypatch.setattr(miner, "screenshot", lambda *_args: None)

    controller.mine_index(0)
    call = operations.calls[-1]
    assert controller.operation_pending

    identity = MiningIdentity("b", 1)
    controller.select_mining_spec(MiningSpec(identity, {"deck": "Deck B", "model": "Lapis"}))
    call["on_finished"](
        EffectFinished(
            EffectId(2),
            Owner.SESSION,
            call["identity"],
            EffectOutcome.SUCCEEDED,
            result=mining_operation.MiningOutcome(
                (mining_operation.MiningAction("mark_mined", ("old",)),)
            ),
        )
    )

    assert not controller.operation_pending
    assert "old" not in controller.index_snapshot()
    controller.close()


def test_rejected_operation_does_not_capture_media(tmp_path, monkeypatch) -> None:
    operations = _RejectingSubmitter()
    controller, _seed, _encounters = _controller(tmp_path, monkeypatch, operation_submit=operations)
    screenshots: list[object] = []
    monkeypatch.setattr(miner, "screenshot", lambda *_args: screenshots.append(object()))

    controller.mine_index(0)

    assert len(operations.calls) == 1
    assert screenshots == []
    assert not controller.operation_pending
    controller.close()


def test_rejected_seed_admission_releases_the_lane(tmp_path, monkeypatch) -> None:
    from saitenka.app.features.mining import mined_store

    monkeypatch.setattr(mined_store, "_DB_PATH_OVERRIDE", tmp_path / "mined.sqlite")
    seed = _RejectingSubmitter()
    identity = MiningIdentity("a", 0)
    controller = MiningController(
        MiningSpec(identity, {"deck": "Deck A", "model": "Lapis"}),
        MiningLifecycle(seed, _Submitter(), lambda _delay, _due: True, lambda: None, lambda: False),
        settings=MiningOptions(max_bulk=20, anki_ok_ttl=5, anki_ping_timeout=0.1),
        encounter=_EncounterSource(),
        apply=_apply,
    )
    assert controller.publish_mining_target(
        MiningTarget(identity, object(), MineConfig(deck="Deck A"))
    )

    controller.request_seed()

    assert len(seed.calls) == 1
    assert controller.seed_lane.inflight is False
    assert controller.index_snapshot().seed_status is SeedStatus.DEGRADED
    controller.close()

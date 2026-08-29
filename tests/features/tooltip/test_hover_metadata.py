from __future__ import annotations

import threading
import time
from dataclasses import replace

from driver import Driver
from session_builder import build_session
from util import FakeIPC

from saitenka.app.features.tooltip import hover_metadata, tooltip, tooltip_controller
from saitenka.app.features.tooltip.hover_metadata import (
    HoverMetadata,
    HoverMetadataKey,
    HoverMetadataRequest,
)
from saitenka.app.session.factory import (
    SessionInfrastructure,
    SessionServices,
)
from saitenka.app.subtitles import WordBox
from saitenka.app.tokenize import Token
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner


def _request(index: int, dictionary=None) -> HoverMetadataRequest:
    return HoverMetadataRequest(
        HoverMetadataKey(1, 1, 1, "cue", index),
        "unidic",
        (
            Token("猫", "猫", "ネコ", "名詞", 0, 1),
            Token("犬", "犬", "イヌ", "名詞", 1, 2),
            Token("鳥", "鳥", "トリ", "名詞", 2, 3),
        ),
        dictionary,
        frozenset(),
    )


def test_metadata_scheduler_keeps_only_the_newest_queued_intent():
    state = hover_metadata.InteractionMetadataState()
    admitted = []
    callbacks = []

    def submitter(**kwargs):
        admitted.append(kwargs["request"])
        callbacks.append(kwargs["on_finished"])
        return True

    def finished(completion):
        hover_metadata.finish(state, completion)
        if completion.identity == 1:
            assert hover_metadata.submit(state, _request(2), submitter, finished)
        hover_metadata.finish_publication(state)
        hover_metadata.submit_pending(state, submitter, finished)

    assert hover_metadata.submit(state, _request(0), submitter, finished)
    assert hover_metadata.submit(state, _request(1), submitter, finished)

    callbacks[0](EffectFinished(EffectId(1), Owner.INTERACTION, 1, EffectOutcome.SUCCEEDED))

    assert [request.key.index for request in admitted] == [0, 2]


def test_metadata_worker_resolves_off_the_event_thread():
    event_thread = threading.get_ident()
    resolved_thread = None

    class Dictionary:
        def has_term(self, _term: str) -> bool:
            nonlocal resolved_thread
            resolved_thread = threading.get_ident()
            return False

    from util import runtime_gateway

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    reader = build_session(ipc, services=SessionServices(dictionaries=Dictionary()))
    reader.turn.subtitle_presentation.cue.replace_tokenized(
        tokens=[Token("猫", "猫", "ネコ", "名詞", 0, 1)]
    )
    assert reader.turn.tooltip_controller.request_interaction_metadata(_request(0, Dictionary()))
    try:
        deadline = time.monotonic() + 1
        while resolved_thread is None and time.monotonic() < deadline:
            reader.turn._drain_events()
            time.sleep(0.001)
        assert resolved_thread is not None and resolved_thread != event_thread
    finally:
        reader.close()
        gateway.close()


def test_metadata_completion_applies_on_the_owner_thread(monkeypatch):
    owner_thread = threading.get_ident()
    resolved_thread = None
    applied_thread = None

    class Dictionary:
        def has_term(self, _term: str) -> bool:
            nonlocal resolved_thread
            resolved_thread = threading.get_ident()
            return False

    def apply_metadata(*_args) -> None:
        nonlocal applied_thread
        applied_thread = threading.get_ident()

    from util import runtime_gateway

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    reader = build_session(ipc, services=SessionServices(dictionaries=Dictionary()))
    monkeypatch.setattr(tooltip_controller.tooltip, "apply_hover_metadata", apply_metadata)

    try:
        assert reader.turn.tooltip_controller.request_interaction_metadata(
            _request(0, Dictionary())
        )
        deadline = time.monotonic() + 1
        while applied_thread is None and time.monotonic() < deadline:
            reader.turn._drain_events()
            time.sleep(0.001)

        assert resolved_thread is not None and resolved_thread != owner_thread
        assert applied_thread == owner_thread
    finally:
        reader.close()
        gateway.close()


def test_metadata_completion_refuses_facts_that_changed_after_submission():
    submitted = []

    def submitter(**kwargs):
        submitted.append(kwargs)
        return True

    reader = build_session(
        FakeIPC(),
        infrastructure=SessionInfrastructure(
            tooltip_jobs=lambda jobs: replace(jobs, metadata=submitter),
        ),
    )
    reader.turn.subtitle_presentation.cue.replace_tokenized(
        tokens=[Token("猫", "猫", "ネコ", "名詞", 0, 1)]
    )
    reader.turn.tooltip_controller.select(0)
    reader.turn.tooltip_controller.surface_state().view.job_id = (
        reader.turn.tooltip_controller.surface_state().jobs.begin("tooltip")
    )
    tooltip._request_hover_metadata(
        reader.turn.tooltip_controller.tip_ports,
        reader.turn.tooltip_controller.word_lookup,
        reader.turn.tooltip_controller.hover_inputs,
        0,
    )
    original = submitted[0]["request"]

    reader.turn.mining_controller.record_mined_expression("__newly-mined__")
    reader.turn.tooltip_preparation.cancel()
    reader.turn.set_subtitle("犬")
    submitted[0]["on_finished"](
        EffectFinished(
            EffectId(1),
            Owner.INTERACTION,
            submitted[0]["identity"],
            EffectOutcome.SUCCEEDED,
            result=HoverMetadata(
                original.key,
                phrase_terms=("猫",),
                phrase_span=(0, 1),
                mined=False,
                group_mined=(),
            ),
        )
    )

    assert len(submitted) == 1
    assert reader.turn.tooltip_controller.observation().metadata.terms == ()
    assert reader.turn.tooltip_controller.surface_state().view.state is None


def test_uncorrelated_metadata_completion_does_not_assemble_apply_ports(monkeypatch):
    reader = build_session(FakeIPC())

    def unexpected_apply():
        raise AssertionError("uncorrelated completion assembled tooltip apply ports")

    monkeypatch.setattr(reader.turn.tooltip_controller, "apply_context", unexpected_apply)

    reader.turn.tooltip_controller.finish_interaction_metadata(
        EffectFinished(EffectId(1), Owner.INTERACTION, 999, EffectOutcome.SUCCEEDED)
    )


def test_interactive_hover_submits_metadata_without_probing_dictionary(monkeypatch):
    class Dictionary:
        def has_term(self, _term: str) -> bool:
            raise AssertionError("dictionary probe ran on the event thread")

    submitted = []
    reader = build_session(
        FakeIPC(),
        services=SessionServices(
            dictionaries=Dictionary(),
        ),
        infrastructure=SessionInfrastructure(
            tooltip_jobs=lambda jobs: replace(
                jobs, metadata=lambda **kwargs: submitted.append(kwargs["request"]) or True
            ),
        ),
    )
    reader.turn.subtitle_presentation.cue.replace_tokenized(
        tokens=[Token("猫", "猫", "ネコ", "名詞", 0, 1)]
    )
    reader.turn.subtitle_presentation.cue.replace_geometry(origin=(0, 0))
    reader.turn.subtitle_presentation.cue.replace_geometry(boxes=[WordBox(0, 100, 100, 40, 40)])
    monkeypatch.setattr(reader.turn.subtitle_presentation, "draw", lambda: None)

    # Through the cursor, because the claim is about the *event thread*: the hit-test and the hover
    # decision run there too, and a `set_hover` call skips both of them.
    Driver(reader).move_to_word(0)

    assert len(submitted) == 1
    assert reader.turn.tooltip_controller.surface_state().view.state is None

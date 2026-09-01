from __future__ import annotations

import threading
import time
from dataclasses import replace

import pytest
from driver import Driver
from saitenka_tokenize.japanese import Token
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

    from util import session_gateway

    ipc = FakeIPC()
    gateway = session_gateway(ipc)
    reader = build_session(ipc, services=SessionServices(dictionaries=Dictionary()))
    reader.graph.subtitle_presentation.cue.replace_tokenized(
        tokens=[Token("猫", "猫", "ネコ", "名詞", 0, 1)]
    )
    assert reader.graph.tooltip.request_interaction_metadata(_request(0, Dictionary()))
    try:
        deadline = time.monotonic() + 1
        while resolved_thread is None and time.monotonic() < deadline:
            reader.pump()
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

    from util import session_gateway

    ipc = FakeIPC()
    gateway = session_gateway(ipc)
    reader = build_session(ipc, services=SessionServices(dictionaries=Dictionary()))
    monkeypatch.setattr(tooltip_controller.tooltip, "apply_hover_metadata", apply_metadata)

    try:
        assert reader.graph.tooltip.request_interaction_metadata(_request(0, Dictionary()))
        deadline = time.monotonic() + 1
        while applied_thread is None and time.monotonic() < deadline:
            reader.pump()
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
    reader.graph.subtitle_presentation.cue.replace_tokenized(
        tokens=[Token("猫", "猫", "ネコ", "名詞", 0, 1)]
    )
    reader.graph.tooltip.select(0)
    reader.graph.tooltip.surface_state().view.job_id = (
        reader.graph.tooltip.surface_state().jobs.begin("tooltip")
    )
    tooltip._request_hover_metadata(
        reader.graph.tooltip.tip_ports,
        reader.graph.tooltip.word_lookup,
        reader.graph.tooltip.hover_inputs,
        0,
    )
    original = submitted[0]["request"]

    reader.graph.mining.record_mined_expression("__newly-mined__")
    reader.graph.tooltip_preparation.cancel()
    reader.graph.cue.set_subtitle("犬")
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
    assert reader.graph.tooltip.observation().metadata.terms == ()
    assert reader.graph.tooltip.surface_state().view.state is None


@pytest.mark.parametrize(
    ("field", "value", "worth_re_asking"),
    [
        ("generation", 99, True),  # prefetch queue epoch — the hover's own arrival bumps it
        ("mined_generation", 99, True),  # the mined set moved; the word did not
        ("dependency_generation", 99, False),  # different dictionaries, so a different answer
        ("cue_identity", "another-cue", False),
        ("index", 7, False),
        ("job_id", 7, False),
    ],
)
def test_same_target_excuses_the_epochs_and_no_other_field(field, value, worth_re_asking):
    """Pin every discriminator, not just the one that last moved.

    `same_target` decides whether a stale result means "ask again" or "that was a different word".
    Widening it is how a wrong tooltip gets shown; narrowing it is how a tooltip goes missing. Both
    edits are one tuple element, and before this test either could be made with the suite green.
    """
    key = HoverMetadataKey(1, 1, 1, "cue", 0, job_id=1)

    assert key.same_target(replace(key, **{field: value})) is worth_re_asking


def test_a_hover_survives_the_prefetch_generation_its_own_arrival_bumps():
    """The prefetch generation is a queue epoch, not the hovered word's identity.

    Engaging — pausing, or the cursor entering the video — is what `update_prefetch` re-keys on, so
    the first hover after the pointer arrives bumps the very generation its in-flight request was
    stamped with. Treating that as a different target drops the answer *and* declines to ask again,
    which is a tooltip that never appears rather than one that appears late. Isolated deliberately:
    the neighbouring refusal test moves the mined set, the cue and this generation at once, so it
    passes whichever of the three is doing the work.
    """
    submitted = []
    reader = build_session(
        FakeIPC(),
        infrastructure=SessionInfrastructure(
            tooltip_jobs=lambda jobs: replace(
                jobs, metadata=lambda **kwargs: submitted.append(kwargs) or True
            ),
        ),
    )
    reader.graph.subtitle_presentation.cue.replace_tokenized(
        tokens=[Token("猫", "猫", "ネコ", "名詞", 0, 1)]
    )
    reader.graph.subtitle_presentation.cue.replace_geometry(origin=(0, 0))
    reader.graph.subtitle_presentation.cue.replace_geometry(boxes=[WordBox(0, 100, 100, 40, 40)])
    Driver(reader).move_to_word(0)
    original = submitted[0]["request"]

    reader.graph.tooltip_preparation.cancel()  # the engagement flip, as `settle` performs it
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

    assert [kwargs["request"].key.index for kwargs in submitted] == [0, 0]


def test_uncorrelated_metadata_completion_does_not_assemble_apply_ports(monkeypatch):
    reader = build_session(FakeIPC())

    def unexpected_apply():
        raise AssertionError("uncorrelated completion assembled tooltip apply ports")

    monkeypatch.setattr(reader.graph.tooltip, "apply_context", unexpected_apply)

    reader.graph.tooltip.finish_interaction_metadata(
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
    reader.graph.subtitle_presentation.cue.replace_tokenized(
        tokens=[Token("猫", "猫", "ネコ", "名詞", 0, 1)]
    )
    reader.graph.subtitle_presentation.cue.replace_geometry(origin=(0, 0))
    reader.graph.subtitle_presentation.cue.replace_geometry(boxes=[WordBox(0, 100, 100, 40, 40)])
    monkeypatch.setattr(reader.graph.subtitle_presentation, "draw", lambda: None)

    # Through the cursor, because the claim is about the *event thread*: the hit-test and the hover
    # decision run there too, and a `set_hover` call skips both of them.
    Driver(reader).move_to_word(0)

    assert len(submitted) == 1
    assert reader.graph.tooltip.surface_state().view.state is None

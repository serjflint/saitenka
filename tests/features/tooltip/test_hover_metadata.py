from __future__ import annotations

import threading
from dataclasses import replace

import dicthelp
import pytest
from driver import Driver
from saitenka_tokenize.japanese import Token
from util import FakeIPC, await_ready, session_gateway

from saitenka.app.features.tooltip import hover_metadata, tooltip_controller
from saitenka.app.features.tooltip.hover_metadata import (
    HoverMetadata,
    HoverMetadataKey,
    HoverMetadataRequest,
)
from saitenka.app.session.factory import (
    SessionInfrastructure,
    SessionServices,
)
from saitenka.app.subtitle_raster import PillowRasterProvider
from saitenka.app.subtitle_render import SubtitleRenderer
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner


def _hover_reader(make_session, submitted, *, dictionary=None):
    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    reader = make_session(
        ipc,
        services=SessionServices(dictionaries=dictionary or dicthelp.load_set()),
        infrastructure=SessionInfrastructure(
            renderer=SubtitleRenderer(PillowRasterProvider()),
            tooltip_jobs=lambda jobs: replace(
                jobs, metadata=lambda **kwargs: submitted.append(kwargs) or True
            ),
        ),
    )
    reader.graph.presentation.refresh_osd()
    reader.graph.playback.observe("sub-text", "猫")
    reader.pump()
    return reader


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


@pytest.mark.timeout(5)
def test_metadata_completion_applies_on_the_owner_thread(monkeypatch, make_session):
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

    ipc = FakeIPC()
    gateway = session_gateway(ipc)
    reader = make_session(ipc, services=SessionServices(dictionaries=Dictionary()))
    monkeypatch.setattr(tooltip_controller.tooltip, "apply_hover_metadata", apply_metadata)

    try:
        assert reader.graph.tooltip.request_interaction_metadata(_request(0, Dictionary()))
        await_ready(
            lambda: applied_thread is not None,
            "metadata completion was not delivered",
            pump=reader.pump,
            timeout=1.0,
        )

        assert resolved_thread is not None and resolved_thread != owner_thread
        assert applied_thread == owner_thread
    finally:
        reader.close()
        gateway.close()


@pytest.mark.parametrize("rejection", ["retired-cue", "uncorrelated-job"])
def test_metadata_completion_refuses_facts_that_changed_after_submission(make_session, rejection):
    submitted = []
    reader = _hover_reader(make_session, submitted)
    Driver(reader).move_to_word(0)
    original = submitted[0]["request"]
    identity = submitted[0]["identity"]
    if rejection == "retired-cue":
        reader.graph.playback.observe("sub-text", "犬")
        reader.pump()
    else:
        identity += 1

    submitted[0]["on_finished"](
        EffectFinished(
            EffectId(1),
            Owner.INTERACTION,
            identity,
            EffectOutcome.SUCCEEDED,
            result=HoverMetadata(
                original.key,
                phrase_terms=("stale",),
                phrase_span=(0, 1),
                mined=True,
                group_mined=(True,),
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


def test_a_hover_survives_the_prefetch_generation_its_own_arrival_bumps(make_session):
    submitted = []
    reader = _hover_reader(make_session, submitted)
    Driver(reader).move_to_word(0)
    original = submitted[0]["request"]
    reader.graph.tooltip_preparation.cancel()

    submitted[0]["on_finished"](
        EffectFinished(
            EffectId(1),
            Owner.INTERACTION,
            submitted[0]["identity"],
            EffectOutcome.SUCCEEDED,
            result=HoverMetadata(
                original.key, phrase_terms=("猫",), phrase_span=(0, 1), mined=False, group_mined=()
            ),
        )
    )

    assert [kwargs["request"].key.index for kwargs in submitted] == [0, 0]


def test_interactive_hover_submits_metadata_without_probing_dictionary(make_session):
    class Dictionary:
        def has_term(self, _term: str) -> bool:
            raise AssertionError("dictionary probe ran on the event thread")

    submitted = []
    reader = _hover_reader(make_session, submitted, dictionary=Dictionary())

    Driver(reader).move_to_word(0)

    assert len(submitted) == 1
    assert reader.graph.tooltip.surface_state().view.state is None

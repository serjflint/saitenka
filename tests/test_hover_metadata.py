from __future__ import annotations

import threading
import time

from driver import Driver
from util import FakeIPC

from saitenka.app import hover_metadata
from saitenka.app.controller import Reader
from saitenka.app.hover_metadata import HoverMetadataKey, HoverMetadataRequest
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
    done = threading.Event()

    class Dictionary:
        def has_term(self, _term: str) -> bool:
            nonlocal resolved_thread
            resolved_thread = threading.get_ident()
            return False

    from util import runtime_gateway

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    reader = Reader(ipc, dict_set=Dictionary())
    reader.tokens = [Token("猫", "猫", "ネコ", "名詞", 0, 1)]
    submitter = reader._interaction_metadata_submit
    assert submitter is not None
    submitter(
        owner=Owner.INTERACTION,
        identity=1,
        lane="interaction-metadata",
        request=_request(0, Dictionary()),
        on_finished=lambda _completion: done.set(),
    )
    try:
        deadline = time.monotonic() + 1
        while not done.is_set() and time.monotonic() < deadline:
            reader._drain_events()
            time.sleep(0.001)
        assert done.is_set()
        assert resolved_thread is not None and resolved_thread != event_thread
    finally:
        reader.close()
        gateway.close()


def test_interactive_hover_submits_metadata_without_probing_dictionary(monkeypatch):
    class Dictionary:
        def has_term(self, _term: str) -> bool:
            raise AssertionError("dictionary probe ran on the event thread")

    reader = Reader(FakeIPC(), dict_set=Dictionary())
    reader.tokens = [Token("猫", "猫", "ネコ", "名詞", 0, 1)]
    reader.sub_origin = (0, 0)
    reader.boxes = [WordBox(0, 100, 100, 40, 40)]
    submitted = []
    reader._interaction_metadata_submit = lambda **kwargs: (
        submitted.append(kwargs["request"]) or True
    )
    monkeypatch.setattr(reader, "_draw_subtitle", lambda: None)

    # Through the cursor, because the claim is about the *event thread*: the hit-test and the hover
    # decision run there too, and a `set_hover` call skips both of them.
    Driver(reader).move_to_word(0)

    assert len(submitted) == 1
    assert reader.tip.view.state is None

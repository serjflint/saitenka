from __future__ import annotations

import threading
import time

from util import FakeIPC

from saitenka.app import prefetch
from saitenka.app.controller import Reader
from saitenka.app.hover_metadata import (
    HoverMetadataKey,
    HoverMetadataRequest,
    InteractionMetadataActor,
)
from saitenka.app.tokenize import Token


def _request(index: int, dictionary=None) -> HoverMetadataRequest:
    return HoverMetadataRequest(
        HoverMetadataKey(1, 1, 1, "cue", index),
        "unidic",
        (
            Token("猫", "猫", "ネコ", "名詞", 0, 1),
            Token("犬", "犬", "イヌ", "名詞", 1, 2),
        ),
        dictionary,
        frozenset(),
    )


def test_metadata_actor_keeps_only_the_newest_queued_intent():
    release = threading.Event()
    entered = threading.Event()

    class Dictionary:
        def has_term(self, _term: str) -> bool:
            entered.set()
            release.wait(1)
            return False

    actor = InteractionMetadataActor()
    try:
        actor.submit(_request(0, Dictionary()))
        assert entered.wait(1)
        actor.submit(_request(1))
        release.set()
        deadline = time.monotonic() + 1
        results = []
        while len(results) < 2 and time.monotonic() < deadline:
            results.extend(actor.drain())
            time.sleep(0.001)

        assert [result.key.index for result in results] == [0, 1]
    finally:
        actor.close()


def test_interactive_hover_does_not_probe_dictionary_on_event_thread(monkeypatch):
    class Dictionary:
        def has_term(self, _term: str) -> bool:
            raise AssertionError("dictionary probe ran on the event thread")

    reader = Reader(FakeIPC(), dict_set=Dictionary())
    reader.tokens = [Token("猫", "猫", "ネコ", "名詞", 0, 1)]
    submitted = []
    monkeypatch.setattr(prefetch, "workers_running", lambda _reader: True)
    monkeypatch.setattr(reader._interaction_metadata, "submit", submitted.append)
    monkeypatch.setattr(reader, "_draw_subtitle", lambda: None)

    reader.set_hover(0)

    assert len(submitted) == 1
    assert reader._tip_state is None

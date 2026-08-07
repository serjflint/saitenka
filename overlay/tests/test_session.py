"""The per-process session id stamped on logs + telemetry spans."""

from __future__ import annotations

import re

from overlay import session


def test_session_id_is_stable_within_the_process(monkeypatch):
    monkeypatch.setattr(session, "_SESSION_ID", None)  # reset the cache for a clean first call
    first = session.session_id()
    assert first == session.session_id()  # cached — same value on every later call


def test_session_id_has_a_time_prefix_and_random_suffix(monkeypatch):
    monkeypatch.setattr(session, "_SESSION_ID", None)
    assert re.fullmatch(r"\d{6}-[0-9a-f]{4}", session.session_id())  # HHMMSS-xxxx

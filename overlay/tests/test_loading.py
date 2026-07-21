"""Startup loading spinner: draws a top-left overlay while loading, clears it on stop."""

from __future__ import annotations

import time

from overlay.app.loading import _LOADING_OID, LoadingIndicator


class _FakeIPC:
    def __init__(self):
        self.cmds: list[tuple] = []

    def command(self, *args):
        self.cmds.append(args)
        return {"error": "success"}


def test_loading_draws_then_clears():
    ipc = _FakeIPC()
    ind = LoadingIndicator(ipc, interval=0.01)
    ind.start("loading")
    time.sleep(0.05)  # let a few frames render
    ind.stop()
    verbs = [c[0] for c in ipc.cmds]
    assert "overlay-add" in verbs  # drew the spinner
    # last overlay op targets our OID and removes it
    removes = [c for c in ipc.cmds if c and c[0] == "overlay-remove"]
    assert removes and removes[-1][1] == _LOADING_OID  # cleared on stop


def test_loading_context_manager():
    ipc = _FakeIPC()
    with LoadingIndicator(ipc, interval=0.01):
        time.sleep(0.03)
    assert any(c[0] == "overlay-remove" for c in ipc.cmds)  # cleared on exit

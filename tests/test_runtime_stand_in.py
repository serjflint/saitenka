"""`NoSessionRuntime` must refuse every runtime port a feature can reach, not merely most of them.

A stand-in that *lacks* a port raises `AttributeError` at the call site, which reads as a crash in
whatever feature happened to reach it first — never as "this fake never declared the port". The class
docstring used to promise the base grows with the surface; it did not, and each gap was found by a
different crash (`submit_runtime_mpv` killed `timeline-bench`; `receive_session` was patched over
locally in `app/prewarm.py` instead). This enumerates the surface instead of promising to.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from saitenka.app.lifecycle_timers import RuntimeTimerPort
from saitenka.app.subtitle_render import SubtitleEgress
from saitenka.mpvio.ipc import MpvIPC
from saitenka.runtime.jobs import NoSessionRuntime, RuntimeJobPort

# Installed by the object that *is* the runtime (`mpvio/gateway.py` hands the transport its mailbox
# and loop). A stand-in is never passed to `RuntimeGateway`, so refusing these would mean nothing.
_RUNTIME_OWNER_ONLY = frozenset({"install_runtime_ingress", "register_runtime_observers"})

_PORT_PROTOCOLS = (RuntimeJobPort, RuntimeTimerPort, SubtitleEgress)


def _is_runtime_port(name: str) -> bool:
    """Does `name` belong to the runtime-session surface, by this repo's naming shape?

    The shape is the denominator because no single declaration owns the surface — three Protocols
    each carve out a slice and three more ports (`publish_runtime_event`, `deliver_runtime_event`,
    `receive_session`) are declared by nothing at all. A port named outside the shape escapes this
    test; that is the stated limit, and the naming is uniform across all eighteen today.

    It also draws the line the stand-in is *for*: `SubtitleEgress.query` and `command` are the mpv
    transport, which a stand-in emulates with its own answers rather than refusing.
    """
    return not name.startswith("_") and ("runtime" in name or "session" in name)


def _runtime_port_names() -> set[str]:
    """The runtime-session methods `MpvIPC` itself defines — the live surface a stand-in stands in for."""
    return {
        name for name, value in vars(MpvIPC).items() if _is_runtime_port(name) and callable(value)
    }


def _call_sites() -> dict[str, list[str]]:
    """Every `<expr>.<port>(...)` in the shipped tree, by port name."""
    files = subprocess.run(
        ["git", "ls-files", "src/saitenka"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    ports = _runtime_port_names()
    found: dict[str, list[str]] = {}
    for path in files:
        if not path.endswith(".py"):
            continue
        tree = ast.parse(Path(path).read_text(encoding="utf-8"), path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in ports:
                found.setdefault(node.func.attr, []).append(f"{path}:{node.lineno}")
    return found


def test_the_stand_in_refuses_every_runtime_port_a_feature_calls():
    reached = set(_call_sites()) - _RUNTIME_OWNER_ONLY
    missing = sorted(name for name in reached if not hasattr(NoSessionRuntime, name))

    assert not missing, (
        f"a feature calls these on its port but the stand-in lacks them: {missing} — "
        "an AttributeError in whichever feature reaches it first, not a refusal"
    )


def test_every_declared_port_protocol_member_has_a_refusal():
    declared = {
        name
        for protocol in _PORT_PROTOCOLS
        for name in protocol.__protocol_attrs__  # type: ignore[attr-defined]
        if _is_runtime_port(name)
    }
    missing = sorted(name for name in declared if not hasattr(NoSessionRuntime, name))

    assert not missing, f"declared by a port Protocol, absent from the stand-in: {missing}"


@pytest.mark.parametrize(
    "name", ["publish_runtime_event", "deliver_runtime_event", "close_session_runtime"]
)
def test_the_stand_in_answers_no_rather_than_none(name):
    """A refusal is `False`, not a falsy `None`.

    `_announce_start` reads a false answer as "run this phase's steps ourselves", so both work
    today — but the port is declared `-> bool`, and a stand-in that drifted to `None` would look
    identical until the first caller wrote `is False`.
    """
    port = getattr(NoSessionRuntime(), name)
    answer = port(object()) if name.endswith("_event") else port()
    assert answer is False


def test_the_stand_in_pump_takes_a_turn_and_hands_over_nothing():
    seen: list[object] = []
    NoSessionRuntime().receive_session(0.0, seen.append)
    assert seen == []


def test_the_call_site_scan_is_alive():
    """The negative control for both structural tests above.

    Each asserts an *empty* difference, so a `_call_sites` that silently found nothing — a
    `git ls-files` run from the wrong cwd, a naming shape that stopped matching — passes them
    exactly as a healthy tree does. Pin the denominator so "no gaps" cannot mean "no census".
    """
    reached = set(_call_sites())
    assert "submit_runtime_mpv" in reached, "the scan lost a port it found before"
    assert len(reached) >= 10, f"only {len(reached)} ports reached — the scan is degraded"

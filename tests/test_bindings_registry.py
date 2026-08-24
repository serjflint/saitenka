"""Registry-level invariants for the keybind catalog — exhaustive over BINDINGS, so a NEW binding is
covered without anyone remembering to add a test. These retire the #244 class (mine keys dead in attach
mode) as a *class*, not a single case: registration must not gate on an async-loaded dep, and every
message must round-trip binding ↔ handler.

The firing tests use ``util.press`` — a synthetic mpv ``client-message`` driven through the REAL
``_drain_events`` → ``_handle`` → command router chain — so they assert a keypress *runs* its action, not
merely that saitenka *sent* the bind string (the seam a plain FakeIPC can't cross)."""

from __future__ import annotations

import pytest
from util import FakeIPC, keybind_registry, press

from saitenka.app.bindings import BINDINGS, active_bindings
from saitenka.app.session_controller import SessionController
from saitenka.app.subtitle_render import NullRenderer

# --- exhaustive registration: no binding may be gated on a dependency ------------------------------


def test_every_global_saitenka_binding_registers_without_deps():
    """Loop over the WHOLE catalog: with anki=None and no tts (attach-mode reality), every global
    saitenka binding that carries a message must end up registered with that message. `requires`
    ("anki"/"tts") is advisory metadata, never a registration gate — the handler checks the dep live.
    A future `requires`-gated binding fails here automatically."""
    ipc = FakeIPC()
    r = SessionController(ipc, anki=None)  # deps absent at registration, exactly like attach mode
    r._register_keybinds()
    reg = keybind_registry(ipc)

    expected = {b.key: b.spec.message for b in active_bindings(r.keys, "global") if b.spec.message}
    assert expected, "no global message bindings resolved — the sweep would be vacuous"
    missing = {k: m for k, m in expected.items() if reg.get(k) != m}
    assert not missing, f"global bindings not registered with anki=None: {missing}"


def test_requires_gated_bindings_still_register_when_the_dep_is_absent():
    """The #244 regression, stated as an invariant over the gated subset: every binding whose
    `requires` is NOT "always" (the anki/tts actions — mine, mine-video, mine-all, preview, speak) must
    still register when the dep is down. This is the exact set a `requires`-gate would have silently
    dropped."""
    ipc = FakeIPC()
    r = SessionController(ipc, anki=None)  # no anki, no tts
    r._register_keybinds()
    reg = keybind_registry(ipc)

    gated = [
        b
        for b in active_bindings(r.keys, "global")
        if b.spec.requires != "always" and b.spec.message
    ]
    assert gated, "no requires-gated global bindings found — guard against the filter going empty"
    for b in gated:
        assert reg.get(b.key) == b.spec.message, (
            f"{b.spec.requires}-gated {b.key} ({b.spec.message}) dropped when the dep was absent"
        )


# --- closure: message ↔ binding ↔ handler must correspond exactly ----------------------------------


def test_binding_messages_and_handlers_correspond_exactly():
    """The three-way closed set. An exhaustive loop over ONE registry only sees registered members —
    a message constant defined but never put in BINDINGS, or a binding whose message has no handler,
    slips through. Assert the sets agree both ways so "defined but never wired" (either direction) is
    a red test."""
    binding_msgs = {b.message for b in BINDINGS if b.source == "saitenka" and b.message is not None}
    handler_msgs = set(SessionController(FakeIPC()).commands.names())

    unhandled = binding_msgs - handler_msgs
    assert not unhandled, (
        f"binding messages with no command handler (a key that no-ops): {unhandled}"
    )
    orphan = handler_msgs - binding_msgs
    assert not orphan, f"command handlers no binding can reach (dead handler / message): {orphan}"


def test_every_command_spec_is_routed_and_keeps_its_owner_and_gates():
    """Owner, cue requirement and help-modality per command, plus: the spec resolves to an action.

    An unrouted spec is the quiet failure — the policy accepts the press, the outcome is `UNBOUND`,
    and the key is documented as working. The golden carried a fifth column recording *how* each
    command was routed while some were still imperative; every row read `migrated` long before this,
    so it had stopped being able to fail and went with the machinery it measured.
    """
    commands = SessionController(FakeIPC()).commands
    actual = {
        spec.name: (spec.owner.value, spec.requires_cue, spec.allowed_while_help_open)
        for spec in commands.policy.specs
    }
    expected = {
        row[0]: (row[1], row[2] == "cue", row[3] == "help")
        for line in _COMMAND_ROUTE_CONTRACT.splitlines()
        if (row := line.split("|"))
    }

    assert actual == expected
    assert commands.routed == commands.names(), "a spec'd command with no action is a dead key"


_COMMAND_ROUTE_CONTRACT = """\
saitenka-toggle-overlay|session|global|modal
saitenka-cycle-profile|session|global|modal
saitenka-toggle-hover-pause|session|global|modal
saitenka-toggle-legacy-renderer|session|global|modal
saitenka-toggle-help|session|global|help
saitenka-help-prev|session|global|help
saitenka-help-next|session|global|help
saitenka-help-close|session|global|help
saitenka-toggle-subtitle-language|playback|global|modal
saitenka-mark-subtitle-japanese|playback|global|modal
saitenka-retry-subtitle-providers|playback|global|modal
saitenka-translate|subtitle|cue|modal
saitenka-toggle-annotations|subtitle|global|modal
saitenka-copy-line|subtitle|cue|modal
saitenka-sub-prev|subtitle|cue|modal
saitenka-sub-next|subtitle|cue|modal
saitenka-sub-replay|subtitle|cue|modal
saitenka-sub-anchor|subtitle|global|modal
saitenka-mine|interaction|cue|modal
saitenka-mine-video|interaction|cue|modal
saitenka-mine-all|interaction|cue|modal
saitenka-toggle-bookmark|interaction|cue|modal
saitenka-toggle-sidebar|interaction|global|modal
saitenka-toggle-analysis|interaction|global|modal
saitenka-preview|interaction|cue|modal
saitenka-preview-close|interaction|global|modal
saitenka-scroll-up|interaction|global|help
saitenka-scroll-down|interaction|global|help
saitenka-speak|interaction|cue|modal
saitenka-copy|interaction|cue|modal
saitenka-copy-click|interaction|cue|modal
saitenka-click|interaction|cue|modal
saitenka-kanji|interaction|cue|modal
saitenka-tip-up|interaction|global|modal
saitenka-tip-down|interaction|global|modal
saitenka-tip-close|interaction|global|modal
saitenka-sub-picker|interaction|global|modal"""


# --- firing: a press actually runs the bound action -----------------------------------------------


def test_press_runs_a_real_handler_through_the_event_loop(monkeypatch):
    """End-to-end proof the firing seam works on a REAL, unstubbed handler: F1 → toggle_help flips
    `help.open`. Exercises client-message → _drain_events → _handle → router with a genuine state
    mutation, not a spy."""
    ipc = FakeIPC()
    r = SessionController(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.osd = (1920, 1080)
    r._register_keybinds()
    assert not r.help.open

    press(r, ipc, r.keys.help_key)

    assert r.help.open  # the keypress drove the real handler to mutate real state


def test_mine_key_fires_its_handler_after_anki_loads_post_registration(monkeypatch):
    """The #244 flagship at the firing level: register while anki=None (attach mode), let anki land
    async AFTER registration with no re-register, then press the mine key — it must reach mine_current
    through the real dispatch chain. A plain registration check can't prove this; only firing does."""
    ipc = FakeIPC()
    r = SessionController(ipc, anki=None)
    r._register_keybinds()  # bound while the dep is down
    calls: list[dict] = []
    monkeypatch.setattr(r, "mine_current", lambda **k: calls.append(k))
    press(r, ipc, r.keys.mine_key)

    assert calls == [{}], "the mine key did not reach mine_current after async anki load"


def test_pressing_an_unbound_key_raises_so_the_fake_cant_pass_silently():
    """Negative control: press must distinguish bound from unbound, or a firing test could pass against
    a dead shortcut. A key never registered raises KeyError."""
    ipc = FakeIPC()
    SessionController(ipc)._register_keybinds()
    with pytest.raises(KeyError):
        # a plausible-but-unbound key: registration emits real key names, never this sentinel
        press(SessionController(ipc), ipc, "Ctrl+Alt+NeverBound")

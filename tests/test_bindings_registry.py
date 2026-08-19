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
from saitenka.app.controller import Reader
from saitenka.app.subtitle_render import NullRenderer

# --- exhaustive registration: no binding may be gated on a dependency ------------------------------


def test_every_global_saitenka_binding_registers_without_deps():
    """Loop over the WHOLE catalog: with anki=None and no tts (attach-mode reality), every global
    saitenka binding that carries a message must end up registered with that message. `requires`
    ("anki"/"tts") is advisory metadata, never a registration gate — the handler checks the dep live.
    A future `requires`-gated binding fails here automatically."""
    ipc = FakeIPC()
    r = Reader(ipc, anki=None)  # deps absent at registration, exactly like attach mode
    r._register_keybinds()
    reg = keybind_registry(ipc)

    expected = {b.key: b.spec.message for b in active_bindings(r, "global") if b.spec.message}
    assert expected, "no global message bindings resolved — the sweep would be vacuous"
    missing = {k: m for k, m in expected.items() if reg.get(k) != m}
    assert not missing, f"global bindings not registered with anki=None: {missing}"


def test_requires_gated_bindings_still_register_when_the_dep_is_absent():
    """The #244 regression, stated as an invariant over the gated subset: every binding whose
    `requires` is NOT "always" (the anki/tts actions — mine, mine-video, mine-all, preview, speak) must
    still register when the dep is down. This is the exact set a `requires`-gate would have silently
    dropped."""
    ipc = FakeIPC()
    r = Reader(ipc, anki=None)  # no anki, no tts
    r._register_keybinds()
    reg = keybind_registry(ipc)

    gated = [
        b for b in active_bindings(r, "global") if b.spec.requires != "always" and b.spec.message
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
    handler_msgs = set(Reader(FakeIPC()).commands.names())

    unhandled = binding_msgs - handler_msgs
    assert not unhandled, (
        f"binding messages with no command handler (a key that no-ops): {unhandled}"
    )
    orphan = handler_msgs - binding_msgs
    assert not orphan, f"command handlers no binding can reach (dead handler / message): {orphan}"


def test_every_temporary_command_binding_has_one_deletion_owner():
    """Each permanent spec routes either to a reducer (migrated, no binding row left to delete)
    or to exactly one temporary binding with a named deletion owner — never to both."""
    commands = Reader(FakeIPC()).commands
    assert commands.migrated.isdisjoint(dict(commands.bindings))
    actual = {
        spec.name: (
            spec.owner.value,
            spec.requires_cue,
            spec.allowed_while_help_open,
            commands.route(spec.name),
        )
        for spec in commands.policy.specs
    }
    expected = {
        row[0]: (row[1], row[2] == "cue", row[3] == "help", row[4])
        for line in _COMMAND_ROUTE_CONTRACT.splitlines()
        if (row := line.split("|"))
    }

    assert actual == expected


_COMMAND_ROUTE_CONTRACT = """\
saitenka-toggle-overlay|session|global|modal|work-package-5
saitenka-cycle-profile|session|global|modal|work-package-5
saitenka-toggle-hover-pause|session|global|modal|migrated
saitenka-toggle-help|session|global|help|migrated
saitenka-help-prev|session|global|help|migrated
saitenka-help-next|session|global|help|migrated
saitenka-help-close|session|global|help|migrated
saitenka-toggle-subtitle-language|playback|global|modal|migrated
saitenka-mark-subtitle-japanese|playback|global|modal|migrated
saitenka-retry-subtitle-providers|playback|global|modal|migrated
saitenka-translate|subtitle|cue|modal|migrated
saitenka-toggle-annotations|subtitle|global|modal|migrated
saitenka-copy-line|subtitle|cue|modal|migrated
saitenka-sub-prev|subtitle|cue|modal|migrated
saitenka-sub-next|subtitle|cue|modal|migrated
saitenka-sub-replay|subtitle|cue|modal|migrated
saitenka-sub-anchor|subtitle|global|modal|migrated
saitenka-mine|interaction|cue|modal|work-package-5
saitenka-mine-video|interaction|cue|modal|work-package-5
saitenka-mine-all|interaction|cue|modal|work-package-5
saitenka-toggle-bookmark|interaction|cue|modal|work-package-5
saitenka-toggle-sidebar|interaction|global|modal|work-package-5
saitenka-toggle-analysis|interaction|global|modal|work-package-5
saitenka-preview|interaction|cue|modal|work-package-5
saitenka-preview-close|interaction|global|modal|work-package-5
saitenka-scroll-up|interaction|global|help|work-package-5
saitenka-scroll-down|interaction|global|help|work-package-5
saitenka-speak|interaction|cue|modal|migrated
saitenka-copy|interaction|cue|modal|migrated
saitenka-copy-click|interaction|cue|modal|work-package-5
saitenka-click|interaction|cue|modal|work-package-5
saitenka-kanji|interaction|cue|modal|migrated
saitenka-tip-up|interaction|global|modal|work-package-5
saitenka-tip-down|interaction|global|modal|work-package-5
saitenka-tip-close|interaction|global|modal|work-package-5
saitenka-sub-picker|interaction|global|modal|work-package-5"""


# --- firing: a press actually runs the bound action -----------------------------------------------


def test_press_runs_a_real_handler_through_the_event_loop(monkeypatch):
    """End-to-end proof the firing seam works on a REAL, unstubbed handler: F1 → toggle_help →
    open_help flips _help_open. Exercises client-message → _drain_events → _handle → router with a
    genuine state mutation, not a spy."""
    ipc = FakeIPC()
    r = Reader(ipc)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.osd = (1920, 1080)
    r._register_keybinds()
    assert not r._help_open

    press(r, ipc, r.help_key)

    assert r._help_open  # the keypress drove the real handler to mutate real state


def test_mine_key_fires_its_handler_after_anki_loads_post_registration(monkeypatch):
    """The #244 flagship at the firing level: register while anki=None (attach mode), let anki land
    async AFTER registration with no re-register, then press the mine key — it must reach mine_current
    through the real dispatch chain. A plain registration check can't prove this; only firing does."""
    ipc = FakeIPC()
    r = Reader(ipc, anki=None)
    r._register_keybinds()  # bound while the dep is down
    calls: list[dict] = []
    monkeypatch.setattr(r, "mine_current", lambda **k: calls.append(k))
    r.anki = object()  # anki arrives later, no second registration pass

    press(r, ipc, r.mine_key)

    assert calls == [{}], "the mine key did not reach mine_current after async anki load"


def test_pressing_an_unbound_key_raises_so_the_fake_cant_pass_silently():
    """Negative control: press must distinguish bound from unbound, or a firing test could pass against
    a dead shortcut. A key never registered raises KeyError."""
    ipc = FakeIPC()
    Reader(ipc)._register_keybinds()
    with pytest.raises(KeyError):
        # a plausible-but-unbound key: registration emits real key names, never this sentinel
        press(Reader(ipc), ipc, "Ctrl+Alt+NeverBound")

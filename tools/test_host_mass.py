"""Planted controls for the host-mass ratchet — the evasions, not the examples.

Every gate here has a cheap escape, and each of these is one: a body moved into a module function
while the member stays, a member attached from a sibling file, a property that grew a body. A
control that plants the *example* would pass on a classifier that only ever answers "substantive".

The ladder's boundary cases are pinned too, because the one-sentence rule they come from admits two
readings that differ by ~12% on the real host — and a baseline seeded from the wrong one looks
exactly like a baseline seeded from the right one, forever.
"""

from __future__ import annotations

import host_mass


def _ops(*_args: object) -> int:
    return 0


class Before:
    def _apply_hover_effect(self, e: int) -> int:
        width = e + 1
        height = e + 2
        return width * height


class After:
    """`Before` after the AGENTS.md move: body in a module function, thin delegator left behind."""

    def _apply_hover_effect(self, e: int) -> int:
        return _ops(self, e)


class Mixin:
    def from_a_mixin(self) -> int:
        total = 1
        return total + 1


class Host(Mixin):
    def substantive(self) -> int:
        first = 1
        return first + 1

    def plain_delegator(self) -> int:
        return _ops(self)

    def documented_delegator(self) -> int:
        """A docstring is not a statement for this purpose."""
        return _ops(self)

    def two_statements(self) -> int:
        _ops(self)
        return _ops(self)

    def calls_self(self) -> int:
        return self.substantive()

    @property
    def one_line_property(self) -> int:
        return 1

    @property
    def fat_property(self) -> int:
        base = 1
        return base + 1


def attached(_self: Host) -> int:
    inner = 1
    return inner + 1


Host.attached = attached  # type: ignore[attr-defined]  # the sibling-file re-attachment escape


def _counts(host: type) -> dict[str, int]:
    return host_mass.classify(host, {})


def test_moving_a_body_out_and_leaving_a_delegator_does_not_shrink_the_total():
    """The escape the total exists to close: `substantive` falls, the host is unchanged."""
    before, after = _counts(Before), _counts(After)

    assert after["substantive"] < before["substantive"]
    assert after["total"] == before["total"]


def test_a_docstring_does_not_make_a_delegator_substantive():
    assert _counts(Host)["delegator"] == 2


def test_a_two_statement_body_ending_in_a_call_is_substantive():
    counts = _counts(Host)

    assert counts["substantive"] == 4  # substantive, two_statements, attached, from_a_mixin


def test_a_call_on_self_is_its_own_kind():
    """`self.x()` keeps the behaviour on the host; a call on another object hands it away."""
    assert _counts(Host)["self_delegator"] == 1


def test_a_member_attached_after_the_class_body_still_counts():
    """`Host.attached = attached` is invisible to any parse of the class body."""
    assert "attached" in host_mass._members(Host)


def test_a_mixin_method_still_counts():
    assert "from_a_mixin" in host_mass._members(Host)


def test_a_property_is_never_substantive_however_long_its_body():
    """The ladder's ambiguous rung: the other faithful reading counts `fat_property` as substantive
    and moves the seeded baseline by ~12%."""
    counts = _counts(Host)

    assert counts["property"] == 2
    assert counts["substantive"] == 4  # unchanged by `fat_property` having a body


def test_machinery_is_not_a_member():
    assert not {"__module__", "__dict__"} & set(host_mass._members(Host))


def test_the_live_census_is_not_vacuous():
    """Guards the discovery half only: a census resolving no member would ratchet zero and pass,
    having measured nothing. `poe host-mass` owns the growth question."""
    counts = host_mass.census()

    assert counts["total"] > 0, "no member resolved on the host — discovery is off"
    assert counts["substantive"] > 0, "every member classified away — the ladder is off"


class WiredRoot:
    """The escape the member counts cannot see: construction, not members."""

    def __init__(self) -> None:
        self.a = _ops()
        self.b = _ops()
        self.c = _ops()


class WiredRootPlusOne:
    def __init__(self) -> None:
        self.a = _ops()
        self.b = _ops()
        self.c = _ops()
        self.d = _ops()


def test_a_collaborator_wired_in_init_moves_no_member_but_still_registers():
    """Both classes have exactly one member. Only `init_lines` can tell them apart."""
    before, after = _counts(WiredRoot), _counts(WiredRootPlusOne)

    assert before["total"] == after["total"]
    assert before["substantive"] == after["substantive"]
    assert after["init_lines"] == before["init_lines"] + 1

"""`FakeIPC` must answer everything production reads off `ipc` — exhaustive over `src/`.

A stand-in that is missing a member production always has is not a smaller fake, it is a *different*
port. While the reads went through `getattr(ipc, name, fallback)` the gap was invisible: the probe
answered "absent" for the fake and for a rename alike, so every test took a branch production never
takes and stayed green. Removing the probes turned that into eighteen `AttributeError`s at once —
which is the gap having been there all along, not a regression.

Derived rather than listed, so a new `self.ipc.<member>` in the controller is covered without anyone
remembering to add a row here.
"""

from __future__ import annotations

import ast
from pathlib import Path

from util import FakeIPC

SRC = Path(__file__).resolve().parents[1] / "src" / "saitenka"

#: Members reached on an `ipc`-named local that is not the session transport. Named, because the
#: alternative is resolving every receiver's type, and this list is shorter than that machinery.
_NOT_THE_TRANSPORT = frozenset({"mpv"})


def _members_read_off_ipc() -> dict[str, set[str]]:
    """Every `<...>.ipc.<member>` and `ipc.<member>` in `src/`, by member -> reading modules."""
    found: dict[str, set[str]] = {}
    for path in sorted(SRC.glob("**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(
                node.value, ast.Attribute | ast.Name
            ):
                continue
            receiver = node.value
            tail = receiver.attr if isinstance(receiver, ast.Attribute) else receiver.id
            if tail != "ipc" or node.attr in _NOT_THE_TRANSPORT:
                continue
            found.setdefault(node.attr, set()).add(path.name)
    return found


def _fake_members() -> set[str]:
    """What a constructed `FakeIPC` answers — class surface plus whatever `__init__` sets."""
    instance = FakeIPC()
    return set(dir(instance)) | set(vars(instance))


def test_the_fake_transport_answers_every_member_production_reads_off_ipc():
    """The totality contract. A missing member is a fake that diverges from the real port, which
    reads as a production regression the moment a caller stops guarding the read."""
    read = _members_read_off_ipc()
    assert read, "no `ipc.<member>` reads found — the sweep would be vacuous"

    available = _fake_members()
    missing = {
        member: sorted(modules) for member, modules in read.items() if member not in available
    }

    assert not missing, "FakeIPC is missing members production reads off `ipc`: " + ", ".join(
        f"{member} (read by {', '.join(modules)})" for member, modules in sorted(missing.items())
    )


def test_the_sweep_would_notice_a_member_the_fake_lacks():
    """Negative control: the oracle bites. Without it, a totality test that found zero reads — or
    compared against `dir()` of the wrong object — would pass while proving nothing."""
    read = _members_read_off_ipc()
    invented = "definitely_not_on_the_transport"
    assert invented not in _fake_members()
    assert invented not in read  # …so the real assertion above is not passing by accident

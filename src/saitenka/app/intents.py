"""Effects more than one reducer emits.

A reducer owns the decisions of its own feature, but telling the user something is not a subtitle
concern or a hover concern — it is the runtime's. Keeping one `Announce` here means one executor
arm and one toast contract, rather than a per-feature copy that drifts in kind names.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Announce:
    text: str
    kind: str = "ok"

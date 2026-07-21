"""Layout blocks produced by the structured-content walker.

A :class:`Block` is one block-level unit (paragraph / list item), carrying an inline *flow*
(``Span | RubyBox | ImgBox``) that :func:`overlay.render.flow.render_flow` renders.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from overlay.render.flow import Inline


@dataclass
class Block:
    flow: list[Inline] = field(default_factory=list)
    kind: str = "para"  # 'para' | 'list-item'
    list_type: str | None = None  # 'ul' | 'ol' when kind == 'list-item'
    ordinal: int | None = None  # 1-based index for ordered lists
    indent: int = 0  # nesting depth (list nesting)

    def is_empty(self) -> bool:
        return not self.flow

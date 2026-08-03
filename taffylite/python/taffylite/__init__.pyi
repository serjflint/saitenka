"""Type stub for the taffylite Rust extension (shipped with the wheel via maturin `python-source`)."""

from typing import Sequence

Edges = tuple[float, float, float, float]

class Tree:
    """An imperative fixed-size flex-tree builder over taffy. Handles are dense creation-order indices;
    :meth:`compute` returns absolute rects index-aligned to them."""

    def __init__(self) -> None: ...
    def add_leaf(self, width: float, height: float, margin: Edges = ...) -> int: ...
    def add_flex(
        self,
        children: Sequence[int],
        direction: str = ...,
        gap: float = ...,
        padding: Edges = ...,
        margin: Edges = ...,
        width: float | None = ...,
        height: float | None = ...,
        wrap: bool = ...,
    ) -> int: ...
    def set_root(self, handle: int) -> None: ...
    def compute(
        self, available_width: float | None = ...
    ) -> list[tuple[float, float, float, float]]: ...

def column(
    heights: Sequence[float], gaps: Sequence[float], top_pad: float
) -> tuple[list[int], list[int]]:
    """Row-stack geometry (the ``LayoutBackend.cumulative`` primitive): ``(starts, ends)`` for
    fixed-height rows with per-row trailing gaps inside ``top_pad`` top padding."""
    ...

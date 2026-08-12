"""An independent pure-Python reference solver for the fixed-size flexbox subset taffylite exposes.

This exists to give `taffylite.Tree` a *differential* oracle, not just recorded-rect replay: the
vendored `taffy_gentest_flex.json` corpus pins Chrome-derived expected rects, but only for the ~14
cases someone hand-vendored. A second, independent implementation lets Hypothesis-generated trees be
checked against taffylite for free — the same "two independent computations must agree" discipline the
`DefaultLayoutBackend`/`FlexColumnBackend` split already uses for the 1-D column path.

Faithfulness is earned, not assumed: `test_layout_backend.py` first asserts THIS solver reproduces the
vendored Chrome rects on every fixture (with a negative control proving that check can fail), and only
then trusts it as the oracle for random trees. It implements exactly taffylite's subset — leaves with a
fixed `(w, h)` + margin; flex containers with row/column direction, symmetric `gap`, padding, margin,
fixed-or-auto box size, and `wrap` — under taffy/CSS defaults (`justify-content: flex-start`,
`align-items: stretch`, no grow/shrink/basis). A definite-size child never stretches; an auto-cross flex
child stretches to its line's cross size (the `nested_*` gentest cases turn on exactly this).

Node shape mirrors the fixtures: a list of nodes in handle order, each `{"leaf": [w, h]}` /
`{"leaf": [w, h, [l, t, r, b]]}` / `{"flex": {...}}`, plus a `root` handle. `solve(nodes, root)` returns
integer `[x, y, w, h]` rects index-aligned to handles — the exact contract of `taffylite.Tree.compute()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Edges = tuple[float, float, float, float]  # (left, top, right, bottom)


class OverflowUnsupported(Exception):
    """Raised when a child overflows a definite-main container — taffy would apply flex-shrink, which
    this reference deliberately does not model (that domain is left to the recorded-rect oracle)."""


@dataclass(slots=True)
class _Node:
    kind: str  # "leaf" | "flex"
    w: float = 0.0
    h: float = 0.0
    margin: Edges = (0.0, 0.0, 0.0, 0.0)
    children: list[int] = field(default_factory=list)
    direction: str = "column"
    gap: float = 0.0
    padding: Edges = (0.0, 0.0, 0.0, 0.0)
    fixed_w: float | None = None
    fixed_h: float | None = None
    wrap: bool = False


def _parse(node: dict) -> _Node:
    if "leaf" in node:
        spec = node["leaf"]
        margin = tuple(float(v) for v in spec[2]) if len(spec) > 2 else (0.0, 0.0, 0.0, 0.0)
        return _Node(kind="leaf", w=float(spec[0]), h=float(spec[1]), margin=margin)  # type: ignore[arg-type]
    f = node["flex"]
    fw, fh = f.get("width"), f.get("height")
    return _Node(
        kind="flex",
        children=list(f["children"]),
        direction=f.get("direction", "column"),
        gap=float(f.get("gap", 0.0)),
        padding=tuple(float(v) for v in f.get("padding", (0.0, 0.0, 0.0, 0.0))),  # type: ignore[arg-type]
        margin=tuple(float(v) for v in f.get("margin", (0.0, 0.0, 0.0, 0.0))),  # type: ignore[arg-type]
        fixed_w=None if fw is None else float(fw),
        fixed_h=None if fh is None else float(fh),
        wrap=bool(f.get("wrap", False)),
    )


@dataclass(slots=True)
class _Box:
    """A laid-out node: its resolved (w, h) plus each child's offset from this node's border-box origin."""

    handle: int
    w: float
    h: float
    children: list[tuple[_Box, float, float]] = field(default_factory=list)  # (box, dx, dy)


def _main_cross_edges(e: Edges, *, row: bool) -> tuple[float, float, float, float]:
    """(main_lead, main_trail, cross_lead, cross_trail) of an (l, t, r, b) edge set for the axis."""
    left, top, right, bottom = e
    return (left, right, top, bottom) if row else (top, bottom, left, right)


class _Solver:
    def __init__(self, nodes: list[dict]) -> None:
        self.nodes = [_parse(n) for n in nodes]

    def _layout(self, idx: int, impose_w: float | None, impose_h: float | None) -> _Box:
        """Resolve node ``idx`` (and its subtree) to a ``_Box``. ``impose_w``/``impose_h`` are a
        definite size handed down by a stretching parent (align-items/align-content: stretch); a set
        dimension is honoured whether it is the child's main or cross axis, matching CSS.

        Scope: the no-shrink domain. A definite-main container is assumed large enough to hold its
        children (``flex-shrink`` never fires) — ``solve`` raises :class:`OverflowUnsupported` if a
        child overflows, so those trees are excluded from the differential and left to the
        recorded-rect oracle instead of reimplementing taffy's proportional shrink + min-content."""
        node = self.nodes[idx]
        if node.kind == "leaf":  # definite: never stretches, impose_* ignored
            return _Box(idx, node.w, node.h)

        row = node.direction == "row"
        pad_main_lead, pad_main_trail, pad_cross_lead, pad_cross_trail = _main_cross_edges(
            node.padding, row=row
        )
        pad_main = pad_main_lead + pad_main_trail
        pad_cross = pad_cross_lead + pad_cross_trail

        # A dimension is definite if fixed on this node OR imposed by a stretching parent — same rule
        # for width and height, so an imposed size on the child's MAIN axis (a stretched nested flex)
        # is honoured, not silently dropped.
        def_w = node.fixed_w if node.fixed_w is not None else impose_w
        def_h = node.fixed_h if node.fixed_h is not None else impose_h
        def_main, def_cross = self._split(def_w, def_h, row=row)

        # Hypothetical child sizes (no stretch imposed yet) → their outer main/cross extents.
        hyp = [self._layout(c, None, None) for c in node.children]
        pairs = list(zip(hyp, node.children, strict=True))
        c_main = [self._child_main(h, self.nodes[c], row=row) for h, c in pairs]
        c_cross = [self._child_cross(h, self.nodes[c], row=row) for h, c in pairs]

        main_avail = None if def_main is None else def_main - pad_main
        lines = self._break_lines(c_main, node.gap, main_avail if node.wrap else None)
        line_used = [sum(c_main[i] for i in ln) + node.gap * (len(ln) - 1) for ln in lines]
        line_cross = [max((c_cross[i] for i in ln), default=0.0) for ln in lines]

        if main_avail is not None and any(u > main_avail + 1e-6 for u in line_used):
            raise OverflowUnsupported  # a child overflows main → taffy would shrink; out of domain

        main_content = def_main - pad_main if def_main is not None else max(line_used, default=0.0)
        if def_cross is not None:
            cross_content = def_cross - pad_cross
        else:
            cross_content = sum(line_cross) + node.gap * (len(lines) - 1)

        # align-content: stretch — a definite cross distributes its leftover equally across lines
        # (the single-line case degenerates to "fill the whole cross"); an auto cross hugs each line.
        if def_cross is not None and lines:
            free = max(0.0, cross_content - sum(line_cross) - node.gap * (len(lines) - 1))
            eff_line_cross = [lc + free / len(lines) for lc in line_cross]
        else:
            eff_line_cross = line_cross

        box = _Box(idx, *self._unsplit(main_content + pad_main, cross_content + pad_cross, row=row))
        cross_cursor = pad_cross_lead
        for ln, eff_cross in zip(lines, eff_line_cross, strict=True):
            main_cursor = pad_main_lead
            for i in ln:
                child = self.nodes[node.children[i]]
                cm_lead, _, cc_lead, cc_trail = _main_cross_edges(child.margin, row=row)
                stretched = self._maybe_stretch(
                    child, hyp[i], eff_cross - cc_lead - cc_trail, row=row
                )
                child_box = stretched if stretched is not None else hyp[i]
                dx, dy = self._unsplit(main_cursor + cm_lead, cross_cursor + cc_lead, row=row)
                box.children.append((child_box, dx, dy))
                main_cursor += c_main[i] + node.gap
            cross_cursor += eff_cross + node.gap
        return box

    def _maybe_stretch(
        self, child: _Node, hyp: _Box, cross_size: float, *, row: bool
    ) -> _Box | None:
        """Re-lay a flex child whose PARENT-cross dimension is auto to fill ``cross_size`` (align-items:
        stretch). Returns None when the child does not stretch (a leaf, or a definite cross size)."""
        if child.kind != "flex":
            return None
        cross_is_auto = (child.fixed_h if row else child.fixed_w) is None
        if not cross_is_auto:
            return None
        return self._layout(
            hyp.handle,
            impose_w=None if row else cross_size,
            impose_h=cross_size if row else None,
        )

    @staticmethod
    def _child_main(box: _Box, node: _Node, *, row: bool) -> float:
        cm_lead, cm_trail, _, _ = _main_cross_edges(node.margin, row=row)
        return cm_lead + (box.w if row else box.h) + cm_trail

    @staticmethod
    def _child_cross(box: _Box, node: _Node, *, row: bool) -> float:
        _, _, cc_lead, cc_trail = _main_cross_edges(node.margin, row=row)
        return cc_lead + (box.h if row else box.w) + cc_trail

    @staticmethod
    def _unsplit(main: float, cross: float, *, row: bool) -> tuple[float, float]:
        return (main, cross) if row else (cross, main)

    @staticmethod
    def _split(w: float | None, h: float | None, *, row: bool) -> tuple[float | None, float | None]:
        """(main, cross) of a (w, h) pair for the axis — the inverse of :meth:`_unsplit`."""
        return (w, h) if row else (h, w)

    @staticmethod
    def _break_lines(main_sizes: list[float], gap: float, avail: float | None) -> list[list[int]]:
        """Greedy flex-wrap: fill a line until the next child's outer main + gap would overflow
        ``avail`` (each line holds ≥1 child). ``avail is None`` → one line with everything."""
        n = len(main_sizes)
        if avail is None or n == 0:
            return [list(range(n))] if n else [[]]
        lines: list[list[int]] = []
        cur: list[int] = []
        used = 0.0
        for i, m in enumerate(main_sizes):
            add = m if not cur else gap + m
            if cur and used + add > avail:
                lines.append(cur)
                cur, used = [i], m
            else:
                cur.append(i)
                used += add
        if cur:
            lines.append(cur)
        return lines

    def solve(self, root: int) -> dict[int, list[int]]:
        root_box = self._layout(root, None, None)
        out: dict[int, list[int]] = {}
        self._flatten(root_box, 0.0, 0.0, out)
        return out

    def _flatten(self, box: _Box, x: float, y: float, out: dict[int, list[int]]) -> None:
        out[box.handle] = [round(x), round(y), round(box.w), round(box.h)]
        for child_box, dx, dy in box.children:
            self._flatten(child_box, x + dx, y + dy, out)


def solve(nodes: list[dict], root: int) -> dict[int, list[int]]:
    """Reference layout: integer ``[x, y, w, h]`` rects keyed by handle (the subset reachable from
    ``root``), matching ``taffylite.Tree.compute()``'s contract for the nodes it lays out."""
    return _Solver(nodes).solve(root)

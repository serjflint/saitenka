# /// script
# requires-python = ">=3.11"
# ///
"""Vendor taffy's own gentest corpus as taffylite `Tree` fixtures.

taffy generates its conformance tests from HTML fixtures rendered in Chrome, committing the
Chrome-derived expected layout as `assert_eq!`s in `tests/generated/**/*.rs` (see taffy's
`scripts/gentest`). Those baked values are the *external* oracle — the same "steal the real
corpus, don't author it" move as overlay's vendored UAX #14 `LineBreakTest.txt` (issue #112).

This reads a taffy checkout's `tests/generated/flex/*.rs`, keeps only the fixtures whose every
node maps onto taffylite's narrow fixed-size flex API (`Tree.add_leaf` / `add_flex`), and emits
`tests/fixtures/taffy_gentest_flex.json`. The excluded subset — anything taffylite cannot express —
is documented in that file's header and in `tests/fixtures/README.md`.

Only the `__border_box` variant of each fixture is read: taffylite leaves `box_sizing` at taffy's
`BorderBox` default, so the `__content_box` sibling would not match.

Re-run after bumping the pinned taffy version (keep TAFFY_TAG in sync with `Cargo.lock`):

    git clone --depth 1 --branch v0.7.7 https://github.com/DioxusLabs/taffy /tmp/taffy-src
    uv run taffylite/tools/gen_taffy_fixtures.py /tmp/taffy-src
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TAFFY_TAG = "v0.7.7"
TAFFY_REPO = "https://github.com/DioxusLabs/taffy"

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "taffy_gentest_flex.json"

# A node is expressible only if every style property it carries is in this whitelist. Anything else
# (flex_grow/shrink/basis, min/max, aspect_ratio, %/auto content sizing, align_*/justify_*, position,
# overflow, grid, content-box, text/measure leaves) means taffylite cannot build the node → skip the
# whole fixture. Leaves additionally may not carry `padding` (taffylite's leaf has no padding param).
_LEAF_PROPS = {"size", "margin", "box_sizing"}
_FLEX_PROPS = {
    "display",
    "flex_direction",
    "flex_wrap",
    "size",
    "margin",
    "padding",
    "gap",
    "box_sizing",
}


class Unsupported(Exception):
    """A fixture uses something outside taffylite's fixed-size flex subset."""


def _matching_brace(text: str, open_idx: int) -> int:
    """Index just past the `}` matching the `{` at `open_idx`."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise Unsupported("unbalanced braces")


def _border_box_body(rs: str, stem: str) -> str:
    m = re.search(rf"fn {re.escape(stem)}__border_box\(\)\s*", rs)
    if not m:
        raise Unsupported("no border_box variant")
    brace = rs.index("{", m.end())
    return rs[brace : _matching_brace(rs, brace)]


def _top_level_props(style_body: str) -> dict[str, str]:
    """Split a `Style { ... }` inner body into `{prop_name: value_text}`, skipping nested braces."""
    props: dict[str, str] = {}
    i, n = 0, len(style_body)
    while i < n:
        m = re.match(r"\s*([a-z_]+)\s*:\s*", style_body[i:])
        if not m:
            break
        key = m.group(1)
        i += m.end()
        depth, start = 0, i
        while i < n:
            c = style_body[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == "," and depth == 0:
                break
            i += 1
        props[key] = style_body[start:i].strip()
        i += 1  # past the comma
    props.pop("", None)
    return props


def _length(value: str) -> float:
    """A `...::Length(Nf32)` scalar; reject Percent/Auto/anything else."""
    m = re.fullmatch(r"[\w:]*Length\((-?[\d.]+)f32\)", value.strip())
    if not m:
        raise Unsupported(f"non-length dimension: {value!r}")
    return float(m.group(1))


def _size(value: str) -> tuple[float | None, float | None]:
    """A `Size { width: .., height: .. }`; `Auto` → None (unset), Percent → reject."""

    def one(axis: str) -> float | None:
        m = re.search(rf"{axis}\s*:\s*([^,}}]+)", value)
        if not m:
            return None
        v = m.group(1).strip()
        if "Auto" in v:
            return None
        return _length(v)

    return one("width"), one("height")


def _edges(value: str) -> tuple[float, float, float, float]:
    return tuple(  # type: ignore[return-value]
        _length(re.search(rf"{side}\s*:\s*([^,}}]+)", value).group(1))  # type: ignore[union-attr]
        for side in ("left", "top", "right", "bottom")
    )


def _parse_nodes(body: str) -> list[dict]:
    """Every `let nodeX = taffy.new_leaf/new_with_children(Style{..}[, &[..]])`, in declaration order."""
    nodes: list[dict] = []
    for m in re.finditer(r"let (node\w*) = taffy\s*\.(new_leaf|new_with_children)\(", body):
        var, kind = m.group(1), m.group(2)
        style_at = body.index("taffy::style::Style {", m.end())
        inner_open = body.index("{", style_at)
        inner = body[inner_open + 1 : _matching_brace(body, inner_open) - 1]
        props = _top_level_props(inner)

        children: list[str] = []
        if kind == "new_with_children":
            after = _matching_brace(body, inner_open)
            lst = re.search(r"&\[([^\]]*)\]", body[after:])
            children = re.findall(r"node\w*", lst.group(1)) if lst else []

        nodes.append({"var": var, "kind": kind, "props": props, "children": children})
    return nodes


def _to_leaf(props: dict[str, str]) -> dict:
    if not props.keys() <= _LEAF_PROPS:
        raise Unsupported(f"leaf props {sorted(props.keys() - _LEAF_PROPS)}")
    if "ContentBox" in props.get("box_sizing", ""):
        raise Unsupported("content-box")
    w, h = _size(props["size"]) if "size" in props else (None, None)
    if w is None or h is None:
        raise Unsupported("leaf without fixed size")
    out: dict = {"w": w, "h": h}
    if "margin" in props:
        out["margin"] = _edges(props["margin"])
    return out


def _to_flex(props: dict[str, str]) -> dict:
    if not props.keys() <= _FLEX_PROPS:
        raise Unsupported(f"flex props {sorted(props.keys() - _FLEX_PROPS)}")
    if "ContentBox" in props.get("box_sizing", ""):
        raise Unsupported("content-box")
    if "Flex" not in props.get("display", "Flex"):
        raise Unsupported("non-flex display")

    # taffylite maps only Row/Column and NoWrap/Wrap — the *-reverse variants change child ordering
    # and are inexpressible.
    fd = props.get("flex_direction", "")
    if "Reverse" in fd:
        raise Unsupported("reverse flex-direction")
    direction = "column" if "Column" in fd else "row"  # taffy's default is Row (omitted == row)
    fw = props.get("flex_wrap", "")
    if "WrapReverse" in fw:
        raise Unsupported("wrap-reverse")
    wrap = "Wrap" in fw

    gap_main = 0.0
    if "gap" in props:
        gw, gh = _size(props["gap"])
        gw, gh = gw or 0.0, gh or 0.0
        # taffylite forces a symmetric gap; a wrapping container also consumes the cross gap, so an
        # asymmetric wrap gap is inexpressible. No-wrap uses only the main axis.
        if wrap and gw != gh:
            raise Unsupported("asymmetric gap under wrap")
        gap_main = gw if direction == "row" else gh

    out: dict = {"direction": direction, "gap": gap_main, "wrap": wrap}
    if "padding" in props:
        out["padding"] = _edges(props["padding"])
    if "margin" in props:
        out["margin"] = _edges(props["margin"])
    w, h = _size(props["size"]) if "size" in props else (None, None)
    out["width"], out["height"] = w, h
    return out


_ASSERT = (
    r"assert_eq!\(size\.width, (-?[\d.]+)f32.*?, (node\w*),.*?\n"
    r".*?assert_eq!\(size\.height, (-?[\d.]+)f32.*?\n"
    r".*?assert_eq!\(location\.x, (-?[\d.]+)f32.*?\n"
    r".*?assert_eq!\(location\.y, (-?[\d.]+)f32"
)


def _parse_expected(body: str) -> dict[str, tuple[float, float, float, float]]:
    """var → (w, h, rel_x, rel_y) from the Chrome-derived asserts (locations are parent-relative)."""
    out: dict[str, tuple[float, float, float, float]] = {}
    for m in re.finditer(_ASSERT, body):
        w, h, x, y = float(m[1]), float(m[3]), float(m[4]), float(m[5])
        out[m[2]] = (w, h, x, y)
    return out


def _absolute(nodes: list[dict], root: str, rel: dict[str, tuple]) -> dict[str, tuple]:
    """taffylite returns absolute rects; taffy asserts parent-relative — accumulate down the tree."""
    parent = {c: node["var"] for node in nodes for c in node["children"]}
    abs_pos: dict[str, tuple[float, float]] = {}

    def resolve(var: str) -> tuple[float, float]:
        if var in abs_pos:
            return abs_pos[var]
        _, _, rx, ry = rel[var]
        if var == root:
            abs_pos[var] = (rx, ry)
        else:
            px, py = resolve(parent[var])
            abs_pos[var] = (px + rx, py + ry)
        return abs_pos[var]

    rects = {}
    for node in nodes:
        v = node["var"]
        w, h, _, _ = rel[v]
        ax, ay = resolve(v)
        rects[v] = (round(ax), round(ay), round(w), round(h))
    return rects


def build_case(path: Path) -> dict | None:
    stem = path.stem
    body = _border_box_body(path.read_text(encoding="utf-8"), stem)
    nodes = _parse_nodes(body)
    if not nodes:
        return None
    handle = {node["var"]: i for i, node in enumerate(nodes)}
    child_vars = {c for node in nodes for c in node["children"]}
    if not child_vars <= handle.keys():
        raise Unsupported("child of an unparsed node kind")  # e.g. new_leaf_with_context (measure)
    roots = [node["var"] for node in nodes if node["var"] not in child_vars]
    if len(roots) != 1:
        raise Unsupported(f"{len(roots)} roots")
    root = roots[0]

    json_nodes: list[dict] = []
    for node in nodes:
        if node["kind"] == "new_leaf":
            leaf = _to_leaf(node["props"])
            entry = [leaf["w"], leaf["h"]]
            if "margin" in leaf:
                entry.append(list(leaf["margin"]))
            json_nodes.append({"leaf": entry})
        else:
            flex = _to_flex(node["props"])
            spec: dict = {"children": [handle[c] for c in node["children"]]}
            spec["direction"] = flex["direction"]
            if flex["gap"]:
                spec["gap"] = flex["gap"]
            if "padding" in flex:
                spec["padding"] = list(flex["padding"])
            if "margin" in flex:
                spec["margin"] = list(flex["margin"])
            if flex["width"] is not None:
                spec["width"] = flex["width"]
            if flex["height"] is not None:
                spec["height"] = flex["height"]
            if flex["wrap"]:
                spec["wrap"] = True
            json_nodes.append({"flex": spec})

    rel = _parse_expected(body)
    if set(rel) != {node["var"] for node in nodes}:
        raise Unsupported("assert/node mismatch")
    abs_rects = _absolute(nodes, root, rel)
    rects = [list(abs_rects[node["var"]]) for node in nodes]

    return {"name": stem, "nodes": json_nodes, "root": handle[root], "rects": rects}


def main() -> None:
    taffy = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/taffy-src")
    gen = taffy / "tests" / "generated" / "flex"
    if not gen.is_dir():
        raise SystemExit(f"taffy generated tests not found under {gen}")

    cases, skipped = [], 0
    for path in sorted(gen.glob("*.rs")):
        try:
            case = build_case(path)
        except Unsupported:
            skipped += 1
            continue
        if case is not None:
            cases.append(case)

    header = (
        f"Vendored from taffy {TAFFY_TAG} ({TAFFY_REPO}), MIT. Chrome-derived expected rects from the "
        "`__border_box` variant of `tests/generated/flex/*.rs`. Regenerate + excluded-subset rationale: "
        "see tests/fixtures/README.md and taffylite/tools/gen_taffy_fixtures.py."
    )
    OUT.write_text(
        json.dumps({"_source": header, "cases": cases}, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(cases)} cases to {OUT} ({skipped} skipped as inexpressible)")


if __name__ == "__main__":
    main()

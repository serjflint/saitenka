"""Walk Yomitan structured-content JSON into layout :class:`Block`s.

Structured content is a recursive tree: a node is a string (text), a list of nodes, or an object
``{"tag": ..., "content": ..., "style": {...}}``. We support the subset the dictionary panel needs —
text, ``span``/style, ``ruby``(``rb``/``rt``), ``br``, ``ul``/``ol``/``li``, ``a`` (link → styled
text), ``img`` (→ opaque box), ``table`` (→ each row a line, cells ``│``-separated — a readable
minimal grid, not column-aligned; true alignment is a layout-engine job). **Unknown tags never fail**:
we recurse into their content and flatten to text, so a novel dictionary can't break rendering.

Reference: Yomitan ``dictionary-term-bank-v3`` structured-content schema.
"""

from __future__ import annotations

import re
import threading
import urllib.parse
from dataclasses import replace

from overlay.model import RGBA, Span, Style
from overlay.render.flow import ChipBox, img_box, ruby
from overlay.render.ruby import RubyBox
from overlay.sc.model import Block

_EXTERNAL_SCHEMES = ("http:", "https:", "mailto:", "//", "ftp:", "tel:")
_LINK_EXTERNAL: RGBA = (128, 132, 138, 255)  # muted gray for inert external source links


def link_query(href: str | None, text: str = "") -> str | None:
    """Resolve an ``<a>``'s target **dictionary term** for opening a related-note nested tooltip,
    or None if it isn't an internal cross-reference. Yomitan cross-refs use ``?query=<term>&…``;
    other internal/relative links carry the term as their visible text. **External** links (a
    dictionary's source-attribution link, http/https/mailto/…) are NOT related notes → left inert."""
    if href is None:
        return text.strip() or None  # bare <a> → its own text is the term
    if not isinstance(href, str):  # runtime guard: malformed SC can carry non-str hrefs
        return None  # type: ignore[unreachable]
    h = href.strip()
    m = re.search(r"[?&]query=([^&]+)", h)
    if m:
        return urllib.parse.unquote(m.group(1)).strip() or None
    if h.lower().startswith(_EXTERNAL_SCHEMES):
        return None  # external source link → not a related note
    return text.strip() or None  # relative / fragment link → visible text


_BORDER_KEYS = ("borderColor", "borderStyle", "borderWidth", "border")
_BG_KEYS = ("backgroundColor", "background")
_BOX_KEYS = frozenset(
    (*_BG_KEYS, *_BORDER_KEYS, "borderRadius")
)  # pill test; hoisted (hot in the SC walk)

INLINE_TAGS = {"span", "a", "em", "strong", "b", "i", "u", "code", "ruby", "rt", "rb", "sub", "sup"}
BLOCK_TAGS = {"div", "p", "ul", "ol", "li", "details", "summary", "table", "tr", "td", "th"}
_LI_BLOCK_TAGS = {"div", "p", "details", "summary", "table"}

_TABLE_SEP: RGBA = (150, 150, 150, 255)  # muted vertical bar between cells — reads as a divider


def _table_rows(node: dict) -> list[dict]:
    """The ``tr`` rows of a ``table``, descending through ``thead``/``tbody``/``tfoot`` wrappers."""
    rows: list[dict] = []
    content = node.get("content")
    for child in content if isinstance(content, list) else [content]:
        if not isinstance(child, dict):
            continue
        tag = child.get("tag")
        if tag == "tr":
            rows.append(child)
        elif tag in {"thead", "tbody", "tfoot"}:
            rows.extend(_table_rows(child))
    return rows


def _table_cells(tr: dict) -> list[dict]:
    content = tr.get("content")
    return [
        c
        for c in (content if isinstance(content, list) else [content])
        if isinstance(c, dict) and c.get("tag") in {"td", "th"}
    ]


_NAMED: dict[str, RGBA] = {
    "black": (0, 0, 0, 255),
    "white": (255, 255, 255, 255),
    "red": (200, 40, 40, 255),
    "blue": (40, 90, 200, 255),
    "green": (40, 150, 60, 255),
    "gray": (120, 120, 120, 255),
    "grey": (120, 120, 120, 255),
}


def _parse_color(v: str | None, fallback: RGBA) -> RGBA:
    if not v or not isinstance(v, str):
        return fallback
    s = v.strip().lower()
    if s in _NAMED:
        return _NAMED[s]
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) >= 6:
            try:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
            except ValueError:
                return fallback
    if s.startswith("rgb"):
        nums = [int(float(n)) for n in s[s.find("(") + 1 : s.find(")")].split(",")[:3]]
        if len(nums) == 3:
            return (nums[0], nums[1], nums[2], 255)
    return fallback


def _parse_size(v, base: int) -> int:
    if v is None:
        return base
    if isinstance(v, (int, float)):
        return max(1, round(v))
    s = str(v).strip().lower()
    try:
        if s.endswith("em"):
            return max(1, round(base * float(s[:-2])))
        if s.endswith("%"):
            return max(1, round(base * float(s[:-1]) / 100))
        if s.endswith("px"):
            return max(1, round(float(s[:-2])))
        return max(1, round(float(s)))
    except ValueError:
        return base


_SUB_SUP_EM = 0.72  # sub/sup annotations render at ~0.72em of the parent size
_SUPER_VALS = {"super", "superscript", "text-top", "top"}
_SUB_VALS = {"sub", "subscript", "text-bottom", "bottom"}


def _valign(tag: str | None, vertical_align) -> int:
    """+1 (raise) for sup, −1 (lower) for sub — from the tag OR ``style.verticalAlign``. 0 otherwise."""
    if tag == "sup":
        return 1
    if tag == "sub":
        return -1
    if isinstance(vertical_align, str):
        v = vertical_align.strip().lower()
        if v in _SUPER_VALS:
            return 1
        if v in _SUB_VALS:
            return -1
    return 0


def _apply_style(node: dict, style: Style) -> Style:
    tag = node.get("tag")
    st = node.get("style") or {}
    kw: dict = {}

    weight = st.get("fontWeight")
    if (
        tag in {"strong", "b"}
        or weight in {"bold", "bolder"}
        or (isinstance(weight, (int, float)) and weight >= 600)
    ):
        kw["weight"] = 700
    if tag in {"em", "i"} or st.get("fontStyle") == "italic":
        kw["italic"] = True
    deco = st.get("textDecorationLine")
    if tag in {"a", "u"} or deco == "underline" or (isinstance(deco, list) and "underline" in deco):
        kw["underline"] = True
    if deco == "line-through" or (isinstance(deco, list) and "line-through" in deco):
        kw["strike"] = True
    if tag == "a":
        kw.setdefault("color", _NAMED["blue"])
    kw["size"] = _parse_size(st.get("fontSize"), style.size)
    kw["color"] = _parse_color(st.get("color"), kw.get("color", style.color))
    valign = _valign(tag, st.get("verticalAlign"))
    if valign:
        kw["valign"] = valign
        kw["size"] = max(1, round(kw["size"] * _SUB_SUP_EM))  # small raised/lowered annotation
    return style.with_(**kw)


def _ruby_parts(node: dict) -> tuple[str, str]:
    """Extract (base_text, reading_text) from a ruby node's rb/rt children."""
    base_parts: list[str] = []
    reading_parts: list[str] = []
    content = node.get("content")
    items = content if isinstance(content, list) else [content]
    for child in items:
        if isinstance(child, str):
            base_parts.append(child)  # bare text inside ruby = base
        elif isinstance(child, dict):
            t = child.get("tag")
            if t == "rt":
                reading_parts.append(_text_of(child.get("content")))
            elif t == "rb":
                base_parts.append(_text_of(child.get("content")))
            elif t == "rp":
                continue  # ruby parenthesis fallback — skip
            else:
                base_parts.append(_text_of(child))
    return "".join(base_parts), "".join(reading_parts)


def _is_boxed(node: dict) -> bool:
    # A filled/bordered/rounded span is a visually separated pill (POS/tag chip); Yomitan spaces these
    # via CSS margins, which plain-text flattening otherwise loses — glueing e.g. `na-adjcolloquial`.
    st = node.get("style") or {}
    return not _BOX_KEYS.isdisjoint(st)


def _list_marker(list_node: dict, item: dict) -> str | None:
    """Literal Yomitan ``listStyleType`` marker, ``""`` for none, or None for our default."""
    item_style = item.get("style")
    list_style = list_node.get("style")
    item_style = item_style if isinstance(item_style, dict) else {}
    list_style = list_style if isinstance(list_style, dict) else {}
    value = item_style.get("listStyleType", list_style.get("listStyleType"))
    data = list_node.get("data")
    if value is None and isinstance(data, dict) and data.get("content") == "glossary":
        return ""  # Jitendex's stylesheet makes semantic glossary lists markerless
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.lower() == "none":
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return None


_tls = threading.local()  # per-walk _text_of memo (see walk()); thread-local so prefetch workers
# don't share, and id()-keyed so it MUST NOT outlive the tree it indexes.


def _text_of(node) -> str:
    # Thin memo wrapper over _flatten. The tree is re-flattened ~4x (chip test → recurse → child chip
    # test …); memoise per walk, keyed on id() — safe because the tree is alive for the walk and the
    # memo is cleared at walk() exit.
    if not isinstance(node, (dict, list)):
        return node if isinstance(node, str) else ""
    memo = getattr(_tls, "memo", None)
    if memo is not None and (hit := memo.get(id(node))) is not None:
        return hit
    result = _flatten(node)
    if memo is not None:
        memo[id(node)] = result
    return result


def _flatten(node) -> str:
    if isinstance(node, list):
        return "".join(_text_of(n) for n in node)
    tag = node.get("tag")
    if tag == "br":
        return "\n"
    text = _text_of(node.get("content"))
    # Insert word boundaries the source encodes structurally, not textually: block elements and chip
    # pills sit apart on screen but flatten adjacent. Callers collapse the extra whitespace.
    if text and (tag in BLOCK_TAGS or _is_boxed(node)):
        return f" {text} "
    return text


class _Walker:
    def __init__(self, base: Style, media: dict[str, bytes] | None = None):
        self.base = base
        # {img path: image bytes}, preloaded at Entry-build so the walk (render/prefetch thread) never
        # touches SQLite. Empty on a default install → every img falls back to ▢.
        self.media = media or {}
        self.blocks: list[Block] = []
        self.cur = Block()

    def _flush(self) -> None:
        if not self.cur.is_empty():
            self.blocks.append(self.cur)
        self.cur = Block()

    def _emit_ruby(self, node: dict, style: Style) -> None:
        base, reading = _ruby_parts(node)
        self.cur.flow.append(ruby(base, reading, _apply_style(node, style)))

    def _emit_img(self, node: dict, style: Style) -> None:
        # Only appearance:"monochrome" recolours to the text colour (a black gaiji reads like a glyph);
        # the Yomitan default "auto" keeps the image's own colours — tinting it would flatten a coloured
        # diagram/label into a solid block. PIL decode/tint lives in render.flow.img_box, so this module
        # stays PIL-agnostic.
        png = self.media.get(str(node.get("path") or ""))
        tint = style.color if node.get("appearance") == "monochrome" else None
        self.cur.flow.append(img_box(png, max(12, style.size), tint))

    def _emit_link(self, node: dict, style: Style) -> None:
        # Visually distinguish the two link kinds: an INTERNAL cross-ref keeps the blue +
        # underline (clickable → opens a related-note nested tooltip) and carries its target
        # term; an EXTERNAL source-attribution link is muted gray + NOT
        # underlined, so it doesn't read as clickable.
        query = link_query(node.get("href"), _text_of(node.get("content")))
        if query:
            astyle = _apply_style(node, style)  # blue + underline
        else:
            astyle = _apply_style(node, style).with_(underline=False, color=_LINK_EXTERNAL)
        start = len(self.cur.flow)
        self._emit_inline(node.get("content"), astyle)
        if query:
            for i in range(start, len(self.cur.flow)):
                seg = self.cur.flow[i]
                if isinstance(seg, Span):
                    self.cur.flow[i] = replace(seg, href=query)
                elif isinstance(seg, RubyBox):
                    # A ruby'd cross-ref (思し召し with furigana) becomes a RubyBox, not a Span — put
                    # the href on its base spans so the ruby base is a clickable link run, not merely
                    # blue-styled text (flow._update_link_run reads span.href off the base).
                    seg.base = [replace(s, href=query) for s in seg.base]

    def _chip_for(self, node: dict, style: Style) -> ChipBox | None:
        # Style gate FIRST: an unstyled node can never be a chip, so skip the (potentially deep)
        # _text_of flatten for it — the common case, and most of the walk's redundant flattening.
        st = node.get("style") or {}
        has_bg = any(k in st for k in _BG_KEYS)
        has_border = any(k in st for k in _BORDER_KEYS)
        if not (has_bg or has_border or "borderRadius" in st):
            return None
        # A short filled/bordered leaf → a chip: POS tags like `noun`/`no-adj` (filled pill,
        # backgroundColor + white text, borderRadius, no borderColor) or labels like 逆引き
        # (transparent + border). Long bordered content (example sentences) must flow and keep
        # its ruby, so recurse instead. Honour backgroundColor — dropping it left white-on-white
        # text in an empty box. A whitespace-only styled span (some dicts' accent/marker spacers) is
        # NOT a chip — gating on the stripped label avoids the stray empty pill.
        label = _text_of(node.get("content"))
        chip_label = label.strip()
        if not (0 < len(chip_label) <= 12 and "\n" not in label):
            return None
        from overlay.render.chip import ChipStyle

        cstyle = _apply_style(node, style)
        bg = (
            _parse_color(st.get("backgroundColor") or st.get("background"), (0, 0, 0, 0))
            if has_bg
            else (0, 0, 0, 0)
        )
        border = _parse_color(st.get("borderColor"), (150, 150, 150, 255)) if has_border else None
        return ChipBox(
            chip_label,
            ChipStyle(
                size=cstyle.size,
                weight=cstyle.weight,
                fg=cstyle.color,
                bg=bg,
                border=border,
                pad_v=1,
            ),
        )

    def _emit_inline(self, node, style: Style) -> None:
        if node is None:
            return
        if isinstance(node, str):
            if node:
                self.cur.flow.append(Span(node, style))
            return
        if isinstance(node, list):
            for n in node:
                self._emit_inline(n, style)
            return
        if isinstance(node, dict):
            tag = node.get("tag")
            if tag == "br":
                self.cur.flow.append(Span("\n", style))
                return
            if tag == "ruby":
                self._emit_ruby(node, style)
                return
            if tag == "img":
                self._emit_img(node, style)
                return
            if tag == "a":
                self._emit_link(node, style)
                return
            chip = self._chip_for(node, style)
            if chip is not None:
                self.cur.flow.append(chip)
                return
            # inline style-carrying / unknown tag → recurse
            self._emit_inline(node.get("content"), _apply_style(node, style))

    def _emit_table(self, node: dict, style: Style, indent: int) -> None:
        # Minimal grid: each row on its own line, cells joined by a muted │. NOT column-aligned (a
        # proportional font has no tab stops — true alignment needs the layout engine); but rows +
        # cell dividers turn the old flattened blob into something readable. Header cells (th) bold.
        self._flush()
        self.cur = Block(indent=indent)
        rows = _table_rows(node)
        sep_style = style.with_(color=_TABLE_SEP)
        for ri, tr in enumerate(rows):
            for ci, cell in enumerate(_table_cells(tr)):
                if ci:
                    self.cur.flow.append(Span(" │ ", sep_style))
                cstyle = _apply_style(cell, style)
                if cell.get("tag") == "th":
                    cstyle = cstyle.with_(weight=700)
                self._emit_inline(cell.get("content"), cstyle)
            if ri < len(rows) - 1:
                self.cur.flow.append(Span("\n", style))  # row break (flow force-breaks on \n)
        self._flush()
        self.cur = Block(indent=indent)

    def _walk_list(self, node: dict, style: Style, indent: int) -> None:
        self._flush()
        list_type = node.get("tag")  # 'ul' | 'ol'
        content = node.get("content")
        items = content if isinstance(content, list) else [content]
        ordinal = 1
        for child in items:
            if isinstance(child, dict) and child.get("tag") == "li":
                self.cur = Block(
                    kind="list-item",
                    list_type=list_type,
                    ordinal=ordinal,
                    marker=_list_marker(node, child),
                    indent=indent,
                )
                self._emit_li(child.get("content"), _apply_style(child, style), indent)
                self._flush()
                ordinal += 1
        self.cur = Block(indent=indent)

    def _emit_li(self, node, style: Style, indent: int) -> None:
        items = node if isinstance(node, list) else [node]
        parent = self.cur
        marker_pending = True
        for child in items:
            tag = child.get("tag") if isinstance(child, dict) else None
            if tag in {"ul", "ol"}:
                marker_pending = self._emit_li_list(
                    child, style, indent, parent, marker_pending=marker_pending
                )
            elif tag in _LI_BLOCK_TAGS:
                marker_pending = self._emit_li_block(
                    child, style, indent, parent, marker_pending=marker_pending
                )
            else:
                self._emit_inline(child, style)

    def _place_parent_marker(self, parent: Block, start: int, style: Style) -> bool:
        if len(self.blocks) <= start:
            return False
        first = self.blocks[start]
        if first.kind == "list-item" and first.marker == "":
            for block in self.blocks[start:]:
                block.indent = max(parent.indent, block.indent - 1)
        if first.kind != "list-item" or first.marker == "":
            first.kind = parent.kind
            first.list_type = parent.list_type
            first.ordinal = parent.ordinal
            first.marker = parent.marker
            first.indent = parent.indent
        elif parent.marker != "":
            parent.flow.append(Span("\N{NO-BREAK SPACE}", style))
            self.blocks.insert(start, parent)
        return True

    def _emit_li_list(
        self, node: dict, style: Style, indent: int, parent: Block, *, marker_pending: bool
    ) -> bool:
        if not self.cur.is_empty():
            self._flush()
            marker_pending = False
        start = len(self.blocks)
        self._walk_list(node, style, indent + 1)
        if marker_pending and self._place_parent_marker(parent, start, style):
            marker_pending = False
        self.cur = parent if marker_pending else Block(indent=indent + 1)
        return marker_pending

    def _emit_li_block(
        self, node: dict, style: Style, indent: int, parent: Block, *, marker_pending: bool
    ) -> bool:
        if not self.cur.is_empty():
            self._flush()
            marker_pending = False
        self.cur = Block(indent=indent + 1)
        start = len(self.blocks)
        self._walk_block(node, style, indent + 1)
        if marker_pending and self._place_parent_marker(parent, start, style):
            marker_pending = False
        self.cur = parent if marker_pending else Block(indent=indent + 1)
        return marker_pending

    def walk(self, node) -> list[Block]:
        self._walk_block(node, self.base, 0)
        self._flush()
        return [b for b in self.blocks if not b.is_empty()]

    def _walk_block(self, node, style: Style, indent: int) -> None:
        if isinstance(node, list):
            for n in node:
                self._walk_block(n, style, indent)
            return
        if isinstance(node, dict):
            tag = node.get("tag")
            if tag in {"ul", "ol"}:
                self._walk_list(node, style, indent)
                return
            if tag == "table":
                self._emit_table(node, style, indent)
                return
            if tag in {"div", "p", "details", "summary"}:
                self._flush()
                self.cur = Block(indent=indent)
                self._walk_block(node.get("content"), _apply_style(node, style), indent)
                self._flush()
                self.cur = Block(indent=indent)
                return
            # inline or unknown at block level → treat as inline in current paragraph
            self._emit_inline(node, style)
            return
        # bare string at block level
        self._emit_inline(node, style)


def walk(node, base: Style | None = None, media: dict[str, bytes] | None = None) -> list[Block]:
    """Turn a structured-content node into a list of layout blocks. ``media`` maps an img node's ``path``
    to preloaded image bytes (SVG gaiji rasterized at import, #283); empty → img renders as ▢."""
    prev = getattr(
        _tls, "memo", None
    )  # save/restore so a nested walk() can't clobber an outer memo
    _tls.memo = {}
    try:
        return _Walker(base or Style(), media).walk(node)
    finally:
        _tls.memo = prev


def collect_img_paths(node) -> list[str]:
    """Every img-node ``path`` in a structured-content tree, in document order (deduped downstream).

    Used at Entry-build to preload exactly the media a definition references — so the DB is queried on
    the lookup thread, never during the walk on the render/prefetch thread (#283)."""
    out: list[str] = []
    _collect_img_paths(node, out)
    return out


def _collect_img_paths(node, out: list[str]) -> None:
    if isinstance(node, list):
        for n in node:
            _collect_img_paths(n, out)
    elif isinstance(node, dict):
        if node.get("tag") == "img":
            path = node.get("path")
            if isinstance(path, str) and path:
                out.append(path)
        _collect_img_paths(node.get("content"), out)


def inline_flow(node, base: Style | None = None, media: dict[str, bytes] | None = None) -> list:
    """Flatten a structured-content node to a single inline flow (for headword / label rows)."""
    flow: list = []
    for b in walk(node, base, media):
        flow.extend(b.flow)
    return flow

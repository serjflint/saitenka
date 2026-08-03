"""Walk Yomitan structured-content JSON into layout :class:`Block`s.

Structured content is a recursive tree: a node is a string (text), a list of nodes, or an object
``{"tag": ..., "content": ..., "style": {...}}``. We support the subset the dictionary panel needs —
text, ``span``/style, ``ruby``(``rb``/``rt``), ``br``, ``ul``/``ol``/``li``, ``a`` (link → styled
text), ``img`` (→ opaque box). **Unknown tags never fail**: we recurse into their content and flatten
to text, so a novel dictionary can't break rendering.

Reference: Yomitan ``dictionary-term-bank-v3`` structured-content schema.
"""

from __future__ import annotations

import re
import threading
import urllib.parse
from dataclasses import replace

from overlay.model import RGBA, Span, Style
from overlay.render.flow import ChipBox, ImgBox, ruby
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


def _apply_style(node: dict, style: Style) -> Style:
    tag = node.get("tag")
    st = node.get("style") or {}
    kw: dict = {}

    weight = st.get("fontWeight")
    if (
        tag in ("strong", "b")
        or weight in ("bold", "bolder")
        or (isinstance(weight, (int, float)) and weight >= 600)
    ):
        kw["weight"] = 700
    if tag in ("em", "i") or st.get("fontStyle") == "italic":
        kw["italic"] = True
    deco = st.get("textDecorationLine")
    if tag in ("a", "u") or deco == "underline" or (isinstance(deco, list) and "underline" in deco):
        kw["underline"] = True
    if tag == "a":
        kw.setdefault("color", _NAMED["blue"])
    kw["size"] = _parse_size(st.get("fontSize"), style.size)
    kw["color"] = _parse_color(st.get("color"), kw.get("color", style.color))
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
    def __init__(self, base: Style):
        self.base = base
        self.blocks: list[Block] = []
        self.cur = Block()

    def _flush(self) -> None:
        if not self.cur.is_empty():
            self.blocks.append(self.cur)
        self.cur = Block()

    def _emit_ruby(self, node: dict, style: Style) -> None:
        base, reading = _ruby_parts(node)
        self.cur.flow.append(ruby(base, reading, _apply_style(node, style)))

    def _emit_img(self, style: Style) -> None:
        h = max(12, style.size)
        self.cur.flow.append(ImgBox(width=round(h * 1.6), height=h, label="▢"))

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
        from overlay.draw.chip import ChipStyle

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
                self._emit_img(style)
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

    def _walk_list(self, node: dict, style: Style, indent: int) -> None:
        self._flush()
        list_type = node.get("tag")  # 'ul' | 'ol'
        content = node.get("content")
        items = content if isinstance(content, list) else [content]
        ordinal = 1
        for child in items:
            if isinstance(child, dict) and child.get("tag") == "li":
                self.cur = Block(
                    kind="list-item", list_type=list_type, ordinal=ordinal, indent=indent
                )
                self._emit_li(child.get("content"), _apply_style(child, style), indent)
                self._flush()
                ordinal += 1
        self.cur = Block(indent=indent)

    def _emit_li(self, node, style: Style, indent: int) -> None:
        # A list item may itself contain nested block content; keep it inline for now unless it has
        # a nested list, which we split into following list-item blocks.
        if isinstance(node, dict) and node.get("tag") in ("ul", "ol"):
            self._walk_list(node, style, indent + 1)
            return
        if isinstance(node, list):
            for n in node:
                if isinstance(n, dict) and n.get("tag") in ("ul", "ol"):
                    self._flush()
                    self._walk_list(n, style, indent + 1)
                else:
                    self._emit_inline(n, style)
            return
        self._emit_inline(node, style)

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
            if tag in ("ul", "ol"):
                self._walk_list(node, style, indent)
                return
            if tag in ("div", "p", "details", "summary"):
                self._flush()
                self._walk_block(node.get("content"), _apply_style(node, style), indent)
                self._flush()
                return
            # inline or unknown at block level → treat as inline in current paragraph
            self._emit_inline(node, style)
            return
        # bare string at block level
        self._emit_inline(node, style)


def walk(node, base: Style | None = None) -> list[Block]:
    """Turn a structured-content node into a list of layout blocks."""
    prev = getattr(
        _tls, "memo", None
    )  # save/restore so a nested walk() can't clobber an outer memo
    _tls.memo = {}
    try:
        return _Walker(base or Style()).walk(node)
    finally:
        _tls.memo = prev


def inline_flow(node, base: Style | None = None) -> list:
    """Flatten a structured-content node to a single inline flow (for headword / label rows)."""
    flow: list = []
    for b in walk(node, base):
        flow.extend(b.flow)
    return flow

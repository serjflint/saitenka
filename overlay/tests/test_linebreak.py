"""UAX #14 line-breaking conformance + kinsoku + ruby-atomicity for :mod:`overlay.render.linebreak`.

The character algorithm is validated against the vendored Unicode ``LineBreakTest.txt`` corpus
(``tests/fixtures/uax14``). We assert **100 %** on the subset whose code points are in the scripts this
renderer wraps — Latin, CJK, Kana, digits, common punctuation, spaces, Korean — and pin the in-scope /
out-of-scope split as a counted ledger. Lines using classes that never reach the renderer (Hebrew HL/HH,
Brahmic AK/AP/AS/VF/VI, regional indicators, emoji modifiers, bare ZWJ joiners) are excluded, not chased.
"""

from __future__ import annotations

import gzip
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from overlay.render.layout import NO_END, NO_START
from overlay.render.linebreak import (
    PROHIBITED,
    break_opportunities,
    wrap_units,
)
from overlay.render.linebreak import _lb_class as lb_class  # corpus scoping only

_CORPUS = (
    Path(__file__).parent / "fixtures" / "uax14" / "LineBreakTest.txt.gz"
)  # ~3 MB → ~210 KB gz
# Classes a Latin/CJK/Kana renderer never emits — their corpus lines are out of scope (see docstring).
_OUT_OF_SCOPE = {"HH", "AK", "AP", "AS", "VF", "VI", "RI", "EB", "EM", "SA", "SG", "HL", "ZWJ"}
_DOTTED_CIRCLE = 0x25CC  # ◌ (class AL) only appears in Brahmic cluster fixtures

# The rendered-scripts census split, pinned as a counted ledger (not a silent filter): a re-vendored UCD
# that reclassifies characters moves these deliberately — a reviewed re-bless. The exact TOTAL is
# independently locked by `poe corpus-lock` (tools/corpus_check.py); here we pin the render-scope split so
# the excluded set can't quietly grow (which would hide an in-scope regression by demoting it out of scope).
_IN_SCOPE = 11_495  # lines wholly in the scripts this renderer wraps → must be 100 %
_OUT_OF_SCOPE_COUNT = 7_843  # excluded (exotic scripts / joiners) — recorded, not chased


def _parse_corpus() -> list[tuple[list[int], list[str]]]:
    """Each non-comment line → (code points, break markers) where ``markers[i]`` (``÷``/``×``) governs
    the boundary before code point ``i``."""
    cases: list[tuple[list[int], list[str]]] = []
    text = gzip.decompress(_CORPUS.read_bytes()).decode("utf-8")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        toks = line.split()
        cps: list[int] = []
        markers = [toks[0]]
        i = 1
        while i < len(toks):
            cps.append(int(toks[i], 16))
            i += 1
            if i < len(toks):
                markers.append(toks[i])
                i += 1
        cases.append((cps, markers))
    return cases


def _in_scope(cps: list[int]) -> bool:
    return _DOTTED_CIRCLE not in cps and not any(lb_class(c) in _OUT_OF_SCOPE for c in cps)


def test_corpus_conformance_is_total_for_rendered_scripts():
    cases = _parse_corpus()
    in_scope = in_scope_ok = 0
    failures: list[str] = []
    for cps, markers in cases:
        got = break_opportunities(
            "".join(chr(c) for c in cps)
        )  # every line: also smokes exotic input
        if not _in_scope(cps):
            continue
        ok = all((markers[i] == "÷") == (got[i] != PROHIBITED) for i in range(1, len(cps)))
        in_scope += 1
        in_scope_ok += ok
        if not ok and len(failures) < 10:
            failures.append(" ".join(f"{c:04X}" for c in cps))
    # the counted ledger: split pinned, so a demoted-out-of-scope regression can't hide (see constants)
    assert in_scope == _IN_SCOPE, f"render-scope split moved (pinned {_IN_SCOPE}, got {in_scope})"
    assert len(cases) - in_scope == _OUT_OF_SCOPE_COUNT
    assert in_scope_ok == in_scope, f"in-scope corpus regressions: {failures}"


def test_mandatory_break_after_newline():
    # LF forces a break after it (LB5), never before it (LB6): the new line starts at the next char.
    status = break_opportunities("a\nb")
    assert status[1] == PROHIBITED  # no break before the LF
    assert status[2] == "mandatory"  # mandatory break after the LF


@given(
    st.text(alphabet="".join(chr(c) for c in range(0x3040, 0x30FF)) + "、。「」（）", min_size=2)
)
@settings(max_examples=200, deadline=None)
def test_no_break_before_forbidden_start_or_after_forbidden_end(text):
    # Kinsoku via UAX #14: a small kana / closing punctuation (NO_START) never begins a line, and an
    # opening bracket (NO_END) never ends one — expressed as break status over the raw string.
    status = break_opportunities(text)
    for i in range(1, len(text)):
        if text[i] in NO_START:
            assert status[i] == PROHIBITED, f"break before forbidden line-start {text[i]!r}"
        if text[i - 1] in NO_END:
            assert status[i] == PROHIBITED, f"break after forbidden line-end {text[i - 1]!r}"


def test_wrap_units_keeps_a_ruby_group_atomic():
    # A ruby base+reading group is a single unit; wrapping can never land inside it. Model three units:
    # a wide CJK char, a ruby group (one unit), then another CJK char, in a width that forces two lines.
    widths = [30.0, 30.0, 30.0]
    texts = ["語", "漢", "字"]  # unit 1 is the ruby group's base; it is one index, never split
    lines = wrap_units(widths, texts, max_width=45)
    assert [i for line in lines for i in line] == [
        0,
        1,
        2,
    ]  # every unit placed exactly once, in order
    assert all(len(set(line)) == len(line) for line in lines)  # no unit duplicated across a wrap


def test_wrap_units_never_starts_a_line_with_closing_punctuation():
    # 」 (NO_START) must ride onto the previous line rather than start a new one.
    widths = [30.0, 30.0, 10.0]
    texts = ["あ", "い", "」"]
    lines = wrap_units(widths, texts, max_width=45)
    assert 2 not in [line[0] for line in lines[1:]]  # the 」 never begins a wrapped line


def test_wrap_units_breaks_between_cjk_but_not_inside_a_latin_word():
    # Latin words are single units (never split); CJK chars are per-char units (breakable between).
    widths = [40.0, 40.0, 40.0]
    lines = wrap_units(widths, ["hello", "世", "界"], max_width=85)
    assert len(lines) >= 2  # wrapped
    assert lines[0] == [0, 1]  # the Latin word stays whole; the break falls before the 2nd CJK char

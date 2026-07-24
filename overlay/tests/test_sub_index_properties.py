"""Property tests hardening the sub-index parser/resolver against boundary + arithmetic bugs.

Written from a cosmic-ray mutation campaign (`poe mutate`): example tests left ~half the mutants in
`locate`/`target`/`_srt_ts` alive — flipped `<=`/`<`, off-by-one steps, tweaked time factors that no
single example exercised. Each property below kills a whole class; the `@example` pins are the shrunk
inputs cosmic-ray needed, kept explicit so the kill is deterministic on every mutation run (Hypothesis
would otherwise find them only sometimes, making mutant verdicts flap)."""

from __future__ import annotations

from hypothesis import example, given, settings
from hypothesis import strategies as st

from overlay.app.sub_index import SubCue, SubIndex, parse_ass, parse_srt

# Hiragana + katakana only — no `<`, `{`, `\`, or whitespace, so _sanitize/_norm leave text untouched
# and SRT round-trip is exact.
jp = st.text(
    alphabet=st.characters(min_codepoint=0x3041, max_codepoint=0x30FF), min_size=1, max_size=6
)


@st.composite
def srt_cues(draw, max_size=6):
    """Cues with millisecond-quantized times (SRT's resolution) so round-trip is lossless. Times stay
    under 100h — `_SRT_TIMING` only matches 1–2 digit hours."""
    out = []
    for _ in range(draw(st.integers(1, max_size))):
        start_ms = draw(st.integers(0, 359_000_000))
        dur_ms = draw(st.integers(1, 20_000))
        out.append((start_ms / 1000, (start_ms + dur_ms) / 1000, draw(jp)))
    return out


@st.composite
def index_cues(draw, min_size=1, max_size=6):
    """Sorted, non-overlapping cues with a real gap after each — so a cue's `end` lands in a gap, which
    is what makes the exclusive upper bound observable."""
    out: list[SubCue] = []
    t = draw(st.floats(0, 5, allow_nan=False, allow_infinity=False))
    for _ in range(draw(st.integers(min_size, max_size))):
        dur = draw(st.floats(0.1, 5, allow_nan=False))
        out.append(SubCue(t, t + dur, draw(jp)))
        t += dur + draw(st.floats(0.1, 3, allow_nan=False))
    return out


def _fmt(t: float) -> str:
    ms = round(t * 1000)
    h, r = divmod(ms, 3_600_000)
    m, r = divmod(r, 60_000)
    s, ms = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


@st.composite
def ass_cues(draw, max_size=6):
    """Cues at centisecond resolution (ASS's `H:MM:SS.cc`)."""
    out = []
    for _ in range(draw(st.integers(1, max_size))):
        start_cs = draw(st.integers(0, 3_500_000))
        dur_cs = draw(st.integers(1, 2000))
        out.append((start_cs / 100, (start_cs + dur_cs) / 100, draw(jp)))
    return out


def _fmt_ass(t: float) -> str:
    cs = round(t * 100)
    h, r = divmod(cs, 360_000)
    m, r = divmod(r, 6000)
    s, cs = divmod(r, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


_ASS_HEADER = "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"


# --- P1: SRT round-trip pins the timestamp arithmetic (h*3600 + m*60 + s + ms/1000) ---------------


@example(cues=[(3661.501, 3663.0, "テスト")])  # 01:01:01,501 — nails every time factor at once
@given(cues=srt_cues())
@settings(max_examples=100, deadline=None)
def test_srt_roundtrip(cues):
    parsed = parse_srt("\n".join(f"{_fmt(s)} --> {_fmt(e)}\n{txt}\n" for s, e, txt in cues))
    assert len(parsed) == len(cues)
    for got, (s, e, txt) in zip(parsed, cues, strict=True):
        assert abs(got.start - s) < 1e-6
        assert abs(got.end - e) < 1e-6
        assert got.text == txt


# --- P1b: ASS round-trip pins _ass_ts (h*3600 + m*60 + s + cc/100) + the Format-order parser --------


@example(cues=[(3723.04, 3725.0, "テスト")])  # 1:02:03.04 — nails the centisecond factors
@given(cues=ass_cues())
@settings(max_examples=100, deadline=None)
def test_ass_roundtrip(cues):
    body = "".join(f"Dialogue: 0,{_fmt_ass(s)},{_fmt_ass(e)},D,,0,0,0,,{txt}\n" for s, e, txt in cues)
    parsed = parse_ass(_ASS_HEADER + body)
    assert len(parsed) == len(cues)
    for got, (s, e, txt) in zip(parsed, cues, strict=True):
        assert abs(got.start - s) < 1e-6
        assert abs(got.end - e) < 1e-6
        assert got.text == txt


# --- P2: locate(sub_start) containment pins the `start <= t < end` boundary ------------------------


@example(cues=[SubCue(0.0, 1.0, "あ"), SubCue(2.0, 3.0, "い")])  # t==0 lower-incl, t==1/3 upper-excl
@given(cues=index_cues())
@settings(max_examples=100, deadline=None)
def test_locate_sub_start_containment(cues):
    idx = SubIndex(cues)
    for k, c in enumerate(cues):
        assert idx.locate(sub_start=c.start) == k  # lower bound INCLUSIVE
        assert idx.locate(sub_start=(c.start + c.end) / 2) == k  # interior
        assert idx.locate(sub_start=c.end) != k  # upper bound EXCLUSIVE (end sits in the gap)


# --- P3: target() stepping spec — pins current±delta, the gap semantics, and 0<=tgt<len ------------


@example(cues=[SubCue(0.0, 1.0, "あ")], current=0, delta=1, inside=True)  # next past last → -1
@example(cues=[SubCue(0, 1, "あ"), SubCue(2, 3, "い")], current=1, delta=-1, inside=False)  # gap prev
@given(
    cues=index_cues(min_size=0),
    current=st.integers(-2, 8),
    delta=st.integers(-1, 1),
    inside=st.booleans(),
)
@settings(max_examples=200, deadline=None)
def test_target_spec(cues, current, delta, inside):
    n = len(cues)
    r = SubIndex(cues).target(current, delta, inside=inside)
    assert r == -1 or 0 <= r < n  # never out of range
    if current < 0:
        assert r == (0 if (delta > 0 and n > 0) else -1)  # nothing current: only "next" opens cue 0
    elif inside:
        exp = current + delta  # on screen → prev/next straddle, replay stays
        assert r == (exp if 0 <= exp < n else -1)
    elif delta > 0:
        assert r == (current if current < n else -1)  # gap "next" lands ON the upcoming cue
    elif delta < 0:
        assert r == (current - 1 if 0 <= current - 1 < n else -1)  # gap "prev" → cue before the gap
    else:
        assert r == -1  # replay from a gap is ambiguous → defer

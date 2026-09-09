"""Walk ONE cue through every state it passes on screen, holding each one long enough to name.

A viewer sees a subtitle become scannable as a single event; the pipeline gets there through
several, and "the delay" can mean any of them. This exists so the two of us can point at the same
one: each state is held for a second, labelled on the terminal, and tagged with the span it emits —
so "stage 3" and "subtitle_geometry_apply" name one thing.

Nothing here is simulated. The states are produced by the same calls the runtime makes; what differs
is only that each is *held* instead of passing in a few milliseconds.

Opt-in: needs a real display + mpv (``SAITENKA_LIVE=1``). Run via ``uv run poe cue-stages``.

    SAITENKA_LIVE=1 uv run --extra full python examples/cue_stages_live.py --dwell 1.0
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# The live setup is shared with the L3 smoke tests (tests/live_harness.py); this diagnostic script
# deliberately reuses it rather than duplicate the real-mpv boot.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))


@dataclass(frozen=True)
class Stage:
    name: str
    span: str
    seen: str


#: What the runtime does, in order, with the span each step emits. `span` is the join key: quote it
#: back and the trace can be filtered to exactly the moment being described.
STAGES = (
    Stage(
        "text-only",
        "subtitle_draw (measured_boxes=0)",
        "mpv's own subtitle, plain white — no word is colored or clickable yet",
    ),
    Stage(
        "colored",
        "subtitle_geometry_apply -> subtitle_draw (measured_boxes>0)",
        "the words take their reading-state colors; this is the flip you are describing",
    ),
    Stage(
        "hovered",
        "hover_transition -> subtitle_draw",
        "one word gains the focus highlight behind it",
    ),
    Stage(
        "unhovered",
        "subtitle_draw",
        "the highlight goes, the colors stay",
    ),
    Stage(
        "retired",
        "cue_redraw (empty)",
        "our color comes off; mpv's own white text stays, exactly as in stage 1",
    ),
)


def _scorer():
    """A scorer that marks part of the demo line known, so `colored` differs visibly from
    `text-only`. Without one every style is `None` and the color ladder has nothing to assign.

    The palette comes from the user's own config, not `Palette()`: a walkthrough that draws the
    stock near-white while the config asks for something loud is showing the wrong thing, and it
    did — the reason `1` and `2` looked identical was five of seven words changing by nothing
    anyone could see.
    """
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords

    from saitenka.app.config import load_config
    from saitenka.app.scoring import Coloring, Palette

    return Coloring(
        Scorer(
            known=KnownWords.from_set(["門前", "経"]),
            enable_freq=False,
            enable_jlpt=False,
        ),
        Palette.from_config(load_config().get("palette")),
    )


def _describe_colors(reader, scorer) -> int:
    """Print what stage 2 is supposed to change, word by word, and return how many words move.

    Printed rather than assumed: the base color (#cad3f5) is a near-white over text that is already
    near-white, so most of a line changes by an amount nobody can see. Without this the honest
    report "1 and 2 look the same" cannot be told apart from a rendering bug — with it, a word the
    terminal calls green and the screen does not is a finding.
    """
    cue = reader.graph.subtitle_presentation.cue.current
    base = scorer.palette.base
    moved = 0
    print("what stage 2 should change:")
    for token, style in zip(cue.tokens, scorer.score_line(list(cue.tokens)), strict=True):
        mark = "  " if style.color == base else "->"
        moved += style.color != base
        print(
            f"  {mark} {token.surface:8} #{style.color[0]:02x}{style.color[1]:02x}{style.color[2]:02x}"
        )
    print(
        f"\n{moved} of {len(cue.tokens)} words should visibly change; the rest are the base color, "
        "which is near-white over near-white text.\n"
    )
    return moved


def _draw(reader) -> None:
    presentation = reader.graph.subtitle_presentation
    presentation.pipeline.draw_current(presentation.target())


def _hold(reader, seconds: float) -> None:
    """Hold a stage on screen, pumping throughout.

    A draw does not reach mpv by returning: the overprint goes out as a correlated command, so its
    write — and, for `text-only`, its *removal* — only lands once the runtime is drained. Sleeping
    without pumping left the previous stage's color on screen under the next stage's label, which
    is how the walkthrough came to show stage 1 and stage 2 as identical.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        reader.pump()
        time.sleep(0.01)


def _settle(reader, seconds: float = 2.0) -> list:
    """Pump until the cue's real geometry has landed, and answer with its boxes.

    Done once per walkthrough rather than per stage: with the geometry already published and the
    fence unmoved, nothing re-publishes underneath a stage, so `text-only` stays uncolored for as
    long as it is held.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        reader.pump()
        boxes = list(reader.graph.subtitle_presentation.cue.current.boxes)
        if boxes:
            return boxes
        time.sleep(0.01)
    return []


def _apply(reader, stage: Stage, boxes: list) -> None:
    """Put the session into `stage` using the calls the runtime itself makes."""
    presentation = reader.graph.subtitle_presentation
    if stage.name == "text-only":
        # What a geometry cache miss leaves on screen: the tokens are installed, their boxes are
        # not. `_degrade_geometry` reaches this state through the same setter.
        presentation.cue.replace_geometry(boxes=[])
    elif stage.name == "colored":
        presentation.cue.replace_geometry(boxes=boxes)
    elif stage.name == "hovered":
        reader.graph.tooltip.select(0)
    elif stage.name == "unhovered":
        reader.graph.tooltip.retire_selection()
    elif stage.name == "retired":
        reader.graph.cue.set_subtitle("")
        return
    _draw(reader)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dwell", type=float, default=1.0, help="seconds to hold each stage")
    parser.add_argument("--loops", type=int, default=3, help="times to walk the cue")
    parser.add_argument(
        "--shots",
        type=Path,
        default=None,
        help="write one window screenshot per stage here, so the stages can be compared as pixels",
    )
    args = parser.parse_args(argv)

    from live_harness import live_reader

    print(f"Each stage is held for {args.dwell:.1f}s. Quote the NAME or the SPAN back.\n")
    for index, stage in enumerate(STAGES, start=1):
        print(f"  {index}. {stage.name:11} {stage.span}")
    print()

    scorer = _scorer()
    with live_reader(scorer=scorer, native_visible=True) as (_tmp, reader, _ipc):
        boxes = list(reader.graph.subtitle_presentation.cue.current.boxes)
        if not boxes:
            print("the cue produced no hit boxes — nothing to color; is the geometry mode on?")
            return 1
        if not _describe_colors(reader, scorer):
            print("no word on this line changes color — stages 1 and 2 would look identical")
            return 1
        line = reader.graph.playback.cue.text
        for loop in range(1, args.loops + 1):
            reader.graph.cue.set_subtitle(line)  # a fresh arrival, as mpv reporting the cue does
            boxes = _settle(reader) or boxes
            for index, stage in enumerate(STAGES, start=1):
                print(f"[{loop}/{args.loops}] {index}. {stage.name:11} — {stage.seen}")
                _apply(reader, stage, boxes)
                _hold(reader, args.dwell)
                if args.shots is not None:
                    args.shots.mkdir(parents=True, exist_ok=True)
                    shot = args.shots / f"loop{loop}-{index}-{stage.name}.png"
                    # `window`, not `video`: the overprint is an OSD overlay, and a video-only
                    # capture would answer with the frame mpv decoded rather than what is on screen.
                    _ipc.command("screenshot-to-file", str(shot), "window")
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

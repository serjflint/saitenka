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
        "the line is readable, every word plain — no word is clickable yet",
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
        "the cue leaves the screen",
    ),
)


def _scorer():
    """A scorer that marks part of the demo line known, so `colored` differs visibly from
    `text-only`. Without one every style is `None` and the color ladder has nothing to assign."""
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords

    from saitenka.app.scoring import Coloring, Palette

    return Coloring(
        Scorer(
            known=KnownWords.from_set(["門前", "経"]),
            enable_freq=False,
            enable_jlpt=False,
        ),
        Palette(),
    )


def _draw(reader) -> None:
    presentation = reader.graph.subtitle_presentation
    presentation.pipeline.draw_current(presentation.target())


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
    args = parser.parse_args(argv)

    from live_harness import live_reader

    print(f"Each stage is held for {args.dwell:.1f}s. Quote the NAME or the SPAN back.\n")
    for index, stage in enumerate(STAGES, start=1):
        print(f"  {index}. {stage.name:11} {stage.span}")
    print()

    with live_reader(scorer=_scorer()) as (_tmp, reader, _ipc):
        boxes = list(reader.graph.subtitle_presentation.cue.current.boxes)
        if not boxes:
            print("the cue produced no hit boxes — nothing to color; is the geometry mode on?")
            return 1
        line = reader.graph.playback.cue.text
        for loop in range(1, args.loops + 1):
            reader.graph.cue.set_subtitle(line)  # a fresh arrival, as mpv reporting the cue does
            for index, stage in enumerate(STAGES, start=1):
                # Drained BEFORE the stage is set, never during the hold: a pump inside the dwell
                # would let the real geometry land and color `text-only` out from under the label.
                reader.pump()
                print(f"[{loop}/{args.loops}] {index}. {stage.name:11} — {stage.seen}")
                _apply(reader, stage, boxes)
                time.sleep(args.dwell)
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The copy paints what live annotation paints: same tokens, same colors, frame by frame."""

from __future__ import annotations

import re

import dicthelp
from saitenka_subtitles import CueIndex, parse_cues
from saitenka_subtitles.colored_track import color_document
from saitenka_tokenize.registry import get_tokenizer
from saitenka_wordstate import Scorer
from saitenka_wordstate.known import KnownWords
from test_colored_track_document import HEAD
from util import FakeIPC

from saitenka.app.colored_subs import event_colors
from saitenka.app.features.annotation.annotation_controller import (
    AnnotationInputs,
    CueAnnotationController,
)
from saitenka.app.native_subtitles import token_annotations
from saitenka.app.scoring import Coloring

#: A token block, the text it colors, and the restore that ends it.
_PAINTED = re.compile(r"\{\\1c&H([0-9A-F]{6})&\\2c&H\1&\}(.*?)\{\\1c&H")

# Co-timed sign and dialogue with one unknown word each: joined, neither is N+1 (#544).
ROWS = (
    "Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,私は本を読む",
    "Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,私は本を書く",
    "Dialogue: 0,0:00:06.00,0:00:08.00,Default,,0,0,0,,本を読む。\\N私は書く。",
)


def _inputs(scorer: Coloring, index: CueIndex) -> AnnotationInputs:
    return AnnotationInputs(
        source_epoch=1,
        track_identity=2,
        subtitle_role="primary",
        observed_start=0.0,
        observed_end=5.0,
        source_order=None,
        tokenizer=get_tokenizer(),
        terms_exist=None,
        scorer=scorer,
        selected_dictionaries=0,
        dependencies_ready=True,
        annotate=True,
        sub_index=index,
    )


def _bgr_to_rgb(bgr: str) -> int:
    value = int(bgr, 16)
    return ((value & 0xFF) << 16) | (value & 0xFF00) | (value >> 16)


def test_every_placeable_frame_is_painted_by_the_copy_as_live_paints_it() -> None:
    source = HEAD + "".join(row + "\n" for row in ROWS)
    index = CueIndex(parse_cues(source, "x.ass"))
    scorer = Coloring(
        Scorer(
            known=KnownWords.from_set(["私", "本"]), jlpt=dicthelp.load_jlpt(), enable_freq=False
        )
    )
    annotation = CueAnnotationController(FakeIPC(), mode="full", cache_max=64)
    inputs = _inputs(scorer, index)
    skippable = inputs.tokenizer.is_skippable
    copy = color_document(
        source,
        "external:/x.ass",
        event_colors(
            lambda text: annotation.event_annotation(
                text, inputs, generation=annotation.token_generation
            ),
            skippable,
        ),
    )
    painted = [
        [(text, _bgr_to_rgb(bgr)) for bgr, text in _PAINTED.findall(line)]
        for line in copy.text.splitlines()
        if line.startswith("Dialogue:")
    ]

    frames = {tuple(index.frame_at_position(position)) for position in range(len(index))}
    for frame in sorted(frames):
        text = index.frame_text(frame[0])
        live = annotation.replace(text, inputs).cue
        assert live is not None and live.styles is not None
        selection = token_annotations(text, live.lines, live.tokens, skippable)
        expected = [
            (
                text[token.text_start : token.text_end],
                int.from_bytes(bytes(live.styles[token.token_index].color[:3]), "big"),
            )
            for token in selection.annotations
        ]
        assert [pair for position in frame for pair in painted[position]] == expected, text

    assert copy.colored == len(ROWS)

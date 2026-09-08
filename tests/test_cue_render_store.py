"""A cue's boxes and the tokens they attach to must reach a draw together, or not at all.

A `DrawRequest` takes its identity (`text`) from `playback.cue.text` and its content (`lines`,
`boxes`) from `CueRenderStore`. Those are two owners on two clocks: mpv's `sub-text` property lands
on playback state the moment it is observed, while the store is rewritten by `set_subtitle`. A
`sub-seek` reliably lands between them, because mpv re-reports a transient mid-seek value before the
real cue.

The field case, from `Ame to Kimi to` ep1 (`20260909-014125`) -- and note the boundaries touch, so
the successor begins in the same frame the predecessor ends:

    0:01:57.12 -> 0:02:11.10  ♬～            (2 chars, both tokenizer-skipped, 0 eligible)
    0:02:11.10 -> 0:02:15.13  犬… かな？        (3 eligible tokens)   <-- unscannable
    0:02:15.13 -> 0:02:25.11  ♬～

    17484.4  draw cue=犬… かな？  tokens=6  measured_boxes=0
    17499.2  apply APPLIED     generation=60  snapshot_tokens=3  observed_tokens=6
    17499.4  draw cue=♬～       tokens=0  measured_boxes=3    <-- 犬's three boxes
    17533.3  draw cue=犬… かな？  tokens=6  measured_boxes=0    <-- and gone again

The geometry pipeline is correct throughout: one generation, an observation matching the snapshot,
and an identity guard that rightly passes. Only the draw pairs the wrong two halves.
"""

from __future__ import annotations

from saitenka_tokenize.japanese import Token

from saitenka.app.subtitle_presentation import CueRenderStore
from saitenka.app.subtitles import WordBox
from saitenka.app.token_cache import TokenizedCue

DOG = "犬…　かな？"
MUSIC = "♬～"


def token(surface: str, start: int) -> Token:
    return Token(
        surface=surface,
        lemma=surface,
        reading="",
        pos="名詞",
        start=start,
        end=start + len(surface),
    )


def tokenized(*surfaces: str) -> TokenizedCue:
    tokens: list[Token] = []
    cursor = 0
    for surface in surfaces:
        tokens.append(token(surface, cursor))
        cursor += len(surface)
    return TokenizedCue(lines=[tokens], tokens=tokens, styles=None)


def box(index: int) -> WordBox:
    return WordBox(index, 10 * index, 20, 30, 40)


def test_a_box_cannot_survive_the_tokens_it_indexes() -> None:
    """`TooltipController.hit` indexes `tokens` by whatever box answers a click, so a box pointing
    past the list is a crash or a hit region over another cue's word. Enforced where the state is
    written, so hit-testing and drawing get one answer rather than each remembering to ask."""
    store = CueRenderStore()
    store.install_tokenized(tokenized("犬", "かな"))
    store.publish_geometry([box(0), box(1)], (0, 0))
    assert store.current.boxes == [box(0), box(1)]

    store.replace_tokenized(lines=[], tokens=[])  # the successor arrives with nothing to annotate

    assert store.current.boxes == []


def test_boxes_reach_the_draw_of_the_cue_that_owns_them() -> None:
    """The negative half. A guard that withheld boxes from their own cue would turn the defect into
    a total loss of colour, which is strictly worse than the mismatch it replaces."""
    store = CueRenderStore()
    store.install_tokenized(tokenized("犬", "かな"))
    store.publish_geometry([box(0), box(1)], (0, 0))

    assert store.current.boxes == [box(0), box(1)]


def test_a_box_indexing_past_the_current_tokens_is_dropped_at_publish() -> None:
    """Geometry measured for a longer cue, published against a shorter one. The index guard is the
    general form: the field case had zero tokens, but three tokens against six is the same defect
    and no emptiness check would see it."""
    store = CueRenderStore()
    store.install_tokenized(tokenized("犬"))

    store.publish_geometry([box(0), box(1), box(2)], (0, 0))

    assert store.current.boxes == [box(0)]


def test_a_cue_with_tokens_and_no_geometry_yet_is_simply_uncoloured() -> None:
    """The ordinary pending state, which must not be confused with the mismatch."""
    store = CueRenderStore()
    store.install_tokenized(tokenized("犬", "かな"))

    assert store.current.boxes == []


def test_the_reset_a_cue_change_runs_clears_both_halves_together() -> None:
    """`_set_subtitle_inner` resets the store on every non-empty cue, so the mismatch cannot come
    from that path -- which is why the two owners' clocks, not this store, are the defect."""
    store = CueRenderStore()
    store.install_tokenized(tokenized("犬", "かな"))
    store.publish_geometry([box(0)], (0, 0))

    store.reset()

    assert (store.current.lines, store.current.boxes) == ([], [])

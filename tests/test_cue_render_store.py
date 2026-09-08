"""A box and the token it indexes reach a draw together, or not at all.

`CueRenderStore` holds a cue's tokens and boxes; `playback.cue.text` names the cue. The two are
written by different owners at different times, so geometry measured for the cue that left can be
published against the one that arrived.
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

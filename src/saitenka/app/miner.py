"""The mining flow: one-key + bulk mining into Anki.

Owns the mine→dedupe→capture→build-note pipeline and the provenance/tag helpers; the Reader keeps
the view side (previews, ⊕→✓ refresh, toasts) and delegates its public mining API here.

`MinerPorts` is the feature's value, built per operation by `Reader.miner_ports`: the collaborators
mining needs, the cue it is mining, and the acts that land elsewhere. Per operation and not stored,
because half of it is *this* cue — a value kept on a session-lived object would mine the line that
was on screen when the session started. The mpv reads happen once at build time for the same
reason: `path`, `time-pos` and the cue span used to be read at three different depths of one mine.

The cheap leaves take what they need instead of the whole value — `frequency` over a dictionary set
charges its caller one member, not thirty.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app.anki import AnkiError, CardContent, bold_word, build_note, dedupe
from saitenka.app.lookup import card_for
from saitenka.app.media import animated_screenshot, clip_audio, current_timespan, screenshot

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from saitenka.app.anki import Anki, MineConfig
    from saitenka.app.card_preview import PreviewPanel
    from saitenka.app.dictionary import DictionarySet
    from saitenka.app.mined_store import MinedCardStore
    from saitenka.app.session_stats import SessionRecorder
    from saitenka.tokenize import Tokenizer

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MineCue:
    """What the target pick reads: the cue's tokens, how they are coloured, and where the pointer is.

    Its own value rather than a slice of `MinerPorts`, because picking a target is also the tooltip's
    question and answering it must not charge a caller the whole mining closure.
    """

    tokens: Sequence
    styles: Sequence | None
    hover: int
    tokenizer: Tokenizer
    max_bulk: int


@dataclass(frozen=True, slots=True)
class MinerPorts:
    """One mine's collaborators, cue and acts. Built per operation — see the module docstring."""

    cue: MineCue
    #: Non-optional by construction: `Reader.miner_ports` answers `None` when mining is not
    #: configured, so "is there a deck to mine into" is decided once instead of at every entry point.
    anki: Anki
    mine_cfg: MineConfig
    dict_set: DictionarySet | None
    mined_store: MinedCardStore
    ipc: object
    #: Per-session scratch dir the captures land in before Anki stores them.
    scratch: Path
    #: The cue being mined, read once at build time.
    media_path: object
    playhead: float
    sentence_html: str
    #: The stacked phrase the tooltip is showing for the hovered word, so mining the hovered word
    #: defaults to the same entry the user is looking at.
    hovered_terms: tuple
    #: The preview panel this mine writes its media onto. A panel, not slice state: it holds what one
    #: capture produced.
    preview: PreviewPanel
    session_recorder: SessionRecorder | None
    toast: Callable[..., object]
    mark_mined: Callable[[str], None]
    #: Tell the sidebar this cue was mined. An act rather than the sidebar's view, so mining does not
    #: carry the sidebar's read set through the mine.
    mined_here: Callable[[], None]
    preview_existing: Callable[..., None]
    preview_mined: Callable[..., None]
    merge_mined: Callable[[Iterable[str]], object]


def tag_slug(text: str) -> str:
    """A tag-safe slug (Anki tags can't contain spaces): 'Nippon Sangoku' → 'Nippon_Sangoku'."""
    s = re.sub(r"\s+", "_", (text or "").strip())
    return re.sub(r"[^\w-]", "", s, flags=re.UNICODE).strip("_-")


def source_meta(video) -> tuple[str, int | None]:
    """(anime title, episode) parsed from the video filename, or ('', None)."""
    if not video:
        return "", None
    try:
        from saitenka.app.jimaku import parse_filename

        return parse_filename(video)
    except (TypeError, ValueError, OSError):
        return "", None


# --- targets ------------------------------------------------------------------------------------
def mine_target(cue: MineCue) -> int | None:
    """Which token to mine: the hovered one, else the N+1 word, else the first content word."""
    if cue.hover >= 0:
        return cue.hover
    if not cue.tokens:
        return None
    if cue.styles:
        for i, s in enumerate(cue.styles):
            if s.tag.startswith("n+1"):
                return i
    for i, t in enumerate(cue.tokens):
        if cue.tokenizer.is_content(t):
            return i
    return 0


def _select_bulk_targets(cue: MineCue) -> list[int]:
    """Token indices ``bulk_mine`` should mine: content words, not already "known"-colored, deduped
    by lemma (first occurrence wins), capped at ``max_bulk``."""
    targets, seen = [], set()
    for i, t in enumerate(cue.tokens):
        if not cue.tokenizer.is_content(t):
            continue
        if cue.styles and cue.styles[i].tag == "known":
            continue
        if t.lemma in seen:
            continue
        seen.add(t.lemma)
        targets.append(i)
        if len(targets) >= cue.max_bulk:
            break
    return targets


# --- provenance / tags --------------------------------------------------------------------------
def provenance(playhead: float, video) -> str:
    """Structured MiscInfo — clean anime · episode · timestamp (parseable, not the filename)."""
    title, ep = source_meta(video)
    t = int(playhead)
    src = title or (Path(video).name if video else "mpv")
    stamp = f"{t // 60:02d}:{t % 60:02d}"
    return f"{src} · ep{ep:02d} · {stamp}" if ep is not None else f"{src} · {stamp}"


def mine_tags(video) -> list[str]:
    """Robust, hierarchical tags so mined cards can be filtered / rearranged by source +
    episode: ``saitenka::mined``, ``saitenka::source::<anime>``, ``saitenka::ep::<nn>``."""
    tags = ["saitenka::mined"]
    title, ep = source_meta(video)
    slug = tag_slug(title)
    if slug:
        tags.append(f"saitenka::source::{slug}")
    if ep is not None:
        tags.append(f"saitenka::ep::{ep:02d}")
    return tags


def frequency(dict_set, tok) -> tuple[str, str]:
    """(Frequency field HTML, FreqSort) for a mined card — the tooltip's green-pill values."""
    return dict_set.frequency_field(tok) if dict_set else ("", "")


def pitch(dict_set, tok) -> tuple[str, str]:
    """(pitch-accent field HTML, positions) for a mined card — the tooltip's purple-pill values.
    Only consulted for the ``[mine.card_format]`` path (``{pitch-accents}``)."""
    return dict_set.pitch_field(tok) if dict_set else ("", "")


def card_for_token(dict_set, tok):
    """Card fields for ``tok`` — the user's dictionaries first (dict-first mining), falling back
    to the JMdict/jamdict source when no dictionary is configured or the word isn't in one. That
    fallback itself degrades to an expression-only card if the optional ``jmdict`` extra (jamdict)
    isn't installed, so mining never hard-depends on jamdict."""
    if dict_set is not None:
        card = dict_set.card_for(tok)
        if card.glossary_html:
            return card
    return card_for(tok)


def _markers_for(p: MinerPorts, tok, card, content: CardContent, *, video, tags):
    """The ``{marker} -> value`` map for the ``[mine.card_format]`` path, or ``None`` when it isn't
    configured (so ``build_note`` takes the plain ``[mine.fields]`` route and skips this work).
    Shares the ``CardContent`` the note is built from — same sentence/media/freq, no re-derivation."""
    if not p.mine_cfg.card_format:
        return None
    from saitenka.app.card_markers import MarkerContext, build_markers
    from saitenka.app.lookup import POS_EN

    title, _ep = source_meta(video)
    pitch_html, pitch_positions = pitch(p.dict_set, tok)
    return build_markers(
        MarkerContext(
            card=card,
            sentence_html=content.sentence_html,
            picture=content.picture,
            audio=content.audio,
            misc=content.misc,
            doc_title=title,
            freq_html=content.freq_html,
            freq_rank=content.freq_sort,
            pos_en=POS_EN.get(tok.pos, tok.pos or "word"),
            tags=tags,
            pitch_html=pitch_html,
            pitch_positions=pitch_positions,
        )
    )


# --- media capture ------------------------------------------------------------------------------
def capture_media(
    p: MinerPorts, base: str, video, *, animated: bool | None = None
) -> tuple[str, str]:
    """Capture the card image (a still frame, or an animated clip of the cue — WebP or GIF) + the cue's
    audio and store both in Anki. Returns (pic, audio).

    ``animated`` overrides ``[mine].animated_screenshot`` for this one mine — the video-mine shortcut
    passes ``True`` regardless of the config default; ``None`` uses the config. The still JPG is always
    captured locally (``preview.last_jpg`` drives the preview and is the fallback); the clip becomes
    the card image only when the encode succeeds. Also stashes ``preview.last_audio``. Warn-toasts on
    failure."""
    # any animated path needs mine_cfg (for the encode opts), so gate the whole thing on it — a per-mine
    # override can't force a clip without a config to encode from.
    want_animated = bool(p.mine_cfg) and (
        p.mine_cfg.animated.enabled if animated is None else animated
    )
    p.preview.last_jpg = p.preview.last_audio = None
    try:
        span = current_timespan(p.ipc)  # guarded: an IPC hiccup must not escape and kill the loop
    except (OSError, ValueError):
        log.debug("cue timespan read failed — image-only mine", exc_info=True)
        span = None
    pic, pic_err = _capture_image(p, base, video, span, animated=want_animated)
    audio, audio_err = _capture_audio(p, base, video, span)
    _warn_capture_failure(p, pic_err, audio_err)
    return pic, audio


def _capture_image(
    p: MinerPorts, base: str, video, span, *, animated: bool
) -> tuple[str, Exception | None]:
    """The card image: the mpv still (always, → ``preview.last_jpg``), replaced by an animated clip when
    ``animated`` and the encode succeeds. Returns (media_name, error-or-None)."""
    try:
        jpg = p.scratch / f"{base}.jpg"
        screenshot(p.ipc, jpg)
        # local still — drives the preview and is the fallback (may not be uploaded)
        p.preview.last_jpg = jpg
        if animated and video and span:
            clip = _encode_animated(p, base, video, span)
            if clip:
                return clip, None  # clip is the card image; the still stays a local-only fallback
        return p.anki.store_media(f"{base}.jpg", jpg), None  # still is the card image → upload it
    except (OSError, AnkiError, json.JSONDecodeError) as e:
        log.debug("screenshot capture failed", exc_info=True)
        return "", e


def _encode_animated(p: MinerPorts, base: str, video, span) -> str | None:
    """Encode + store the animated clip, returning its media name — or None on any failure (a missing
    encoder, a bad encode, a store error), so the caller keeps the still."""
    try:
        # nominal path; animated_screenshot swaps the suffix to the real format (.webp or .gif)
        clip = animated_screenshot(video, span, p.scratch / f"{base}.webp", p.mine_cfg.animated)
        if clip:
            return p.anki.store_media(clip.name, clip)
    except (OSError, subprocess.SubprocessError, AnkiError, json.JSONDecodeError):
        log.debug("animated screenshot failed — keeping the still", exc_info=True)
    return None


def _capture_audio(p: MinerPorts, base: str, video, span) -> tuple[str, Exception | None]:
    """The cue audio clip (→ ``preview.last_audio``). Returns (media_name, error-or-None)."""
    try:
        if video and span:
            aud = p.scratch / f"{base}.m4a"
            clip_audio(video, span, aud, normalize=p.mine_cfg.normalize_audio)
            p.preview.last_audio = aud
            return p.anki.store_media(f"{base}.m4a", aud), None
    except (OSError, subprocess.CalledProcessError, AnkiError, json.JSONDecodeError) as e:
        log.debug("audio capture failed", exc_info=True)
        return "", e
    return "", None


def _warn_capture_failure(p: MinerPorts, pic_err, audio_err) -> None:
    if pic_err and audio_err:
        p.toast("media capture failed (no image/audio on card)", "warn")
    elif pic_err:
        p.toast("screenshot failed — audio only", "warn")
    elif audio_err:
        p.toast("audio clip failed — image only", "warn")


# --- mining -------------------------------------------------------------------------------------
def _attach_word_audio(p: MinerPorts, note: dict, card) -> None:
    """Populate the configured word-audio field from the local yomichan/yomitan pack (#93) —
    ADDITIVE to the mined scene/sentence audio, never a replacement. Best-effort: an unconfigured
    pack, a resolve miss, or a store failure leaves the field unset (never an empty ``[sound:]``)."""
    if not p.mine_cfg.word_audio_pack:
        return
    from saitenka.app.word_audio import resolve

    try:
        hit = resolve(p.mine_cfg.word_audio_pack, card.expression, card.reading)
        if hit is None:
            return
        media_name = p.anki.store_media(hit.filename, hit.path)
        note["fields"][p.mine_cfg.word_audio_field] = f"[sound:{media_name}]"
    except (OSError, AnkiError, json.JSONDecodeError):
        log.debug("word-audio attach failed", exc_info=True)


def _persist_mined(p: MinerPorts, note_id: int, card, video) -> None:
    """Record the mined note ↔ episode/cue link in the durable mined-card store (#253), so the
    sidebar Mine tab can list this episode's cards offline. Best-effort: a store failure (or a
    non-int ``addNote`` result / no active cue) must never break the mine."""
    if not isinstance(note_id, int) or not video:
        return
    span = current_timespan(p.ipc)
    try:
        with otel_metrics.traced("mined_store_write"):  # main-thread SQLite on a mine (#253 link)
            p.mined_store.record(
                note_id=note_id,
                video_path=str(video),
                cue_start=span.start if span else 0.0,
                cue_end=span.end if span else 0.0,
                expression=card.expression,
                reading=card.reading,
                deck=p.mine_cfg.deck,
            )
    except (OSError, sqlite3.Error, ValueError):
        log.debug("mined-card store write failed", exc_info=True)


def mine_current(p: MinerPorts) -> None:
    idx = mine_target(p.cue)
    if idx is None:
        p.toast("no word to mine", "warn")
        return
    tok = p.cue.tokens[idx]
    # Mining the hovered word defaults to its longest stacked phrase (数ある over 数), matching the
    # tooltip's top entry; the explicit per-entry ⊕ still mines any specific stacked entry.
    cards = (
        p.dict_set.cards_for(tok, extra_terms=p.hovered_terms)
        if (p.dict_set and idx == p.cue.hover and p.hovered_terms)
        else []
    )
    mine_token(p, tok, card=cards[0] if cards else None)


def mine_token(p: MinerPorts, tok, *, force: bool = False, card=None, animated=None) -> None:
    """Mine a specific token into Anki — the hovered subtitle word, or an inner word discovered
    by scanning inside the tooltip (the nested popup's ⊕). ``force`` mines a second card for an
    expression already in the deck (the preview's explicit "add anyway" for a different scene).
    ``card`` mines an explicit CardData (a specific entry chosen from the panel's per-entry ⊕),
    bypassing the default entry pick — otherwise the dict-first ``card_for_token`` derives it.
    ``animated`` overrides ``[mine].animated_screenshot`` for this mine (the video-mine shortcut
    passes ``True``)."""
    try:
        card = card if card is not None else card_for_token(p.dict_set, tok)
        if not force:
            existing = dedupe(p.anki, p.mine_cfg, card.expression)
            if existing:
                p.mark_mined(card.expression)  # already in the deck → ✓
                p.mined_here()
                p.preview.dup_tok = tok  # for an explicit "add anyway"
                p.preview_existing(existing[0], card, "exists")
                return
        video = p.media_path
        pic, audio = capture_media(
            p, f"saitenka_{int(time.time() * 1000)}", video, animated=animated
        )
        freq = frequency(p.dict_set, tok)
        sentence_html = bold_word(p.sentence_html, tok.surface)
        misc, tags = provenance(p.playhead, video), mine_tags(video)
        content = CardContent(
            sentence_html=sentence_html,
            picture=pic,
            audio=audio,
            misc=misc,
            freq_html=freq[0],
            freq_sort=freq[1],
        )
        markers = _markers_for(p, tok, card, content, video=video, tags=tags)
        note = build_note(p.mine_cfg, card, content, tags, allow_duplicate=force, markers=markers)
        if not force and not p.anki.can_add(note):
            p.toast(f"can't add {card.expression}", "err")
            return
        _attach_word_audio(p, note, card)
        # --- mine-time add_note seam (shared by #253 note-id retention + #93 word-audio) -------
        note_id = p.anki.add_note(note)
        _persist_mined(p, note_id, card, video)
        if p.session_recorder is not None:
            p.session_recorder.record_mined()
        p.mark_mined(card.expression)
        p.mined_here()
        p.preview_mined(card, tok, video, "duplicate" if force else "mined")
    except AnkiError as e:
        p.toast(f"mine failed: {e}", "err")
    except Exception as e:  # never let a mine crash the loop
        log.exception("mine_token failed")
        p.toast(f"mine error: {e}", "err")


def bulk_mine(p: MinerPorts) -> None:
    """Mine every unknown content word in the current cue, sharing one screenshot + audio."""
    if not p.cue.tokens:
        p.toast("nothing to mine", "warn")
        return
    targets = _select_bulk_targets(p.cue)
    if not targets:
        p.toast("no new words", "warn")
        return
    video = p.media_path
    pic, audio = capture_media(p, f"saitenka_{int(time.time() * 1000)}", video)
    misc, sentence, tags = provenance(p.playhead, video), p.sentence_html, mine_tags(video)
    mined = dup = 0
    try:
        for idx in targets:
            tok = p.cue.tokens[idx]
            card = card_for_token(p.dict_set, tok)
            if not card.glossary_html:  # no dict entry (name/particle) — skip
                continue
            if dedupe(p.anki, p.mine_cfg, card.expression):
                dup += 1
                continue
            freq = frequency(p.dict_set, tok)
            content = CardContent(
                sentence_html=bold_word(sentence, tok.surface),
                picture=pic,
                audio=audio,
                misc=misc,
                freq_html=freq[0],
                freq_sort=freq[1],
            )
            markers = _markers_for(p, tok, card, content, video=video, tags=tags)
            note = build_note(p.mine_cfg, card, content, tags, markers=markers)
            if p.anki.can_add(note):
                p.anki.add_note(note)
                p.mark_mined(card.expression)
                mined += 1
            else:
                dup += 1
        p.toast(f"mined {mined} · {dup} dup", "ok" if mined else "warn")
        if p.session_recorder is not None:
            p.session_recorder.record_mined(mined)
    except AnkiError as e:
        p.toast(f"bulk failed: {e}", "err")


def mined_expressions(anki, mine_cfg) -> set[str] | None:
    """Fetch the mining deck's expressions. `None` means the deck could not be read at all, which is
    a different fact from an empty deck and is what the seed's retry branches on."""
    from saitenka.app.miner_ui import _strip_tags

    if not anki or not mine_cfg:
        return set()
    fieldname = mine_cfg.expression_field() or "Expression"
    expressions: set[str] = set()
    try:
        ids = anki.find_notes(f'deck:"{mine_cfg.deck}"')
        for chunk in (ids[i : i + 500] for i in range(0, len(ids), 500)):
            for note in anki.notes_info(chunk):
                val = _strip_tags(note.get("fields", {}).get(fieldname, {}).get("value", ""))
                if val:
                    expressions.add(val)
    except Exception:
        log.debug("seed_mined failed (AnkiConnect down?)", exc_info=True)
        return None
    return expressions


def seed_mined(p: MinerPorts) -> None:
    """Pre-load already-mined expressions from the mining deck."""
    p.merge_mined(mined_expressions(p.anki, p.mine_cfg) or ())

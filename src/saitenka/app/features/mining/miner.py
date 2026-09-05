"""The mining flow: one-key + bulk mining into Anki.

Owns the mine→dedupe→capture→build-note transaction kernel and provenance/tag helpers. The
bounded mining owner admits operations and assembles one `_MinerContext`; presentation and session
accounting cross the boundary only as named `MiningApply` acts.

The cheap leaves take what they need instead of the whole value — `frequency` over a dictionary set
charges its caller one member, not thirty.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka_card import CardContent, bold_word, build_note, markers_in, strip_field_html

from saitenka.app.anki import AnkiError, dedupe
from saitenka.app.lookup import card_for
from saitenka.app.media import (
    AudioOutputMissingError,
    Timespan,
    animated_screenshot,
    clip_audio,
    screenshot,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from saitenka_card import CardData, MineConfig
    from saitenka_tokenize.japanese import Token
    from saitenka_tokenize.registry import Tokenizer

    from saitenka.app.anki import Anki
    from saitenka.app.dictionary import DictionarySet

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MineCue:
    """What the target pick reads: the cue's tokens, how they are coloured, and where the pointer is.

    Its own value because picking a target is also the tooltip's
    question and answering it must not charge a caller the whole mining closure.
    """

    tokens: Sequence
    styles: Sequence | None
    hover: int
    tokenizer: Tokenizer
    max_bulk: int


@dataclass(frozen=True, slots=True)
class MiningEncounter:
    """Facts sampled once when the owner admits an operation."""

    cue: MineCue
    dict_set: DictionarySet | None
    ipc: object
    media_path: object
    playhead: float
    span: Timespan | None
    sentence_html: str
    hovered_terms: tuple


@dataclass(frozen=True, slots=True)
class MiningApply:
    """Owner-thread effects whose state remains outside mining."""

    toast: Callable[..., object]
    reset_capture: Callable[[], None]
    captured_image: Callable[[Path], None]
    captured_audio: Callable[[Path], None]
    mark_mined: Callable[[str], None]
    mined_here: Callable[[], None]
    remember_duplicate: Callable[[Token], None]
    preview_existing: Callable[..., None]
    preview_mined: Callable[..., None]
    record_mined: Callable[[int], None]
    record_link: Callable[..., None]


@dataclass(frozen=True, slots=True)
class MiningTransaction:
    """One admitted transaction, constructed only by the bounded mining owner."""

    anki: Anki
    mine_cfg: MineConfig
    scratch: Path
    encounter: MiningEncounter
    apply: MiningApply
    cancelled: Callable[[], bool] = lambda: False


@dataclass(frozen=True, slots=True)
class TokenMinePlan:
    token: Token
    card: CardData
    force: bool


@dataclass(frozen=True, slots=True)
class BulkMineItem:
    token: Token
    card: CardData


@dataclass(frozen=True, slots=True)
class BulkMinePlan:
    items: tuple[BulkMineItem, ...]
    duplicates: int


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
            if s.verdict.is_n_plus_one:
                return i
    for i, t in enumerate(cue.tokens):
        if cue.tokenizer.is_content(t):
            return i
    return 0


def sentence_html(lines) -> str:
    """Join the admitted cue lines for the mined note's sentence field."""
    return "<br>".join("".join(token.surface for token in line) for line in lines)


def _select_bulk_targets(cue: MineCue) -> list[int]:
    """Token indices ``bulk_mine`` should mine: content words, not already "known"-colored, deduped
    by lemma (first occurrence wins), capped at ``max_bulk``."""
    targets, seen = [], set()
    for i, t in enumerate(cue.tokens):
        if not cue.tokenizer.is_content(t):
            continue
        # Reads the verdict, not the tag string. `tag == "known"` also excluded every known word that
        # HAS a JLPT level, because the level suffixes the tag ("known/jlpt-N3") — so those were
        # offered as bulk-mine candidates despite being known-colored.
        if cue.styles and cue.styles[i].verdict.is_mature:
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


def _markers_for(p: MiningTransaction, tok, card, content: CardContent, *, video, tags):
    """The ``{marker} -> value`` map for the ``[mine.card_format]`` path, or ``None`` when it isn't
    configured (so ``build_note`` takes the plain ``[mine.fields]`` route and skips this work).
    Shares the ``CardContent`` the note is built from — same sentence/media/freq, no re-derivation."""
    if not p.mine_cfg.card_format:
        return None
    from saitenka_card import MarkerContext, build_markers

    from saitenka.app.lookup import POS_EN

    title, _ep = source_meta(video)
    pitch_html, pitch_positions = pitch(p.encounter.dict_set, tok)
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
@dataclass(frozen=True, slots=True)
class MediaNeeds:
    picture: bool
    audio: bool


@dataclass(frozen=True, slots=True)
class PreparedCapture:
    base: str
    span: Timespan | None
    needs: MediaNeeds
    still: Path | None
    still_error: Exception | None
    animated: bool


def media_needs(config: MineConfig) -> MediaNeeds:
    if config.card_format:
        used = set().union(*(markers_in(str(value)) for value in config.card_format.values()))
        return MediaNeeds("screenshot" in used, "sentence-audio" in used)
    return MediaNeeds("picture" in config.fields, "audio" in config.fields)


def prepare_capture(
    p: MiningTransaction, base: str, *, animated: bool | None = None
) -> PreparedCapture:
    """Freeze media inputs and perform the sole mpv command before background work starts."""
    needs = media_needs(p.mine_cfg)
    use_animation = needs.picture and (
        p.mine_cfg.animated.enabled if animated is None else animated
    )
    p.apply.reset_capture()
    still = p.scratch / f"{base}.jpg" if needs.picture else None
    error: Exception | None = None
    if still is not None:
        try:
            screenshot(p.encounter.ipc, still)
        except OSError as exc:
            error = exc
            still = None
    return PreparedCapture(base, p.encounter.span, needs, still, error, use_animation)


def capture_media(
    p: MiningTransaction,
    base: str,
    video,
    *,
    animated: bool | None = None,
    prepared: PreparedCapture | None = None,
) -> tuple[str, str]:
    """Capture the card image (a still frame, or an animated clip of the cue — WebP or GIF) + the cue's
    audio and store both in Anki. Returns (pic, audio).

    ``animated`` overrides ``[mine].animated_screenshot`` for this one mine — the video-mine shortcut
    passes ``True`` regardless of the config default; ``None`` uses the config. The still JPG is always
    captured locally (``preview.last_jpg`` drives the preview and is the fallback); the clip becomes
    the card image only when the encode succeeds. Also stashes ``preview.last_audio``. Warn-toasts on
    failure."""
    prepared = prepared or prepare_capture(p, base, animated=animated)
    pic, pic_err = _capture_image(p, video, prepared)
    audio, audio_err = _capture_audio(
        p, prepared.base, video, prepared.span, enabled=prepared.needs.audio
    )
    _warn_capture_failure(p, pic_err, audio_err)
    return pic, audio


def _capture_image(
    p: MiningTransaction, video, prepared: PreparedCapture
) -> tuple[str, Exception | None]:
    """The card image: the mpv still (always, → ``preview.last_jpg``), replaced by an animated clip when
    ``animated`` and the encode succeeds. Returns (media_name, error-or-None)."""
    if not prepared.needs.picture:
        return "", None
    if prepared.still_error is not None:
        return "", prepared.still_error
    assert prepared.still is not None
    try:
        p.apply.captured_image(prepared.still)
        if prepared.animated and video and prepared.span:
            clip = _encode_animated(p, prepared.base, video, prepared.span)
            if clip:
                return clip, None
        if p.cancelled():
            return "", None
        return p.anki.store_media(f"{prepared.base}.jpg", prepared.still), None
    except (OSError, AnkiError, json.JSONDecodeError) as e:
        log.debug("screenshot capture failed", exc_info=True)
        return "", e


def _encode_animated(p: MiningTransaction, base: str, video, span) -> str | None:
    """Encode + store the animated clip, returning its media name — or None on any failure (a missing
    encoder, a bad encode, a store error), so the caller keeps the still."""
    try:
        # nominal path; animated_screenshot swaps the suffix to the real format (.webp or .gif)
        clip = animated_screenshot(video, span, p.scratch / f"{base}.webp", p.mine_cfg.animated)
        if p.cancelled():
            return None
        if clip:
            return p.anki.store_media(clip.name, clip)
    except (OSError, subprocess.SubprocessError, AnkiError, json.JSONDecodeError):
        log.debug("animated screenshot failed — keeping the still", exc_info=True)
    return None


def _capture_audio(
    p: MiningTransaction, base: str, video, span, *, enabled: bool = True
) -> tuple[str, Exception | None]:
    """The cue audio clip (→ ``preview.last_audio``). Returns (media_name, error-or-None)."""
    try:
        if enabled and video and span:
            aud = p.scratch / f"{base}.m4a"
            clip_audio(video, span, aud, normalize=p.mine_cfg.normalize_audio)
            if p.cancelled():
                return "", None
            p.apply.captured_audio(aud)
            return p.anki.store_media(f"{base}.m4a", aud), None
    except (
        OSError,
        subprocess.SubprocessError,
        AudioOutputMissingError,
        AnkiError,
        json.JSONDecodeError,
    ) as e:
        log.debug("audio capture failed", exc_info=True)
        return "", e
    return "", None


def _warn_capture_failure(p: MiningTransaction, pic_err, audio_err) -> None:
    message = _capture_failure_message(pic_err, audio_err)
    if message is not None:
        p.apply.toast(*message)


def _capture_failure_message(pic_err, audio_err) -> tuple[str, str] | None:
    audio_reason = _audio_failure_reason(audio_err)
    if pic_err and audio_err:
        return f"media capture failed (no image; {audio_reason})", "warn"
    if pic_err:
        return "screenshot failed — audio only", "warn"
    if audio_err:
        return f"{audio_reason} — image only", "warn"
    return None


def _audio_failure_reason(error: Exception | None) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return "audio extraction timed out"
    if isinstance(error, subprocess.CalledProcessError):
        return "audio ffmpeg failed"
    if isinstance(error, AudioOutputMissingError):
        return "ffmpeg produced no audio"
    return "audio clip failed"


# --- mining -------------------------------------------------------------------------------------
def _attach_word_audio(p: MiningTransaction, note: dict, card) -> None:
    """Populate the configured word-audio field from the local yomichan/yomitan pack (#93) —
    ADDITIVE to the mined scene/sentence audio, never a replacement. Best-effort: an unconfigured
    pack, a resolve miss, or a store failure leaves the field unset (never an empty ``[sound:]``)."""
    if not p.mine_cfg.word_audio_pack:
        return
    from saitenka.app.features.mining.word_audio import resolve

    try:
        hit = resolve(p.mine_cfg.word_audio_pack, card.expression, card.reading)
        if hit is None or p.cancelled():
            return
        media_name = p.anki.store_media(hit.filename, hit.path)
        note["fields"][p.mine_cfg.word_audio_field] = f"[sound:{media_name}]"
    except (OSError, AnkiError, json.JSONDecodeError):
        log.debug("word-audio attach failed", exc_info=True)


def _persist_mined(p: MiningTransaction, note_id: int, card, video) -> None:
    """Record the mined note ↔ episode/cue link in the durable mined-card store (#253), so the
    sidebar Mine tab can list this episode's cards offline. Best-effort: a store failure (or a
    non-int ``addNote`` result / no active cue) must never break the mine."""
    if not isinstance(note_id, int) or not video:
        return
    span = p.encounter.span
    p.apply.record_link(
        note_id,
        str(video),
        span.start if span else 0.0,
        span.end if span else 0.0,
        card.expression,
        card.reading,
        p.mine_cfg.deck,
    )


def mine_current(p: MiningTransaction) -> None:
    idx = mine_target(p.encounter.cue)
    if idx is None:
        p.apply.toast("no word to mine", "warn")
        return
    tok = p.encounter.cue.tokens[idx]
    # Mining the hovered word defaults to its longest stacked phrase (数ある over 数), matching the
    # tooltip's top entry; the explicit per-entry ⊕ still mines any specific stacked entry.
    cards = (
        p.encounter.dict_set.cards_for(tok, extra_terms=p.encounter.hovered_terms)
        if (p.encounter.dict_set and idx == p.encounter.cue.hover and p.encounter.hovered_terms)
        else []
    )
    mine_token(p, tok, card=cards[0] if cards else None)


def mine_token(
    p: MiningTransaction,
    tok,
    *,
    force: bool = False,
    card=None,
    animated=None,
    prepared: PreparedCapture | None = None,
) -> None:
    """Mine a specific token into Anki — the hovered subtitle word, or an inner word discovered
    by scanning inside the tooltip (the nested popup's ⊕). ``force`` mines a second card for an
    expression already in the deck (the preview's explicit "add anyway" for a different scene).
    ``card`` mines an explicit CardData (a specific entry chosen from the panel's per-entry ⊕),
    bypassing the default entry pick — otherwise the dict-first ``card_for_token`` derives it.
    ``animated`` overrides ``[mine].animated_screenshot`` for this mine (the video-mine shortcut
    passes ``True``)."""
    plan = preflight_token(p, tok, force=force, card=card)
    if plan is None:
        return
    prepared = prepared or prepare_capture(
        p, f"saitenka_{int(time.time() * 1000)}", animated=animated
    )
    commit_token(p, plan, prepared)


def preflight_token(
    p: MiningTransaction, tok, *, force: bool = False, card=None
) -> TokenMinePlan | None:
    """Resolve and deduplicate a token before any media capture."""
    if p.cancelled():
        return None
    try:
        card = card if card is not None else card_for_token(p.encounter.dict_set, tok)
        existing = dedupe(p.anki, p.mine_cfg, card.expression)
        if p.cancelled():
            return None
        if existing and not force:
            p.apply.mark_mined(card.expression)
            p.apply.mined_here()
            p.apply.remember_duplicate(tok)
            p.apply.preview_existing(existing[0], card, "exists")
            return None
        return TokenMinePlan(tok, card, force)
    except AnkiError as error:
        p.apply.toast(f"mine failed: {error}", "err")
    except Exception as error:
        log.exception("mine_token preflight failed")
        p.apply.toast(f"mine error: {error}", "err")
    return None


def commit_token(p: MiningTransaction, plan: TokenMinePlan, prepared: PreparedCapture) -> None:
    """Build and add one preflighted card; ``add_note`` is the commit point."""
    if p.cancelled():
        return
    tok, card, force = plan.token, plan.card, plan.force
    try:
        video = p.encounter.media_path
        pic, audio = capture_media(p, prepared.base, video, prepared=prepared)
        if p.cancelled():
            return
        freq = frequency(p.encounter.dict_set, tok)
        sentence_html = bold_word(p.encounter.sentence_html, tok.surface)
        misc, tags = provenance(p.encounter.playhead, video), mine_tags(video)
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
        _attach_word_audio(p, note, card)
        if p.cancelled():
            return
        if not p.anki.can_add(note):
            p.apply.toast(f"can't add {card.expression}", "err")
            return
        if p.cancelled():
            return
        # AnkiConnect cannot retract a request after this call begins.
        note_id = p.anki.add_note(note)
        _persist_mined(p, note_id, card, video)
        p.apply.record_mined(1)
        p.apply.mark_mined(card.expression)
        p.apply.mined_here()
        p.apply.preview_mined(card, tok, video, "duplicate" if force else "mined")
    except AnkiError as e:
        p.apply.toast(f"mine failed: {e}", "err")
    except Exception as e:  # never let a mine crash the loop
        log.exception("mine_token failed")
        p.apply.toast(f"mine error: {e}", "err")


def bulk_mine(p: MiningTransaction, *, prepared: PreparedCapture | None = None) -> None:
    """Mine every unknown content word in the current cue, sharing one screenshot + audio."""
    plan = preflight_bulk(p)
    if plan is None:
        return
    prepared = prepared or prepare_capture(p, f"saitenka_{int(time.time() * 1000)}")
    commit_bulk(p, plan, prepared)


def preflight_bulk(p: MiningTransaction) -> BulkMinePlan | None:
    """Resolve and deduplicate a cue before any media capture."""
    if not p.encounter.cue.tokens:
        p.apply.toast("nothing to mine", "warn")
        return None
    targets = _select_bulk_targets(p.encounter.cue)
    if not targets:
        p.apply.toast("no new words", "warn")
        return None
    items: list[BulkMineItem] = []
    duplicates = 0
    try:
        for idx in targets:
            if p.cancelled():
                return None
            tok = p.encounter.cue.tokens[idx]
            card = card_for_token(p.encounter.dict_set, tok)
            if not card.glossary_html:
                continue
            if dedupe(p.anki, p.mine_cfg, card.expression):
                duplicates += 1
            else:
                items.append(BulkMineItem(tok, card))
    except AnkiError as error:
        p.apply.toast(f"bulk failed: {error}", "err")
        return None
    if not items:
        p.apply.toast(f"mined 0 · {duplicates} dup", "warn")
        p.apply.record_mined(0)
        return None
    return BulkMinePlan(tuple(items), duplicates)


def commit_bulk(p: MiningTransaction, plan: BulkMinePlan, prepared: PreparedCapture) -> None:
    """Add a preflighted cue batch, stopping before the next commit when cancelled."""
    if p.cancelled():
        return
    video = p.encounter.media_path
    pic, audio = capture_media(p, prepared.base, video, prepared=prepared)
    if p.cancelled():
        return
    misc, sentence, tags = (
        provenance(p.encounter.playhead, video),
        p.encounter.sentence_html,
        mine_tags(video),
    )
    mined, dup = 0, plan.duplicates
    try:
        for item in plan.items:
            if p.cancelled():
                break
            tok, card = item.token, item.card
            freq = frequency(p.encounter.dict_set, tok)
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
                if p.cancelled():
                    return
                note_id = p.anki.add_note(note)
                _persist_mined(p, note_id, card, video)
                p.apply.mark_mined(card.expression)
                mined += 1
            else:
                dup += 1
        p.apply.toast(f"mined {mined} · {dup} dup", "ok" if mined else "warn")
    except AnkiError as e:
        p.apply.toast(f"bulk failed: {e}", "err")
    finally:
        p.apply.record_mined(mined)


def mined_expressions(anki, mine_cfg) -> set[str] | None:
    """Fetch the mining deck's expressions. `None` means the deck could not be read at all, which is
    a different fact from an empty deck and is what the seed's retry branches on."""
    if not anki or not mine_cfg:
        return set()
    fieldname = mine_cfg.expression_field() or "Expression"
    expressions: set[str] = set()
    try:
        ids = anki.find_notes(f'deck:"{mine_cfg.deck}"')
        for chunk in (ids[i : i + 500] for i in range(0, len(ids), 500)):
            for note in anki.notes_info(chunk):
                val = strip_field_html(note.get("fields", {}).get(fieldname, {}).get("value", ""))
                if val:
                    expressions.add(val)
    except Exception:
        log.debug("seed_mined failed (AnkiConnect down?)", exc_info=True)
        return None
    return expressions

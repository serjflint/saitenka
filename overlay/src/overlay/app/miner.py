"""The mining flow: one-key + bulk mining into Anki.

``Miner`` owns the mine→dedupe→capture→build-note pipeline and the provenance/tag helpers; the
Reader keeps the view side (previews, ⊕→✓ refresh, toasts) and delegates its public mining API
here. Composition: the Miner reaches collaborators (ipc, anki, tokens, toasts) through the Reader.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path

from overlay.app.anki import AnkiError, bold_word, build_note, dedupe
from overlay.app.lookup import card_for
from overlay.app.media import animated_screenshot, clip_audio, current_timespan, screenshot

log = logging.getLogger(__name__)


def tag_slug(text: str) -> str:
    """A tag-safe slug (Anki tags can't contain spaces): 'Nippon Sangoku' → 'Nippon_Sangoku'."""
    s = re.sub(r"\s+", "_", (text or "").strip())
    return re.sub(r"[^\w-]", "", s, flags=re.UNICODE).strip("_-")


def source_meta(video) -> tuple[str, int | None]:
    """(anime title, episode) parsed from the video filename, or ('', None)."""
    if not video:
        return "", None
    try:
        from overlay.app.jimaku import parse_filename

        return parse_filename(video)
    except (TypeError, ValueError, OSError):
        return "", None


def _select_bulk_targets(r) -> list[int]:
    """Token indices ``bulk_mine`` should mine: content words, not already "known"-colored, deduped
    by lemma (first occurrence wins), capped at ``r.max_bulk``."""
    from overlay.app.controller import SKIP_POS

    targets, seen = [], set()
    for i, t in enumerate(r.tokens):
        if not (t.is_content and t.pos not in SKIP_POS):
            continue
        if r.styles and r.styles[i].tag == "known":
            continue
        if t.lemma in seen:
            continue
        seen.add(t.lemma)
        targets.append(i)
        if len(targets) >= r.max_bulk:
            break
    return targets


class Miner:
    """Mines words from the current cue into Anki. All IPC happens on the main thread (the mining
    entry points are key/click handlers dispatched by the Reader's poll loop)."""

    def __init__(self, reader):
        self.r = reader

    # --- targets ------------------------------------------------------------------------------
    def mine_target(self) -> int | None:
        """Which token to mine: the hovered one, else the N+1 word, else the first content word."""
        r = self.r
        from overlay.app.controller import SKIP_POS

        if r.hover >= 0:
            return r.hover
        if not r.tokens:
            return None
        if r.styles:
            for i, s in enumerate(r.styles):
                if s.tag.startswith("n+1"):
                    return i
        for i, t in enumerate(r.tokens):
            if t.is_content and t.pos not in SKIP_POS:
                return i
        return 0

    # --- provenance / tags ----------------------------------------------------------------------
    def provenance(self, video) -> str:
        """Structured MiscInfo — clean anime · episode · timestamp (parseable, not the filename)."""
        title, ep = source_meta(video)
        t = int(self.r._get("time-pos") or 0)
        src = title or (Path(video).name if video else "mpv")
        stamp = f"{t // 60:02d}:{t % 60:02d}"
        return f"{src} · ep{ep:02d} · {stamp}" if ep is not None else f"{src} · {stamp}"

    def mine_tags(self, video) -> list[str]:
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

    def frequency(self, tok) -> tuple[str, str]:
        """(Frequency field HTML, FreqSort) for a mined card — the tooltip's green-pill values."""
        r = self.r
        return r.dict_set.frequency_field(tok) if r.dict_set else ("", "")

    def pitch(self, tok) -> tuple[str, str]:
        """(pitch-accent field HTML, positions) for a mined card — the tooltip's purple-pill values.
        Only consulted for the ``[mine.card_format]`` path (``{pitch-accents}``)."""
        r = self.r
        return r.dict_set.pitch_field(tok) if r.dict_set else ("", "")

    def _markers_for(self, tok, card, *, sentence_html, pic, audio, video, misc, tags, freq):
        """The ``{marker} -> value`` map for the ``[mine.card_format]`` path, or ``None`` when it isn't
        configured (so ``build_note`` takes the plain ``[mine.fields]`` route and skips this work)."""
        if not self.r.mine_cfg.card_format:
            return None
        from overlay.app.card_markers import build_markers
        from overlay.app.lookup import POS_EN

        title, _ep = source_meta(video)
        pitch_html, pitch_positions = self.pitch(tok)
        return build_markers(
            card,
            sentence_html=sentence_html,
            picture=pic,
            audio=audio,
            misc=misc,
            doc_title=title,
            freq_html=freq[0],
            freq_rank=freq[1],
            pos_en=POS_EN.get(tok.pos, tok.pos or "word"),
            tags=tags,
            pitch_html=pitch_html,
            pitch_positions=pitch_positions,
        )

    def _card_for(self, tok):
        """Card fields for ``tok`` — the user's dictionaries first (dict-first mining), falling back
        to the JMdict/jamdict source when no dictionary is configured or the word isn't in one. That
        fallback itself degrades to an expression-only card if the optional ``jmdict`` extra (jamdict)
        isn't installed, so mining never hard-depends on jamdict."""
        ds = self.r.dict_set
        if ds is not None:
            card = ds.card_for(tok)
            if card.glossary_html:
                return card
        return card_for(tok)

    # --- media capture --------------------------------------------------------------------------
    def capture_media(self, base: str, video, *, animated: bool | None = None) -> tuple[str, str]:
        """Capture the card image (a still frame, or an animated clip of the cue — WebP or GIF) + the cue's
        audio and store both in Anki. Returns (pic, audio).

        ``animated`` overrides ``[mine].animated_screenshot`` for this one mine — the video-mine shortcut
        passes ``True`` regardless of the config default; ``None`` uses the config. The still JPG is always
        captured locally (``_last_jpg`` drives the preview and is the fallback); the clip becomes the card
        image only when the encode succeeds. Also stashes ``_last_audio``. Warn-toasts on failure."""
        r = self.r
        # any animated path needs mine_cfg (for the encode opts), so gate the whole thing on it — a per-mine
        # override can't force a clip without a config to encode from.
        want_animated = bool(r.mine_cfg) and (
            r.mine_cfg.animated.enabled if animated is None else animated
        )
        r._last_jpg = r._last_audio = None
        try:
            span = current_timespan(
                r.ipc
            )  # guarded: an IPC hiccup here must not escape and kill the loop
        except (OSError, ValueError):
            log.debug("cue timespan read failed — image-only mine", exc_info=True)
            span = None
        pic, pic_err = self._capture_image(base, video, span, animated=want_animated)
        audio, audio_err = self._capture_audio(base, video, span)
        self._warn_capture_failure(pic_err, audio_err)
        return pic, audio

    def _capture_image(
        self, base: str, video, span, *, animated: bool
    ) -> tuple[str, Exception | None]:
        """The card image: the mpv still (always, → ``_last_jpg``), replaced by an animated clip when
        ``animated`` and the encode succeeds. Returns (media_name, error-or-None)."""
        r = self.r
        try:
            jpg = r._tmp / f"{base}.jpg"
            screenshot(r.ipc, jpg)
            r._last_jpg = (
                jpg  # local still — drives the preview and is the fallback (may not be uploaded)
            )
            if animated and video and span:
                clip = self._encode_animated(base, video, span)
                if clip:
                    return (
                        clip,
                        None,
                    )  # clip is the card image; the still stays a local-only fallback
            return r.anki.store_media(
                f"{base}.jpg", jpg
            ), None  # still is the card image → upload it
        except (OSError, AnkiError, json.JSONDecodeError) as e:
            log.debug("screenshot capture failed", exc_info=True)
            return "", e

    def _encode_animated(self, base: str, video, span) -> str | None:
        """Encode + store the animated clip, returning its media name — or None on any failure (a missing
        encoder, a bad encode, a store error), so the caller keeps the still."""
        r = self.r
        try:
            # nominal path; animated_screenshot swaps the suffix to the real format (.webp or .gif)
            clip = animated_screenshot(video, span, r._tmp / f"{base}.webp", r.mine_cfg.animated)
            if clip:
                return r.anki.store_media(clip.name, clip)
        except (OSError, subprocess.SubprocessError, AnkiError, json.JSONDecodeError):
            log.debug("animated screenshot failed — keeping the still", exc_info=True)
        return None

    def _capture_audio(self, base: str, video, span) -> tuple[str, Exception | None]:
        """The cue audio clip (→ ``_last_audio``). Returns (media_name, error-or-None)."""
        r = self.r
        try:
            if video and span:
                aud = r._tmp / f"{base}.m4a"
                clip_audio(video, span, aud, normalize=r.mine_cfg.normalize_audio)
                r._last_audio = aud
                return r.anki.store_media(f"{base}.m4a", aud), None
        except (OSError, subprocess.CalledProcessError, AnkiError, json.JSONDecodeError) as e:
            log.debug("audio capture failed", exc_info=True)
            return "", e
        return "", None

    def _warn_capture_failure(self, pic_err, audio_err) -> None:
        if pic_err and audio_err:
            self.r._toast("media capture failed (no image/audio on card)", "warn")
        elif pic_err:
            self.r._toast("screenshot failed — audio only", "warn")
        elif audio_err:
            self.r._toast("audio clip failed — image only", "warn")

    # --- mining -------------------------------------------------------------------------------
    def mine_current(self) -> None:
        r = self.r
        if not r.anki or not r.mine_cfg:
            return
        idx = self.mine_target()
        if idx is None:
            r._toast("no word to mine", "warn")
            return
        tok = r.tokens[idx]
        # Mining the hovered word defaults to its longest stacked phrase (数ある over 数), matching the
        # tooltip's top entry; the explicit per-entry ⊕ still mines any specific stacked entry.
        cards = (
            r.dict_set.cards_for(tok, extra_terms=r._hover_terms)
            if (r.dict_set and idx == r.hover and r._hover_terms)
            else []
        )
        self.mine_token(tok, card=cards[0] if cards else None)

    def mine_token(
        self, tok, *, force: bool = False, card=None, animated: bool | None = None
    ) -> None:
        """Mine a specific token into Anki — the hovered subtitle word, or an inner word discovered
        by scanning inside the tooltip (the nested popup's ⊕). ``force`` mines a second card for an
        expression already in the deck (the preview's explicit "add anyway" for a different scene).
        ``card`` mines an explicit CardData (a specific entry chosen from the panel's per-entry ⊕),
        bypassing the default entry pick — otherwise the dict-first ``_card_for`` derives it. ``animated``
        overrides ``[mine].animated_screenshot`` for this mine (the video-mine shortcut passes ``True``)."""
        r = self.r
        if not r.anki or not r.mine_cfg:
            return
        try:
            card = card if card is not None else self._card_for(tok)
            if not force:
                existing = dedupe(r.anki, r.mine_cfg, card.expression)
                if existing:
                    r._mark_mined(card.expression)  # already in the deck → ✓
                    from overlay.app import sidebar

                    sidebar.mark_active_mined(r)
                    r._dup_tok = tok  # remember for an explicit "add anyway"
                    r._preview_existing(existing[0], card, "exists")
                    return
            video = r._get("path")
            pic, audio = self.capture_media(
                f"saitenka_{int(time.time() * 1000)}", video, animated=animated
            )
            freq = self.frequency(tok)
            sentence_html = bold_word(r._sentence_html(), tok.surface)
            misc, tags = self.provenance(video), self.mine_tags(video)
            markers = self._markers_for(
                tok,
                card,
                sentence_html=sentence_html,
                pic=pic,
                audio=audio,
                video=video,
                misc=misc,
                tags=tags,
                freq=freq,
            )
            note = build_note(
                r.mine_cfg,
                card,
                sentence_html,
                pic,
                audio,
                misc,
                freq[0],
                freq[1],
                tags,
                allow_duplicate=force,
                markers=markers,
            )
            if not force and not r.anki.can_add(note):
                r._toast(f"can't add {card.expression}", "err")
                return
            r.anki.add_note(note)
            if r._session_recorder is not None:
                r._session_recorder.record_mined()
            r._mark_mined(card.expression)
            from overlay.app import sidebar

            sidebar.mark_active_mined(r)
            r._preview_mined(card, tok, video, "duplicate" if force else "mined")
        except AnkiError as e:
            r._toast(f"mine failed: {e}", "err")
        except Exception as e:  # never let a mine crash the loop
            log.exception("mine_token failed")
            r._toast(f"mine error: {e}", "err")

    def bulk_mine(self) -> None:
        """Mine every unknown content word in the current cue, sharing one screenshot + audio."""
        r = self.r
        if not r.anki or not r.mine_cfg or not r.tokens:
            r._toast("nothing to mine", "warn")
            return
        targets = _select_bulk_targets(r)
        if not targets:
            r._toast("no new words", "warn")
            return
        video = r._get("path")
        pic, audio = self.capture_media(f"saitenka_{int(time.time() * 1000)}", video)
        misc, sentence, tags = self.provenance(video), r._sentence_html(), self.mine_tags(video)
        mined = dup = 0
        try:
            for idx in targets:
                tok = r.tokens[idx]
                card = self._card_for(tok)
                if not card.glossary_html:  # no dict entry (name/particle) — skip
                    continue
                if dedupe(r.anki, r.mine_cfg, card.expression):
                    dup += 1
                    continue
                freq = self.frequency(tok)
                sentence_html = bold_word(sentence, tok.surface)
                markers = self._markers_for(
                    tok,
                    card,
                    sentence_html=sentence_html,
                    pic=pic,
                    audio=audio,
                    video=video,
                    misc=misc,
                    tags=tags,
                    freq=freq,
                )
                note = build_note(
                    r.mine_cfg,
                    card,
                    sentence_html,
                    pic,
                    audio,
                    misc,
                    freq[0],
                    freq[1],
                    tags,
                    markers=markers,
                )
                if r.anki.can_add(note):
                    r.anki.add_note(note)
                    r._mark_mined(card.expression)
                    mined += 1
                else:
                    dup += 1
            r._toast(f"mined {mined} · {dup} dup", "ok" if mined else "warn")
            if r._session_recorder is not None:
                r._session_recorder.record_mined(mined)
        except AnkiError as e:
            r._toast(f"bulk failed: {e}", "err")

    def seed_mined(self) -> None:
        """Pre-load already-mined expressions from the mining deck, so a word mined in a past
        session shows ✓ (not ⊕) from the first hover. Best-effort."""
        r = self.r
        if not r.anki or not r.mine_cfg:
            return
        from overlay.app.miner_ui import _strip_tags

        fieldname = r.mine_cfg.expression_field() or "Expression"
        try:
            ids = r.anki.find_notes(f'deck:"{r.mine_cfg.deck}"')
            for chunk in (ids[i : i + 500] for i in range(0, len(ids), 500)):
                for note in r.anki.notes_info(chunk):
                    val = _strip_tags(note.get("fields", {}).get(fieldname, {}).get("value", ""))
                    if val:
                        r._mined.add(val)
        except Exception:
            log.debug("seed_mined failed (AnkiConnect down?)", exc_info=True)

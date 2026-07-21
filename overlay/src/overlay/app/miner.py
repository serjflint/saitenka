"""The mining flow: one-key + bulk mining into Anki.

``Miner`` owns the mine→dedupe→capture→build-note pipeline and the provenance/tag helpers; the
Reader keeps the view side (previews, ⊕→✓ refresh, toasts) and delegates its public mining API
here. Composition: the Miner reaches collaborators (ipc, anki, tokens, toasts) through the Reader.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from overlay.app.anki import AnkiError, bold_word, build_note, dedupe
from overlay.app.lookup import card_for
from overlay.app.media import clip_audio, current_timespan, screenshot

log = logging.getLogger(__name__)

MAX_BULK = 12


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
    except Exception:
        return "", None


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

    # --- media capture --------------------------------------------------------------------------
    def capture_media(self, base: str, video) -> tuple[str, str]:
        """Screenshot the frame + clip the cue's audio, store both in Anki. Returns (pic, audio).

        Also stashes the local files (``_last_jpg``/``_last_audio``) on the Reader for the card
        preview + audio replay. Shows a warn toast if captures fail so the user knows."""
        r = self.r
        pic = audio = ""
        r._last_jpg = r._last_audio = None
        pic_err = audio_err = None
        try:
            jpg = r._tmp / f"{base}.jpg"
            screenshot(r.ipc, jpg)
            pic = r.anki.store_media(f"{base}.jpg", jpg)
            r._last_jpg = jpg
        except Exception as e:
            pic_err = e
        try:
            span = current_timespan(r.ipc)
            if video and span:
                aud = r._tmp / f"{base}.m4a"
                clip_audio(video, span, aud)
                audio = r.anki.store_media(f"{base}.m4a", aud)
                r._last_audio = aud
        except Exception as e:
            audio_err = e
        if pic_err and audio_err:
            r._toast("media capture failed (no image/audio on card)", "warn")
        elif pic_err:
            r._toast("screenshot failed — audio only", "warn")
        elif audio_err:
            r._toast("audio clip failed — image only", "warn")
        return pic, audio

    # --- mining -------------------------------------------------------------------------------
    def mine_current(self) -> None:
        r = self.r
        if not r.anki or not r.mine_cfg:
            return
        idx = self.mine_target()
        if idx is None:
            r._toast("no word to mine", "warn")
            return
        self.mine_token(r.tokens[idx])

    def mine_token(self, tok) -> None:
        """Mine a specific token into Anki — the hovered subtitle word, or an inner word discovered
        by scanning inside the tooltip (the nested popup's ⊕)."""
        r = self.r
        if not r.anki or not r.mine_cfg:
            return
        try:
            card = card_for(tok)
            existing = dedupe(r.anki, r.mine_cfg, card.expression)
            if existing:
                r._mark_mined(card.expression)  # already in the deck → ✓
                r._preview_existing(existing[0], card, "duplicate")
                return
            video = r._get("path")
            pic, audio = self.capture_media(f"saitenka_{int(time.time() * 1000)}", video)
            freq_html, freq_sort = self.frequency(tok)
            note = build_note(
                r.mine_cfg,
                card,
                bold_word(r._sentence_html(), tok.surface),
                pic,
                audio,
                self.provenance(video),
                freq_html,
                freq_sort,
                self.mine_tags(video),
            )
            if not r.anki.can_add(note):
                r._toast(f"can't add {card.expression}", "err")
                return
            r.anki.add_note(note)
            r._mark_mined(card.expression)
            r._preview_mined(card, tok, video)
        except AnkiError as e:
            r._toast(f"mine failed: {e}", "err")
        except Exception as e:  # never let a mine crash the loop
            r._toast(f"mine error: {e}", "err")

    def bulk_mine(self) -> None:
        """Mine every unknown content word in the current cue, sharing one screenshot + audio."""
        r = self.r
        from overlay.app.controller import SKIP_POS

        if not r.anki or not r.mine_cfg or not r.tokens:
            r._toast("nothing to mine", "warn")
            return
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
            if len(targets) >= MAX_BULK:
                break
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
                card = card_for(tok)
                if not card.glossary_html:  # no dict entry (name/particle) — skip
                    continue
                if dedupe(r.anki, r.mine_cfg, card.expression):
                    dup += 1
                    continue
                freq_html, freq_sort = self.frequency(tok)
                note = build_note(
                    r.mine_cfg,
                    card,
                    bold_word(sentence, tok.surface),
                    pic,
                    audio,
                    misc,
                    freq_html,
                    freq_sort,
                    tags,
                )
                if r.anki.can_add(note):
                    r.anki.add_note(note)
                    r._mark_mined(card.expression)
                    mined += 1
                else:
                    dup += 1
            r._toast(f"mined {mined} · {dup} dup", "ok" if mined else "warn")
        except AnkiError as e:
            r._toast(f"bulk failed: {e}", "err")

    def seed_mined(self) -> None:
        """Pre-load already-mined expressions from the mining deck, so a word mined in a past
        session shows ✓ (not ⊕) from the first hover. Best-effort."""
        r = self.r
        if not r.anki or not r.mine_cfg:
            return
        from overlay.app.controller import _strip_tags

        fieldname = r.mine_cfg.fields.get("expression", "Expression")
        try:
            ids = r.anki.find_notes(f'deck:"{r.mine_cfg.deck}"')
            for chunk in (ids[i : i + 500] for i in range(0, len(ids), 500)):
                for note in r.anki.notes_info(chunk):
                    val = _strip_tags(note.get("fields", {}).get(fieldname, {}).get("value", ""))
                    if val:
                        r._mined.add(val)
        except Exception:
            log.debug("seed_mined failed (AnkiConnect down?)", exc_info=True)

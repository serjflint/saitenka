"""
saitenka · backfill_deeplink_id — fill an EMPTY deep-link ID field on existing mined cards.

The live mine path fills the Kanji Study `ID` field (`kanjistudy://word?id={{ID}}`) at mine time, but
never revisits a card mined before an id source existed (#255). This closes that gap for a whole deck:
for every note whose ID field is empty, it resolves the JMdict `ent_seq` for that card's word with the
SAME resolution the live path uses — dict-first `entries.seq` from a JMdict-derived imported dict
(`persist_seq`), else jamdict — via `dictionary.card_for` / `lookup.card_for`. No heuristic is
duplicated here; the tool only enumerates, decides, and (under `--apply`) writes.

Never overwrites a non-empty field, and re-running changes nothing new (idempotent).

SAFETY — read → decide → write:
  * DRY-RUN (default): enumerates via AnkiConnect, reports the plan, writes NOTHING.
  * --apply: writes via AnkiConnect `updateNoteFields` (Anki must be OPEN).

Reuses overlay code, so run it in the project env (NOT `uv run <script>` isolation):
  uv run --extra full python tools/backfill_deeplink_id.py                 # dry-run, default deck+ID
  uv run --extra full python tools/backfill_deeplink_id.py --deck "My::Deck"
  uv run --extra full python tools/backfill_deeplink_id.py --apply         # writes (Anki open)
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from overlay.app.dictionary import DictionarySet

_TAG = re.compile(r"<[^>]+>")
_FURIGANA = re.compile(r"\[[^\]]*\]")  # Anki inline reading: 読[よ]む → 読む


def log(msg: str) -> None:
    print(f"[backfill-id] {msg}")


def _plain(s: str) -> str:
    """A note field's display text as a bare term: strip HTML tags and Anki inline-furigana brackets."""
    return _FURIGANA.sub("", _TAG.sub("", s or "")).replace(" ", "").replace("　", "").strip()


def _field_value(note: dict, name: str) -> str:
    """AnkiConnect `notesInfo` field value (`{fields: {Name: {value, order}}}`), '' when absent."""
    fields = note.get("fields") or {}
    return (fields.get(name) or {}).get("value", "") or ""


# --- pure decision core (unit-tested against constructed notes + a fake resolver, no Anki) -------


@dataclass
class BackfillPlan:
    """The decided writes for one deck backfill — a note_id -> new-field-value map plus the tallies a
    dry-run reports. `writes` is exactly what `--apply` would push; everything else is a count/sample."""

    writes: dict[int, str] = field(default_factory=dict)
    skipped_filled: int = 0  # field already non-empty → never overwritten
    unresolved: int = 0  # empty field but no ent_seq found for the word
    examples: list[tuple[str, str]] = field(default_factory=list)  # (word, seq), for the report


def plan_backfill(
    notes: list[dict],
    *,
    field_name: str,
    word_field: str,
    reading_field: str,
    resolve: Callable[[str, str], str | None],
    max_examples: int = 8,
) -> BackfillPlan:
    """Decide the per-note writes: for each note whose `field_name` is empty, resolve the word's
    `ent_seq` via `resolve(term, reading)` and record a write when one is found. A note with an
    already-filled field is left untouched (counted `skipped_filled`); an empty field whose word
    doesn't resolve is counted `unresolved`. Pure — the caller supplies notes + resolver."""
    plan = BackfillPlan()
    for note in notes:
        nid = note.get("noteId")
        if nid is None:
            continue
        if _plain(_field_value(note, field_name)):
            plan.skipped_filled += 1
            continue
        term = _plain(_field_value(note, word_field))
        reading = _plain(_field_value(note, reading_field))
        seq = resolve(term, reading) if term else None
        if seq:
            plan.writes[nid] = seq
            if len(plan.examples) < max_examples:
                plan.examples.append((term, seq))
        else:
            plan.unresolved += 1
    return plan


# --- overlay-backed resolver + Anki I/O ---------------------------------------------------------


def make_resolver(dict_set: DictionarySet | None) -> Callable[[str, str], str | None]:
    """`(term, reading) -> ent_seq | None`, reusing the live-mine resolution: the user's JMdict-derived
    dict's persisted `seq` first (`DictionarySet.card_for`), else jamdict (`lookup.card_for`). A
    non-JMdict dict's seq never surfaces as an id (the `_looks_like_jmdict` gate lives inside
    `card_for`), so it's never duplicated here."""
    from overlay.app import lookup
    from overlay.app.tokenize import Token

    def resolve(term: str, reading: str) -> str | None:
        if not term:
            return None
        tok = Token(surface=term, lemma=term, reading=reading, pos="", start=0, end=len(term))
        if dict_set is not None and (seq := dict_set.card_for(tok).idseq):
            return seq
        return lookup.card_for(tok).idseq or None

    return resolve


def _build_dict_set(cfg: dict) -> DictionarySet | None:
    """The configured definition dictionaries as a `DictionarySet` (id source when JMdict-derived +
    `persist_seq`), or None when none are imported — then resolution falls back to jamdict only."""
    from overlay.app.dictdb import DictionaryDb
    from overlay.app.dictionary import DictionarySet

    titles = list(cfg.get("dicts") or [])
    if not titles:
        return None
    db = DictionaryDb.open()
    d_rows, _missing = db.resolve(titles)
    return DictionarySet.from_rows(db, d_rows) if d_rows else None


def _apply_writes(anki, field_name: str, writes: dict[int, str]) -> None:
    """Push each note_id -> value via AnkiConnect `updateNoteFields`, in chunks through the `multi`
    action (per-note round-trips are slow at deck scale). Idempotent: only empty fields were planned."""
    items = list(writes.items())
    chunk = 200
    for i in range(0, len(items), chunk):
        batch = items[i : i + chunk]
        anki._call(
            "multi",
            actions=[
                {
                    "action": "updateNoteFields",
                    "params": {"note": {"id": nid, "fields": {field_name: seq}}},
                }
                for nid, seq in batch
            ],
        )
        log(f"  wrote {min(i + chunk, len(items))}/{len(items)}")


def _run(args: argparse.Namespace) -> int:
    from overlay.app.anki import ANKI_DOWN_ERRORS, Anki
    from overlay.app.config import load_config
    from overlay.app.reader_deps import _mine_config_from

    cfg = load_config()
    deck = args.deck or _mine_config_from(cfg.get("mine") or {}).deck
    query = f'deck:"{deck}"'
    if args.model:
        query += f' note:"{args.model}"'
    if args.query:
        query += f" {args.query}"

    anki = Anki()
    try:
        ids = anki.find_notes(query)
        notes = anki.notes_info(ids) if ids else []
    except ANKI_DOWN_ERRORS as e:
        log(f"AnkiConnect unreachable ({e}); open Anki (AnkiConnect on :8765) and retry.")
        return 1

    dict_set = _build_dict_set(cfg)
    resolve = make_resolver(dict_set)
    plan = plan_backfill(
        notes,
        field_name=args.field,
        word_field=args.word_field,
        reading_field=args.reading_field,
        resolve=resolve,
    )

    log("─" * 54)
    log(f"deck {deck!r} · field {args.field!r} · {len(notes)} notes")
    log(f"would fill: {len(plan.writes)}")
    log(f"skipped (already filled): {plan.skipped_filled}")
    log(f"unresolved (no ent_seq for the word): {plan.unresolved}")
    if plan.examples:
        log("examples (word → ID):")
        for word, seq in plan.examples:
            log(f"    {word} → {seq}")
    if dict_set is None:
        log(
            "no imported dictionary configured → resolving via jamdict only (needs the `jmdict` extra)"
        )

    if not args.apply:
        log("DRY-RUN — nothing written. Re-run with --apply (Anki OPEN) to execute.")
        return 0
    if not plan.writes:
        log("nothing to write.")
        return 0
    try:
        _apply_writes(anki, args.field, plan.writes)
    except ANKI_DOWN_ERRORS as e:
        log(f"write failed — AnkiConnect error ({e}).")
        return 1
    log(f"APPLY complete: filled {len(plan.writes)} ID(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill an empty deep-link ID field on mined cards.")
    ap.add_argument("--deck", default=None, help="target deck (default: [mine] deck from config)")
    ap.add_argument("--field", default="ID", help="note field to backfill (default: ID)")
    ap.add_argument(
        "--word-field", default="Expression", help="field holding the word (default: Expression)"
    )
    ap.add_argument(
        "--reading-field",
        default="ExpressionReading",
        help="field holding the reading, for homograph disambiguation (default: ExpressionReading)",
    )
    ap.add_argument("--model", default=None, help="restrict to one note type (optional)")
    ap.add_argument("--query", default=None, help="extra Anki search terms added to the deck query")
    ap.add_argument(
        "--apply", action="store_true", help="write via AnkiConnect (Anki open); default dry-run"
    )
    return _run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())

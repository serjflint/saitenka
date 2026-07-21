# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""
saitenka · anki_annotate_source — write card provenance (anime/deck origin) WITHOUT moving cards.

The non-disruptive half of the old refiler. For every card in your anime/game/mining decks it
records where it came from, so the origin survives any later deck move and is searchable now:
  * tag       saitenka_orig::<deck>            (primary; universal; reversible via removeTags)
  * MiscInfo  = <deck>                         (only when the note type has a MiscInfo field AND
                                                it's empty — never clobbers SubMiner's writes)

Deck moves live in the sibling script `anki_refile_by_state.py` (run that when you adopt SubMiner).

SAFETY — read → test → write:
  * DRY-RUN (default): reads a COPY of collection.anki2; reports scope; writes NOTHING.
  * --apply: AnkiConnect (:8765) — addTags + updateNoteFields (batched via `multi`). Idempotent.

Usage:
  uv run tools/anki_annotate_source.py                    # dry-run (anime/game/mining decks)
  uv run tools/anki_annotate_source.py --apply
  uv run tools/anki_annotate_source.py --deck-pattern 'アニメ|ゲーム' --exclude-deck Backlog
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import tempfile
import urllib.request
from collections import Counter
from pathlib import Path

MAC_DEFAULT = "~/Library/Application Support/Anki2/User 1/collection.anki2"
ANKICONNECT = "http://127.0.0.1:8765"


def log(m):
    print(f"[annotate] {m}")


def anki(action, **params):
    req = json.dumps({"action": action, "version": 6, "params": params}).encode()
    with urllib.request.urlopen(
        urllib.request.Request(ANKICONNECT, req, {"Content-Type": "application/json"}),
        timeout=60,
    ) as r:
        out = json.loads(r.read())
    if out.get("error"):
        raise RuntimeError(f"AnkiConnect {action}: {out['error']}")
    return out["result"]


def sanitize_tag(deck):
    return "saitenka_orig::" + deck.replace(" ", "_").replace("::", "-").replace(
        '"', ""
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default=MAC_DEFAULT)
    ap.add_argument(
        "--deck-pattern",
        default="アニメ|ゲーム|Mining|In progress",
        help="regex; annotate cards in decks matching this",
    )
    ap.add_argument(
        "--exclude-deck",
        action="append",
        default=None,
        help="skip decks under this path (repeatable; default: Backlog, Saitenka)",
    )
    ap.add_argument(
        "--apply", action="store_true", help="write via AnkiConnect (Anki open)"
    )
    args = ap.parse_args()

    src = Path(os.path.expanduser(args.collection))
    if not src.exists():
        raise SystemExit(f"collection not found: {src}")
    pat = re.compile(args.deck_pattern)
    excl = (
        args.exclude_deck if args.exclude_deck is not None else ["Backlog", "Saitenka"]
    )

    tmp = Path(tempfile.mkdtemp(prefix="saitenka_annot_"))
    dst = tmp / "c.anki2"
    shutil.copy2(src, dst)
    for suf in ("-wal", "-shm"):
        if Path(str(src) + suf).exists():
            shutil.copy2(str(src) + suf, str(dst) + suf)
    con = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    con.text_factory = str
    con.create_collation("unicase", lambda a, b: (a > b) - (a < b))
    cur = con.cursor()

    deck_name = {
        i: n.replace("\x1f", "::") for i, n in cur.execute("SELECT id,name FROM decks")
    }

    def excluded(n):
        return any(n == p or n.startswith(p + "::") for p in excl)

    misc_ord = {
        ntid: o
        for ntid, o, name in cur.execute("SELECT ntid,ord,name FROM fields")
        if name.lower() == "miscinfo"
    }

    # each note → the (first) matching deck it lives in
    note_deck = {}
    for nid, did in cur.execute("SELECT nid,did FROM cards"):
        d = deck_name.get(did, "")
        if pat.search(d) and not excluded(d):
            note_deck.setdefault(nid, d)

    # build plan (tag always; MiscInfo only when field present & empty)
    plan = []  # {nid, deck, tag, misc: bool}
    for nid, mid, flds in cur.execute("SELECT id,mid,flds FROM notes"):
        if nid not in note_deck:
            continue
        deck = note_deck[nid]
        o = misc_ord.get(mid)
        parts = flds.split("\x1f")
        misc_empty = o is not None and o < len(parts) and not parts[o].strip()
        plan.append(
            {"nid": nid, "deck": deck, "tag": sanitize_tag(deck), "misc": misc_empty}
        )
    con.close()

    by_deck = Counter(p["deck"] for p in plan)
    log("─" * 52)
    log(f"deck pattern: /{args.deck_pattern}/   excluding: {excl}")
    log(f"decks matched ({len(by_deck)}):")
    for d, n in by_deck.most_common(12):
        log(f"    {n:>5}  {d}")
    if len(by_deck) > 12:
        log(f"    … +{len(by_deck) - 12} more decks")
    log(
        f"TOTAL: {len(plan)} notes → tag; {sum(1 for p in plan if p['misc'])} → MiscInfo fill"
    )

    if not args.apply:
        log("DRY-RUN — nothing written. Re-run with --apply (Anki OPEN) to execute.")
        return

    try:
        anki("version")
    except Exception as e:
        raise SystemExit(f"AnkiConnect unreachable ({e}). Open Anki first.")
    # tags (grouped)
    by_tag = {}
    for p in plan:
        by_tag.setdefault(p["tag"], []).append(p["nid"])
    for tag, nids in by_tag.items():
        anki("addTags", notes=sorted(set(nids)), tags=tag)
    log(f"tagged {len({p['nid'] for p in plan})} notes")
    # MiscInfo (batched via multi)
    fills = [(p["nid"], p["deck"]) for p in plan if p["misc"]]
    CHUNK = 200
    for i in range(0, len(fills), CHUNK):
        anki(
            "multi",
            actions=[
                {
                    "action": "updateNoteFields",
                    "params": {"note": {"id": nid, "fields": {"MiscInfo": d}}},
                }
                for nid, d in fills[i : i + CHUNK]
            ],
        )
        log(f"  MiscInfo {min(i + CHUNK, len(fills))}/{len(fills)}")
    log(
        f"APPLY complete. Tagged provenance + filled {len(fills)} MiscInfo. No cards moved."
    )


if __name__ == "__main__":
    main()

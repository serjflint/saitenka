# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""
saitenka · anki_build_decks — build the N+1 study structure so you're never overwhelmed.

Creates the deck tree + presets (daily caps) and routes cards by FSRS state:

  Saitenka::Known       mature + retained    → SubMiner known-word source; normal review
  Saitenka::Active      young + learning     → your live review core
  Saitenka::Reactivate  forgotten (R<0.85)   → preset caps REVIEWS/day (default 20), freq-first
  Saitenka::Mining      new, NOT in Backlog  → preset caps NEW/day (default 15) — content i+1 intake
  Backlog::*            imports               → SUSPENDED (never in review; mining source only)

The caps are the anti-overwhelm valves: the 5.7k forgotten trickle at 20/day, new intake is
bounded so mining can't outrun review, and the 14.5k import flood is simply suspended.

Origin is stashed as tag `saitenka_orig::<deck>` before moving (reversible). Progress (FSRS
s/d, reps, revlog) survives deck moves untouched.

SAFETY — read → test → write:
  * DRY-RUN (default): reads a COPY; reports the routing + preset plan; writes NOTHING.
  * --apply: AnkiConnect (:8765) — createDeck, clone/save presets, changeDeck, suspend. Idempotent.

Usage:
  uv run tools/anki_build_decks.py                        # dry-run
  uv run tools/anki_build_decks.py --reactivate-cap 20 --mining-cap 15
  uv run tools/anki_build_decks.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import tempfile
import time
import urllib.request
from collections import Counter
from pathlib import Path

MAC_DEFAULT = "~/Library/Application Support/Anki2/User 1/collection.anki2"
ANKICONNECT = "http://127.0.0.1:8765"
FSRS_DEFAULT_DECAY = 0.1542


def log(m):
    print(f"[decks] {m}")


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


def retrievability(s, elapsed, decay):
    if not s or s <= 0 or elapsed < 0:
        return None
    factor = 0.9 ** (1.0 / decay) - 1.0
    return (1.0 + factor * elapsed / s) ** decay


def classify(ctype, ivl, r, mature_ivl, forgotten_r):
    if ctype == 0:
        return "new"
    if ctype in (1, 3):
        return "learning"
    if r is not None and r < forgotten_r:
        return "forgotten"
    return "known" if ivl >= mature_ivl else "young"


def sanitize_tag(deck):
    return "saitenka_orig::" + deck.replace(" ", "_").replace("::", "-").replace(
        '"', ""
    )


# state → (target deck suffix, is it a move?)
ROUTE = {
    "known": "Known",
    "young": "Active",
    "learning": "Active",
    "forgotten": "Reactivate",
    "new": "Mining",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default=MAC_DEFAULT)
    ap.add_argument("--root", default="Saitenka")
    ap.add_argument("--backlog", default="Backlog", help="deck prefix to suspend")
    ap.add_argument(
        "--reactivate-cap", type=int, default=20, help="forgotten reviews/day"
    )
    ap.add_argument("--mining-cap", type=int, default=15, help="new cards/day")
    ap.add_argument("--mature-ivl", type=int, default=21)
    ap.add_argument("--forgotten-r", type=float, default=0.85)
    ap.add_argument(
        "--route-new",
        action="store_true",
        help="also move content 'new' cards into ::Mining (default: leave them in place)",
    )
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    src = Path(os.path.expanduser(args.collection))
    if not src.exists():
        raise SystemExit(f"collection not found: {src}")
    tmp = Path(tempfile.mkdtemp(prefix="saitenka_decks_"))
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
    # per-card decay (per-card cards.data['decay'] else FSRS-6 default)
    last_rev = dict(cur.execute("SELECT cid, MAX(id) FROM revlog GROUP BY cid"))
    now = time.time() * 1000.0

    plan = []  # {cardId, noteId, state, orig, target, tag}
    suspend_ids = []  # Backlog card ids
    dist = Counter()
    for cid, nid, did, ctype, queue, ivl, data in cur.execute(
        "SELECT id,nid,did,type,queue,ivl,data FROM cards"
    ):
        deck = deck_name.get(did, "")
        if deck == args.backlog or deck.startswith(args.backlog + "::"):
            if queue != -1:
                suspend_ids.append(cid)
            continue
        if deck.startswith(args.root + "::"):
            continue  # already in the Saitenka tree
        s = dcard = None
        if data:
            try:
                j = json.loads(data)
                s = j.get("s")
                dcard = j.get("decay")
            except Exception:
                pass
        decay = -dcard if dcard else -FSRS_DEFAULT_DECAY
        elapsed = (now - last_rev[cid]) / 86_400_000.0 if cid in last_rev else None
        r = retrievability(s, elapsed, decay) if elapsed is not None else None
        st = classify(ctype, ivl or 0, r, args.mature_ivl, args.forgotten_r)
        dist[st] += 1
        if st == "new" and not args.route_new:
            continue  # leave content-new where it is unless asked to route it
        target = f"{args.root}::{ROUTE[st]}"
        if deck == target:
            continue
        plan.append(
            {
                "cardId": cid,
                "noteId": nid,
                "orig": deck,
                "target": target,
                "tag": sanitize_tag(deck),
            }
        )
    con.close()

    by_target = Counter(p["target"] for p in plan)
    log("─" * 54)
    log(
        f"whole-collection card states (outside {args.root}/{args.backlog}): {dict(dist)}"
    )
    log("routing plan:")
    for tgt, n in sorted(by_target.items()):
        log(f"    → {tgt:22} {n:>6} cards")
    log(f"suspend under {args.backlog}::  {len(suspend_ids)} cards")
    log("presets (daily caps):")
    log(f"    {args.root}::Reactivate  reviews/day = {args.reactivate_cap}")
    log(f"    {args.root}::Mining      new/day     = {args.mining_cap}")
    log(f"TOTAL: {len(plan)} cards to move, {len(suspend_ids)} to suspend")

    if not args.apply:
        log("DRY-RUN — nothing written. Re-run with --apply (Anki OPEN) to execute.")
        return

    try:
        anki("version")
    except Exception as e:
        raise SystemExit(f"AnkiConnect unreachable ({e}). Open Anki first.")

    # 1) decks + presets with caps
    presets = {
        f"{args.root}::Reactivate": (
            "Saitenka Reactivate",
            {"rev": args.reactivate_cap},
        ),
        f"{args.root}::Mining": ("Saitenka Mining", {"new": args.mining_cap}),
        f"{args.root}::Active": ("Saitenka Active", {}),
        f"{args.root}::Known": ("Saitenka Known", {}),
    }
    for deck, (pname, caps) in presets.items():
        anki("createDeck", deck=deck)
        cid = anki("cloneDeckConfigId", name=pname, cloneFrom=1)
        anki("setDeckConfigId", decks=[deck], configId=cid)
        cfg = anki("getDeckConfig", deck=deck)
        if "rev" in caps:
            cfg["rev"]["perDay"] = caps["rev"]
        if "new" in caps:
            cfg["new"]["perDay"] = caps["new"]
        anki("saveDeckConfig", config=cfg)
        log(f"deck+preset ready: {deck} ({pname})")

    # 2) tag origin (reversible), then move — batched via `multi`
    by_tag = {}
    for p in plan:
        by_tag.setdefault(p["tag"], []).append(p["noteId"])
    for tag, nids in by_tag.items():
        anki("addTags", notes=sorted(set(nids)), tags=tag)
    by_deck = {}
    for p in plan:
        by_deck.setdefault(p["target"], []).append(p["cardId"])
    for deck, cids in by_deck.items():
        for i in range(0, len(cids), 500):
            anki("changeDeck", cards=cids[i : i + 500], deck=deck)
        log(f"moved {len(cids)} → {deck}")

    # 3) suspend Backlog
    for i in range(0, len(suspend_ids), 1000):
        anki("suspend", cards=suspend_ids[i : i + 1000])
    log(f"suspended {len(suspend_ids)} Backlog cards")
    log(
        "APPLY complete. Point SubMiner knownWords.decks at "
        f"'{args.root}::Known'. Re-run any time to rebuild."
    )


if __name__ == "__main__":
    main()

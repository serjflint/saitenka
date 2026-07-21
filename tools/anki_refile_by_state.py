# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""
saitenka · anki_refile_by_state — FSRS-driven deck refiling for SubMiner's known-word.

SubMiner's "known word" = deck membership only (no FSRS). This gives it an FSRS-curated
deck by re-filing REAL cards into state decks (Saitenka::Known / ::Forgotten / …), while
**stashing each card's origin deck into a tag** so nothing is lost and it's fully
reversible. Decks are just folders; progress (FSRS s/d, reps, revlog) and tags survive a
deck move untouched.

SAFETY — read → test → write:
  * PLAN/DRY-RUN (default): reads a COPY of collection.anki2 (Anki may be closed), computes
    the plan, writes plan JSON, prints a summary. Writes NOTHING to Anki.
  * APPLY (--apply): requires Anki OPEN with AnkiConnect (:8765). Adds origin tags, then
    moves cards via changeDeck. Idempotent; safe to re-run ("rebuild as needed").

  Origin is preserved as tag  saitenka_orig::<deck>  → revert = move each card back to its
  origin tag and drop the saitenka_orig::/Saitenka::* — trivial, so the write is reversible.

Usage:
  uv run tools/anki_refile_by_state.py                       # dry-run, states=known,forgotten
  uv run tools/anki_refile_by_state.py --states known,forgotten,new,learning
  uv run tools/anki_refile_by_state.py --apply               # writes via AnkiConnect (Anki open)
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


def log(m: str) -> None:
    print(f"[refile] {m}")


def retrievability(s, elapsed_days, decay):
    if not s or s <= 0 or elapsed_days < 0:
        return None
    factor = 0.9 ** (1.0 / decay) - 1.0
    return (1.0 + factor * elapsed_days / s) ** decay


def classify(ctype, ivl, r, mature_ivl, forgotten_r):
    if ctype == 0:
        return "new"
    if ctype in (1, 3):
        return "learning"
    if r is not None and r < forgotten_r:
        return "forgotten"
    if ivl >= mature_ivl:
        return "known"
    return "young"


def anki(action, **params):
    req = json.dumps({"action": action, "version": 6, "params": params}).encode()
    with urllib.request.urlopen(
        urllib.request.Request(ANKICONNECT, req, {"Content-Type": "application/json"}),
        timeout=30,
    ) as r:
        out = json.loads(r.read())
    if out.get("error"):
        raise RuntimeError(f"AnkiConnect {action}: {out['error']}")
    return out["result"]


def sanitize_tag(deck: str) -> str:
    return "saitenka_orig::" + deck.replace(" ", "_").replace("::", "-").replace(
        '"', ""
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="FSRS-driven Anki deck refiling")
    ap.add_argument("--collection", default=MAC_DEFAULT)
    ap.add_argument(
        "--states",
        default="known,forgotten",
        help="comma list of states to refile (known,forgotten,young,learning,new)",
    )
    ap.add_argument("--root", default="Saitenka", help="target deck root")
    ap.add_argument("--mature-ivl", type=int, default=21)
    ap.add_argument("--forgotten-r", type=float, default=0.85)
    ap.add_argument("--decay", type=float, default=-0.5)
    ap.add_argument("--include-suspended", action="store_true")
    ap.add_argument(
        "--exclude-deck",
        action="append",
        default=None,
        help="never touch cards under this deck path (repeatable; default: Backlog)",
    )
    ap.add_argument("--plan-out", default="/tmp/saitenka-refile-plan.json")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="ACTUALLY write via AnkiConnect (Anki must be open). Default: dry-run.",
    )
    args = ap.parse_args()

    states = {s.strip() for s in args.states.split(",") if s.strip()}
    src = Path(os.path.expanduser(args.collection))
    if not src.exists():
        raise SystemExit(f"collection not found: {src}")

    # ── read (always from a copy) ────────────────────────────────────────────
    tmp = Path(tempfile.mkdtemp(prefix="saitenka_refile_"))
    dst = tmp / "collection.anki2"
    shutil.copy2(src, dst)
    for suf in ("-wal", "-shm"):
        if Path(str(src) + suf).exists():
            shutil.copy2(str(src) + suf, str(dst) + suf)
    con = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    con.text_factory = str
    con.create_collation("unicase", lambda a, b: (a > b) - (a < b))
    cur = con.cursor()

    # modern Anki stores deck hierarchy with \x1f as the separator, not "::"
    deck_name = {
        i: n.replace("\x1f", "::") for i, n in cur.execute("SELECT id,name FROM decks")
    }
    exclude_pats = args.exclude_deck if args.exclude_deck is not None else ["Backlog"]

    def _excluded(name):
        return any(name == p or name.startswith(p + "::") for p in exclude_pats)

    last_rev = dict(cur.execute("SELECT cid, MAX(id) FROM revlog GROUP BY cid"))
    now_ms = time.time() * 1000.0

    plan = []  # {cardId, noteId, state, origDeck, targetDeck, tag}
    dist = Counter()
    for cid, nid, did, ctype, queue, ivl, data in cur.execute(
        "SELECT id,nid,did,type,queue,ivl,data FROM cards"
    ):
        if queue == -1 and not args.include_suspended:
            continue
        if _excluded(deck_name.get(did, "")):
            continue
        s = None
        if data:
            try:
                s = json.loads(data).get("s")
            except Exception:
                s = None
        elapsed = (now_ms - last_rev[cid]) / 86_400_000.0 if cid in last_rev else None
        r = retrievability(s, elapsed, args.decay) if elapsed is not None else None
        st = classify(ctype, ivl or 0, r, args.mature_ivl, args.forgotten_r)
        dist[st] += 1
        if st not in states:
            continue
        orig = deck_name.get(did, str(did))
        target = f"{args.root}::{st.capitalize()}"
        if orig == target:  # already refiled → skip (idempotent)
            continue
        plan.append(
            {
                "cardId": cid,
                "noteId": nid,
                "state": st,
                "origDeck": orig,
                "targetDeck": target,
                "tag": sanitize_tag(orig),
            }
        )
    con.close()

    Path(args.plan_out).write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8"
    )

    # ── summary ──────────────────────────────────────────────────────────────
    by_target = Counter(p["targetDeck"] for p in plan)
    log("─" * 52)
    log(f"card-state distribution (whole collection): {dict(dist)}")
    log(f"selected states: {sorted(states)}")
    for tgt, n in sorted(by_target.items()):
        log(f"    → {tgt:24} {n:>6} cards")
    sample_decks = Counter(p["origDeck"] for p in plan).most_common(6)
    log(f"top origin decks affected: {[f'{d}×{n}' for d, n in sample_decks]}")
    log(
        f"TOTAL: {len(plan)} cards to move, "
        f"{len({p['noteId'] for p in plan})} notes to tag. Plan → {args.plan_out}"
    )

    if not args.apply:
        log(
            "DRY-RUN — nothing written. Review the plan; re-run with --apply (Anki OPEN) to execute."
        )
        return

    # ── write (AnkiConnect; Anki must be open) ───────────────────────────────
    try:
        ver = anki("version")
    except Exception as e:
        raise SystemExit(
            f"AnkiConnect unreachable ({e}). Open Anki (with AnkiConnect) first."
        )
    log(f"AnkiConnect v{ver} reachable — applying …")
    # 1) stash origin as tags (group notes by tag)
    by_tag: dict[str, list[int]] = {}
    for p in plan:
        by_tag.setdefault(p["tag"], []).append(p["noteId"])
    for tag, nids in by_tag.items():
        anki("addTags", notes=sorted(set(nids)), tags=tag)
    log(f"tagged origin on {len({p['noteId'] for p in plan})} notes")
    # 2) move cards to state decks
    by_deck: dict[str, list[int]] = {}
    for p in plan:
        by_deck.setdefault(p["targetDeck"], []).append(p["cardId"])
    for deck, cids in by_deck.items():
        anki("changeDeck", cards=sorted(set(cids)), deck=deck)
        log(f"moved {len(cids)} cards → {deck}")
    log(
        "APPLY complete. Point SubMiner knownWords.decks at "
        f"'{args.root}::Known'. Re-run any time to rebuild. "
        "(Provenance tags/MiscInfo: see anki_annotate_source.py)"
    )


if __name__ == "__main__":
    main()

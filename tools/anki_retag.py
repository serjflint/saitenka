# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""
saitenka · anki_retag — migrate the static mining tag saitenka-overlay → saitenka.

The default note tag stamped on mined cards was renamed with the distribution (saitenka-overlay →
saitenka). New cards already get `saitenka`; this one-shot retags the cards you mined BEFORE the
rename so past and future share one tag.

SAFETY — dry-run first:
  * DRY-RUN (default): counts the notes carrying the OLD tag via AnkiConnect; changes NOTHING.
  * --apply: AnkiConnect `replaceTagsInAllNotes(OLD, NEW)` — idempotent (re-running finds 0).

Anki must be running with AnkiConnect (:8765). Run against your live collection.

  uv run tools/anki_retag.py            # dry-run: how many notes would move
  uv run tools/anki_retag.py --apply    # do it
"""

import argparse
import json
import sys
import urllib.request

ANKICONNECT = "http://127.0.0.1:8765"
OLD_TAG = "saitenka-overlay"
NEW_TAG = "saitenka"


def log(m):
    print(f"[retag] {m}")


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


def main():
    ap = argparse.ArgumentParser(description=f"retag {OLD_TAG} → {NEW_TAG} on mined Anki notes")
    ap.add_argument("--apply", action="store_true", help="perform the retag (default: dry-run)")
    args = ap.parse_args()

    try:
        old = anki("findNotes", query=f"tag:{OLD_TAG}")
        already = anki("findNotes", query=f"tag:{NEW_TAG}")
    except (OSError, RuntimeError) as e:
        log(f"AnkiConnect unreachable ({e}). Open Anki (AnkiConnect on :8765) and retry.")
        return 1

    log(f"notes tagged {OLD_TAG!r}: {len(old)}   (already {NEW_TAG!r}: {len(already)})")
    if not old:
        log("nothing to migrate — already done or nothing mined under the old tag.")
        return 0
    if not args.apply:
        log(f"DRY-RUN — re-run with --apply to move {len(old)} notes to {NEW_TAG!r}.")
        return 0

    anki("replaceTagsInAllNotes", tag_to_replace=OLD_TAG, replace_with_tag=NEW_TAG)
    remaining = anki("findNotes", query=f"tag:{OLD_TAG}")
    log(f"done — {len(old)} notes retagged; {len(remaining)} still carry {OLD_TAG!r}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""
saitenka · anki_normalize_fields — add MiscInfo/ID fields to mining note types and
backfill the Kanji Study deep-link ID from JMdict sequence numbers.

Verified on this collection: Kanji Study's word `ID` == JMdict `ent_seq` (99% of 7,352 IDs
are valid JMdict sequences). So we can fill `ID` for any card by looking up (term, reading)
in JMdict, which makes `kanjistudy://word?id={{ID}}` deep-links work on Android from
every mining card — not just the ~1% of Animecards that had an ID.

What it does (to the mining note types — Animecards, Kanji Study Word Model v3, Lapis by
default; override with --note-type):
  1. add a `MiscInfo` field where missing        (Animecards, Kanji Study Word Model v3)
  2. add an `ID` field where missing              (Lapis, …)
  3. backfill empty `ID` from JMdict ent_seq via (term, reading) — homograph-safe
  4. (--add-deeplink) wrap the word field in <a href="kanjistudy://word?id={{ID}}"> on types
     whose template lacks it (Animecards already has it)

SAFETY — read → test → write:
  * DRY-RUN (default): reads a COPY of collection.anki2 (Anki may be closed); reports the plan;
    writes NOTHING.
  * --apply: requires Anki OPEN (AnkiConnect :8765). modelFieldAdd + updateNoteFields
    (+ updateModelTemplates if --add-deeplink). Idempotent; safe to re-run.

Usage:
  uv run tools/anki_normalize_fields.py                       # dry-run
  uv run tools/anki_normalize_fields.py --jmdict ~/Downloads/JMdict_english_with_examples.zip
  uv run tools/anki_normalize_fields.py --apply               # writes (Anki open)
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import os
import re
import shutil
import sqlite3
import tempfile
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

MAC_DEFAULT = "~/Library/Application Support/Anki2/User 1/collection.anki2"
ANKICONNECT = "http://127.0.0.1:8765"
MINING_DECK_RE = re.compile(r"アニメ|ゲーム|In progress|Mining|日学")
TERM_FIELDS = [
    "expression",
    "word",
    "characters",
    "entry",
    "vocab",
    "kanji",
    "単語",
    "japanese",
]
READING_FIELDS = [
    "expressionreading",
    "reading",
    "entryfurigana",
    "kana",
    "hiragana",
    "yomikata",
    "furigana",
    "expressionfurigana",
]
KANA_RUN = re.compile(r"[ぁ-ゟァ-ヿーｦ-ﾟ・〜]+")


def log(m):
    print(f"[normalize] {m}")


def strip_markup(s):
    return re.sub(r"<[^>]+>", "", html.unescape(s or "")).strip()


def term_base(s):
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = re.sub(r"[［（【〈《][^］）】〉》]*[］）】〉》]", "", s)
    return s.replace(" ", "").replace("　", "").strip()


def to_reading(s):
    s = re.sub(r"[^\[\]\s]*\[([^\]]*)\]", r"\1", s).replace(" ", "").replace("　", "")
    m = KANA_RUN.match(s)
    return m.group(0) if m else ""


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


def load_jmdict(zip_path):
    """(term, reading) -> ent_seq  and  term -> {seqs}  from a JMdict Yomitan zip."""
    pair, byterm = {}, {}
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if not re.search(r"term_bank_\d+\.json$", n):
                continue
            for e in json.loads(z.read(n)):
                if len(e) < 7 or not isinstance(e[6], int):
                    continue
                term, reading, seq = e[0], e[1], e[6]
                pair.setdefault((term, reading), seq)
                if not reading:  # kana headword: reading==term implicitly
                    pair.setdefault((term, term), seq)
                byterm.setdefault(term, set()).add(seq)
    return pair, byterm


def seq_for(term, reading, pair, byterm):
    if (term, reading) in pair:
        return pair[(term, reading)]
    if (term, term) in pair:  # kana word
        return pair[(term, term)]
    s = byterm.get(term)
    return next(iter(s)) if s and len(s) == 1 else None  # term-only only if unambiguous


# ── card templates (protobuf: q_format=1, a_format=2) + deep-link wrap ───────
def _varint(b, i):
    sh = v = 0
    while True:
        c = b[i]
        i += 1
        v |= (c & 0x7F) << sh
        if not c & 0x80:
            return v, i
        sh += 7


def _tmpl_fields(cfg):
    out, i, n = {}, 0, len(cfg)
    try:
        while i < n:
            tag, i = _varint(cfg, i)
            fn, wt = tag >> 3, tag & 7
            if wt == 2:
                ln, i = _varint(cfg, i)
                out.setdefault(fn, cfg[i : i + ln])
                i += ln
            elif wt == 0:
                _, i = _varint(cfg, i)
            elif wt == 1:
                i += 8
            elif wt == 5:
                i += 4
            else:
                break
    except IndexError:
        pass
    return out


def read_templates(cur, mid):
    out = []
    for name, cfg in cur.execute(
        "SELECT name,config FROM templates WHERE ntid=? ORDER BY ord", (mid,)
    ):
        f = _tmpl_fields(cfg)
        out.append(
            (
                name,
                f.get(1, b"").decode("utf-8", "replace"),
                f.get(2, b"").decode("utf-8", "replace"),
            )
        )
    return out


DEEPLINK_OPEN = '<a href="kanjistudy://word?id={{ID}}">'
# wrap the vocab-display element (Lapis `front-vocab`, or a bare word field) in the deep-link,
# mirroring Animecards' `<a href="kanjistudy://word?id={{ID}}"><div ...>{{Word}}</div></a>`
VOCAB_DIV = re.compile(
    r'(<div[^>]*class="front-vocab"[^>]*>\s*\{\{Expression\}\}\s*</div>)'
)


def wrap_deeplink(tpl):
    if "kanjistudy://" in tpl:  # already has it (e.g. Animecards)
        return tpl, False
    new = VOCAB_DIV.sub(DEEPLINK_OPEN + r"\1</a>", tpl)
    return new, new != tpl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default=MAC_DEFAULT)
    ap.add_argument(
        "--jmdict", default=None, help="JMdict Yomitan zip (auto-detected)"
    )
    ap.add_argument(
        "--note-type",
        action="append",
        default=None,
        help="target note type (repeatable; default: mining types auto-detected)",
    )
    ap.add_argument(
        "--add-deeplink",
        action="store_true",
        help="also add kanjistudy:// anchor to templates that lack it",
    )
    ap.add_argument(
        "--apply", action="store_true", help="write via AnkiConnect (Anki open)"
    )
    args = ap.parse_args()

    src = Path(os.path.expanduser(args.collection))
    if not src.exists():
        raise SystemExit(f"collection not found: {src}")
    jm = args.jmdict or next(
        iter(
            glob.glob(
                os.path.expanduser("~/Downloads/JMdict_english_with_examples.zip")
            )
            or glob.glob(os.path.expanduser("~/Downloads/*itendex*.zip"))
        ),
        None,
    )
    if not jm or not os.path.exists(os.path.expanduser(jm)):
        raise SystemExit("no JMdict zip found; pass --jmdict PATH")
    jm = os.path.expanduser(jm)

    tmp = Path(tempfile.mkdtemp(prefix="saitenka_norm_"))
    dst = tmp / "c.anki2"
    shutil.copy2(src, dst)
    for suf in ("-wal", "-shm"):
        if Path(str(src) + suf).exists():
            shutil.copy2(str(src) + suf, str(dst) + suf)
    con = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    con.text_factory = str
    con.create_collation("unicase", lambda a, b: (a > b) - (a < b))
    cur = con.cursor()

    ntname = {i: n for i, n in cur.execute("SELECT id,name FROM notetypes")}
    flds = {}
    for ntid, ord_, name in cur.execute("SELECT ntid,ord,name FROM fields"):
        flds.setdefault(ntid, {})[ord_] = name
    dn = {
        i: n.replace("\x1f", "::") for i, n in cur.execute("SELECT id,name FROM decks")
    }

    # target note types: those used in mining/anime/game decks, unless overridden
    if args.note_type:
        targets = [i for i, n in ntname.items() if n in args.note_type]
    else:
        mining = [d for d, n in dn.items() if MINING_DECK_RE.search(n)]
        used = Counter()
        for did in mining:
            for (mid,) in cur.execute(
                "SELECT n.mid FROM cards c JOIN notes n ON n.id=c.nid WHERE c.did=?",
                (did,),
            ):
                used[mid] += 1
        targets = [mid for mid, _ in used.most_common()]

    log(f"loading JMdict from {os.path.basename(jm)} …")
    pair, byterm = load_jmdict(jm)
    log(f"JMdict: {len(pair)} (term,reading) keys, {len(byterm)} terms")

    def fmap(mid):
        return {n.lower(): o for o, n in flds[mid].items()}

    add_fields = []  # (modelName, fieldName)
    id_fill = []  # (noteId, seq)
    stats = {}
    for mid in targets:
        name = ntname[mid]
        fm = fmap(mid)
        has_misc, has_id = "miscinfo" in fm, "id" in fm
        if not has_misc:
            add_fields.append((name, "MiscInfo"))
        if not has_id:
            add_fields.append((name, "ID"))
        term_o = next((fm[c] for c in TERM_FIELDS if c in fm), None)
        read_o = next((fm[c] for c in READING_FIELDS if c in fm), None)
        id_o = fm.get("id")
        if term_o is None:
            stats[name] = "no term field — skip ID fill"
            continue
        matched = empty = 0
        for nid, fl in cur.execute("SELECT id,flds FROM notes WHERE mid=?", (mid,)):
            p = fl.split("\x1f")
            cur_id = p[id_o].strip() if (id_o is not None and id_o < len(p)) else ""
            if cur_id:
                continue  # never overwrite an existing ID
            empty += 1
            term = term_base(strip_markup(p[term_o])) if term_o < len(p) else ""
            reading = (
                to_reading(strip_markup(p[read_o]))
                if (read_o is not None and read_o < len(p))
                else ""
            )
            seq = seq_for(term, reading, pair, byterm)
            if seq:
                id_fill.append((nid, str(seq)))
                matched += 1
        stats[name] = (
            f"ID empty {empty}, JMdict-matched {matched} ({100 * matched // max(empty, 1)}%)"
        )

    deeplink_plan = []  # (model, [template names], preview snippet)
    if args.add_deeplink:
        for mid in targets:
            changed, preview = [], ""
            for tname, front, _back in read_templates(cur, mid):
                _new, ch = wrap_deeplink(front)
                if ch:
                    changed.append(tname)
                    m = VOCAB_DIV.search(front)
                    if m and not preview:
                        preview = DEEPLINK_OPEN + m.group(1) + "</a>"
            if changed:
                deeplink_plan.append((ntname[mid], changed, preview))
    con.close()

    # ── report ───────────────────────────────────────────────────────────────
    log("─" * 54)
    log(f"target note types: {[ntname[m] for m in targets]}")
    log("fields to ADD:")
    for m, f in add_fields:
        log(f"    + {f:9} → {m}")
    log("ID backfill (from JMdict ent_seq):")
    for m in targets:
        if ntname[m] in stats:
            log(f"    {ntname[m]:32} {stats[ntname[m]]}")
    if args.add_deeplink:
        log("deep-link template wrap (kanjistudy://word?id={{ID}}):")
        for model, changed, preview in deeplink_plan:
            log(f"    {model}: {changed}")
            if preview:
                log(f"        → {preview}")
        if not deeplink_plan:
            log("    (nothing to wrap — templates already deep-linked or no vocab div)")
    log(
        f"TOTAL: {len(add_fields)} field-adds, {len(id_fill)} IDs to backfill"
        + (
            f", {sum(len(c) for _, c, _ in deeplink_plan)} template(s) to deep-link"
            if args.add_deeplink
            else ""
        )
    )
    Path("/tmp/saitenka-normalize-plan.json").write_text(
        json.dumps({"add_fields": add_fields, "id_fill": id_fill}, ensure_ascii=False),
        "utf-8",
    )

    if not args.apply:
        log("DRY-RUN — nothing written. Re-run with --apply (Anki OPEN) to execute.")
        return

    # ── apply (Anki open) ──────────────────────────────────────────────────────
    try:
        anki("version")
    except Exception as e:
        raise SystemExit(f"AnkiConnect unreachable ({e}). Open Anki first.")
    for model, field in add_fields:
        existing = anki("modelFieldNames", modelName=model)
        if field in existing:
            continue
        anki("modelFieldAdd", modelName=model, fieldName=field, index=len(existing))
        log(f"added field {field} → {model}")
    # batch ID fills via AnkiConnect `multi` (per-note updateNoteFields is too slow at scale)
    CHUNK = 200
    for i in range(0, len(id_fill), CHUNK):
        batch = id_fill[i : i + CHUNK]
        anki(
            "multi",
            actions=[
                {
                    "action": "updateNoteFields",
                    "params": {"note": {"id": nid, "fields": {"ID": seq}}},
                }
                for nid, seq in batch
            ],
        )
        log(f"  filled {min(i + CHUNK, len(id_fill))}/{len(id_fill)} IDs")
    log(f"filled {len(id_fill)} IDs")
    if args.add_deeplink:
        for model, _changed, _prev in deeplink_plan:
            tmpls = anki("modelTemplates", modelName=model)  # {tname: {Front, Back}}
            upd = {}
            for tname, sides in tmpls.items():
                nf, cf = wrap_deeplink(sides.get("Front", ""))
                nb, cb = wrap_deeplink(sides.get("Back", ""))
                if cf or cb:
                    upd[tname] = {"Front": nf, "Back": nb}
            if upd:
                anki("updateModelTemplates", model={"name": model, "templates": upd})
                log(f"deep-linked {model}: {list(upd)}")
    log(
        "APPLY complete. MiscInfo now exists on the anime/game types → refiler can dual-fill it."
    )


if __name__ == "__main__":
    main()

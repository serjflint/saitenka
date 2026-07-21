# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""
saitenka · anki_rank_dicts — Anki + FSRS → ranking artifacts (READ-ONLY; never writes to Anki).

Reads a COPY of collection.anki2 and emits:

  1. KNOWN-NESS Yomitan freq dict  (saitenka-knowness.zip)
     Studied words; weak/forgotten sort to the TOP, rock-solid mature to the BOTTOM.
     displayValue shows the FSRS state (known·s210·R98 / forgot·R62). Feeds SubMiner's
     frequency layer + a personal knowledge badge everywhere Yomitan runs.

  2. N+1 REACTIVATE ranking  (saitenka-reactivate.{zip,json,nids.txt})
     FORGOTTEN words (you learned them, R<threshold now), ranked by frequency — the highest-
     ROI (re)learning queue for a re-igniter.

  3. N+1 LEARN-NEW ranking  (saitenka-learn-new.{zip,json,nids.txt})
     NEW / never-studied words, ranked by frequency — what to learn first for comprehension.

FSRS is exact: retrievability matches py-fsrs card.py:232 verbatim; decay is read PER-CARD
from cards.data['decay'] when present, else PER-PRESET from fsrs_params_6[20] (parsed from
the deck-config protobuf), else the FSRS-6 default (0.1542). Cross-checked vs a parameter-free
0.9^(t/ivl) model: 98.8% label agreement on this collection.

Frequency is HARMONIC-BLENDED (like Yomitan's frequency-harmonic-rank) across a directory of Yomitan
freq zips — a mix of general + domain-specific frequency lists works well. The zips are NOT shipped
with the repo; point ``--freq-dir`` (or ``$SAITENKA_FREQ_DIR``) at your own, or pass ``--freq-dict``
per file. Default dir is ``tools/freq/``.

Usage:
  uv run tools/anki_rank_dicts.py --freq-dir ~/yomitan-dicts --out-dir ./out   # blend a dir of zips
  uv run tools/anki_rank_dicts.py --freq-dict A.zip --freq-dict B.zip --out-dir ./out
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sqlite3
import struct
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path

FSRS_DEFAULT_DECAY = 0.1542  # FSRS-6 default w20 (py-fsrs scheduler.py)
MAC_DEFAULT = "~/Library/Application Support/Anki2/User 1/collection.anki2"
_env_freq = os.environ.get("SAITENKA_FREQ_DIR")
# Default dir of Yomitan freq zips to blend. Overridable via $SAITENKA_FREQ_DIR or --freq-dir; the
# zips are user-supplied (not shipped in-repo), so tools/freq/ may be empty on a fresh checkout.
FREQ_DIR = Path(_env_freq) if _env_freq else Path(__file__).resolve().parent / "freq"
JP = re.compile(r"[぀-ヿ㐀-鿿豈-﫿]")
KANA_RUN = re.compile(r"[ぁ-ゟァ-ヿーｦ-ﾟ・〜]+")
SENTENCE_MARKS = "。、！？…「」『』（）\n\t"
TERM_FIELDS = [
    "expression",
    "word",
    "vocab",
    "vocabkanji",
    "kanji",
    "単語",
    "japanese",
    "target",
    "term",
    "vocabulary",
    "characters",
    "front",
]
READING_FIELDS = [
    "expressionreading",
    "reading",
    "kana",
    "hiragana",
    "yomikata",
    "yomi",
    "読み",
    "expressionfurigana",
    "vocabfurigana",
    "furigana",
]


def log(m: str) -> None:
    print(f"[rank] {m}")


# ── minimal protobuf reader (for deck-config FSRS params + deck→config_id) ────
def _varint(b, i):
    shift = val = 0
    while True:
        c = b[i]
        i += 1
        val |= (c & 0x7F) << shift
        if not c & 0x80:
            return val, i
        shift += 7


def _walk(b):
    """Return {field_number: [raw_bytes_or_int, ...]} for one protobuf message."""
    out, i, n = {}, 0, len(b)
    try:
        while i < n:
            tag, i = _varint(b, i)
            fn, wt = tag >> 3, tag & 7
            if wt == 0:
                v, i = _varint(b, i)
            elif wt == 1:
                v, i = b[i : i + 8], i + 8
            elif wt == 2:
                ln, i = _varint(b, i)
                v, i = b[i : i + ln], i + ln
            elif wt == 5:
                v, i = b[i : i + 4], i + 4
            else:
                break
            out.setdefault(fn, []).append(v)
    except (IndexError, KeyError):
        pass
    return out


def config_fsrs_params(cfg_blob):
    """deck_config.config → fsrs_params_6 (field 6) or _5 (field 5) as float list."""
    f = _walk(cfg_blob)
    for field in (6, 5, 3):  # v6, v5, v4
        if field in f and isinstance(f[field][-1], (bytes, bytearray)):
            raw = f[field][-1]
            if len(raw) % 4 == 0 and raw:
                return [
                    struct.unpack_from("<f", raw, k)[0] for k in range(0, len(raw), 4)
                ]
    return []


def deck_config_id(kind_blob):
    """decks.kind (Normal msg) → config_id (field 1)."""
    f = _walk(kind_blob)
    return f[1][-1] if 1 in f and isinstance(f[1][-1], int) else None


# ── text cleaning ─────────────────────────────────────────────────────────
def strip_markup(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", "", s)
    for z in ("​", "﻿", "‎", "‏"):
        s = s.replace(z, "")
    return s.strip()


def term_base(s):
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = re.sub(r"[［（【〈《][^］）】〉》]*[］）】〉》]", "", s)
    return s.replace(" ", "").replace("　", "").strip()


def to_reading(s):
    s = re.sub(r"[^\[\]\s]*\[([^\]]*)\]", r"\1", s).replace(" ", "").replace("　", "")
    m = KANA_RUN.match(s)
    return m.group(0) if m else ""


def wordlike(t):
    return (
        bool(t)
        and bool(JP.search(t))
        and len(t) <= 12
        and not any(c in t for c in SENTENCE_MARKS)
        and "  " not in t
    )


# ── FSRS (identical to py-fsrs card.py:232) ─────────────────────────────────
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


def knowledge_score(c):
    s = c["s"] or float(c["ivl"] or 0)
    r = c["r"] if c["r"] is not None else 1.0
    return {2: s * r, 3: 0.5 * s * r, 1: 0.5}.get(c["type"], 0.0)


# ── frequency dicts (harmonic blend) ─────────────────────────────────────────
def load_freq_dict(zip_path):
    ranks = {}

    def rank_of(d):
        if isinstance(d, (int, float)):
            return int(d)
        if isinstance(d, str):
            m = re.search(r"\d+", d)
            return int(m.group()) if m else None
        if isinstance(d, dict):
            if "frequency" in d:
                return rank_of(d["frequency"])
            for k in ("value", "rank"):
                if k in d:
                    return rank_of(d[k])
        return None

    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not re.search(r"term_meta_bank_\d+\.json$", name):
                continue
            for e in json.loads(z.read(name)):
                if len(e) < 3 or e[1] != "freq":
                    continue
                r = rank_of(e[2])
                if r is not None and (e[0] not in ranks or r < ranks[e[0]]):
                    ranks[e[0]] = r
    return ranks


def harmonic(term, freqs):
    rs = [f[term] for f in freqs if term in f]
    return len(rs) / sum(1.0 / r for r in rs) if rs else None


# ── Yomitan output ───────────────────────────────────────────────────────────
def write_yomitan(out, title, desc, rows):
    idx = {
        "title": title,
        "format": 3,
        "revision": time.strftime("saitenka-%Y%m%d-%H%M%S"),
        "sequenced": False,
        "author": "saitenka",
        "frequencyMode": "rank-based",
        "description": desc,
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.json", json.dumps(idx, ensure_ascii=False))
        for n in range(0, max(1, len(rows)), 10000):
            chunk = rows[n : n + 10000]
            bank = [
                [t, "freq", {"value": int(v), "displayValue": d}] for t, v, d in chunk
            ]
            z.writestr(
                f"term_meta_bank_{n // 10000 + 1}.json",
                json.dumps(bank, ensure_ascii=False),
            )


def emit_ranking(out_dir, stem, title, desc, ranked):
    """ranked: list of dicts {term,reading,state,freq,stability,r,nids} in priority order."""
    rows = [
        (w["term"], i + 1, f"{w['state']}·f{w['freq'] if w['freq'] else '-'}")
        for i, w in enumerate(ranked)
    ]
    write_yomitan(out_dir / f"{stem}.zip", title, desc, rows)
    js = [
        {
            "rank": i + 1,
            **{
                k: w[k]
                for k in ("term", "reading", "state", "freq", "stability", "r", "nids")
            },
        }
        for i, w in enumerate(ranked)
    ]
    (out_dir / f"{stem}.json").write_text(
        json.dumps(js, ensure_ascii=False, indent=1), "utf-8"
    )
    top = sorted({nid for w in ranked[:2000] for nid in w["nids"]})
    (out_dir / f"{stem}.nids.txt").write_text(
        "nid:" + ",".join(map(str, top)) + "\n", "utf-8"
    )
    return len(rows), len(top)


# ── main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default=MAC_DEFAULT)
    ap.add_argument("--out-dir", default="./saitenka-dicts")
    ap.add_argument(
        "--freq-dir",
        type=Path,
        default=FREQ_DIR,
        help="dir of Yomitan freq zips to blend when no --freq-dict is given "
        "(default: $SAITENKA_FREQ_DIR, else tools/freq/)",
    )
    ap.add_argument(
        "--freq-dict",
        action="append",
        default=[],
        help="Yomitan freq zip (repeatable; harmonic-blended; default = every *.zip in --freq-dir)",
    )
    ap.add_argument("--mature-ivl", type=int, default=21)
    ap.add_argument("--forgotten-r", type=float, default=0.85)
    ap.add_argument(
        "--decay-override",
        type=float,
        default=None,
        help="force decay magnitude (else per-card/per-preset)",
    )
    ap.add_argument("--include-suspended", action="store_true")
    ap.add_argument("--readings", action="store_true")
    ap.add_argument(
        "--exclude-deck",
        action="append",
        default=None,
        help="skip cards under this deck path (repeatable; default: Backlog)",
    )
    args = ap.parse_args()

    src = Path(os.path.expanduser(args.collection))
    if not src.exists():
        raise SystemExit(f"collection not found: {src}")
    out_dir = Path(os.path.expanduser(args.out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="saitenka_rank_"))
    dst = tmp / "c.anki2"
    shutil.copy2(src, dst)
    for suf in ("-wal", "-shm"):
        if Path(str(src) + suf).exists():
            shutil.copy2(str(src) + suf, str(dst) + suf)
    con = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    con.text_factory = str
    con.create_collation("unicase", lambda a, b: (a > b) - (a < b))
    cur = con.cursor()

    # decay per deck (from presets), used when the card lacks a stored decay
    cfg_decay = {}
    for cid, _n, blob in cur.execute("SELECT id,name,config FROM deck_config"):
        p = config_fsrs_params(blob)
        cfg_decay[cid] = (
            -p[20] if len(p) >= 21 else (-0.5 if len(p) >= 17 else -FSRS_DEFAULT_DECAY)
        )
    exclude_pats = args.exclude_deck if args.exclude_deck is not None else ["Backlog"]
    deckname, deck_decay = {}, {}
    for did, dname, kind in cur.execute("SELECT id,name,kind FROM decks"):
        dname = dname.replace("\x1f", "::")
        deckname[did] = dname
        cfgid = deck_config_id(kind) if kind else None
        deck_decay[did] = cfg_decay.get(cfgid, -FSRS_DEFAULT_DECAY)
    excluded_dids = {
        d
        for d, n in deckname.items()
        if any(n == p or n.startswith(p + "::") for p in exclude_pats)
    }
    log(
        f"deck presets: {len(cfg_decay)} | decays seen: "
        f"{sorted({round(d, 3) for d in cfg_decay.values()})}"
    )
    log(
        f"excluding {len(excluded_dids)} decks under {exclude_pats}: "
        f"{sorted({deckname[d].split('::')[0] for d in excluded_dids})}"
    )

    fields = {}
    for ntid, ord_, name in cur.execute("SELECT ntid,ord,name FROM fields"):
        fields.setdefault(ntid, {})[name.lower()] = ord_

    def pick(cands, fmap):
        return next((fmap[c] for c in cands if c in fmap), None)

    term_ord, read_ord = {}, {}
    for ntid, fmap in fields.items():
        t = pick(TERM_FIELDS, fmap)
        if t is not None:
            term_ord[ntid] = t
            read_ord[ntid] = pick(READING_FIELDS, fmap)

    last_rev = dict(cur.execute("SELECT cid, MAX(id) FROM revlog GROUP BY cid"))
    now = time.time() * 1000.0
    ovr = -args.decay_override if args.decay_override else None

    note_cards, decay_src = {}, Counter()
    for cid, nid, did, ctype, queue, ivl, data in cur.execute(
        "SELECT id,nid,did,type,queue,ivl,data FROM cards"
    ):
        if did in excluded_dids:
            continue
        s = dcard = None
        if data:
            try:
                j = json.loads(data)
                s = j.get("s")
                dcard = j.get("decay")
            except Exception:
                pass
        decay = ovr or (-dcard if dcard else deck_decay.get(did, -FSRS_DEFAULT_DECAY))
        decay_src["card" if dcard else "preset"] += 1
        elapsed = (now - last_rev[cid]) / 86_400_000.0 if cid in last_rev else None
        r = retrievability(s, elapsed, decay) if elapsed is not None else None
        note_cards.setdefault(nid, []).append(
            {"type": ctype, "ivl": ivl or 0, "s": s, "r": r, "suspended": queue == -1}
        )

    words = {}
    for nid, mid, flds in cur.execute("SELECT id,mid,flds FROM notes"):
        if mid not in term_ord:
            continue
        parts = flds.split("\x1f")
        clean = strip_markup(parts[term_ord[mid]] if term_ord[mid] < len(parts) else "")
        term = term_base(clean)
        if not wordlike(term):
            continue
        reading = ""
        ro = read_ord.get(mid)
        if ro is not None and ro < len(parts):
            reading = to_reading(strip_markup(parts[ro]))
        reading = reading or to_reading(clean)
        if reading == term or not args.readings:
            reading = ""
        cards = [
            c
            for c in note_cards.get(nid, [])
            if args.include_suspended or not c["suspended"]
        ]
        if not cards:
            continue
        best = max(cards, key=knowledge_score)
        st = classify(
            best["type"], best["ivl"], best["r"], args.mature_ivl, args.forgotten_r
        )
        k = knowledge_score(best)
        w = words.get(term)
        if w is None or k > w["k"]:
            words[term] = {
                "term": term,
                "reading": reading,
                "state": st,
                "k": k,
                "stability": best["s"],
                "r": round(best["r"], 3) if best["r"] else None,
                "nids": [nid],
            }
        else:
            w["nids"].append(nid)
    con.close()

    # frequency (harmonic blend). Default to every *.zip in --freq-dir when no --freq-dict is passed.
    freq_paths = [Path(os.path.expanduser(fp)) for fp in args.freq_dict]
    if not freq_paths:
        freq_paths = sorted(args.freq_dir.glob("*.zip"))
        if freq_paths:
            log(f"no --freq-dict → blending {args.freq_dir}/: {[p.name for p in freq_paths]}")
        else:
            log(
                f"no --freq-dict and no *.zip in {args.freq_dir} — pass --freq-dict, set --freq-dir, "
                "or export $SAITENKA_FREQ_DIR to point at your Yomitan freq zips"
            )
    freqs = []
    for p in freq_paths:
        if p.exists():
            freqs.append(load_freq_dict(p))
            log(f"freq loaded: {p.name} ({len(freqs[-1])} terms)")
        else:
            log(f"WARN freq-dict missing: {p}")
    for w in words.values():
        h = harmonic(w["term"], freqs)
        w["freq"] = int(round(h)) if h else None

    # 1) known-ness
    studied = sorted(
        (w for w in words.values() if w["state"] != "new"), key=lambda w: w["k"]
    )
    kn = []
    for rank, w in enumerate(studied, 1):
        rp = f"R{int(round(w['r'] * 100))}" if w["r"] is not None else "R--"
        sd = f"s{int(w['stability'])}" if w["stability"] else ""
        kn.append(
            (
                w["term"],
                rank,
                f"{w['state']}·{sd}{'·' if sd else ''}{rp}".replace("··", "·"),
            )
        )
    write_yomitan(
        out_dir / "saitenka-knowness.zip",
        "Saitenka Known-ness",
        "Personal FSRS knowledge; weak/forgotten high, mature low.",
        kn,
    )

    # 2) reactivate (forgotten) & 3) learn-new (new) — ranked by blended frequency
    def rank_by_freq(state):
        pool = [w for w in words.values() if w["state"] == state]
        # common (small freq) first; words without freq go last
        pool.sort(key=lambda w: (w["freq"] is None, w["freq"] or 10**9, -(w["k"] or 0)))
        return pool

    forgotten = rank_by_freq("forgotten")
    new = rank_by_freq("new")
    nf, tf = emit_ranking(
        out_dir,
        "saitenka-reactivate",
        "Saitenka Reactivate (forgotten)",
        "Forgotten words ranked by frequency — highest-ROI reactivation.",
        forgotten,
    )
    nn, tn = emit_ranking(
        out_dir,
        "saitenka-learn-new",
        "Saitenka Learn-new",
        "Never-studied words ranked by frequency.",
        new,
    )

    dist = Counter(w["state"] for w in words.values())
    log("─" * 54)
    log(f"decay source: {dict(decay_src)}  (per-card decay preferred, else preset)")
    log(f"words (deduped): {len(words)}   states: {dict(dist)}")
    log(
        f"freq coverage: forgotten {sum(1 for w in forgotten if w['freq'])}/{len(forgotten)}, "
        f"new {sum(1 for w in new if w['freq'])}/{len(new)}"
    )
    log(f"1. known-ness : {len(kn):>6} → saitenka-knowness.zip")
    log(
        f"2. REACTIVATE : {nf:>6} forgotten → saitenka-reactivate.{{zip,json,nids.txt}} (seed {tf} notes)"
    )
    log(
        f"3. LEARN-NEW  : {nn:>6} new       → saitenka-learn-new.{{zip,json,nids.txt}} (seed {tn} notes)"
    )
    log(f"out: {out_dir}")


if __name__ == "__main__":
    main()

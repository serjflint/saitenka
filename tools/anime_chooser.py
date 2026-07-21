# /// script
# requires-python = ">=3.13"
# dependencies = ["sudachipy", "sudachidict-core", "jreadability", "httpx", "httpx-aiohttp", "aiohttp"]
# ///
"""
saitenka · anime_chooser — pick what to watch next by N+1 fit and forgotten-density.

Reads your AniList list (watching / planning / rewatching), checks JP-sub availability on
Jimaku, and — with --deep — downloads a sample subtitle per series, tokenizes it, and scores
each against your Anki known/forgotten sets:

  * coverage %       fraction of running words you already know   (want ~90–98% = N+1 zone)
  * new words/ep     unique never-seen words in a sample episode  (want ~10–30, not 100–300!)
  * i+1 line %       lines with exactly ONE unknown word          (the minable sweet spot)
  * forgotten words  how many of your forgotten words appear       (free reactivation)
  * popularity       AniList popularity

Network: async httpx over the aiohttp transport (httpx-aiohttp), Jimaku calls run concurrently.

Modes:
  --mode n1          rank by N+1 fit  (coverage in-band × i+1% × popularity × sub-availability)
  --mode reactivate  rank by forgotten-density (best shows to re-encounter what you've lost)

Config:
  --anilist-user NAME            (AniList lists are public by username + a UA header; no token)
  --jimaku-key KEY  | env JIMAKU_API_KEY | ~/.jimaku   (free key from jimaku.cc)
  --deep                         download + tokenize a sample sub per series (SudachiPy normalized_form)

Usage:
  uv run tools/anime_chooser.py --anilist-user serjflint --deep --max-series 20
  uv run tools/anime_chooser.py --anilist-user serjflint --mode reactivate --deep
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from httpx_aiohttp import HttpxAiohttpClient

# MoeDB: open, AniList-keyed anime difficulty CSV (9.9k titles, vocab-density methodology)
MOEDB_URL = "https://raw.githubusercontent.com/Moe-DB/Moe-DB.github.io/master/anilist_data_with_img_status.csv"
MOEDB_CACHE = Path.home() / ".cache" / "saitenka" / "moedb.csv"

MAC_DEFAULT = "~/Library/Application Support/Anki2/User 1/collection.anki2"
ANILIST = "https://graphql.anilist.co"
JIMAKU = "https://jimaku.cc/api"
UA = {
    "User-Agent": "saitenka/1.0 (Japanese study tool)"
}  # AniList/Cloudflare blocks default UA
FSRS_DEFAULT_DECAY = 0.1542
JP = re.compile(r"[぀-ヿ㐀-鿿]")
TERM_FIELDS = ["expression", "word", "characters", "entry", "vocab"]
CONCURRENCY = 6
RARE_RANK = (
    30000  # freq rank beyond which a word is "rare/hard" (rarity-based difficulty)
)


def load_freq(zip_path):
    """Any Yomitan freq zip → {term: rank}. Difficulty = vocab rarity (rarer words → harder)."""
    ranks = {}
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not re.search(r"term_meta_bank_\d+\.json$", name):
                continue
            for e in json.loads(z.read(name)):
                if len(e) < 3 or e[1] != "freq":
                    continue
                d = e[2]
                r = None
                if isinstance(d, int | float):
                    r = int(d)
                elif isinstance(d, dict):
                    f = d.get("frequency", d)
                    r = f.get("value") if isinstance(f, dict) else f
                if isinstance(r, int | float) and (
                    e[0] not in ranks or r < ranks[e[0]]
                ):
                    ranks[e[0]] = int(r)
    return ranks


def load_moedb(refresh=False):
    """MoeDB anilist_id → {overall(1-100), density%, words/ep}. Cached; offline after 1st fetch."""
    if refresh or not MOEDB_CACHE.exists():
        MOEDB_CACHE.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(MOEDB_URL, headers=UA)
        MOEDB_CACHE.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    out = {}
    with open(MOEDB_CACHE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            aid = (row.get("anilist_id") or "").strip()
            if not aid:
                continue

            def num(col):
                v = (row.get(col) or "").strip()
                return float(v) if v else None

            try:
                out[int(float(aid))] = {
                    "overall": num("Overall Difficulty (1-100)"),
                    "density": num("Vocab Density (%)"),
                    "wpe": num("Avg. Words/Episode"),
                }
            except ValueError:
                pass
    return out


def log(m):
    print(f"[chooser] {m}")


# ── tokenization: SudachiPy normalized_form collapses inflections + spelling
#    variants (植えます→植える, okurigana/kanji-kana), applied to BOTH sides ──────
CONTENT_POS = ("名詞", "動詞", "形容詞", "副詞")


def make_tokenizer():
    from sudachipy import dictionary, tokenizer

    return dictionary.Dictionary(dict="core").create(), tokenizer.Tokenizer.SplitMode.C


def normalize_words(text, tokmode):
    tok, mode = tokmode
    return [
        m.normalized_form()
        for m in tok.tokenize(text, mode)
        if m.part_of_speech()[0] in CONTENT_POS and JP.search(m.surface())
    ]


# ── 1. known / forgotten sets from Anki (normalized dictionary forms) ─────────
def retrievability(s, elapsed, decay):
    if not s or s <= 0 or elapsed < 0:
        return None
    f = 0.9 ** (1.0 / decay) - 1.0
    return (1.0 + f * elapsed / s) ** decay


def load_word_states(collection, tokmode=None, mature_ivl=21, forgotten_r=0.85):
    src = Path(os.path.expanduser(collection))
    tmp = Path(tempfile.mkdtemp(prefix="saitenka_chooser_"))
    dst = tmp / "c.anki2"
    shutil.copy2(src, dst)
    for suf in ("-wal", "-shm"):
        if Path(str(src) + suf).exists():
            shutil.copy2(str(src) + suf, str(dst) + suf)
    con = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    con.text_factory = str
    con.create_collation("unicase", lambda a, b: (a > b) - (a < b))
    cur = con.cursor()
    flds = {}
    for ntid, ord_, name in cur.execute("SELECT ntid,ord,name FROM fields"):
        flds.setdefault(ntid, {})[name.lower()] = ord_
    term_ord = {
        m: next((o[c] for c in TERM_FIELDS if c in o), None) for m, o in flds.items()
    }
    last = dict(cur.execute("SELECT cid, MAX(id) FROM revlog GROUP BY cid"))
    now = time.time() * 1000.0
    note_state = {}
    rank = {"known": 3, "forgotten": 2, "new": 1}
    for cid, nid, ctype, ivl, data in cur.execute(
        "SELECT id,nid,type,ivl,data FROM cards"
    ):
        s = dc = None
        if data:
            try:
                j = json.loads(data)
                s = j.get("s")
                dc = j.get("decay")
            except Exception:
                pass
        elapsed = (now - last[cid]) / 86_400_000.0 if cid in last else None
        r = (
            retrievability(s, elapsed, -(dc or FSRS_DEFAULT_DECAY))
            if elapsed is not None
            else None
        )
        if ctype == 2 and r is not None and r < forgotten_r:
            st = "forgotten"
        elif ctype == 2:
            st = "known"
        else:
            st = "new"
        cur_st = note_state.get(nid)
        if cur_st is None or rank[st] > rank[cur_st]:
            note_state[nid] = st
    known, forgotten = set(), set()
    for nid, mid, flds_str in cur.execute("SELECT id,mid,flds FROM notes"):
        o = term_ord.get(mid)
        if o is None or nid not in note_state:
            continue
        parts = flds_str.split("\x1f")
        if o >= len(parts):
            continue
        raw = re.sub(r"<[^>]+>", "", parts[o])
        raw = re.sub(r"\[[^\]]*\]", "", raw).replace(" ", "").replace("　", "")
        st = note_state[nid]
        if st not in ("known", "forgotten"):
            continue
        for term in (v for v in re.split(r"[,，、/／;；]", raw) if v and JP.search(v)):
            forms = {term}
            if tokmode:
                norm = normalize_words(term, tokmode)
                if len(norm) == 1:  # single vocab word → also index its normalized form
                    forms.add(norm[0])
            for w in forms:
                if st == "known":
                    known.add(w)
                else:  # forgotten was known → in vocab too
                    forgotten.add(w)
                    known.add(w)
    con.close()
    return known, forgotten


# ── 2. AniList + 3. Jimaku (async httpx over aiohttp) ────────────────────────
async def anilist_list(client, user, statuses):
    q = """query($u:String,$s:[MediaListStatus]){MediaListCollection(userName:$u,type:ANIME,
      status_in:$s){lists{entries{status media{id popularity averageScore episodes status
      title{romaji english native}}}}}}"""
    r = await client.post(
        ANILIST,
        json={"query": q, "variables": {"u": user, "s": statuses}},
        headers={"Accept": "application/json"},
    )
    data = r.json()
    out = []
    for lst in data["data"]["MediaListCollection"]["lists"]:
        for e in lst["entries"]:
            m = e["media"]
            out.append(
                {
                    "id": m["id"],
                    "status": e["status"],
                    "airing": m.get("status"),
                    "title": m["title"]["romaji"] or m["title"]["native"],
                    "popularity": m.get("popularity") or 0,
                    "score": m.get("averageScore") or 0,
                    "episodes": m.get("episodes") or 0,
                }
            )
    return out


def pick_episodes(files, n):
    """One sub file per distinct episode number (prefer .srt), up to n."""
    if isinstance(files, dict):  # some entries return {"files": [...]}
        files = files.get("files", [])
    by_ep = {}
    for f in files:
        if not isinstance(f, dict):
            continue
        nm = f.get("name", "").lower()
        if not nm.endswith((".srt", ".ass")):
            continue
        m = re.search(r"(?:s\d+e|[\s\-_#.]e?)(\d{1,3})(?=[\s.\[\(_-]|$)", nm)
        key = m.group(1) if m else nm
        cur = by_ep.get(key)
        if cur is None or (
            nm.endswith(".srt") and not cur["name"].lower().endswith(".srt")
        ):
            by_ep[key] = f
    return list(by_ep.values())[:n]


async def jimaku_subs(client, sem, anilist_id, key, download, n_eps):
    h = {"Authorization": key}
    async with sem:
        try:
            entries = (
                await client.get(
                    f"{JIMAKU}/entries/search?anilist_id={anilist_id}", headers=h
                )
            ).json()
        except Exception:
            return None, []
        if not entries:
            return False, []
        if not download:
            return True, []
        try:
            files = (
                await client.get(
                    f"{JIMAKU}/entries/{entries[0]['id']}/files", headers=h
                )
            ).json()
        except Exception:
            return True, []
        texts = []
        for f in pick_episodes(files, n_eps):
            try:
                texts.append((await client.get(f["url"], headers=h)).text)
            except Exception:
                pass
        return True, texts


async def fetch(args):
    async with HttpxAiohttpClient(
        headers=UA, timeout=30, follow_redirects=True
    ) as client:
        media = await anilist_list(client, args.anilist_user, args.status.split(","))
        log(f"{len(media)} titles ({args.status})")
        if args.airing:
            media = [m for m in media if m["airing"] == "RELEASING"]
            log(f"{len(media)} currently airing (RELEASING)")
        media = sorted(media, key=lambda m: m["popularity"], reverse=True)[
            : args.max_series
        ]
        log(f"processing {len(media)} titles, concurrency={CONCURRENCY}")
        if not args.jimaku_key:
            return media, [(None, [])] * len(media)
        sem = asyncio.Semaphore(CONCURRENCY)
        subs = await asyncio.gather(
            *[
                jimaku_subs(
                    client, sem, m["id"], args.jimaku_key, args.deep, args.episodes
                )
                for m in media
            ]
        )
        return media, subs


# ── 4. coverage from a subtitle file ─────────────────────────────────────────
def sub_lines(text):
    text = text.lstrip("﻿")
    if "Dialogue:" in text:  # ASS
        out = []
        for ln in text.splitlines():
            if not ln.startswith("Dialogue:"):
                continue  # skips Comment: / signs headers
            parts = ln.split(",", 9)
            if len(parts) != 10:
                continue
            t = re.sub(r"\{[^}]*\}", "", parts[9])
            t = t.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ").strip()
            if t:
                out.append(t)
        return out
    out = []  # SRT
    for ln in text.splitlines():
        s = ln.strip().lstrip("﻿")
        if not s or "-->" in s or s.isdigit():
            continue
        out.append(re.sub(r"<[^>]+>", "", s))
    return out


def readability_score(text):
    """Objective difficulty via jReadability (public methodology): 0.5 hard … 6.5 easy."""
    try:
        from jreadability import compute_readability

        return round(compute_readability(text), 1) if text.strip() else 0.0
    except Exception:
        return 0.0


def episode_stats(text, known, forgotten, tokmode, freq_ranks):
    total = kn = i1 = i2 = rare = 0
    lines = sub_lines(text)
    new_set, forg_set = set(), set()
    for ln in lines:
        words = normalize_words(ln, tokmode)
        if not words:
            continue
        unk = [w for w in words if w not in known]
        total += len(words)
        kn += len(words) - len(unk)
        for w in words:
            if w in forgotten:
                forg_set.add(w)
            elif w not in known:
                new_set.add(w)
            rk = freq_ranks.get(w) if freq_ranks else None
            if freq_ranks and (rk is None or rk > RARE_RANK):
                rare += 1
        n_unk = len(set(unk))
        if len(words) >= 3:
            if n_unk == 1:
                i1 += 1
            elif n_unk == 2:
                i2 += 1
    return {
        "total": total,
        "kn": kn,
        "i1": i1,
        "i2": i2,
        "rare": rare,
        "lines": len(lines),
        "new": new_set,
        "forg": forg_set,
        "read": readability_score("\n".join(lines)),
    }


def coverage(texts, known, forgotten, tokmode, freq_ranks):
    """Aggregate over up to N sampled episodes; word-level dedup for a stable estimate."""
    stats = [
        episode_stats(t, known, forgotten, tokmode, freq_ranks)
        for t in texts
        if t and t.strip()
    ]
    if not stats:
        return None
    n = len(stats)
    total = sum(s["total"] for s in stats)
    lines = max(sum(s["lines"] for s in stats), 1)
    i1 = sum(s["i1"] for s in stats)
    i2 = sum(s["i2"] for s in stats)
    kn = sum(s["kn"] for s in stats)
    rare = sum(s["rare"] for s in stats)
    new_union = set().union(*(s["new"] for s in stats))
    forg_union = set().union(*(s["forg"] for s in stats))
    return {
        "episodes": n,
        "cov": kn / total if total else 0,
        "i1_pct": i1 / lines,
        "i2_pct": i2 / lines,
        "minable_pct": (i1 + i2) / lines,  # lines with 1 OR 2 unknowns
        "new_words": round(sum(len(s["new"]) for s in stats) / n),  # avg new/episode
        "new_unique": len(new_union),  # deduped across the sampled episodes
        "forgotten_hits": len(forg_union),
        "readability": round(sum(s["read"] for s in stats) / n, 1),
        "difficulty": rare / total if (total and freq_ranks) else 0,  # rarity 0–1
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default=MAC_DEFAULT)
    # freq zips are user-supplied (not shipped in-repo); use the first zip in the freq dir.
    _env_freq = os.environ.get("SAITENKA_FREQ_DIR")
    _freqdir = Path(_env_freq) if _env_freq else Path(__file__).resolve().parent / "freq"
    fdef = next(iter(sorted(_freqdir.glob("*.zip"))), None)
    ap.add_argument(
        "--freq-dict",
        default=str(fdef) if fdef else None,
        help="Yomitan freq zip → rarity-based difficulty "
        "(default: first *.zip in $SAITENKA_FREQ_DIR or tools/freq/)",
    )
    ap.add_argument("--anilist-user", required=True)
    ap.add_argument("--status", default="CURRENT,PLANNING,REPEATING")
    jdef = os.environ.get("JIMAKU_API_KEY")
    if not jdef and os.path.exists(os.path.expanduser("~/.jimaku")):
        jdef = open(os.path.expanduser("~/.jimaku")).read().strip()
    ap.add_argument("--jimaku-key", default=jdef)
    ap.add_argument("--mode", choices=["n1", "reactivate"], default="n1")
    ap.add_argument(
        "--airing", action="store_true", help="only currently-airing (RELEASING) titles"
    )
    ap.add_argument(
        "--sort",
        choices=["comprehensive", "new", "coverage"],
        default="comprehensive",
        help="comprehensive = learnability × enjoyment; new = fewest new words/ep first",
    )
    ap.add_argument(
        "--fun",
        type=float,
        default=0.25,
        help="0 = maximize comprehensibility, 1 = maximize enjoyment (default 0.25)",
    )
    ap.add_argument(
        "--episodes",
        type=int,
        default=3,
        help="sample up to N distinct episodes/series and aggregate (default 3)",
    )
    ap.add_argument(
        "--no-moedb",
        dest="moedb",
        action="store_false",
        help="disable the MoeDB external difficulty cross-check + no-subs fallback",
    )
    ap.add_argument(
        "--moedb-refresh", action="store_true", help="re-download the MoeDB CSV cache"
    )
    ap.add_argument(
        "--deep", action="store_true", help="download+tokenize a sample sub per series"
    )
    ap.add_argument(
        "--max-series",
        type=int,
        default=40,
        help="cap series processed (by popularity)",
    )
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    tokmode = make_tokenizer() if args.deep else None
    freq_ranks = {}
    if (
        args.deep
        and args.freq_dict
        and os.path.exists(os.path.expanduser(args.freq_dict))
    ):
        freq_ranks = load_freq(os.path.expanduser(args.freq_dict))
        log(
            f"loaded {len(freq_ranks)} freq ranks ({Path(args.freq_dict).name}) for difficulty"
        )
    log("loading known/forgotten sets from Anki …")
    known, forgotten = load_word_states(args.collection, tokmode)
    log(f"known {len(known)} · forgotten {len(forgotten)}")

    moedb = {}
    if args.moedb:
        try:
            moedb = load_moedb(args.moedb_refresh)
            log(f"MoeDB: {len(moedb)} AniList-keyed difficulty entries")
        except Exception as e:
            log(f"MoeDB unavailable ({e}); using local difficulty only")

    log(f"fetching AniList list for {args.anilist_user} …")
    media, sub_results = asyncio.run(fetch(args))
    rows = [
        {
            **m,
            "subs": avail,
            "moe": moedb.get(m["id"]),
            "cov": (
                coverage(texts, known, forgotten, tokmode, freq_ranks)
                if (texts and tokmode)
                else None
            ),
        }
        for m, (avail, texts) in zip(media, sub_results)
    ]

    def enjoyment(row):
        # AniList averageScore (quality) + popularity (social proof), each 0–1
        rating = (row["score"] or 65) / 100.0
        popn = min(1.0, ((row["popularity"] or 0) ** 0.5) / (300000**0.5))
        return 0.6 * rating + 0.4 * popn

    def comprehensive(row):
        avail = 1.0 if row["subs"] else (0.3 if row["subs"] is None else 0.0)
        if args.mode == "reactivate":
            return (row["cov"]["forgotten_hits"] if row["cov"] else 0) * avail
        enjoy = enjoyment(row)
        c = row["cov"]
        if not c:  # no local subs → fall back to MoeDB difficulty, else enjoyment
            moe = row.get("moe")
            if moe and moe.get("overall") is not None:
                learn = max(0.0, 1 - moe["overall"] / 100.0)
                return (learn ** (1 - args.fun)) * (enjoy**args.fun) * avail
            return 0.3 * enjoy * avail
        # learnability = coverage + minable(i≤2) nudge, penalized by objective rarity difficulty
        learn = min(1.0, c["cov"] + 0.3 * c["minable_pct"])
        learn *= 1 - 0.35 * c["difficulty"]
        # geometric blend: a show must be BOTH learnable AND fun; --fun tilts the balance
        return (learn ** (1 - args.fun)) * (enjoy**args.fun) * avail

    for r in rows:
        r["cscore"] = comprehensive(r)
    if args.sort == "new":  # most appropriate N+1 (fewest new words) → hardest
        rows.sort(key=lambda r: r["cov"]["new_words"] if r["cov"] else 10**9)
    elif args.sort == "coverage":
        rows.sort(key=lambda r: r["cov"]["cov"] if r["cov"] else -1, reverse=True)
    else:  # comprehensive: learnability × enjoyment
        rows.sort(key=lambda r: r["cscore"], reverse=True)
    log("─" * 84)
    log(
        f"{'#':>2} {'title':28} {'cov':>5} {'new':>5} {'diff':>5} {'moe':>4} {'i≤2':>5} "
        f"{'rate':>4} {'pop':>7} {'sc':>4}"
    )
    for i, r in enumerate(rows[: args.limit], 1):
        c = r["cov"]
        moe_v = (
            r["moe"]["overall"]
            if (r.get("moe") and r["moe"]["overall"] is not None)
            else None
        )
        cov = f"{c['cov'] * 100:4.0f}%" if c else "  –  "
        new = f"{c['new_words']:>5}" if c else "    –"
        diff = f"{c['difficulty'] * 100:4.0f}%" if c else "    –"
        moe = f"{moe_v:>4.0f}" if moe_v is not None else "   –"
        minb = f"{c['minable_pct'] * 100:4.0f}%" if c else "    –"
        rate = f"{r['score'] or '–':>4}"
        log(
            f"{i:>2} {r['title'][:28]:28} {cov:>5} {new} {diff:>5} {moe} {minb:>5} "
            f"{rate} {r['popularity']:>7} {r['cscore'] * 100:4.0f}"
        )
    if not args.jimaku_key:
        log(
            "note: no --jimaku-key → sub availability unknown; add a key + --deep for real N+1 scoring"
        )
    elif not args.deep:
        log(
            "note: pass --deep to download+tokenize sample subs for coverage / i+1 / forgotten scoring"
        )


if __name__ == "__main__":
    main()

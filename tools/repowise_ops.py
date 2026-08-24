"""Guarded, observable operations for the local repowise wiki.

Every command that spends GPU time streams a progress line while it runs, because
the failure mode here is not slowness — it is *silence that looks like slowness*.
A run whose requests are being dropped server-side looks exactly like a healthy
one: the progress bar sits still, the GPU pins at 100%, and nothing is logged.

The three faults that cost a night, all diagnosable from the line `watch` prints:

* **Requests dropped by the server, not slow.** ``queue_timeout`` in the MLX
  server config is a hard per-request ceiling; when a call needs longer, the
  server kills it and repowise sees no error. The signature is *starts without
  settles*, which is why both are counted.
* **Embeddings expiring.** The OpenAI embedder hardcodes a 10s timeout; one
  chunk of page-sized items measures ~13s, so batches fail wholesale and the
  run still exits 0. ``REPOWISE_EMBEDDING_TIMEOUT`` (honoured by a patched
  repowise) is set on every run below; `vectors` repairs what was lost.
* **A killed run's job record says "running" forever.** Nothing rewrites it, so
  `watch` treats a stale ``updated_at`` as stale rather than live.

GPU utilisation answers none of this: a single decode stream is
memory-bandwidth bound, so the meter reads 100% whether or not work progresses.
Throughput available to a *fresh* request does, and that is what `--probe` asks.

Choosing a command:

* `update`  — the default. Incremental; re-indexes what changed and regenerates
  the affected pages.
* `reindex` — a full re-plan. Needed after a mass rename, because `update` moves
  the *file* layer but never re-partitions the *concept* layer: subsystem pages
  keep naming directories that no longer exist, and stay marked fresh.
* `vectors` — embeddings only, no LLM. The repair after an embedding loss.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / ".repowise"
BASE_URL = "http://127.0.0.1:11435/v1"
CHAT_MODEL = "mlx-community/Qwen3.5-9B-MLX-4bit"
EMBED_MODEL = "qwen3-embedding-4b"

#: Seconds allowed per embedding request. One chunk of page-sized items measures
#: ~13s here against an idle endpoint and saturates near ~14s (the embedder
#: truncates at its context window), so this is ~2x the measured ceiling. Only a
#: repowise that honours it helps; stock builds hardcode 10s.
EMBED_TIMEOUT_S = 180

#: How long a job record may go unwritten before `watch` stops believing it.
STALE_JOB_S = 600

#: Pages kept as source-verified manual corrections across rebuilds. A regen
#: overwrites them; `restore` puts them back keeping the current page id and
#: source hash. See the Basic Memory note "Repowise local indexing".
CORRECTIONS = (
    "module_page:src/saitenka",
    "onboarding:onboarding/getting_started",
    "onboarding:onboarding/how_it_works",
    "onboarding:onboarding/key_concepts",
    "file_page:src/saitenka/app/session_controller.py",
)


def _fail(msg: str) -> None:
    sys.stderr.write(f"repowise-ops: {msg}\n")
    raise SystemExit(1)


def _post(path: str, payload: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def preflight() -> None:
    """Refuse to start a long run against an endpoint that cannot serve it."""
    served: set[str] = set()
    try:
        with urllib.request.urlopen(f"{BASE_URL}/models", timeout=10) as resp:
            served = {m["id"] for m in json.loads(resp.read())["data"]}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _fail(f"no MLX endpoint at {BASE_URL} ({exc}). Start it with ensure-mlx-server.")
    missing = {CHAT_MODEL, EMBED_MODEL} - served
    if missing:
        _fail(f"endpoint is up but not serving {sorted(missing)}")
    print(f"preflight: {BASE_URL} serving both models")


def latest_job() -> dict | None:
    jobs = sorted((INDEX / "jobs").glob("*.json"), key=lambda p: p.stat().st_mtime)
    return json.loads(jobs[-1].read_text(encoding="utf-8")) if jobs else None


def probe_tokens_per_second(*, tokens: int = 120, timeout: int = 180) -> float:
    """Generation speed a fresh request gets — the load signal that GPU% is not."""
    start = time.monotonic()
    body = _post(
        "/chat/completions",
        {
            "model": CHAT_MODEL,
            "messages": [{"role": "user", "content": "Count from 1 to 60, one per line."}],
            "max_tokens": tokens,
        },
        timeout,
    )
    elapsed = time.monotonic() - start
    return body["usage"]["completion_tokens"] / elapsed if elapsed else 0.0


def _run_env() -> dict[str, str]:
    return {
        **os.environ,
        "OPENAI_BASE_URL": BASE_URL,
        "OPENAI_API_KEY": "not-needed",
        "REPOWISE_EMBEDDING_TIMEOUT": str(EMBED_TIMEOUT_S),
    }


def _pages_written_since(minutes: int) -> int:
    """Pages persisted recently — the only ground truth that work is landing."""
    db = INDEX / "wiki.db"
    if not db.exists():
        return 0
    cut = (datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes)).isoformat(" ")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(
            "select count(*) from wiki_pages where updated_at > ?", (cut,)
        ).fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def _log_signals(log: Path) -> tuple[int, int, str]:
    """``(llm_starts, llm_settles, phase)`` from a verbose run log.

    ``settled`` needs a patched repowise; on a stock build it stays 0 and only
    the start count moves, which is itself the "requests are vanishing" tell.
    """
    if not log.exists():
        return 0, 0, ""
    raw = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", log.read_bytes().decode("utf-8", "replace"))
    lines = [x.strip() for x in raw.replace("\r", "\n").splitlines() if x.strip()]
    starts = sum("generate.start" in x for x in lines)
    # A patched build emits both events per request, so counting either-or would
    # double every call and show more settles than starts. Prefer `.settled`
    # (it fires on the failure path too) and fall back to `.done` on a stock build.
    settles = sum("generate.settled" in x for x in lines) or sum(
        "generate.done" in x for x in lines
    )
    bar = next((x for x in reversed(lines) if "…" in x or "..." in x), "")
    phase = re.sub(r"\s+", " ", re.sub(r"[━╸{}◉◒─]+", " ", bar)).strip()[:46]
    return starts, settles, phase


def _run_observed(cmd: list[str], log: Path, *, every: int = 30) -> int:
    """Run *cmd*, printing what it is doing until it exits.

    A child that writes nothing for ten minutes is indistinguishable from one
    that has wedged, so the parent narrates instead of waiting quietly.
    """
    print(f"running: {' '.join(cmd)}\n  log: {log}")
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, cwd=REPO, stdout=fh, stderr=subprocess.STDOUT, env=_run_env())
        while proc.poll() is None:
            time.sleep(every)
            starts, settles, phase = _log_signals(log)
            pages = _pages_written_since(5)
            mins, secs = divmod(int(time.monotonic() - started), 60)
            # Nothing landing *and* a request open is the drop signature; the
            # window is already 5 minutes wide, so one empty reading is enough.
            note = (
                "  (nothing landed in 5m — check `watch --probe`)"
                if pages == 0 and starts > settles
                else ""
            )
            print(
                f"  [{mins:02d}:{secs:02d}] llm {settles}/{starts} settled · "
                f"pages/5m {pages} · {phase}{note}"
            )
    code = proc.returncode
    starts, settles, _ = _log_signals(log)
    lost = starts - settles
    if lost > 0:
        print(f"  WARNING: {lost} LLM request(s) started and never settled — see {log}")
    print(f"done (exit {code}) in {int(time.monotonic() - started)}s")
    return code


def cmd_preflight(_args: argparse.Namespace) -> int:
    preflight()
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    if not INDEX.is_dir():
        _fail(f"no index at {INDEX}")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dest = Path(args.into or REPO.parent) / f".repowise-backup-{stamp}"
    # Outside the index by default: a backup inside the directory a rebuild
    # rewrites is not a backup.
    shutil.copytree(INDEX, dest)
    print(f"backup: {dest}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Is a run alive? Job record, pages landing, and optionally the endpoint."""
    job = latest_job()
    if job is None:
        _fail("no job records — nothing has run against this index")
    assert job is not None
    updated = datetime.fromisoformat(job["updated_at"])
    age = (datetime.now(UTC) - updated).total_seconds()
    # Only the running process writes this file, so a "running" job nobody has
    # touched for STALE_JOB_S was killed. Reporting it as live is how a dead run
    # kept looking busy.
    state = job["status"]
    if state == "running" and age > STALE_JOB_S:
        state = f"running (STALE — untouched for {int(age // 60)}m; the run is gone)"
    print(
        f"job {state}  {job['completed_pages']}/{job['total_pages']} pages  "
        f"level {job['current_level']}  failed {job['failed_pages']}"
    )
    for name, log in (("update", ".repowise-update.log"), ("reindex", ".repowise-reindex.log")):
        starts, settles, phase = _log_signals(REPO / log)
        if starts:
            print(f"{name}: llm {settles}/{starts} settled · {phase}")
    print(f"pages written in the last 5m: {_pages_written_since(5)}")
    if args.probe:
        tps = probe_tokens_per_second()
        busy = tps < args.idle_tps * 0.7
        print(
            f"probe {tps:.1f} tok/s (idle ~{args.idle_tps:.0f}) — "
            f"{'endpoint busy serving' if busy else 'endpoint IDLE'}"
        )
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Incremental update — the everyday path."""
    preflight()
    cmd = ["repowise", "update", "--no-workspace", "--concurrency", str(args.concurrency), "-v"]
    if not args.index_only:
        # Without this the run honours the persisted docs_mode, which for an
        # index-only init means structure is refreshed and every page the change
        # touched is left as it was.
        cmd.append("--docs")
    else:
        cmd.append("--index-only")
    return _run_observed(cmd, REPO / ".repowise-update.log")


def cmd_vectors(args: argparse.Namespace) -> int:
    """Rebuild the vector index from existing pages. No LLM calls."""
    preflight()
    cmd = ["repowise", "reindex"]
    if args.batch_size:
        cmd += ["--batch-size", str(args.batch_size)]
    return _run_observed(cmd, REPO / ".repowise-vectors.log")


def cmd_reindex(args: argparse.Namespace) -> int:
    """Full re-plan. Slower than `update`, and the only thing that re-partitions."""
    preflight()
    if not args.no_backup:
        cmd_backup(argparse.Namespace(into=None))
    cmd = [
        "repowise",
        "init",
        "--prose",
        "-y",
        "--no-editor-setup",  # a plain run otherwise rewrites editor MCP config
        "--concurrency",
        str(args.concurrency),
        "-v",  # repowise logs at ERROR otherwise, and never to a file
        "--provider",
        "openai",
        "--model",
        CHAT_MODEL,
    ]
    if args.fresh:
        # Skips prompt-hash reuse, so an interrupted run restarts from zero and
        # every manual correction is regenerated. Default off for both reasons.
        cmd.insert(3, "--force")
        print(f"--fresh: {len(CORRECTIONS)} manual pages will be overwritten; see `corrections`")
    return _run_observed(cmd, REPO / ".repowise-reindex.log")


def cmd_bench(args: argparse.Namespace) -> int:
    """Whether concurrency buys throughput here — the number the runs depend on.

    Server-dependent, and it has already flipped once: `mlx_lm.server`
    serialised, `mlx-openai-server` batches. Re-run after changing either.
    """
    preflight()
    one = probe_tokens_per_second(tokens=150)
    with ThreadPoolExecutor(max_workers=args.n) as pool:
        start = time.monotonic()
        for fut in [pool.submit(probe_tokens_per_second, tokens=150) for _ in range(args.n)]:
            fut.result()
        wall = time.monotonic() - start
    agg = (150 * args.n) / wall if wall else 0.0
    print(f"1 request : {one:.1f} tok/s")
    print(f"{args.n} parallel: {agg:.1f} tok/s aggregate")
    print(
        "batches — keep concurrency high"
        if agg > one * 1.3
        else "serialises — concurrency buys nothing"
    )
    return 0


def cmd_corrections(_args: argparse.Namespace) -> int:
    """The hand-corrected pages, and whether the live wiki still carries them."""
    live = _live_titles()
    print("manual corrections (a --fresh regen overwrites these):")
    for page_id in CORRECTIONS:
        target = page_id.split(":", 1)[1]
        mark = "present" if target in live else "MISSING"
        print(f"  [{mark}] {page_id}")
    print("\nrestore from a backup: uv run vibe/repowise-corrections.py restore <json>")
    return 0


def _live_titles() -> dict[str, str]:
    db = INDEX / "wiki.db"
    if not db.exists():
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return dict(con.execute("select target_path, title from wiki_pages"))
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("preflight", help="endpoint reachable and serving both models")
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("backup", help="snapshot .repowise outside the repo")
    p.add_argument("--into", help="parent directory for the snapshot")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("watch", help="is a run alive? (safe to run any time)")
    # Measured on this machine with the 9B chat model resident and no other load.
    p.add_argument("--idle-tps", type=float, default=20.0, help="idle throughput baseline")
    p.add_argument("--probe", action="store_true", help="also spend one request on a load probe")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("update", help="incremental update (the everyday path)")
    p.add_argument("--concurrency", type=int, default=8, help="see `bench` before lowering this")
    p.add_argument("--index-only", action="store_true", help="structure only, no page generation")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("vectors", help="rebuild embeddings from existing pages (no LLM)")
    p.add_argument("--batch-size", type=int, default=0)
    p.set_defaults(func=cmd_vectors)

    p = sub.add_parser("reindex", help="full re-plan (needed after a mass rename)")
    p.add_argument("--fresh", action="store_true", help="--force: no reuse, overwrites corrections")
    p.add_argument("--concurrency", type=int, default=8, help="see `bench` before lowering this")
    p.add_argument("--no-backup", action="store_true")
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("bench", help="does this endpoint batch? throughput at 1 vs N requests")
    p.add_argument("-n", type=int, default=3)
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("corrections", help="the manual pages a regen overwrites")
    p.set_defaults(func=cmd_corrections)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

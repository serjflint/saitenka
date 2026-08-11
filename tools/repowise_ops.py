"""Guarded operations for the local repowise wiki.

`repowise init --prose` against the local MLX endpoint has four foot-guns that
each look like a different failure than they are, so the safe invocation lives
here rather than in anyone's shell history:

* **Silence, not slowness.** Nothing reports done until a page completes, so a
  healthy run looks wedged for as long as its slowest page. Throttling to
  `--concurrency 1` to get progress back is a bad trade: measured on this
  endpoint, 3 concurrent requests deliver ~7.6 tok/s against ~3.8 for one, so
  serialising roughly halves throughput and lengthens the silence it was meant
  to fix. Use `watch` instead, and re-measure with `bench` after a server or
  model change — the opposite was true of the older `mlx_lm.server`.
* **`--force` overwrites the hand-corrected pages** listed in `CORRECTIONS`.
* **`--dry-run` still re-indexes** and still rewrites editor MCP config; only
  page *generation* is skipped.
* **No log file.** repowise logs at ERROR unless `-v`.

`watch` exists because neither signal alone is conclusive: `completed_pages`
only moves when a page finishes, and GPU utilisation cannot distinguish
"generating" from "spinning". Throughput available to a *fresh* probe can.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / ".repowise"
BASE_URL = "http://127.0.0.1:11435/v1"
CHAT_MODEL = "mlx-community/Qwen3.5-9B-MLX-4bit"
EMBED_MODEL = "qwen3-embedding-4b"

#: Pages kept as source-verified manual corrections across rebuilds. A regen
#: overwrites them; `restore` puts them back keeping the current page id and
#: source hash. See the Basic Memory note "Repowise local indexing".
CORRECTIONS = (
    "module_page:overlay/src/overlay",
    "onboarding:onboarding/getting_started",
    "onboarding:onboarding/how_it_works",
    "onboarding:onboarding/key_concepts",
    "file_page:overlay/src/overlay/app/controller.py",
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
    """Is the run alive? Page progress plus the throughput a fresh request gets."""
    job = latest_job()
    if job is None:
        _fail("no job records — nothing has run against this index")
    assert job is not None
    done, total = job["completed_pages"], job["total_pages"]
    print(
        f"job {job['status']}  {done}/{total} pages  level {job['current_level']}  "
        f"failed {job['failed_pages']}  updated {job['updated_at'][11:19]}Z"
    )
    if job["status"] != "running" or args.no_probe:
        return 0
    tps = probe_tokens_per_second()
    verdict = (
        "GPU busy serving — the run is alive"
        if tps < args.idle_tps * 0.7
        else "endpoint looks IDLE"
    )
    print(f"probe {tps:.1f} tok/s (idle baseline ~{args.idle_tps:.0f}) — {verdict}")
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
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
    log = REPO / ".repowise-reindex.log"
    print(
        f"running: {' '.join(cmd)}\n  log:     {log}\n  watch:   uv run tools/repowise_ops.py watch"
    )
    env = {**os.environ, "OPENAI_BASE_URL": BASE_URL, "OPENAI_API_KEY": "not-needed"}
    with log.open("w", encoding="utf-8") as fh:
        return subprocess.run(
            cmd, cwd=REPO, stdout=fh, stderr=subprocess.STDOUT, check=False, env=env
        ).returncode


def cmd_bench(args: argparse.Namespace) -> int:
    """Whether concurrency buys throughput here — the number `reindex` depends on.

    Server-dependent and it has already flipped once: `mlx_lm.server` serialised,
    `mlx-openai-server` batches. Re-run after changing either.
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

    p = sub.add_parser("watch", help="is a running generation alive?")
    # Measured on this machine with the 9B chat model resident and no other load.
    p.add_argument("--idle-tps", type=float, default=11.0, help="idle throughput baseline")
    p.add_argument("--no-probe", action="store_true", help="job record only, no model call")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("reindex", help="guarded regeneration (reuses unchanged pages)")
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

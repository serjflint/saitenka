"""Replay the portable corpus's reversible fault witnesses against this checkout."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "contracts/evidence-v0.json"


def _selected_witnesses(ids: set[str]) -> list[dict[str, str]]:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    if ledger.get("source_revision") != "HEAD":
        raise ValueError("evidence ledger must target the checked-out revision")
    witnesses = ledger["witnesses"]
    known = {witness["id"] for witness in witnesses}
    if missing := ids - known:
        raise ValueError(f"unknown witness ids: {sorted(missing)}")
    return [witness for witness in witnesses if not ids or witness["id"] in ids]


def _require_clean(path: Path) -> None:
    relative = path.relative_to(ROOT)
    clean = subprocess.run(
        ["git", "diff", "--quiet", "--", str(relative)],
        cwd=ROOT,
        check=False,
    )
    if clean.returncode != 0:
        raise RuntimeError(f"refusing to mutate modified file: {relative}")


def _run_witness(witness: dict[str, str]) -> str | None:
    path = ROOT / witness["path"]
    _require_clean(path)
    original = path.read_bytes()
    source = original.decode("utf-8")
    count = source.count(witness["find"])
    if count != 1:
        return f"expected one source match, found {count}"

    path.write_bytes(source.replace(witness["find"], witness["replace"], 1).encode("utf-8"))
    try:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            env = os.environ | {
                "PYTHONPYCACHEPREFIX": str(temporary / "pycache"),
                "SAITENKA_CONFIG": str(temporary / "overlay.toml"),
            }
            completed = subprocess.run(
                shlex.split(witness["command"]),
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
    finally:
        path.write_bytes(original)

    if completed.returncode == 0:
        return "focused command passed with the fault applied"
    command = shlex.split(witness["command"])
    if "tests/contracts/protocol.py" in command:
        scenario_index = command.index("--scenario") + 1
        if command[scenario_index] != witness["scenario_id"]:
            return "focused command targets a different scenario"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", action="append", default=[], help="replay one witness id")
    args = parser.parse_args()

    failures: list[tuple[str, str]] = []
    witnesses = _selected_witnesses(set(args.id))
    for witness in witnesses:
        if failure := _run_witness(witness):
            failures.append((witness["id"], failure))
        else:
            print(f"PASS {witness['id']}")

    if failures:
        for witness_id, failure in failures:
            print(f"FAIL {witness_id}: {failure}")
        raise SystemExit(1)
    print(f"verified {len(witnesses)} reversible fault witnesses")


if __name__ == "__main__":
    main()

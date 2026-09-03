"""Fail-closed validator for an assurance-pipeline completion receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HEX = re.compile(r"^[0-9a-f]{64}$")
REV = re.compile(r"^[0-9a-f]{40,64}$")
ARTIFACT_STAGES = ["freeze", "inquire", "route", "re-enfold", "gate", "freeze-packet", "review"]


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(receipt: dict) -> None:
    _require(receipt.get("version") == 1, "version must be 1")
    result = receipt.get("result")
    _require(result in {"artifact", "no-change"}, "result must be artifact or no-change")
    _require(bool(str(receipt.get("human_decision", "")).strip()), "human_decision is required")
    if result == "no-change":
        _require(
            receipt.get("stages") == ["freeze", "inquire", "route", "re-enfold", "restore"],
            "invalid no-change stage order",
        )
        restoration = receipt.get("restoration", {})
        _require(restoration.get("status_matches") is True, "no-change status was not restored")
        _require(restoration.get("index_matches") is True, "no-change index was not restored")
        _require(receipt.get("disposition") == "no-change", "invalid no-change disposition")
        return

    disposition = receipt.get("disposition")
    expected_stages = [*ARTIFACT_STAGES, *(["publish"] if disposition == "published" else [])]
    _require(receipt.get("stages") == expected_stages, "invalid artifact stage order")
    for key in ("base", "head"):
        _require(bool(REV.fullmatch(str(receipt.get(key, "")))), f"invalid {key} revision")
    for key in ("diff_digest", "packet_digest", "tree_digest", "index_digest"):
        _require(bool(HEX.fullmatch(str(receipt.get(key, "")))), f"invalid {key}")
    _require(bool(str(receipt.get("packet_path", "")).strip()), "packet_path is required")
    contribution = receipt.get("contribution", {})
    _require(contribution.get("prepare_only_completed") is True, "prepare-only was not completed")
    gates = receipt.get("gates", {})
    _require(gates.get("deterministic") == "pass", "deterministic gate did not pass")
    _require(gates.get("free_threaded") == "pass", "free-threaded gate did not pass")
    reviews = receipt.get("reviews")
    if not isinstance(reviews, list) or len(reviews) != 2:
        raise ValueError("exactly two reviews are required")
    invocations: set[str] = set()
    expected = {
        "head": receipt["head"],
        "diff_digest": receipt["diff_digest"],
        "packet_digest": receipt["packet_digest"],
        "tree_digest": receipt["tree_digest"],
        "index_digest": receipt["index_digest"],
    }
    for review in reviews:
        invocation = review.get("invocation")
        _require(isinstance(invocation, str) and invocation, "review invocation is required")
        _require(invocation not in invocations, "review invocations must be distinct")
        invocations.add(invocation)
        _require(review.get("status") == "completed", "every reviewer must complete")
        _require(review.get("read_only") is True, "reviewer was not read-only")
        _require(review.get("blocking_findings") == [], "review has unresolved P0/P1")
        _require(review.get("unresolved_p2") == [], "review has unresolved P2")
        _require(
            review.get("pre") == expected and review.get("post") == expected,
            "review did not verify the exact packet before and after",
        )
    _require(disposition in {"ready-pr", "published"}, "invalid artifact disposition")
    publish_only = contribution.get("publish_only_completed") is True
    if disposition == "published":
        _require(publish_only, "published result lacks publish-only completion")
        _require(receipt.get("publication_verified") is True, "publication was not verified")
    else:
        _require(not publish_only, "ready-pr receipt cannot claim publish-only completion")


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout


def verify_live(receipt: dict, repo: Path) -> None:
    if receipt.get("result") != "artifact":
        return
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    _require(head == receipt["head"], "current HEAD differs from receipt")
    diff = _git(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        f"{receipt['base']}...{receipt['head']}",
    )
    _require(
        hashlib.sha256(diff).hexdigest() == receipt["diff_digest"],
        "current diff digest differs from receipt",
    )
    packet = (repo / receipt["packet_path"]).resolve()
    try:
        packet.relative_to(repo.resolve())
    except ValueError as exc:
        raise ValueError("packet_path escapes the repository") from exc
    _require(
        hashlib.sha256(packet.read_bytes()).hexdigest() == receipt["packet_digest"],
        "current packet digest differs from receipt",
    )
    _require(not _git(repo, "diff", "--name-only"), "tracked worktree is dirty")
    _require(not _git(repo, "diff", "--cached", "--name-only"), "index is dirty")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--structure-only", action="store_true")
    args = parser.parse_args()
    try:
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        _require(isinstance(receipt, dict), "receipt must be a JSON object")
        validate(receipt)
        if not args.structure_only:
            verify_live(receipt, args.repo.resolve())
    except (OSError, json.JSONDecodeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"assurance-receipt: error: {exc}", file=sys.stderr)
        return 2
    print("assurance receipt: valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Fail-closed validator for an assurance-pipeline completion receipt and packet."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HEX = re.compile(r"^[0-9a-f]{64}$")
OID = re.compile(r"^[0-9a-f]{40,64}$")
ARTIFACT_STAGES = ["freeze", "inquire", "route", "re-enfold", "gate", "freeze-packet", "review"]
NO_CHANGE_STAGES = ["freeze", "inquire", "route", "re-enfold", "restore"]


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: object, name: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{name} is required")
    assert isinstance(value, str)
    return value


def _validate_packet(packet: dict, result: str) -> None:
    _require(packet.get("version") == 1, "packet version must be 1")
    _require(packet.get("result") == result, "packet result differs from receipt")
    _require(
        packet.get("entry_state") in {"fresh-inquiry", "accepted-dossier"}, "invalid entry state"
    )
    for key in (
        "supported_scenario",
        "product_invariant",
        "accepted_dossier",
        "scope_guard",
        "discriminator",
        "final_scenario_trace",
    ):
        _text(packet.get(key), f"packet {key}")
    decision = packet.get("human_decision")
    _require(isinstance(decision, dict), "packet human_decision is required")
    assert isinstance(decision, dict)
    for key in ("decision_id", "decision", "accepted_at"):
        _text(decision.get(key), f"human_decision {key}")
    datetime.fromisoformat(decision["accepted_at"])
    owners = packet.get("affected_owners")
    _require(
        isinstance(owners, list) and owners and all(isinstance(x, str) and x for x in owners),
        "packet affected_owners are required",
    )
    for key in ("mechanism_proofs", "validation_evidence"):
        value = packet.get(key)
        _require(isinstance(value, list) and value, f"packet {key} is required")
    for key in ("residual_uncertainty", "followups", "falsified_hypotheses"):
        _require(isinstance(packet.get(key), list), f"packet {key} must be a list")
    if result == "no-change":
        _require(
            bool(packet["falsified_hypotheses"]), "no-change packet requires a falsified hypothesis"
        )


def _validate_events(receipt: dict, expected_stages: list[str]) -> None:
    events = receipt.get("events")
    _require(isinstance(events, list), "ordered events are required")
    assert isinstance(events, list)
    _require(all(isinstance(event, dict) for event in events), "stage event must be an object")
    _require([event.get("stage") for event in events] == expected_stages, "invalid stage order")
    times: list[datetime] = []
    for event in events:
        _text(event.get("evidence"), "stage evidence")
        raw = _text(event.get("completed_at"), "stage completed_at")
        times.append(datetime.fromisoformat(raw))
    _require(
        times == sorted(times) and len(set(times)) == len(times), "stage timestamps are not ordered"
    )


def _validate_review_findings(findings: object) -> None:
    _require(isinstance(findings, list), "review findings must be a list")
    assert isinstance(findings, list)
    allowed = {
        "P0": {"fixed"},
        "P1": {"fixed"},
        "P2": {"fixed", "human-accepted"},
        "P3": {"recorded"},
    }
    for finding in findings:
        _require(isinstance(finding, dict), "review finding must be an object")
        severity = finding.get("severity")
        _require(isinstance(severity, str) and severity in allowed, "invalid finding severity")
        _require(finding.get("disposition") in allowed[severity], "unresolved review finding")
        _text(finding.get("summary"), "finding summary")


def validate(receipt: dict, packet: dict) -> None:
    _require(receipt.get("version") == 2, "receipt version must be 2")
    result = receipt.get("result")
    _require(result in {"artifact", "no-change"}, "result must be artifact or no-change")
    assert isinstance(result, str)
    _validate_packet(packet, result)
    for key in ("base", "head", "tree_digest"):
        _require(bool(OID.fullmatch(str(receipt.get(key, "")))), f"invalid {key}")
    for key in ("diff_digest", "packet_digest", "index_digest"):
        _require(bool(HEX.fullmatch(str(receipt.get(key, "")))), f"invalid {key}")
    _text(receipt.get("packet_path"), "packet_path")
    exclusions = receipt.get("scratch_exclusions")
    _require(
        isinstance(exclusions, list) and all(isinstance(x, str) and x for x in exclusions),
        "scratch_exclusions must be a string list",
    )
    assert isinstance(exclusions, list)
    _require(
        all(path.startswith("vibe/") for path in exclusions),
        "scratch exclusions must stay under vibe",
    )

    if result == "no-change":
        _validate_events(receipt, NO_CHANGE_STAGES)
        _require(receipt.get("disposition") == "no-change", "invalid no-change disposition")
        _require(receipt["base"] == receipt["head"], "no-change base and head differ")
        _require(
            receipt.get("baseline_index_digest") == receipt["index_digest"],
            "no-change index differs from baseline",
        )
        manifest = receipt.get("candidate_manifest")
        _require(
            isinstance(manifest, list) and manifest, "no-change candidate manifest is required"
        )
        assert isinstance(manifest, list)
        for item in manifest:
            _require(isinstance(item, dict), "candidate manifest item must be an object")
            _text(item.get("path"), "candidate path")
            _require(
                isinstance(item.get("existed_before"), bool), "candidate existed_before is required"
            )
            digest = item.get("sha256")
            _require(
                (item["existed_before"] and bool(HEX.fullmatch(str(digest))))
                or (not item["existed_before"] and digest is None),
                "invalid candidate digest",
            )
        return

    disposition = receipt.get("disposition")
    _require(
        disposition in {"local-proof", "ready-pr", "published"}, "invalid artifact disposition"
    )
    expected_stages = [*ARTIFACT_STAGES, *(["publish"] if disposition == "published" else [])]
    _validate_events(receipt, expected_stages)
    contribution = receipt.get("contribution")
    _require(isinstance(contribution, dict), "contribution evidence is required")
    assert isinstance(contribution, dict)
    _require(
        contribution.get("prepare_only") == {"status": "completed", "head": receipt["head"]},
        "prepare-only was not completed on head",
    )
    publish = contribution.get("publish_only")
    publish_generation = None
    if disposition == "published":
        _require(isinstance(publish, dict), "published result lacks publish-only evidence")
        assert isinstance(publish, dict)
        _require(
            publish.get("status") == "completed" and publish.get("head") == receipt["head"],
            "publish-only did not use head",
        )
        publish_generation = publish.get("review_generation")
        _require(
            isinstance(publish_generation, int) and publish_generation >= 1,
            "publish-only lacks review generation",
        )
        _require(receipt.get("publication_verified") is True, "publication was not verified")
    else:
        _require(publish is None, "unpublished receipt cannot claim publish-only completion")
    gates = receipt.get("gates")
    _require(
        isinstance(gates, list)
        and all(isinstance(gate, dict) for gate in gates)
        and {g.get("name") for g in gates} == {"deterministic", "free-threaded"},
        "both gates are required",
    )
    assert isinstance(gates, list)
    for gate in gates:
        _require(
            gate.get("status") == "pass" and gate.get("head") == receipt["head"],
            "gate is not passing on head",
        )
        _text(gate.get("command"), "gate command")
    reviews = receipt.get("reviews")
    if not isinstance(reviews, list) or len(reviews) != 2:
        raise ValueError("exactly two reviews are required")
    _require(all(isinstance(review, dict) for review in reviews), "review must be an object")
    identities: set[str] = set()
    invocations: set[str] = set()
    generations = {review.get("generation") for review in reviews}
    _require(
        len(generations) == 1
        and all(isinstance(generation, int) and generation >= 1 for generation in generations),
        "reviews must share one generation",
    )
    if publish_generation is not None:
        _require(
            publish_generation in generations, "publish-only used a different review generation"
        )
    expected = {
        key: receipt[key]
        for key in ("head", "diff_digest", "packet_digest", "tree_digest", "index_digest")
    }
    for review in reviews:
        identity = _text(review.get("identity"), "review identity")
        invocation = _text(review.get("invocation"), "review invocation")
        _require(
            identity not in identities and invocation not in invocations,
            "reviewers must be distinct",
        )
        identities.add(identity)
        invocations.add(invocation)
        _require(review.get("status") == "completed", "every reviewer must complete")
        _require(review.get("read_only") is True, "reviewer was not read-only")
        _require(
            review.get("isolation") in {"disposable-worktree", "context-free"},
            "review isolation is invalid",
        )
        _validate_review_findings(review.get("findings"))
        _require(
            review.get("pre") == expected and review.get("post") == expected,
            "review did not verify the exact packet before and after",
        )


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout


def _path(repo: Path, relative: str) -> Path:
    path = (repo / relative).resolve()
    try:
        path.relative_to(repo.resolve())
    except ValueError as exc:
        raise ValueError("receipt path escapes the repository") from exc
    return path


def _unexpected_status(repo: Path, exclusions: list[str]) -> list[str]:
    raw = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries = [entry for entry in raw.decode().split("\0") if entry]
    return [entry for entry in entries if entry[3:] not in exclusions]


def verify_live(receipt: dict, packet_bytes: bytes, repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    _require(head == receipt["head"], "current HEAD differs from receipt")
    tree = _git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
    _require(tree == receipt["tree_digest"], "current tree differs from receipt")
    index = hashlib.sha256(_git(repo, "ls-files", "--stage", "-z")).hexdigest()
    _require(index == receipt["index_digest"], "current index differs from receipt")
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
    _require(
        hashlib.sha256(packet_bytes).hexdigest() == receipt["packet_digest"],
        "current packet digest differs from receipt",
    )
    unexpected = _unexpected_status(repo, receipt["scratch_exclusions"])
    _require(not unexpected, f"worktree status differs from receipt: {unexpected}")
    if receipt["result"] == "no-change":
        for item in receipt["candidate_manifest"]:
            path = _path(repo, item["path"])
            if item["existed_before"]:
                _require(path.is_file(), f"candidate path missing: {item['path']}")
                _require(
                    hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"],
                    f"candidate path changed: {item['path']}",
                )
            else:
                _require(not path.exists(), f"absent-before candidate remains: {item['path']}")


def _load_object(path: Path, name: str) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    value = json.loads(raw)
    _require(isinstance(value, dict), f"{name} must be a JSON object")
    assert isinstance(value, dict)
    return value, raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path, nargs="?")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            _require(args.receipt is None, "self-test does not accept a receipt")
            refs = Path(__file__).parents[1] / "references"
            receipt, _ = _load_object(refs / "completion.example.json", "receipt")
            packet, _ = _load_object(refs / "packet.example.json", "packet")
            validate(receipt, packet)
            invalid = {**receipt, "reviews": receipt["reviews"][:1]}
            try:
                validate(invalid, packet)
            except ValueError:
                pass
            else:
                raise ValueError("self-test accepted a one-review completion")
            print("assurance receipt self-test: valid")
            return 0
        _require(args.receipt is not None, "receipt path is required")
        assert args.receipt is not None
        receipt, _ = _load_object(args.receipt, "receipt")
        repo = args.repo.resolve()
        packet_path = _path(repo, _text(receipt.get("packet_path"), "packet_path"))
        packet, packet_bytes = _load_object(packet_path, "packet")
        validate(receipt, packet)
        verify_live(receipt, packet_bytes, repo)
    except (OSError, json.JSONDecodeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"assurance-receipt: error: {exc}", file=sys.stderr)
        return 2
    print("assurance receipt: valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())

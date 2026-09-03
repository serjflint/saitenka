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
EMPTY_DIFF_DIGEST = hashlib.sha256(b"").hexdigest()
GATE_COMMANDS = {
    "deterministic": "uv run poe all",
    "free-threaded": "uv run poe test-ft",
}


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: object, name: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{name} is required")
    assert isinstance(value, str)
    return value.strip()


def _canonical_digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _validate_proof_rows(packet: dict, receipt: dict) -> None:
    proofs = packet.get("mechanism_proofs")
    _require(isinstance(proofs, list) and proofs, "packet mechanism_proofs is required")
    assert isinstance(proofs, list)
    for proof in proofs:
        _require(isinstance(proof, dict), "mechanism proof must be an object")
        assert isinstance(proof, dict)
        for key in ("mechanism", "obligation", "evidence"):
            _text(proof.get(key), f"mechanism proof {key}")
        _require(
            proof.get("disposition") in {"proved", "not-applicable"}, "invalid proof disposition"
        )
    evidence = packet.get("validation_evidence")
    _require(isinstance(evidence, list) and evidence, "packet validation_evidence is required")
    assert isinstance(evidence, list)
    for row in evidence:
        _require(isinstance(row, dict), "validation evidence must be an object")
        assert isinstance(row, dict)
        _text(row.get("command"), "validation command")
        _require(row.get("result") == "pass", "validation evidence must pass")
        _require(row.get("head") == receipt["head"], "validation evidence lacks exact head")


def _validate_packet(packet: dict, receipt: dict, result: str) -> datetime:
    _require(packet.get("version") == 2, "packet version must be 2")
    _require(packet.get("result") == result, "packet result differs from receipt")
    for key in ("base", "head", "tree_digest", "diff_digest", "index_digest"):
        _require(packet.get(key) == receipt.get(key), f"packet {key} differs from receipt")
    _require(
        packet.get("entry_state") in {"fresh-inquiry", "accepted-dossier"}, "invalid entry state"
    )
    for key in (
        "supported_scenario",
        "product_invariant",
        "scope_guard",
        "discriminator",
        "final_scenario_trace",
    ):
        _text(packet.get(key), f"packet {key}")
    dossier = packet.get("accepted_dossier")
    _require(isinstance(dossier, dict), "packet accepted_dossier is required")
    assert isinstance(dossier, dict)
    _text(dossier.get("id"), "accepted dossier id")
    _require(dossier.get("base") == receipt["base"], "accepted dossier base differs")
    decision = packet.get("human_decision")
    _require(isinstance(decision, dict), "packet human_decision is required")
    assert isinstance(decision, dict)
    for key in ("decision_id", "decision", "accepted_at"):
        _text(decision.get(key), f"human_decision {key}")
    expected_decision = "no-change" if result == "no-change" else "implement"
    _require(decision.get("decision") == expected_decision, "human decision differs from result")
    accepted_at = datetime.fromisoformat(decision["accepted_at"])
    _require(dossier.get("decision_id") == decision["decision_id"], "dossier decision differs")
    owners = packet.get("affected_owners")
    _require(
        isinstance(owners, list)
        and len(owners) >= 2
        and len({_text(x, "affected owner").casefold() for x in owners}) == len(owners),
        "packet requires at least two distinct affected owners",
    )
    _validate_proof_rows(packet, receipt)
    for key in ("residual_uncertainty", "followups", "falsified_hypotheses"):
        _require(isinstance(packet.get(key), list), f"packet {key} must be a list")
    if result == "no-change":
        _require(
            bool(packet["falsified_hypotheses"]), "no-change packet requires a falsified hypothesis"
        )
        _require(
            packet.get("no_change_baseline") == receipt.get("no_change_baseline"),
            "no-change baseline differs",
        )
        _require(
            packet.get("baseline_digest") == receipt.get("baseline_digest"),
            "no-change baseline digest differs",
        )
    return accepted_at


def _validate_events(receipt: dict, expected_stages: list[str]) -> dict[str, datetime]:
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
    return {event["stage"]: timestamp for event, timestamp in zip(events, times, strict=True)}


def _validate_review_findings(findings: object) -> None:
    _require(isinstance(findings, list), "review findings must be a list")
    assert isinstance(findings, list)
    allowed = {"P2": {"fixed", "human-accepted"}, "P3": {"recorded"}}
    for finding in findings:
        _require(isinstance(finding, dict), "review finding must be an object")
        severity = finding.get("severity")
        _require(severity not in {"P0", "P1"}, "P0/P1 invalidates the review generation")
        _require(isinstance(severity, str) and severity in allowed, "invalid finding severity")
        _require(finding.get("disposition") in allowed[severity], "unresolved review finding")
        _text(finding.get("summary"), "finding summary")
        if severity == "P2" and finding.get("disposition") == "human-accepted":
            acceptance = finding.get("acceptance")
            _require(isinstance(acceptance, dict), "P2 human acceptance is required")
            assert isinstance(acceptance, dict)
            for key in ("identity", "decision_id", "accepted_at", "evidence"):
                _text(acceptance.get(key), f"P2 acceptance {key}")
            datetime.fromisoformat(acceptance["accepted_at"])


def _validate_identity(receipt: dict) -> None:
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


def _validate_no_change(receipt: dict) -> dict[str, datetime]:
    events = _validate_events(receipt, NO_CHANGE_STAGES)
    _require(receipt.get("disposition") == "no-change", "invalid no-change disposition")
    _require(receipt["base"] == receipt["head"], "no-change base and head differ")
    baseline = receipt.get("no_change_baseline")
    _require(isinstance(baseline, dict), "no-change baseline is required")
    assert isinstance(baseline, dict)
    baseline_digest = receipt.get("baseline_digest")
    _require(
        bool(HEX.fullmatch(str(baseline_digest)))
        and baseline_digest == _canonical_digest(baseline),
        "no-change baseline digest is invalid",
    )
    _require(
        receipt["events"][0].get("evidence") == f"baseline:{baseline_digest}",
        "freeze event does not bind the no-change baseline",
    )
    for key in ("base", "head", "tree_digest", "index_digest"):
        _require(baseline.get(key) == receipt[key], f"no-change baseline {key} differs")
    _require(
        baseline.get("scratch_exclusions") == receipt["scratch_exclusions"],
        "baseline exclusions differ",
    )
    _require(
        isinstance(baseline.get("status_entries"), list), "baseline status entries are required"
    )
    manifest = baseline.get("candidate_manifest")
    _require(isinstance(manifest, list) and manifest, "no-change candidate manifest is required")
    assert isinstance(manifest, list)
    _require(
        baseline.get("touched_paths")
        == [item.get("path") for item in manifest if isinstance(item, dict)],
        "no-change touched paths differ from candidate manifest",
    )
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
    return events


def _timestamp(row: dict, name: str) -> datetime:
    return datetime.fromisoformat(_text(row.get("completed_at"), f"{name} completed_at"))


def _validate_contribution(
    receipt: dict, disposition: str, events: dict[str, datetime]
) -> int | None:
    contribution = receipt.get("contribution")
    _require(isinstance(contribution, dict), "contribution evidence is required")
    assert isinstance(contribution, dict)
    prepare = contribution.get("prepare_only")
    _require(isinstance(prepare, dict), "prepare-only was not completed on head")
    assert isinstance(prepare, dict)
    _require(
        prepare.get("status") == "completed" and prepare.get("head") == receipt["head"],
        "prepare-only was not completed on head",
    )
    prepare_at = _timestamp(prepare, "prepare-only")
    _require(
        events["gate"] <= prepare_at <= events["freeze-packet"],
        "prepare-only chronology is invalid",
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
        published_at = _timestamp(publish, "publish-only")
        _require(
            events["review"] <= published_at <= events["publish"],
            "publish-only chronology is invalid",
        )
        publish_generation = publish.get("review_generation")
        _require(
            isinstance(publish_generation, int) and publish_generation >= 1,
            "publish-only lacks review generation",
        )
        _require(receipt.get("publication_verified") is True, "publication was not verified")
    else:
        _require(publish is None, "unpublished receipt cannot claim publish-only completion")
    return publish_generation


def _validate_gates(receipt: dict, events: dict[str, datetime]) -> None:
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
        _require(
            gate.get("command") == GATE_COMMANDS[gate["name"]], "gate command is not canonical"
        )
        _require(
            bool(HEX.fullmatch(str(gate.get("evidence_digest", "")))),
            "gate evidence digest is required",
        )
        completed_at = _timestamp(gate, "gate")
        _require(
            events["re-enfold"] <= completed_at <= events["gate"],
            "gate chronology is invalid",
        )


def _validate_review_attempts(receipt: dict) -> list[dict]:
    reviews = receipt.get("reviews")
    _require(isinstance(reviews, list) and reviews, "review attempts are required")
    assert isinstance(reviews, list)
    _require(all(isinstance(review, dict) for review in reviews), "review must be an object")
    review_times = receipt.get("review_completed_at")
    _require(isinstance(review_times, dict), "review completion times are required")
    assert isinstance(review_times, dict)
    invocations: set[str] = set()
    for review in reviews:
        _text(review.get("identity"), "review identity")
        invocation = _text(review.get("invocation"), "review invocation")
        _text(review_times.get(invocation), "review completed_at")
        _require(invocation.casefold() not in invocations, "review invocations must be unique")
        invocations.add(invocation.casefold())
        if review.get("status") in {"failed", "canceled"}:
            _text(review.get("terminal_reason"), "review terminal reason")
    return reviews


def _latest_review_pair(reviews: list[dict], publish_generation: int | None) -> list[dict]:
    raw_generations = {review.get("generation") for review in reviews}
    _require(
        all(isinstance(generation, int) and generation >= 1 for generation in raw_generations),
        "invalid review generation",
    )
    generations = {generation for generation in raw_generations if isinstance(generation, int)}
    _require(
        all(review.get("status") in {"completed", "failed", "canceled"} for review in reviews),
        "review attempt is unfinished",
    )
    latest = max(generations)
    completed_latest = [
        review
        for review in reviews
        if review.get("generation") == latest and review.get("status") == "completed"
    ]
    _require(len(completed_latest) == 2, "latest generation requires exactly two reviews")
    _require(
        all(
            review.get("status") == "completed"
            for review in reviews
            if review.get("generation") == latest
        ),
        "latest review generation did not complete",
    )
    if publish_generation is not None:
        _require(publish_generation == latest, "publish-only used a different review generation")
    return completed_latest


def _validate_completed_review(
    review: dict, receipt: dict, events: dict[str, datetime]
) -> tuple[str, str]:
    identity = _text(review.get("identity"), "review identity").casefold()
    invocation = _text(review.get("invocation"), "review invocation").casefold()
    _require(review.get("read_only") is True, "reviewer was not read-only")
    _require(
        review.get("isolation") in {"disposable-worktree", "context-free"},
        "review isolation is invalid",
    )
    _validate_review_findings(review.get("findings"))
    review_times = receipt.get("review_completed_at")
    _require(isinstance(review_times, dict), "review completion times are required")
    assert isinstance(review_times, dict)
    completed_at = datetime.fromisoformat(
        _text(review_times.get(review["invocation"].strip()), "review completed_at")
    )
    _require(
        events["freeze-packet"] <= completed_at <= events["review"],
        "review chronology is invalid",
    )
    expected = {
        key: receipt[key]
        for key in ("head", "diff_digest", "packet_digest", "tree_digest", "index_digest")
    }
    _require(
        review.get("pre") == expected and review.get("post") == expected,
        "review did not verify the exact packet before and after",
    )
    return identity, invocation


def _validate_reviews(
    receipt: dict, publish_generation: int | None, events: dict[str, datetime]
) -> None:
    completed_latest = _latest_review_pair(_validate_review_attempts(receipt), publish_generation)
    identities: set[str] = set()
    invocations: set[str] = set()
    for review in completed_latest:
        identity, invocation = _validate_completed_review(review, receipt, events)
        _require(
            identity not in identities and invocation not in invocations,
            "reviewers must be distinct",
        )
        identities.add(identity)
        invocations.add(invocation)


def validate(receipt: dict, packet: dict) -> None:
    _require(receipt.get("version") == 3, "receipt version must be 3")
    result = receipt.get("result")
    _require(result in {"artifact", "no-change"}, "result must be artifact or no-change")
    assert isinstance(result, str)
    _validate_identity(receipt)
    accepted_at = _validate_packet(packet, receipt, result)
    if result == "no-change":
        events = _validate_no_change(receipt)
        _require(
            events["inquire"] <= accepted_at <= events["route"],
            "human decision chronology is invalid",
        )
        return
    disposition = receipt.get("disposition")
    _require(
        disposition in {"local-proof", "ready-pr", "published"}, "invalid artifact disposition"
    )
    assert isinstance(disposition, str)
    _require(receipt["base"] != receipt["head"], "artifact base and head must differ")
    _require(receipt["diff_digest"] != EMPTY_DIFF_DIGEST, "artifact diff must be non-empty")
    events = _validate_events(
        receipt, [*ARTIFACT_STAGES, *(["publish"] if disposition == "published" else [])]
    )
    _require(
        events["inquire"] <= accepted_at <= events["route"], "human decision chronology is invalid"
    )
    publish_generation = _validate_contribution(receipt, disposition, events)
    _validate_gates(receipt, events)
    _validate_reviews(receipt, publish_generation, events)


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
        _require(
            unexpected == receipt["no_change_baseline"]["status_entries"],
            "current status differs from no-change baseline",
        )
        for item in receipt["no_change_baseline"]["candidate_manifest"]:
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

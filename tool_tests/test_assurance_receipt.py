from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
REFS = ROOT / ".agents/skills/assurance-pipeline/references"
SCRIPT = ROOT / ".agents/skills/assurance-pipeline/scripts/verify_receipt.py"
SPEC = importlib.util.spec_from_file_location("assurance_verify", SCRIPT)
assert SPEC and SPEC.loader
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def _examples() -> tuple[dict, dict]:
    receipt = json.loads((REFS / "completion.example.json").read_text(encoding="utf-8"))
    packet = json.loads((REFS / "packet.example.json").read_text(encoding="utf-8"))
    return receipt, packet


def test_valid_exact_packet_receipt_passes():
    verify.validate(*_examples())


def test_live_verification_recomputes_every_current_artifact(tmp_path, monkeypatch):
    receipt, _packet = _examples()
    packet_bytes = b"packet"
    receipt["packet_digest"] = hashlib.sha256(packet_bytes).hexdigest()
    receipt["diff_digest"] = hashlib.sha256(b"diff").hexdigest()
    receipt["tree_digest"] = "a" * 40
    receipt["index_digest"] = hashlib.sha256(b"index").hexdigest()

    def fake_git(_repo, *args):
        if args == ("rev-parse", "HEAD"):
            return f"{receipt['head']}\n".encode()
        if args == ("rev-parse", "HEAD^{tree}"):
            return b"a" * 40 + b"\n"
        if args[:2] == ("ls-files", "--stage"):
            return b"index"
        if "--binary" in args:
            return b"diff"
        return b""

    monkeypatch.setattr(verify, "_git", fake_git)
    verify.verify_live(receipt, packet_bytes, tmp_path)
    receipt["tree_digest"] = "b" * 40
    with pytest.raises(ValueError, match="tree"):
        verify.verify_live(receipt, packet_bytes, tmp_path)


def test_live_verification_rejects_untracked_non_scratch_path(tmp_path, monkeypatch):
    receipt, _packet = _examples()
    receipt["tree_digest"] = "a" * 40
    receipt["index_digest"] = hashlib.sha256(b"index").hexdigest()
    receipt["diff_digest"] = hashlib.sha256(b"diff").hexdigest()
    receipt["packet_digest"] = hashlib.sha256(b"packet").hexdigest()

    def fake_git(_repo, *args):
        if args == ("rev-parse", "HEAD"):
            return f"{receipt['head']}\n".encode()
        if args == ("rev-parse", "HEAD^{tree}"):
            return b"a" * 40 + b"\n"
        if args[:2] == ("ls-files", "--stage"):
            return b"index"
        if "--binary" in args:
            return b"diff"
        if args and args[0] == "status":
            return b"?? src/saitenka/rogue.py\0"
        return b""

    monkeypatch.setattr(verify, "_git", fake_git)
    with pytest.raises(ValueError, match="status"):
        verify.verify_live(receipt, b"packet", tmp_path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r, _p: r["events"].reverse(), "stage order"),
        (lambda r, _p: r["reviews"].pop(), "exactly two reviews"),
        (lambda r, _p: r["reviews"][1].update(identity="reviewer-a"), "distinct"),
        (lambda r, _p: r["reviews"][0].update(read_only=False), "read-only"),
        (lambda r, _p: r["reviews"][1]["post"].update(head="0" * 40), "exact packet"),
        (lambda r, _p: r["contribution"].update(prepare_only=None), "prepare-only"),
        (lambda r, _p: r["gates"][0].update(command="x"), "canonical"),
        (lambda _r, p: p.update(human_decision={}), "decision_id"),
        (lambda _r, p: p.update(mechanism_proofs=[]), "mechanism_proofs"),
        (lambda _r, p: p.update(mechanism_proofs=[None]), "object"),
        (lambda r, p: p.update(head=r["base"]), "packet head differs"),
        (lambda r, _p: r["reviews"][1].update(identity=" reviewer-a "), "distinct"),
        (lambda r, _p: r["reviews"].append({"generation": 2, "status": "running"}), "identity"),
    ],
)
def test_counterexamples_fail_closed(mutate, message):
    receipt, packet = copy.deepcopy(_examples())
    mutate(receipt, packet)
    with pytest.raises(ValueError, match=message):
        verify.validate(receipt, packet)


def test_no_change_requires_falsification_manifest_and_restored_index():
    receipt, packet = _examples()
    manifest = [{"path": "vibe/candidate", "existed_before": False, "sha256": None}]
    baseline = {
        "base": receipt["base"],
        "head": receipt["base"],
        "tree_digest": receipt["tree_digest"],
        "index_digest": receipt["index_digest"],
        "scratch_exclusions": [],
        "status_entries": [],
        "touched_paths": ["vibe/candidate"],
        "candidate_manifest": manifest,
    }
    receipt.update(
        result="no-change",
        disposition="no-change",
        head=receipt["base"],
        events=[
            {"stage": stage, "completed_at": f"2026-09-03T10:0{index}:00Z", "evidence": stage}
            for index, stage in enumerate(verify.NO_CHANGE_STAGES)
        ],
        no_change_baseline=baseline,
    )
    baseline_digest = verify._canonical_digest(baseline)
    receipt["baseline_digest"] = baseline_digest
    receipt["events"][0]["evidence"] = f"baseline:{baseline_digest}"
    packet.update(
        result="no-change",
        head=receipt["head"],
        accepted_dossier={**packet["accepted_dossier"], "base": receipt["base"]},
        human_decision={**packet["human_decision"], "decision": "no-change"},
        validation_evidence=[{**packet["validation_evidence"][0], "head": receipt["head"]}],
        falsified_hypotheses=["ordering premise was false"],
        no_change_baseline=baseline,
        baseline_digest=baseline_digest,
    )
    verify.validate(receipt, packet)
    empty_baseline = {**baseline, "candidate_manifest": []}
    receipt["no_change_baseline"] = empty_baseline
    packet["no_change_baseline"] = empty_baseline
    empty_digest = verify._canonical_digest(empty_baseline)
    receipt["baseline_digest"] = empty_digest
    packet["baseline_digest"] = empty_digest
    receipt["events"][0]["evidence"] = f"baseline:{empty_digest}"
    with pytest.raises(ValueError, match="manifest"):
        verify.validate(receipt, packet)


def test_human_decision_and_review_chronology_are_enforced():
    receipt, packet = copy.deepcopy(_examples())
    packet["human_decision"]["accepted_at"] = "2099-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="human decision chronology"):
        verify.validate(receipt, packet)

    receipt, packet = copy.deepcopy(_examples())
    receipt["review_completed_at"]["review-b"] = "2026-09-03T10:04:00Z"
    with pytest.raises(ValueError, match="review chronology"):
        verify.validate(receipt, packet)


def test_terminal_failed_older_review_generation_is_recorded_but_does_not_count():
    receipt, packet = copy.deepcopy(_examples())
    for review in receipt["reviews"]:
        review["generation"] = 2
    receipt["reviews"].insert(
        0,
        {
            "identity": "old",
            "invocation": "old-review",
            "generation": 1,
            "status": "failed",
            "terminal_reason": "timeout",
        },
    )
    receipt["review_completed_at"]["old-review"] = "2026-09-03T10:05:15Z"
    verify.validate(receipt, packet)


def test_failed_attempt_in_latest_review_generation_invalidates_it():
    receipt, packet = copy.deepcopy(_examples())
    receipt["reviews"].append(
        {
            "identity": "third",
            "invocation": "third-review",
            "generation": 1,
            "status": "failed",
            "terminal_reason": "crash",
        }
    )
    receipt["review_completed_at"]["third-review"] = "2026-09-03T10:05:50Z"
    with pytest.raises(ValueError, match="latest review generation"):
        verify.validate(receipt, packet)


def test_artifact_requires_a_nonempty_revision_diff():
    receipt, packet = copy.deepcopy(_examples())
    receipt["base"] = receipt["head"]
    receipt["diff_digest"] = verify.EMPTY_DIFF_DIGEST
    packet["base"] = receipt["head"]
    packet["diff_digest"] = verify.EMPTY_DIFF_DIGEST
    packet["accepted_dossier"]["base"] = receipt["head"]
    for review in receipt["reviews"]:
        for snapshot in (review["pre"], review["post"]):
            snapshot["diff_digest"] = verify.EMPTY_DIFF_DIGEST
    with pytest.raises(ValueError, match="must differ"):
        verify.validate(receipt, packet)


def test_latest_p1_invalidates_review_generation():
    receipt, packet = copy.deepcopy(_examples())
    receipt["reviews"][0]["findings"] = [
        {"severity": "P1", "disposition": "fixed", "summary": "artifact is wrong"}
    ]
    with pytest.raises(ValueError, match="invalidates"):
        verify.validate(receipt, packet)


def test_human_accepted_p2_requires_decision_provenance():
    receipt, packet = copy.deepcopy(_examples())
    receipt["reviews"][0]["findings"] = [
        {"severity": "P2", "disposition": "human-accepted", "summary": "risk"}
    ]
    with pytest.raises(ValueError, match="acceptance"):
        verify.validate(receipt, packet)

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
        (lambda r, _p: r["reviews"].pop(), "two reviews"),
        (lambda r, _p: r["reviews"][1].update(identity="reviewer-a"), "distinct"),
        (lambda r, _p: r["reviews"][0].update(read_only=False), "read-only"),
        (lambda r, _p: r["reviews"][1]["post"].update(head="0" * 40), "exact packet"),
        (lambda r, _p: r["contribution"].update(prepare_only=None), "prepare-only"),
        (lambda _r, p: p.update(human_decision={}), "decision_id"),
        (lambda _r, p: p.update(mechanism_proofs=[]), "mechanism_proofs"),
    ],
)
def test_counterexamples_fail_closed(mutate, message):
    receipt, packet = copy.deepcopy(_examples())
    mutate(receipt, packet)
    with pytest.raises(ValueError, match=message):
        verify.validate(receipt, packet)


def test_no_change_requires_falsification_manifest_and_restored_index():
    receipt, packet = _examples()
    receipt.update(
        result="no-change",
        disposition="no-change",
        head=receipt["base"],
        events=[
            {"stage": stage, "completed_at": f"2026-09-03T10:0{index}:00Z", "evidence": stage}
            for index, stage in enumerate(verify.NO_CHANGE_STAGES)
        ],
        baseline_index_digest=receipt["index_digest"],
        candidate_manifest=[{"path": "vibe/candidate", "existed_before": False, "sha256": None}],
    )
    packet.update(result="no-change", falsified_hypotheses=["ordering premise was false"])
    verify.validate(receipt, packet)
    receipt["candidate_manifest"] = []
    with pytest.raises(ValueError, match="manifest"):
        verify.validate(receipt, packet)

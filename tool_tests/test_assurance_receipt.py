from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / ".agents/skills/assurance-pipeline/scripts/verify_receipt.py"
SPEC = importlib.util.spec_from_file_location("assurance_verify", SCRIPT)
assert SPEC and SPEC.loader
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def _valid() -> dict:
    import json

    path = ROOT / ".agents/skills/assurance-pipeline/references/completion.example.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_valid_exact_packet_receipt_passes():
    verify.validate(_valid())


def test_live_verification_recomputes_head_diff_packet_and_cleanliness(tmp_path, monkeypatch):
    receipt = _valid()
    packet = tmp_path / "packet.json"
    packet.write_bytes(b"packet")
    receipt["packet_path"] = packet.name
    receipt["packet_digest"] = hashlib.sha256(b"packet").hexdigest()
    receipt["diff_digest"] = hashlib.sha256(b"diff").hexdigest()

    def fake_git(_repo, *args):
        if args[:2] == ("rev-parse", "HEAD"):
            return f"{receipt['head']}\n".encode()
        if "--binary" in args:
            return b"diff"
        return b""

    monkeypatch.setattr(verify, "_git", fake_git)
    verify.verify_live(receipt, tmp_path)

    receipt["diff_digest"] = "0" * 64
    with pytest.raises(ValueError, match="diff digest"):
        verify.verify_live(receipt, tmp_path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r["stages"].reverse(), "stage order"),
        (lambda r: r["reviews"].pop(), "two reviews"),
        (lambda r: r["reviews"][0].update(read_only=False), "read-only"),
        (lambda r: r["reviews"][1]["post"].update(head="0" * 40), "exact packet"),
        (lambda r: r["contribution"].update(prepare_only_completed=False), "prepare-only"),
    ],
)
def test_counterexamples_fail_closed(mutate, message):
    receipt = copy.deepcopy(_valid())
    mutate(receipt)
    with pytest.raises(ValueError, match=message):
        verify.validate(receipt)

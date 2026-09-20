from hashlib import sha256
import json
from pathlib import Path

from tallyguard.audit import canonical_json
from tallyguard.release_audit import (
    _acceptance_check,
    _git_commit_check,
    _mainnet_acceptance_check,
    _pilot_check,
    _url_check,
)


def test_release_audit_binds_the_current_git_commit():
    check = _git_commit_check(Path(__file__).resolve().parents[1])

    assert check.status == "passed"
    assert len(check.detail) == 40
    assert check.evidence_sha256 == sha256(check.detail.encode("ascii")).hexdigest()


def test_external_url_check_distinguishes_pending_invalid_and_public_urls():
    assert _url_check("Live", None).status == "pending"
    assert _url_check("Live", "http://localhost:8000").status == "failed"
    assert _url_check("Live", "https://demo.example.com").status == "passed"


def test_acceptance_check_requires_capped_arc_testnet_proof(tmp_path):
    artifact = tmp_path / "acceptance.json"
    artifact.write_text(
        json.dumps(
            {
                "network": "ARC-TESTNET",
                "amount_usdc": "0.01",
                "decision_action": "PAY",
                "audit_chain_valid": True,
                "receipt": {
                    "transaction_hash": "0x" + "a" * 64,
                    "explorer_url": "https://explorer.testnet.arc.io/tx/0x" + "a" * 64,
                },
                "idempotent_replay": {"same_receipt": True},
            }
        ),
        encoding="utf-8",
    )

    assert _acceptance_check(str(artifact)).status == "passed"

    report = json.loads(artifact.read_text(encoding="utf-8"))
    report["amount_usdc"] = "0.11"
    artifact.write_text(json.dumps(report), encoding="utf-8")
    assert _acceptance_check(str(artifact)).status == "failed"


def test_mainnet_acceptance_check_rejects_synthetic_and_validates_approval_binding(
    tmp_path,
):
    artifact = tmp_path / "mainnet-acceptance.json"
    approval_id = "approval_123"
    report = {
        "classification": "controlled-live-mainnet-proof",
        "network": "ARC-MAINNET",
        "amount_usdc": "0.01",
        "decision_action": "PAY",
        "audit_chain_valid": True,
        "approval": {
            "id": approval_id,
            "status": "APPROVED",
            "requested_by_user_id": "operator",
            "resolved_by_user_id": "approver",
        },
        "intent": {
            "approval_reference": approval_id,
            "network": "ARC-MAINNET",
            "amount_usdc": "0.01",
        },
        "receipt": {
            "provider": "circle-developer-wallets+arc-rpc",
            "network": "ARC-MAINNET",
            "status": "CONFIRMED",
            "transaction_hash": "0x" + "b" * 64,
            "explorer_url": "https://explorer.arc.io/tx/0x" + "b" * 64,
        },
        "idempotent_replay": {
            "same_receipt": True,
            "second_request_reused_receipt": True,
        },
    }
    artifact.write_text(json.dumps(report), encoding="utf-8")

    assert _mainnet_acceptance_check(str(artifact)).status == "passed"

    report["classification"] = "synthetic-mainnet-path-test"
    artifact.write_text(json.dumps(report), encoding="utf-8")
    assert _mainnet_acceptance_check(str(artifact)).status == "failed"

    report["classification"] = "controlled-live-mainnet-proof"
    report["intent"]["approval_reference"] = "approval_other"
    artifact.write_text(json.dumps(report), encoding="utf-8")
    assert _mainnet_acceptance_check(str(artifact)).status == "failed"


def test_pilot_check_verifies_content_address_and_claim_boundaries(tmp_path):
    report = {
        "usage_measurement": {"source": "operator attestation"},
        "product_evidence": {"packet_verified": True},
        "attestation": {"reporting_consent": True},
    }
    envelope = {
        "report": report,
        "report_sha256": sha256(canonical_json(report).encode("utf-8")).hexdigest(),
    }
    path = tmp_path / "pilot.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    assert _pilot_check(str(path)).status == "passed"

    envelope["report"]["product_evidence"]["packet_verified"] = False
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert _pilot_check(str(path)).status == "failed"

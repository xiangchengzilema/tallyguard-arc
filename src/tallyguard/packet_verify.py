"""Standalone verification for exported TallyGuard evidence packets.

The verifier needs no database and no TallyGuard server.  It recomputes the
packet content address and deterministic policy decision from the sealed
snapshot.  For a real Circle-backed receipt it can additionally ask Arc RPC
to prove the exact canonical-USDC transfer.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .audit import audit_event_hash, canonical_json
from .circle_arc import ArcRpcClient
from .decisions import DecisionReplayInputs, DecisionService
from .network import ArcNetworkConfig
from .policies import policy_content_hash
from .policy import PolicyEngine


@dataclass(frozen=True, slots=True)
class PacketCheck:
    code: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class PacketVerification:
    packet_id: str
    verified: bool
    arc_rpc_requested: bool
    checks: tuple[PacketCheck, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "verified": self.verified,
            "arc_rpc_requested": self.arc_rpc_requested,
            "checks": [asdict(check) for check in self.checks],
        }


def verify_evidence_packet(
    envelope: dict[str, Any],
    *,
    verify_arc: bool = False,
    arc_rpc: ArcRpcClient | None = None,
) -> PacketVerification:
    """Verify one exported invoice evidence-packet envelope."""

    checks: list[PacketCheck] = []
    packet_value = envelope.get("packet")
    packet = packet_value if isinstance(packet_value, dict) else {}
    packet_id = str(envelope.get("packet_id", ""))

    computed_packet_hash = sha256(canonical_json(packet).encode("utf-8")).hexdigest()
    recorded_packet_hash = str(envelope.get("packet_sha256", ""))
    _check(
        checks,
        "PACKET_CONTENT_HASH",
        recorded_packet_hash == computed_packet_hash,
        "Packet SHA-256 matches canonical packet JSON.",
    )
    _check(
        checks,
        "PACKET_ID",
        packet_id == f"packet_{computed_packet_hash[:24]}",
        "Packet ID is derived from the canonical content hash.",
    )
    _check(
        checks,
        "SCHEMA_VERSION",
        packet.get("schema_version") == "1.0",
        "Supported evidence-packet schema is 1.0.",
    )

    decision_value = packet.get("decision")
    decision = decision_value if isinstance(decision_value, dict) else {}
    inputs_value = decision.get("sealed_replay_inputs")
    inputs: DecisionReplayInputs | None = None
    try:
        if not isinstance(inputs_value, dict):
            raise ValueError("sealed replay inputs are missing")
        inputs = DecisionReplayInputs.from_payload(inputs_value)
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        _check(checks, "SEALED_INPUTS", False, f"Cannot decode sealed replay inputs: {exc}.")
    else:
        _check(checks, "SEALED_INPUTS", True, "Sealed replay inputs decode successfully.")

    if inputs is not None:
        _verify_decision(checks, packet=packet, decision=decision, inputs=inputs)
        _verify_payment(checks, packet=packet, inputs=inputs)
        _verify_audit_subset(checks, packet=packet)
        if verify_arc:
            _verify_arc_transfer(checks, packet=packet, inputs=inputs, arc_rpc=arc_rpc)
    elif verify_arc:
        _check(checks, "ARC_TRANSFER", False, "Arc verification requires valid sealed inputs.")

    return PacketVerification(
        packet_id=packet_id,
        verified=bool(checks) and all(check.passed for check in checks),
        arc_rpc_requested=verify_arc,
        checks=tuple(checks),
    )


def _verify_decision(
    checks: list[PacketCheck],
    *,
    packet: dict[str, Any],
    decision: dict[str, Any],
    inputs: DecisionReplayInputs,
) -> None:
    snapshot_hash = inputs.content_hash
    _check(
        checks,
        "REPLAY_INPUT_HASH",
        decision.get("replay_input_hash") == snapshot_hash,
        "The replay snapshot hash matches the sealed inputs.",
    )
    _check(
        checks,
        "ORGANIZATION_BINDING",
        packet.get("organization_id") == inputs.evidence.invoice.organization_id
        and decision.get("organization_id") == inputs.evidence.invoice.organization_id,
        "Packet, decision, and invoice belong to one organization.",
    )
    _check(
        checks,
        "INVOICE_BINDING",
        decision.get("invoice_id") == inputs.evidence.invoice.id
        and _nested(packet, "invoice", "id") == inputs.evidence.invoice.id,
        "Packet and decision reference the sealed invoice.",
    )
    _check(
        checks,
        "EVIDENCE_MANIFEST",
        decision.get("evidence_manifest_hash") == inputs.evidence.manifest_hash,
        "Decision is bound to the sealed evidence manifest.",
    )

    computed_policy_hash = policy_content_hash(inputs.policy)
    _check(
        checks,
        "POLICY_CONTENT_HASH",
        decision.get("policy_content_hash") == computed_policy_hash
        and decision.get("policy_version") == inputs.policy.version,
        "Policy version and content hash match the sealed policy.",
    )

    replayed = PolicyEngine().evaluate(
        invoice=inputs.evidence.invoice,
        vendor=inputs.vendor,
        purchase_order=inputs.evidence.purchase_order,
        delivery=inputs.evidence.delivery,
        treasury=inputs.treasury,
        policy=inputs.policy,
        known_invoice_fingerprints=inputs.known_invoice_fingerprints,
        asset=inputs.asset,
        network=inputs.network,
        evaluation_date=inputs.evaluation_date,
        vendor_wallet_event_type=inputs.vendor_wallet_event_type,
        vendor_wallet_verified_date=inputs.vendor_wallet_verified_date,
    )
    recorded_rules = decision.get("rules")
    replayed_rules = [
        {
            "code": result.code,
            "disposition": result.disposition.value,
            "message": result.message,
            "remediation": result.remediation,
        }
        for result in replayed.rule_results
    ]
    _check(
        checks,
        "DETERMINISTIC_RULE_TRACE",
        recorded_rules == replayed_rules,
        "Every recorded deterministic rule matches a fresh replay.",
    )
    _check(
        checks,
        "DECISION_ACTION",
        decision.get("final_action") == replayed.action.value
        and decision.get("reason_codes") == list(replayed.reason_codes),
        "Final action and failure reasons match the replayed rules.",
    )
    replayed_id = DecisionService._decision_id(
        organization_id=inputs.evidence.invoice.organization_id,
        invoice_id=inputs.evidence.invoice.id,
        evidence_manifest_hash=inputs.evidence.manifest_hash,
        policy_content_hash_value=computed_policy_hash,
        replay_input_hash=snapshot_hash,
        decision=replayed,
    )
    _check(
        checks,
        "DECISION_ID",
        decision.get("id") == replayed_id,
        "Decision ID is derived from the replayed evidence, policy, and rule trace.",
    )


def _verify_payment(
    checks: list[PacketCheck],
    *,
    packet: dict[str, Any],
    inputs: DecisionReplayInputs,
) -> None:
    payment_value = packet.get("payment")
    if payment_value is None:
        _check(checks, "PAYMENT_BINDING", True, "Packet contains no settlement receipt.")
        return
    if not isinstance(payment_value, dict):
        _check(checks, "PAYMENT_BINDING", False, "Payment section is malformed.")
        return
    intent_value = payment_value.get("intent")
    receipt_value = payment_value.get("receipt")
    intent = intent_value if isinstance(intent_value, dict) else {}
    receipt = receipt_value if isinstance(receipt_value, dict) else {}
    invoice = inputs.evidence.invoice
    amount = format(invoice.amount, "f")
    bound = (
        intent.get("organization_id") == invoice.organization_id
        and intent.get("invoice_id") == invoice.id
        and intent.get("decision_id") == _nested(packet, "decision", "id")
        and str(intent.get("recipient", "")).lower() == invoice.payment_wallet_address.lower()
        and intent.get("amount_usdc") == amount
        and intent.get("network") == inputs.network
        and str(receipt.get("confirmed_recipient", "")).lower()
        == invoice.payment_wallet_address.lower()
        and receipt.get("confirmed_amount_usdc") == amount
        and receipt.get("network") == inputs.network
        and receipt.get("status") == "CONFIRMED"
        and isinstance(receipt.get("block_number"), int)
        and int(receipt.get("block_number", 0)) > 0
        and _is_transaction_hash(receipt.get("transaction_hash"))
    )
    _check(
        checks,
        "PAYMENT_BINDING",
        bound,
        "Intent and confirmed receipt match the sealed invoice amount, recipient, and network.",
    )
    if bound:
        config = ArcNetworkConfig.for_network(inputs.network)
        expected_url = f"{config.explorer_url.rstrip('/')}/tx/{receipt['transaction_hash']}"
        _check(
            checks,
            "EXPLORER_LINK",
            receipt.get("explorer_url") == expected_url,
            "Explorer URL is derived from the configured Arc network and transaction hash.",
        )


def _verify_audit_subset(checks: list[PacketCheck], *, packet: dict[str, Any]) -> None:
    audit_value = packet.get("audit")
    audit = audit_value if isinstance(audit_value, dict) else {}
    events_value = audit.get("invoice_events")
    events = events_value if isinstance(events_value, list) else []
    valid = bool(events)
    previous_sequence = -1
    previous_event: dict[str, Any] | None = None
    for event_value in events:
        if not isinstance(event_value, dict):
            valid = False
            continue
        try:
            created_at = datetime.fromisoformat(str(event_value["created_at"]))
            expected = audit_event_hash(
                sequence=int(event_value["sequence"]),
                aggregate_type=str(event_value["aggregate_type"]),
                aggregate_id=str(event_value["aggregate_id"]),
                event_type=str(event_value["event_type"]),
                payload=dict(event_value["payload"]),
                previous_hash=str(event_value["previous_hash"]),
                created_at=created_at,
            )
            sequence = int(event_value["sequence"])
        except (KeyError, TypeError, ValueError):
            valid = False
            continue
        if expected != event_value.get("event_hash") or sequence <= previous_sequence:
            valid = False
        if previous_event is not None and sequence == previous_sequence + 1:
            if event_value.get("previous_hash") != previous_event.get("event_hash"):
                valid = False
        previous_sequence = sequence
        previous_event = event_value
    if events:
        valid = valid and audit.get("last_invoice_event_hash") == events[-1].get("event_hash")
    _check(
        checks,
        "AUDIT_EVENT_HASHES",
        valid,
        "Included invoice events retain valid hashes and sequence ordering.",
    )
    _check(
        checks,
        "SERVER_CHAIN_ATTESTATION",
        audit.get("tenant_chain_valid") is True,
        "Exporting server attested that the complete tenant audit chain was valid.",
    )


def _verify_arc_transfer(
    checks: list[PacketCheck],
    *,
    packet: dict[str, Any],
    inputs: DecisionReplayInputs,
    arc_rpc: ArcRpcClient | None,
) -> None:
    payment_value = packet.get("payment")
    payment = payment_value if isinstance(payment_value, dict) else {}
    receipt_value = payment.get("receipt")
    receipt = receipt_value if isinstance(receipt_value, dict) else {}
    if not receipt:
        _check(checks, "ARC_TRANSFER", False, "No receipt is available for Arc verification.")
        return
    client = arc_rpc or ArcRpcClient(config=ArcNetworkConfig.for_network(inputs.network))
    try:
        proof = client.confirm_usdc_transfer(
            transaction_hash=str(receipt.get("transaction_hash", "")),
            recipient=inputs.evidence.invoice.payment_wallet_address,
            amount_usdc=inputs.evidence.invoice.amount,
        )
    except Exception as exc:
        _check(checks, "ARC_TRANSFER", False, f"Arc RPC verification failed: {exc}")
        return
    _check(
        checks,
        "ARC_TRANSFER",
        proof.block_number == receipt.get("block_number"),
        "Arc RPC independently confirmed the exact canonical-USDC transfer and block.",
    )


def _nested(value: dict[str, Any], first: str, second: str) -> Any:
    child = value.get(first)
    return child.get(second) if isinstance(child, dict) else None


def _is_transaction_hash(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 66 or not value.startswith("0x"):
        return False
    try:
        int(value[2:], 16)
    except ValueError:
        return False
    return True


def _check(checks: list[PacketCheck], code: str, passed: bool, detail: str) -> None:
    checks.append(PacketCheck(code=code, passed=passed, detail=detail))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a TallyGuard Payment Evidence Packet without a TallyGuard server"
    )
    parser.add_argument("packet", type=Path, help="exported evidence-packet JSON file")
    parser.add_argument(
        "--verify-arc",
        action="store_true",
        help="also query Arc RPC and prove the exact canonical-USDC transfer",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = parser.parse_args()

    try:
        envelope = json.loads(args.packet.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read evidence packet: {exc}") from exc
    if not isinstance(envelope, dict):
        raise SystemExit("Evidence packet root must be a JSON object.")

    result = verify_evidence_packet(envelope, verify_arc=args.verify_arc)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"TallyGuard packet: {result.packet_id or '<missing>'}")
        for check in result.checks:
            print(f"[{'PASS' if check.passed else 'FAIL'}] {check.code}: {check.detail}")
        print(f"Verdict: {'VERIFIED' if result.verified else 'INVALID'}")
    raise SystemExit(0 if result.verified else 1)


if __name__ == "__main__":
    main()

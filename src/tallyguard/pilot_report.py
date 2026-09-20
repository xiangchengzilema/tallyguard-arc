"""Create an honest, content-addressed report for a genuine pilot workflow."""

from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .audit import canonical_json
from .packet_verify import verify_evidence_packet


class PilotReportError(ValueError):
    """Raised when pilot evidence is incomplete, inconsistent, or unverifiable."""


def _required_text(value: Any, label: str, *, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PilotReportError(f"{label} must be a non-empty string.")
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise PilotReportError(f"{label} exceeds {maximum} characters.")
    return cleaned


def _text_list(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise PilotReportError(f"{label} must be a non-empty list.")
    if len(value) > 20:
        raise PilotReportError(f"{label} cannot contain more than 20 items.")
    return tuple(_required_text(item, f"{label} item", maximum=300) for item in value)


def _positive_decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise PilotReportError(f"{label} must be a positive number.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PilotReportError(f"{label} must be a positive number.") from exc
    if not parsed.is_finite() or parsed <= 0 or parsed > Decimal("1440"):
        raise PilotReportError(f"{label} must be greater than zero and at most 1440 minutes.")
    return parsed


def _aware_timestamp(value: Any, label: str) -> datetime:
    raw = _required_text(value, label, maximum=64)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PilotReportError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PilotReportError(f"{label} must include a timezone offset.")
    return parsed


def build_pilot_report(
    evidence_envelope: dict[str, Any],
    attestation: dict[str, Any],
    *,
    verify_arc: bool = False,
) -> dict[str, Any]:
    """Bind a human usage attestation to a verified Payment Evidence Packet."""

    verification = verify_evidence_packet(evidence_envelope, verify_arc=verify_arc)
    if not verification.verified:
        failed = ", ".join(check.code for check in verification.checks if not check.passed)
        raise PilotReportError(f"Evidence packet verification failed: {failed}.")

    if attestation.get("schema_version") != "1.0":
        raise PilotReportError("Attestation schema_version must be 1.0.")
    pilot_id = _required_text(attestation.get("pilot_id"), "pilot_id", maximum=100)
    classification = _required_text(
        attestation.get("classification"), "classification", maximum=30
    )
    if classification not in {"self-operated", "external-pilot"}:
        raise PilotReportError("classification must be self-operated or external-pilot.")
    organization_alias = _required_text(
        attestation.get("organization_alias"), "organization_alias", maximum=100
    )
    operator_role = _required_text(
        attestation.get("operator_role"), "operator_role", maximum=100
    )
    started_at = _aware_timestamp(attestation.get("started_at"), "started_at")
    completed_at = _aware_timestamp(attestation.get("completed_at"), "completed_at")
    if completed_at < started_at:
        raise PilotReportError("completed_at cannot be earlier than started_at.")

    workflow_value = attestation.get("workflow")
    if not isinstance(workflow_value, dict):
        raise PilotReportError("workflow must be a JSON object.")
    baseline_process = _required_text(
        workflow_value.get("baseline_process"), "workflow.baseline_process", maximum=500
    )
    baseline_minutes = _positive_decimal(
        workflow_value.get("baseline_minutes"), "workflow.baseline_minutes"
    )
    tallyguard_minutes = _positive_decimal(
        workflow_value.get("tallyguard_minutes"), "workflow.tallyguard_minutes"
    )
    acceptance_criteria = _text_list(
        workflow_value.get("acceptance_criteria"), "workflow.acceptance_criteria"
    )
    criteria_met = _text_list(
        workflow_value.get("criteria_met"), "workflow.criteria_met"
    )
    if not set(criteria_met).issubset(set(acceptance_criteria)):
        raise PilotReportError("Every criteria_met item must exactly match an acceptance criterion.")

    consent_value = attestation.get("consent")
    if not isinstance(consent_value, dict):
        raise PilotReportError("consent must be a JSON object.")
    if consent_value.get("authorized_for_anonymized_hackathon_reporting") is not True:
        raise PilotReportError("Anonymized hackathon reporting consent is required.")
    attested_by = _required_text(
        consent_value.get("attested_by"), "consent.attested_by", maximum=100
    )
    attested_at = _aware_timestamp(consent_value.get("attested_at"), "consent.attested_at")
    if attested_at < completed_at:
        raise PilotReportError("consent.attested_at cannot precede completed_at.")

    packet_value = evidence_envelope.get("packet")
    if not isinstance(packet_value, dict):
        raise PilotReportError("Verified envelope is missing its packet object.")
    decision_value = packet_value.get("decision")
    decision = decision_value if isinstance(decision_value, dict) else {}
    payment_value = packet_value.get("payment")
    payment = payment_value if isinstance(payment_value, dict) else {}
    receipt_value = payment.get("receipt")
    receipt = receipt_value if isinstance(receipt_value, dict) else {}
    provider = str(receipt.get("provider", "none"))
    arc_check = next(
        (check for check in verification.checks if check.code == "ARC_TRANSFER"),
        None,
    )
    if provider == "arc-simulator":
        settlement_evidence = "simulation"
        funds_claim = "no funds moved"
    elif arc_check is not None and arc_check.passed:
        settlement_evidence = "arc-rpc-verified"
        funds_claim = "exact transfer independently confirmed on Arc"
    else:
        settlement_evidence = "provider-receipt-only"
        funds_claim = "funds movement not independently confirmed by this report"

    saved = baseline_minutes - tallyguard_minutes
    saved_percent = (saved / baseline_minutes * Decimal("100")).quantize(Decimal("0.1"))
    attestation_hash = sha256(canonical_json(attestation).encode("utf-8")).hexdigest()
    report = {
        "schema_version": "1.0",
        "pilot": {
            "id": pilot_id,
            "classification": classification,
            "organization_alias": organization_alias,
            "operator_role": operator_role,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
        },
        "usage_measurement": {
            "source": "operator attestation",
            "independently_verified_customer_claim": False,
            "baseline_process": baseline_process,
            "baseline_minutes": format(baseline_minutes, "f"),
            "tallyguard_minutes": format(tallyguard_minutes, "f"),
            "time_saved_minutes": format(saved, "f"),
            "time_saved_percent": format(saved_percent, "f"),
            "acceptance_criteria": list(acceptance_criteria),
            "criteria_met": list(criteria_met),
            "criteria_completion": f"{len(criteria_met)}/{len(acceptance_criteria)}",
        },
        "product_evidence": {
            "packet_id": verification.packet_id,
            "packet_sha256": str(evidence_envelope.get("packet_sha256", "")),
            "packet_verified": True,
            "verification_checks": len(verification.checks),
            "decision_action": str(decision.get("final_action", "")),
            "settlement_evidence": settlement_evidence,
            "funds_claim": funds_claim,
            "network": str(receipt.get("network", "")),
            "transaction_hash": str(receipt.get("transaction_hash", "")),
        },
        "attestation": {
            "sha256": attestation_hash,
            "attested_by": attested_by,
            "attested_at": attested_at.isoformat(),
            "reporting_consent": True,
        },
        "limitations": [
            "Usage timing and workflow context are operator-attested, not independently audited.",
            "A self-operated pilot is genuine product usage but is not an external customer claim.",
            "Simulation settlement does not prove an onchain transfer or revenue.",
        ],
    }
    report_hash = sha256(canonical_json(report).encode("utf-8")).hexdigest()
    return {
        "report_id": f"pilot_report_{report_hash[:24]}",
        "report_sha256": report_hash,
        "report": report,
    }


def render_pilot_markdown(envelope: dict[str, Any]) -> str:
    report = envelope["report"]
    pilot = report["pilot"]
    usage = report["usage_measurement"]
    evidence = report["product_evidence"]
    return f"""# TallyGuard pilot evidence

Report ID: `{envelope['report_id']}`  
Report SHA-256: `{envelope['report_sha256']}`  
Pilot classification: `{pilot['classification']}`  
Organization alias: `{pilot['organization_alias']}`

## Observed workflow

- Operator role: {pilot['operator_role']}
- Window: {pilot['started_at']} to {pilot['completed_at']}
- Prior process: {usage['baseline_process']}
- Operator-attested baseline: {usage['baseline_minutes']} minutes
- Operator-attested TallyGuard run: {usage['tallyguard_minutes']} minutes
- Difference: {usage['time_saved_minutes']} minutes ({usage['time_saved_percent']}%)
- Acceptance criteria completed: {usage['criteria_completion']}

## Product proof

- Evidence packet: `{evidence['packet_id']}`
- Evidence packet SHA-256: `{evidence['packet_sha256']}`
- Deterministic action: `{evidence['decision_action']}`
- Settlement evidence: `{evidence['settlement_evidence']}`
- Funds statement: {evidence['funds_claim']}
- Network: `{evidence['network'] or 'not applicable'}`

## Claim boundary

This report binds an operator attestation to a cryptographically verified
TallyGuard Payment Evidence Packet. Usage timing and workflow context were not
independently audited. A self-operated pilot is genuine product usage, but it is
not an external customer claim. A simulated receipt is not an onchain transfer
or revenue claim.
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bind a genuine pilot attestation to a verified TallyGuard evidence packet."
    )
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path)
    parser.add_argument(
        "--verify-arc",
        action="store_true",
        help="independently verify the exact receipt through Arc RPC",
    )
    args = parser.parse_args()
    try:
        packet = json.loads(args.packet.read_text(encoding="utf-8"))
        attestation = json.loads(args.attestation.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read pilot input: {exc}") from exc
    if not isinstance(packet, dict) or not isinstance(attestation, dict):
        raise SystemExit("Pilot inputs must each contain a JSON object.")
    try:
        report = build_pilot_report(packet, attestation, verify_arc=args.verify_arc)
    except PilotReportError as exc:
        raise SystemExit(f"Pilot report rejected: {exc}") from exc

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if args.output_markdown is not None:
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text(render_pilot_markdown(report), encoding="utf-8")
    print(f"Pilot report verified: {report['report_id']}")
    print(f"SHA-256: {report['report_sha256']}")


if __name__ == "__main__":
    main()

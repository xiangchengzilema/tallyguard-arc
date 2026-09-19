"""Non-authoritative evidence analyst used before deterministic policy control.

The analyst may interpret and summarize records, but it never creates a payment
payload. Its recommendation is stored beside the authoritative policy result so
reviewers can see agreement or disagreement without trusting free-form output.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from .decisions import AgentRecommendation
from .models import TreasurySnapshot, Vendor
from .normalization import NormalizedEvidence
from .policy import DecisionAction, Policy


class EvidenceAnalyst(Protocol):
    def recommend(
        self,
        *,
        evidence: NormalizedEvidence,
        vendor: Vendor,
        treasury: TreasurySnapshot,
        policy: Policy,
        evaluation_date: date | None = None,
    ) -> AgentRecommendation: ...


class DeterministicEvidenceAnalyst:
    """Credential-free structured analyst for the public judge playground.

    A hosted model adapter can implement the same protocol later. This fallback
    intentionally does not call the policy engine and cannot authorize payment.
    """

    def recommend(
        self,
        *,
        evidence: NormalizedEvidence,
        vendor: Vendor,
        treasury: TreasurySnapshot,
        policy: Policy,
        evaluation_date: date | None = None,
    ) -> AgentRecommendation:
        invoice = evidence.invoice
        checked_at = evaluation_date or date.today()
        evidence_refs = (evidence.package_id, evidence.manifest_hash)
        reason_codes: list[str] = []
        action = DecisionAction.PAY

        if evidence.purchase_order is None:
            reason_codes.append("AGENT_MISSING_PURCHASE_ORDER")
            action = DecisionAction.HOLD
        if evidence.delivery is None:
            reason_codes.append("AGENT_MISSING_DELIVERY_EVIDENCE")
            action = DecisionAction.HOLD
        if invoice.payment_wallet_address != vendor.approved_wallet_address:
            reason_codes.append("AGENT_VENDOR_WALLET_MISMATCH")
            action = DecisionAction.HOLD
        if not vendor.active:
            reason_codes.append("AGENT_VENDOR_INACTIVE")
            action = DecisionAction.HOLD

        autonomous_limit = min(
            vendor.autopay_limit,
            policy.maximum_autonomous_payment_usdc,
        )
        if action == DecisionAction.PAY and invoice.amount > autonomous_limit:
            reason_codes.append("AGENT_AUTONOMY_LIMIT_EXCEEDED")
            action = DecisionAction.ESCALATE
        if action == DecisionAction.PAY and policy.schedule_payments_before_due_days is not None:
            scheduled_for = invoice.due_date - timedelta(
                days=policy.schedule_payments_before_due_days
            )
            if scheduled_for > checked_at:
                reason_codes.append("AGENT_PAYMENT_NOT_DUE")
                action = DecisionAction.SCHEDULE

        if action == DecisionAction.PAY:
            summary = (
                f"Reviewed the immutable invoice package for {invoice.invoice_number}. "
                f"The {invoice.amount} {invoice.currency} request has matching vendor, "
                "purchase-order, delivery, and wallet evidence. Recommend PAY; policy "
                "controls remain authoritative."
            )
            reason_codes.append("AGENT_THREE_WAY_MATCH_COMPLETE")
            confidence = Decimal("0.94")
        else:
            summary = (
                f"Reviewed the immutable invoice package for {invoice.invoice_number}. "
                f"Recommend {action.value} because: {', '.join(reason_codes)}. "
                "This recommendation cannot authorize or construct a payment."
            )
            confidence = Decimal("0.90")

        return AgentRecommendation(
            action=action,
            summary=summary,
            reason_codes=tuple(reason_codes),
            confidence=confidence,
            evidence_refs=evidence_refs,
        )

"""Deterministic payment policy evaluation.

The policy engine is intentionally independent from any language model. An AI
may propose an action, but this module is the authority that decides whether a
payment intent may be created.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
import json
from typing import Iterable

from .models import DeliveryEvidence, Invoice, PurchaseOrder, TreasurySnapshot, Vendor


WALLET_CHANGE_COOLDOWN_DAYS = 2
VENDOR_INVOICE_NUMBER_SIGNAL = "vendor-invoice-number:"
NEAR_DUPLICATE_SIGNAL = "near-duplicate-fields:"
PO_COMMITMENT_SIGNAL = "po-commitment:"
DELIVERY_COMMITMENT_SIGNAL = "delivery-commitment:"


def _frozen_signal(fingerprints: Iterable[str], prefix: str, invoice: Invoice) -> dict[str, object] | None:
    """Read a tenant-scoped, point-in-time signal sealed with replay inputs."""
    for value in fingerprints:
        if not value.startswith(prefix):
            continue
        try:
            payload = json.loads(value[len(prefix):])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("fingerprint") == invoice.fingerprint:
            return payload
    return None


class DecisionAction(StrEnum):
    PAY = "PAY"
    SCHEDULE = "SCHEDULE"
    HOLD = "HOLD"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"


class RuleDisposition(StrEnum):
    PASS = "PASS"
    SCHEDULE = "SCHEDULE"
    HOLD = "HOLD"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True, slots=True)
class RuleResult:
    code: str
    disposition: RuleDisposition
    message: str
    remediation: str | None = None

    @property
    def passed(self) -> bool:
        return self.disposition == RuleDisposition.PASS


@dataclass(frozen=True, slots=True)
class Policy:
    version: str
    organization_id: str
    daily_payment_limit_usdc: Decimal
    minimum_cash_reserve_usdc: Decimal
    maximum_autonomous_payment_usdc: Decimal
    daily_autonomous_payment_limit_usdc: Decimal | None = None
    autonomous_payments_enabled: bool = False
    po_amount_tolerance_usdc: Decimal = Decimal("0")
    allowed_asset: str = "USDC"
    allowed_network: str = "ARC-TESTNET"
    kill_switch_enabled: bool = False
    schedule_payments_before_due_days: int | None = None

    def __post_init__(self) -> None:
        if not self.version.strip() or not self.organization_id.strip():
            raise ValueError("Policy version and organization ID are required.")
        if self.daily_autonomous_payment_limit_usdc is None:
            object.__setattr__(
                self,
                "daily_autonomous_payment_limit_usdc",
                Decimal(str(self.daily_payment_limit_usdc)),
            )
        for field_name in (
            "daily_payment_limit_usdc",
            "daily_autonomous_payment_limit_usdc",
            "minimum_cash_reserve_usdc",
            "maximum_autonomous_payment_usdc",
            "po_amount_tolerance_usdc",
        ):
            object.__setattr__(self, field_name, Decimal(str(getattr(self, field_name))))
        if any(
            getattr(self, field_name) < 0
            for field_name in (
                "daily_payment_limit_usdc",
                "daily_autonomous_payment_limit_usdc",
                "minimum_cash_reserve_usdc",
                "maximum_autonomous_payment_usdc",
                "po_amount_tolerance_usdc",
            )
        ):
            raise ValueError("Policy monetary limits must not be negative.")
        object.__setattr__(self, "allowed_asset", self.allowed_asset.strip().upper())
        object.__setattr__(self, "allowed_network", self.allowed_network.strip().upper())
        if self.schedule_payments_before_due_days is not None and self.schedule_payments_before_due_days < 0:
            raise ValueError("Payment scheduling lead time must not be negative.")


@dataclass(frozen=True, slots=True)
class Decision:
    action: DecisionAction
    policy_version: str
    invoice_fingerprint: str
    rule_results: tuple[RuleResult, ...]
    approval_reference: str | None = None

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(result.code for result in self.rule_results if not result.passed)

    @property
    def remediation(self) -> tuple[str, ...]:
        return tuple(
            result.remediation
            for result in self.rule_results
            if result.remediation is not None and not result.passed
        )


class PolicyEngine:
    """Evaluate evidence in a stable order and return one final action."""

    def evaluate(
        self,
        *,
        invoice: Invoice,
        vendor: Vendor,
        purchase_order: PurchaseOrder | None,
        delivery: DeliveryEvidence | None,
        treasury: TreasurySnapshot,
        policy: Policy,
        known_invoice_fingerprints: Iterable[str] = (),
        asset: str = "USDC",
        network: str = "ARC-TESTNET",
        evaluation_date: date | None = None,
        vendor_wallet_event_type: str | None = None,
        vendor_wallet_verified_date: date | None = None,
        autonomous_spent_today_usdc: Decimal | None = None,
    ) -> Decision:
        resolved_evaluation_date = evaluation_date or date.today()
        frozen_signals = tuple(known_invoice_fingerprints)
        results = (
            self._kill_switch(policy),
            self._tenant_boundary(invoice, vendor, purchase_order, delivery, treasury, policy),
            self._duplicate(invoice, frozen_signals),
            self._vendor(invoice, vendor),
            self._wallet(invoice, vendor),
            self._wallet_change_cooldown(
                vendor_wallet_event_type,
                vendor_wallet_verified_date,
                resolved_evaluation_date,
            ),
            self._asset_and_network(asset, network, policy),
            self._purchase_order(invoice, vendor, purchase_order, policy, frozen_signals),
            self._delivery(invoice, purchase_order, delivery, policy, frozen_signals),
            self._autonomous_payments_enabled(policy),
            self._autonomy_limit(invoice, vendor, policy),
            self._daily_autonomy_limit(invoice, treasury, policy, autonomous_spent_today_usdc),
            self._daily_limit(invoice, treasury, policy),
            self._cash_reserve(invoice, treasury, policy),
            self._payment_timing(invoice, policy, resolved_evaluation_date),
        )
        action = self._final_action(results)
        return Decision(
            action=action,
            policy_version=policy.version,
            invoice_fingerprint=invoice.fingerprint,
            rule_results=results,
        )

    @staticmethod
    def _pass(code: str, message: str) -> RuleResult:
        return RuleResult(code=code, disposition=RuleDisposition.PASS, message=message)

    @staticmethod
    def _tenant_boundary(
        invoice: Invoice,
        vendor: Vendor,
        po: PurchaseOrder | None,
        delivery: DeliveryEvidence | None,
        treasury: TreasurySnapshot,
        policy: Policy,
    ) -> RuleResult:
        organization_ids = {
            invoice.organization_id,
            vendor.organization_id,
            treasury.organization_id,
            policy.organization_id,
        }
        if po is not None:
            organization_ids.add(po.organization_id)
        if delivery is not None:
            organization_ids.add(delivery.organization_id)
        if len(organization_ids) != 1:
            return RuleResult(
                code="TENANT_BOUNDARY_VIOLATION",
                disposition=RuleDisposition.REJECT,
                message="Evidence or policy records belong to different organizations.",
                remediation="Rebuild the evaluation using records from exactly one organization.",
            )
        return PolicyEngine._pass("TENANT_BOUNDARY_OK", "All records belong to one organization.")

    @staticmethod
    def _kill_switch(policy: Policy) -> RuleResult:
        if policy.kill_switch_enabled:
            return RuleResult(
                code="KILL_SWITCH_ACTIVE",
                disposition=RuleDisposition.HOLD,
                message="Autonomous settlement is disabled for the organization.",
                remediation="Disable the kill switch after a treasury owner reviews the incident.",
            )
        return PolicyEngine._pass("KILL_SWITCH_CLEAR", "Autonomous settlement is enabled.")

    @staticmethod
    def _duplicate(invoice: Invoice, fingerprints: Iterable[str]) -> RuleResult:
        known = set(fingerprints)
        if invoice.fingerprint in known:
            return RuleResult(
                code="DUPLICATE_INVOICE",
                disposition=RuleDisposition.REJECT,
                message="The invoice fingerprint has already been recorded.",
                remediation="Use the existing invoice record or submit corrected source evidence.",
            )
        marker = f"{VENDOR_INVOICE_NUMBER_SIGNAL}{invoice.fingerprint}:"
        previous = next((value[len(marker):] for value in sorted(known) if value.startswith(marker)), None)
        if previous is not None:
            return RuleResult(
                code="VENDOR_INVOICE_NUMBER_REUSED",
                disposition=RuleDisposition.ESCALATE,
                message=(
                    f"The same vendor invoice number is already present in request {previous}; "
                    "autonomous payment is blocked."
                ),
                remediation=(
                    "Compare the sealed documents and payment history for both requests. "
                    "Record whether this is a correction or a duplicate before independent approval."
                ),
            )
        near_duplicate = _frozen_signal(known, NEAR_DUPLICATE_SIGNAL, invoice)
        if near_duplicate is not None:
            previous_id = str(near_duplicate["previous_id"])
            similarity = str(near_duplicate["similarity"])
            return RuleResult(
                code="NEAR_DUPLICATE_INVOICE_FIELDS",
                disposition=RuleDisposition.ESCALATE,
                message=(
                    f"Invoice fields closely resemble request {previous_id} "
                    f"({similarity} similarity) despite a changed invoice number; "
                    "autonomous payment is blocked."
                ),
                remediation=(
                    "Compare source documents, service periods, and prior payment status. "
                    "Record whether this is a separate obligation or a corrected duplicate."
                ),
            )
        return PolicyEngine._pass("INVOICE_UNIQUE", "No duplicate invoice fingerprint was found.")

    @staticmethod
    def _vendor(invoice: Invoice, vendor: Vendor) -> RuleResult:
        if invoice.vendor_id != vendor.id:
            return RuleResult(
                code="VENDOR_ID_MISMATCH",
                disposition=RuleDisposition.HOLD,
                message="Invoice vendor does not match the selected vendor profile.",
                remediation="Attach the correct vendor profile or correct the invoice metadata.",
            )
        if not vendor.active:
            return RuleResult(
                code="VENDOR_INACTIVE",
                disposition=RuleDisposition.HOLD,
                message="The vendor is not active for autonomous payments.",
                remediation="Re-verify and reactivate the vendor before payment.",
            )
        return PolicyEngine._pass("VENDOR_APPROVED", "Vendor is active and matches the invoice.")

    @staticmethod
    def _wallet(invoice: Invoice, vendor: Vendor) -> RuleResult:
        if invoice.payment_wallet_address != vendor.approved_wallet_address:
            return RuleResult(
                code="VENDOR_WALLET_CHANGED",
                disposition=RuleDisposition.HOLD,
                message="Invoice payment address differs from the verified vendor wallet.",
                remediation="Verify the wallet change out-of-band and update the vendor profile.",
            )
        return PolicyEngine._pass("VENDOR_WALLET_VERIFIED", "Payment address matches the vendor profile.")

    @staticmethod
    def _wallet_change_cooldown(
        event_type: str | None,
        verified_date: date | None,
        evaluation_date: date,
    ) -> RuleResult:
        if event_type != "REPLACED":
            return PolicyEngine._pass(
                "WALLET_CHANGE_COOLDOWN_CLEAR",
                "Vendor payout wallet is not in a recent replacement cooldown.",
            )
        if verified_date is None:
            return RuleResult(
                code="WALLET_CHANGE_TIMESTAMP_MISSING",
                disposition=RuleDisposition.HOLD,
                message="The replacement wallet has no independently replayable verification date.",
                remediation="Re-verify the wallet change and record its verification timestamp.",
            )
        release_date = verified_date + timedelta(days=WALLET_CHANGE_COOLDOWN_DAYS)
        if evaluation_date < release_date:
            return RuleResult(
                code="WALLET_CHANGE_COOLDOWN_ACTIVE",
                disposition=RuleDisposition.HOLD,
                message=f"The replacement wallet is cooling down until {release_date.isoformat()}.",
                remediation=(
                    "Wait for the payout-address cooldown to expire, then re-evaluate against "
                    "current evidence and policy."
                ),
            )
        return PolicyEngine._pass(
            "WALLET_CHANGE_COOLDOWN_COMPLETE",
            "The independently verified payout-address cooldown has elapsed.",
        )

    @staticmethod
    def _asset_and_network(asset: str, network: str, policy: Policy) -> RuleResult:
        if asset.strip().upper() != policy.allowed_asset or network.strip().upper() != policy.allowed_network:
            return RuleResult(
                code="SETTLEMENT_ROUTE_NOT_ALLOWED",
                disposition=RuleDisposition.HOLD,
                message="The requested settlement asset or network is not allowed.",
                remediation=f"Use {policy.allowed_asset} on {policy.allowed_network}.",
            )
        return PolicyEngine._pass("SETTLEMENT_ROUTE_ALLOWED", "Asset and network are allowed.")

    @staticmethod
    def _purchase_order(
        invoice: Invoice,
        vendor: Vendor,
        po: PurchaseOrder | None,
        policy: Policy,
        frozen_signals: Iterable[str] = (),
    ) -> RuleResult:
        if po is None:
            return RuleResult(
                code="MISSING_PURCHASE_ORDER",
                disposition=RuleDisposition.HOLD,
                message="No purchase order is attached to the invoice.",
                remediation="Attach an authorized purchase order before settlement.",
            )
        if po.vendor_id != vendor.id or po.currency != invoice.currency:
            return RuleResult(
                code="PO_IDENTITY_MISMATCH",
                disposition=RuleDisposition.HOLD,
                message="Purchase order vendor or currency does not match the invoice.",
                remediation="Attach the correct purchase order or correct the source records.",
            )
        if invoice.amount > po.authorized_amount + policy.po_amount_tolerance_usdc:
            return RuleResult(
                code="INVOICE_EXCEEDS_PO",
                disposition=RuleDisposition.HOLD,
                message="Invoice amount exceeds the authorized purchase-order amount.",
                remediation="Approve a PO amendment or submit a corrected invoice.",
            )
        commitment = _frozen_signal(frozen_signals, PO_COMMITMENT_SIGNAL, invoice)
        if commitment is not None:
            prior = Decimal(str(commitment["committed_usdc"]))
            if prior + invoice.amount > po.authorized_amount + policy.po_amount_tolerance_usdc:
                return RuleResult(
                    code="PO_CUMULATIVE_EXCEEDED",
                    disposition=RuleDisposition.HOLD,
                    message=(
                        f"This PO already backs {prior} USDC in other active requests "
                        f"({commitment['request_ids']}); adding {invoice.amount} USDC "
                        f"exceeds its {po.authorized_amount} USDC authorization."
                    ),
                    remediation="Correct or cancel the overlapping request, or attach an authorized PO amendment and re-evaluate.",
                )
            return PolicyEngine._pass(
                "PO_MATCHED",
                f"Current request plus {prior} USDC already committed remains within the PO authorization.",
            )
        return PolicyEngine._pass("PO_MATCHED", "Invoice is within the authorized PO amount.")

    @staticmethod
    def _delivery(
        invoice: Invoice,
        po: PurchaseOrder | None,
        delivery: DeliveryEvidence | None,
        policy: Policy,
        frozen_signals: Iterable[str] = (),
    ) -> RuleResult:
        if delivery is None:
            return RuleResult(
                code="MISSING_DELIVERY_EVIDENCE",
                disposition=RuleDisposition.HOLD,
                message="No delivery or acceptance evidence is attached to the invoice.",
                remediation="Attach delivery or acceptance evidence before settlement.",
            )
        if po is None:
            return RuleResult(
                code="DELIVERY_WITHOUT_PURCHASE_ORDER",
                disposition=RuleDisposition.HOLD,
                message="Delivery evidence cannot be matched without a purchase order.",
                remediation="Attach the purchase order referenced by the delivery evidence.",
            )
        if delivery.purchase_order_id != po.id:
            return RuleResult(
                code="DELIVERY_PO_MISMATCH",
                disposition=RuleDisposition.HOLD,
                message="Delivery evidence does not belong to the attached purchase order.",
                remediation="Attach delivery evidence for the correct purchase order.",
            )
        if delivery.delivered_value + policy.po_amount_tolerance_usdc < invoice.amount:
            return RuleResult(
                code="DELIVERY_VALUE_INSUFFICIENT",
                disposition=RuleDisposition.HOLD,
                message="Delivered value does not cover the requested invoice amount.",
                remediation="Provide additional delivery proof or reduce the invoice amount.",
            )
        commitment = _frozen_signal(frozen_signals, DELIVERY_COMMITMENT_SIGNAL, invoice)
        if commitment is not None:
            prior = Decimal(str(commitment["committed_usdc"]))
            if prior + invoice.amount > delivery.delivered_value + policy.po_amount_tolerance_usdc:
                return RuleResult(
                    code="DELIVERY_CUMULATIVE_EXCEEDED",
                    disposition=RuleDisposition.HOLD,
                    message=(
                        f"This delivery record already backs {prior} USDC in other active requests "
                        f"({commitment['request_ids']}); adding {invoice.amount} USDC "
                        f"exceeds its {delivery.delivered_value} USDC accepted value."
                    ),
                    remediation="Correct or cancel the overlapping request, or attach new delivery evidence and re-evaluate.",
                )
            return PolicyEngine._pass(
                "DELIVERY_MATCHED",
                f"Current request plus {prior} USDC already committed remains within the delivery value.",
            )
        return PolicyEngine._pass("DELIVERY_MATCHED", "Delivery evidence covers the invoice amount.")

    @staticmethod
    def _autonomy_limit(invoice: Invoice, vendor: Vendor, policy: Policy) -> RuleResult:
        limit = min(vendor.autopay_limit, policy.maximum_autonomous_payment_usdc)
        if invoice.amount > limit:
            return RuleResult(
                code="AUTONOMY_LIMIT_EXCEEDED",
                disposition=RuleDisposition.ESCALATE,
                message=f"Invoice amount exceeds the autonomous payment limit of {limit} USDC.",
                remediation="Obtain the configured human approval before settlement.",
            )
        return PolicyEngine._pass("AUTONOMY_LIMIT_OK", "Invoice is within the autonomous payment limit.")

    @staticmethod
    def _autonomous_payments_enabled(policy: Policy) -> RuleResult:
        if not policy.autonomous_payments_enabled:
            return RuleResult(
                code="AUTONOMOUS_PAYMENTS_DISABLED",
                disposition=RuleDisposition.ESCALATE,
                message="Finance has not enabled no-touch settlement for this policy.",
                remediation="Obtain finance approval, or activate a policy that explicitly enables no-touch settlement.",
            )
        return PolicyEngine._pass(
            "AUTONOMOUS_PAYMENTS_ENABLED",
            "Finance has explicitly enabled no-touch settlement for this policy.",
        )

    @staticmethod
    def _daily_autonomy_limit(
        invoice: Invoice,
        treasury: TreasurySnapshot,
        policy: Policy,
        autonomous_spent_today_usdc: Decimal | None = None,
    ) -> RuleResult:
        projected = (
            treasury.spent_today_usdc
            if autonomous_spent_today_usdc is None
            else Decimal(str(autonomous_spent_today_usdc))
        ) + invoice.amount
        limit = policy.daily_autonomous_payment_limit_usdc
        assert limit is not None
        if projected > limit:
            return RuleResult(
                code="DAILY_AUTONOMY_LIMIT_EXCEEDED",
                disposition=RuleDisposition.ESCALATE,
                message=(
                    "Payment would exceed the organization's daily no-touch payment ceiling "
                    f"of {limit} USDC."
                ),
                remediation="Obtain the configured human approval before settlement.",
            )
        return PolicyEngine._pass(
            "DAILY_AUTONOMY_LIMIT_OK",
            "Projected autonomous spend remains within the daily no-touch ceiling.",
        )

    @staticmethod
    def _daily_limit(invoice: Invoice, treasury: TreasurySnapshot, policy: Policy) -> RuleResult:
        projected = treasury.spent_today_usdc + invoice.amount
        if projected > policy.daily_payment_limit_usdc:
            return RuleResult(
                code="DAILY_LIMIT_EXCEEDED",
                disposition=RuleDisposition.HOLD,
                message="Payment would exceed the organization's daily payment limit.",
                remediation="Schedule for a later day or obtain a policy change.",
            )
        return PolicyEngine._pass("DAILY_LIMIT_OK", "Projected daily spend remains within policy.")

    @staticmethod
    def _cash_reserve(invoice: Invoice, treasury: TreasurySnapshot, policy: Policy) -> RuleResult:
        projected = treasury.available_usdc - invoice.amount
        if projected < policy.minimum_cash_reserve_usdc:
            return RuleResult(
                code="MINIMUM_RESERVE_BREACH",
                disposition=RuleDisposition.HOLD,
                message="Payment would reduce treasury funds below the minimum reserve.",
                remediation="Fund the treasury, schedule later, or approve a reserve-policy change.",
            )
        return PolicyEngine._pass("MINIMUM_RESERVE_OK", "Post-payment balance preserves the required reserve.")

    @staticmethod
    def _payment_timing(invoice: Invoice, policy: Policy, evaluation_date: date) -> RuleResult:
        lead_days = policy.schedule_payments_before_due_days
        if lead_days is None:
            return PolicyEngine._pass("PAYMENT_TIMING_IMMEDIATE", "Policy permits immediate payment.")
        scheduled_for = invoice.due_date - timedelta(days=lead_days)
        if scheduled_for > evaluation_date:
            return RuleResult(
                code="PAYMENT_SCHEDULED_FOR_DUE_DATE",
                disposition=RuleDisposition.SCHEDULE,
                message=f"Payment should be scheduled for {scheduled_for.isoformat()}.",
                remediation=f"Queue payment for {scheduled_for.isoformat()} unless a human approves early payment.",
            )
        return PolicyEngine._pass("PAYMENT_DUE", "Invoice is within the configured payment window.")

    @staticmethod
    def _final_action(results: tuple[RuleResult, ...]) -> DecisionAction:
        dispositions = {result.disposition for result in results}
        if RuleDisposition.REJECT in dispositions:
            return DecisionAction.REJECT
        if RuleDisposition.HOLD in dispositions:
            return DecisionAction.HOLD
        if RuleDisposition.ESCALATE in dispositions:
            return DecisionAction.ESCALATE
        if RuleDisposition.SCHEDULE in dispositions:
            return DecisionAction.SCHEDULE
        return DecisionAction.PAY

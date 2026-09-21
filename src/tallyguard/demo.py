"""Deterministic judge scenarios exercising the real control engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib

from .decisions import AgentRecommendation
from .models import DeliveryEvidence, Invoice, PurchaseOrder, TreasurySnapshot, Vendor
from .normalization import NormalizedEvidence
from .policy import DecisionAction, Policy


APPROVED_WALLET = "0x1111111111111111111111111111111111111111"
CHANGED_WALLET = "0x2222222222222222222222222222222222222222"


@dataclass(frozen=True, slots=True)
class DemoScenarioDefinition:
    key: str
    title: str
    description: str
    expected_action: DecisionAction


@dataclass(frozen=True, slots=True)
class DemoScenarioCase:
    definition: DemoScenarioDefinition
    evidence: NormalizedEvidence
    vendor: Vendor
    treasury: TreasurySnapshot
    policy: Policy
    recommendation: AgentRecommendation
    known_invoice_fingerprints: tuple[str, ...]
    evaluation_date: date


DEMO_SCENARIOS: tuple[DemoScenarioDefinition, ...] = (
    DemoScenarioDefinition(
        key="clean-payment",
        title="Clean three-way match",
        description="Verified vendor, exact PO, accepted delivery, and sufficient treasury reserve.",
        expected_action=DecisionAction.PAY,
    ),
    DemoScenarioDefinition(
        key="duplicate-invoice",
        title="Duplicate invoice blocked",
        description="The same evidence-bound invoice fingerprint was already recorded.",
        expected_action=DecisionAction.REJECT,
    ),
    DemoScenarioDefinition(
        key="wallet-change",
        title="Vendor wallet change held",
        description="Invoice recipient differs from the independently verified vendor wallet.",
        expected_action=DecisionAction.HOLD,
    ),
    DemoScenarioDefinition(
        key="po-overage",
        title="PO overage held",
        description="Invoice value exceeds the authorized purchase order.",
        expected_action=DecisionAction.HOLD,
    ),
    DemoScenarioDefinition(
        key="missing-delivery",
        title="Missing delivery held",
        description="No delivery or acceptance evidence exists for the requested payment.",
        expected_action=DecisionAction.HOLD,
    ),
    DemoScenarioDefinition(
        key="large-invoice",
        title="Large invoice escalated",
        description="Valid evidence exceeds the autonomous limit and requires a second approver.",
        expected_action=DecisionAction.ESCALATE,
    ),
    DemoScenarioDefinition(
        key="scheduled-payment",
        title="Cash-aware scheduling",
        description="Valid invoice is queued for its configured due-date payment window.",
        expected_action=DecisionAction.SCHEDULE,
    ),
    DemoScenarioDefinition(
        key="provider-recovery",
        title="Provider timeout recovery",
        description="The first settlement attempt fails safely; retry reuses the durable payment intent and idempotency key.",
        expected_action=DecisionAction.PAY,
    ),
)


def scenario_catalog() -> tuple[DemoScenarioDefinition, ...]:
    return DEMO_SCENARIOS


def build_demo_scenario(
    key: str,
    *,
    organization_id: str,
    invoice_id: str,
) -> DemoScenarioCase:
    try:
        definition = next(item for item in DEMO_SCENARIOS if item.key == key)
    except StopIteration as exc:
        raise ValueError("Unknown demo scenario.") from exc

    amount = Decimal("2200") if key == "large-invoice" else Decimal("1200")
    if key == "po-overage":
        amount = Decimal("1300")
    if key == "scheduled-payment":
        amount = Decimal("500")
    invoice_hash = hashlib.sha256(f"{organization_id}:{invoice_id}:invoice".encode()).hexdigest()
    delivery_hash = hashlib.sha256(f"{organization_id}:{invoice_id}:delivery".encode()).hexdigest()
    manifest_hash = hashlib.sha256(f"{organization_id}:{invoice_id}:manifest".encode()).hexdigest()
    invoice = Invoice(
        id=invoice_id,
        organization_id=organization_id,
        vendor_id="vendor-acme",
        invoice_number=f"INV-{invoice_id[-8:].upper()}",
        currency="USDC",
        amount=amount,
        due_date=date(2026, 10, 10),
        payment_wallet_address=CHANGED_WALLET if key == "wallet-change" else APPROVED_WALLET,
        source_document_hash=invoice_hash,
    )
    po_amount = Decimal("1200") if key == "po-overage" else amount
    purchase_order = PurchaseOrder(
        id=f"po-{invoice_id}",
        organization_id=organization_id,
        vendor_id="vendor-acme",
        po_number=f"PO-{invoice_id[-8:].upper()}",
        currency="USDC",
        authorized_amount=po_amount,
    )
    delivery = None
    if key != "missing-delivery":
        delivery = DeliveryEvidence(
            id=f"delivery-{invoice_id}",
            organization_id=organization_id,
            purchase_order_id=purchase_order.id,
            delivered_value=amount,
            source_document_hash=delivery_hash,
        )
    evidence = NormalizedEvidence(
        package_id=f"package-{invoice_id}",
        manifest_hash=manifest_hash,
        invoice=invoice,
        purchase_order=purchase_order,
        delivery=delivery,
    )
    vendor = Vendor(
        id="vendor-acme",
        organization_id=organization_id,
        legal_name="Acme Data LLC",
        approved_wallet_address=APPROVED_WALLET,
        autopay_limit=Decimal("2500"),
    )
    treasury = TreasurySnapshot(
        organization_id=organization_id,
        available_usdc=Decimal("10000"),
        spent_today_usdc=Decimal("400"),
    )
    policy = Policy(
        version=f"demo-2026-09-20.{invoice_id[-8:]}",
        organization_id=organization_id,
        daily_payment_limit_usdc=Decimal("5000"),
        minimum_cash_reserve_usdc=Decimal("3000"),
        maximum_autonomous_payment_usdc=Decimal("2000"),
        autonomous_payments_enabled=True,
        schedule_payments_before_due_days=3 if key == "scheduled-payment" else None,
    )
    recommendation = AgentRecommendation(
        action=(
            DecisionAction.REJECT
            if key == "duplicate-invoice"
            else DecisionAction.PAY
        ),
        summary="The agent summarized the source package and recommended an action for policy review.",
        reason_codes=("AGENT_EVIDENCE_REVIEW",),
        confidence=Decimal("0.91"),
    )
    known = (invoice.fingerprint,) if key == "duplicate-invoice" else ()
    return DemoScenarioCase(
        definition=definition,
        evidence=evidence,
        vendor=vendor,
        treasury=treasury,
        policy=policy,
        recommendation=recommendation,
        known_invoice_fingerprints=known,
        evaluation_date=date(2026, 9, 20),
    )

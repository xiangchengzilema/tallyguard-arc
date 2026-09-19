from datetime import date
from decimal import Decimal

from tallyguard.agent import DeterministicEvidenceAnalyst
from tallyguard.models import (
    DeliveryEvidence,
    Invoice,
    PurchaseOrder,
    TreasurySnapshot,
    Vendor,
)
from tallyguard.normalization import NormalizedEvidence
from tallyguard.policy import DecisionAction, Policy


WALLET = "0x1111111111111111111111111111111111111111"


def case(*, amount: str = "1200", wallet: str = WALLET, include_match: bool = True):
    invoice = Invoice(
        id="invoice-1",
        organization_id="org-1",
        vendor_id="vendor-1",
        invoice_number="INV-1007",
        currency="USDC",
        amount=Decimal(amount),
        due_date=date(2026, 10, 8),
        payment_wallet_address=wallet,
        source_document_hash="a" * 64,
    )
    purchase_order = (
        PurchaseOrder(
            id="po-1",
            organization_id="org-1",
            vendor_id="vendor-1",
            po_number="PO-1007",
            currency="USDC",
            authorized_amount=Decimal(amount),
        )
        if include_match
        else None
    )
    delivery = (
        DeliveryEvidence(
            id="delivery-1",
            organization_id="org-1",
            purchase_order_id="po-1",
            delivered_value=Decimal(amount),
            source_document_hash="b" * 64,
        )
        if include_match
        else None
    )
    evidence = NormalizedEvidence(
        package_id="package:invoice-1",
        manifest_hash="c" * 64,
        invoice=invoice,
        purchase_order=purchase_order,
        delivery=delivery,
    )
    vendor = Vendor(
        id="vendor-1",
        organization_id="org-1",
        legal_name="Verified Vendor",
        approved_wallet_address=WALLET,
        autopay_limit=Decimal("2000"),
    )
    treasury = TreasurySnapshot(
        organization_id="org-1",
        available_usdc=Decimal("10000"),
        spent_today_usdc=Decimal("500"),
    )
    policy = Policy(
        version="v1",
        organization_id="org-1",
        daily_payment_limit_usdc=Decimal("5000"),
        minimum_cash_reserve_usdc=Decimal("1000"),
        maximum_autonomous_payment_usdc=Decimal("2000"),
    )
    return evidence, vendor, treasury, policy


def test_analyst_recommends_pay_with_manifest_citations_for_complete_match():
    evidence, vendor, treasury, policy = case()
    recommendation = DeterministicEvidenceAnalyst().recommend(
        evidence=evidence,
        vendor=vendor,
        treasury=treasury,
        policy=policy,
        evaluation_date=date(2026, 9, 20),
    )

    assert recommendation.action == DecisionAction.PAY
    assert recommendation.reason_codes == ("AGENT_THREE_WAY_MATCH_COMPLETE",)
    assert recommendation.evidence_refs == ("package:invoice-1", "c" * 64)
    assert "cannot" not in recommendation.summary.lower()


def test_analyst_holds_missing_match_and_changed_wallet_without_payment_payload():
    changed_wallet = "0x2222222222222222222222222222222222222222"
    evidence, vendor, treasury, policy = case(
        wallet=changed_wallet,
        include_match=False,
    )
    recommendation = DeterministicEvidenceAnalyst().recommend(
        evidence=evidence,
        vendor=vendor,
        treasury=treasury,
        policy=policy,
    )

    assert recommendation.action == DecisionAction.HOLD
    assert set(recommendation.reason_codes) == {
        "AGENT_MISSING_PURCHASE_ORDER",
        "AGENT_MISSING_DELIVERY_EVIDENCE",
        "AGENT_VENDOR_WALLET_MISMATCH",
    }
    assert "cannot authorize" in recommendation.summary


def test_analyst_escalates_above_autonomy_limit():
    evidence, vendor, treasury, policy = case(amount="2500")
    recommendation = DeterministicEvidenceAnalyst().recommend(
        evidence=evidence,
        vendor=vendor,
        treasury=treasury,
        policy=policy,
    )

    assert recommendation.action == DecisionAction.ESCALATE
    assert recommendation.reason_codes == ("AGENT_AUTONOMY_LIMIT_EXCEEDED",)

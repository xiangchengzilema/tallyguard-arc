from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

from tallyguard.audit import AuditChain
from tallyguard.decisions import AgentRecommendation, DecisionService
from tallyguard.models import DeliveryEvidence, Invoice, PurchaseOrder, TreasurySnapshot, Vendor
from tallyguard.normalization import NormalizedEvidence
from tallyguard.policy import DecisionAction, Policy


WALLET = "0x1111111111111111111111111111111111111111"


def case():
    invoice = Invoice(
        id="invoice-1",
        organization_id="org-1",
        vendor_id="vendor-1",
        invoice_number="INV-1007",
        currency="USDC",
        amount=Decimal("1200"),
        due_date=date(2026, 10, 8),
        payment_wallet_address=WALLET,
        source_document_hash="a" * 64,
    )
    po = PurchaseOrder(
        id="po-1",
        organization_id="org-1",
        vendor_id="vendor-1",
        po_number="PO-204",
        currency="USDC",
        authorized_amount=Decimal("1200"),
    )
    delivery = DeliveryEvidence(
        id="delivery-1",
        organization_id="org-1",
        purchase_order_id="po-1",
        delivered_value=Decimal("1200"),
        source_document_hash="b" * 64,
    )
    evidence = NormalizedEvidence(
        package_id="package-1",
        manifest_hash="c" * 64,
        invoice=invoice,
        purchase_order=po,
        delivery=delivery,
    )
    vendor = Vendor(
        id="vendor-1",
        organization_id="org-1",
        legal_name="Acme Data LLC",
        approved_wallet_address=WALLET,
        autopay_limit=Decimal("2500"),
    )
    treasury = TreasurySnapshot(
        organization_id="org-1",
        available_usdc=Decimal("10000"),
        spent_today_usdc=Decimal("0"),
    )
    policy = Policy(
        version="v1",
        organization_id="org-1",
        daily_payment_limit_usdc=Decimal("5000"),
        minimum_cash_reserve_usdc=Decimal("1000"),
        maximum_autonomous_payment_usdc=Decimal("2000"),
    )
    return evidence, vendor, treasury, policy


def test_agent_recommendation_cannot_override_missing_evidence_hold():
    evidence, vendor, treasury, policy = case()
    evidence = replace(evidence, delivery=None)
    recommendation = AgentRecommendation(
        action=DecisionAction.PAY,
        summary="Model believes the invoice looks routine.",
        reason_codes=("ROUTINE_VENDOR",),
        confidence=Decimal("0.98"),
    )
    record = DecisionService().evaluate(
        evidence=evidence,
        vendor=vendor,
        treasury=treasury,
        policy=policy,
        agent_recommendation=recommendation,
    )
    assert record.final_action == DecisionAction.HOLD
    assert record.agent_disagreed
    assert "MISSING_DELIVERY_EVIDENCE" in record.policy_decision.reason_codes


def test_same_evidence_and_policy_produce_one_stable_control_record():
    evidence, vendor, treasury, policy = case()
    audit = AuditChain()
    service = DecisionService(audit_chain=audit)
    timestamp = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    first = service.evaluate(
        evidence=evidence,
        vendor=vendor,
        treasury=treasury,
        policy=policy,
        created_at=timestamp,
    )
    second = service.evaluate(
        evidence=evidence,
        vendor=vendor,
        treasury=treasury,
        policy=policy,
        created_at=timestamp,
    )
    assert first == second
    assert first.final_action == DecisionAction.PAY
    assert len(audit.events) == 1
    assert audit.verify()


def test_policy_change_creates_new_decision_identity():
    evidence, vendor, treasury, policy = case()
    service = DecisionService()
    first = service.evaluate(
        evidence=evidence,
        vendor=vendor,
        treasury=treasury,
        policy=policy,
    )
    second = service.evaluate(
        evidence=evidence,
        vendor=vendor,
        treasury=treasury,
        policy=replace(policy, version="v2", maximum_autonomous_payment_usdc=Decimal("1000")),
    )
    assert first.id != second.id
    assert first.final_action == DecisionAction.PAY
    assert second.final_action == DecisionAction.ESCALATE


def test_decision_service_rejects_cross_tenant_policy():
    evidence, vendor, treasury, policy = case()
    try:
        DecisionService().evaluate(
            evidence=evidence,
            vendor=vendor,
            treasury=treasury,
            policy=replace(policy, organization_id="org-2"),
        )
    except ValueError as exc:
        assert "another organization" in str(exc)
    else:
        raise AssertionError("Expected cross-tenant policy rejection")

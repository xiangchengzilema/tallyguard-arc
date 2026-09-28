from dataclasses import replace
from datetime import date
from decimal import Decimal
import json

import pytest

from tallyguard.models import DeliveryEvidence, Invoice, PurchaseOrder, TreasurySnapshot, Vendor
from tallyguard.policy import (
    DELIVERY_COMMITMENT_SIGNAL,
    NEAR_DUPLICATE_SIGNAL,
    PO_COMMITMENT_SIGNAL,
    VENDOR_INVOICE_NUMBER_SIGNAL,
    DecisionAction,
    Policy,
    PolicyEngine,
)


WALLET = "0x1111111111111111111111111111111111111111"


@pytest.fixture
def case():
    vendor = Vendor(
        id="vendor-1",
        organization_id="org-1",
        legal_name="Acme Data LLC",
        approved_wallet_address=WALLET,
        autopay_limit=Decimal("2500"),
    )
    invoice = Invoice(
        id="invoice-1",
        organization_id="org-1",
        vendor_id=vendor.id,
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
        vendor_id=vendor.id,
        po_number="PO-204",
        currency="USDC",
        authorized_amount=Decimal("1200"),
    )
    delivery = DeliveryEvidence(
        id="delivery-1",
        organization_id="org-1",
        purchase_order_id=po.id,
        delivered_value=Decimal("1200"),
        source_document_hash="b" * 64,
    )
    treasury = TreasurySnapshot(
        organization_id="org-1",
        available_usdc=Decimal("10000"),
        spent_today_usdc=Decimal("800"),
    )
    policy = Policy(
        version="2026-09-19.1",
        organization_id="org-1",
        daily_payment_limit_usdc=Decimal("5000"),
        minimum_cash_reserve_usdc=Decimal("3000"),
        maximum_autonomous_payment_usdc=Decimal("2000"),
        autonomous_payments_enabled=True,
    )
    return vendor, invoice, po, delivery, treasury, policy


def evaluate(case, **overrides):
    vendor, invoice, po, delivery, treasury, policy = case
    values = {
        "invoice": invoice,
        "vendor": vendor,
        "purchase_order": po,
        "delivery": delivery,
        "treasury": treasury,
        "policy": policy,
    }
    values.update(overrides)
    return PolicyEngine().evaluate(**values)


def test_clean_invoice_can_pay_autonomously(case):
    decision = evaluate(case)
    assert decision.action == DecisionAction.PAY
    assert decision.reason_codes == ()


def test_autonomous_payments_are_disabled_by_default(case):
    policy = Policy(
        version="default-off",
        organization_id="org-1",
        daily_payment_limit_usdc=Decimal("5000"),
        minimum_cash_reserve_usdc=Decimal("3000"),
        maximum_autonomous_payment_usdc=Decimal("2000"),
    )

    decision = evaluate(case, policy=policy)

    assert decision.action == DecisionAction.ESCALATE
    assert "AUTONOMOUS_PAYMENTS_DISABLED" in decision.reason_codes


def test_duplicate_invoice_is_rejected(case):
    invoice = case[1]
    decision = evaluate(case, known_invoice_fingerprints={invoice.fingerprint})
    assert decision.action == DecisionAction.REJECT
    assert "DUPLICATE_INVOICE" in decision.reason_codes


def test_reexported_vendor_invoice_number_requires_independent_review(case):
    original = case[1]
    corrected = replace(
        original,
        id="invoice-2",
        invoice_number="inv 1007",
        amount=Decimal("1201"),
        source_document_hash="c" * 64,
    )
    match = f"{VENDOR_INVOICE_NUMBER_SIGNAL}{corrected.fingerprint}:{original.id}"

    decision = evaluate(
        case,
        invoice=corrected,
        purchase_order=replace(case[2], authorized_amount=Decimal("1201")),
        delivery=replace(case[3], delivered_value=Decimal("1201")),
        known_invoice_fingerprints={match},
    )

    assert decision.action == DecisionAction.ESCALATE
    assert "VENDOR_INVOICE_NUMBER_REUSED" in decision.reason_codes
    assert original.id in next(
        rule.message for rule in decision.rule_results
        if rule.code == "VENDOR_INVOICE_NUMBER_REUSED"
    )


def test_near_matching_fields_route_to_review_but_do_not_auto_reject(case):
    invoice = case[1]
    signal = NEAR_DUPLICATE_SIGNAL + json.dumps({
        "fingerprint": invoice.fingerprint,
        "previous_id": "earlier-invoice",
        "similarity": "0.9500",
    })
    decision = evaluate(case, known_invoice_fingerprints=(signal,))
    assert decision.action == DecisionAction.ESCALATE
    assert "NEAR_DUPLICATE_INVOICE_FIELDS" in decision.reason_codes
    assert "earlier-invoice" in next(
        rule.message for rule in decision.rule_results
        if rule.code == "NEAR_DUPLICATE_INVOICE_FIELDS"
    )


@pytest.mark.parametrize("committed, expected", [
    ("400", DecisionAction.PAY),
    ("600", DecisionAction.HOLD),
])
def test_po_and_delivery_partial_invoices_respect_cumulative_value(case, committed, expected):
    invoice = case[1]
    signals = tuple(prefix + json.dumps({
        "fingerprint": invoice.fingerprint,
        "committed_usdc": committed,
        "request_ids": "earlier-invoice",
    }) for prefix in (PO_COMMITMENT_SIGNAL, DELIVERY_COMMITMENT_SIGNAL))
    po = replace(case[2], authorized_amount=Decimal("1600"))
    delivery = replace(case[3], delivered_value=Decimal("1600"))
    decision = evaluate(
        case, purchase_order=po, delivery=delivery,
        known_invoice_fingerprints=signals,
    )
    assert decision.action == expected
    if expected == DecisionAction.HOLD:
        assert "PO_CUMULATIVE_EXCEEDED" in decision.reason_codes
        assert "DELIVERY_CUMULATIVE_EXCEEDED" in decision.reason_codes


def test_changed_vendor_wallet_is_held(case):
    original = case[1]
    changed = Invoice(
        id=original.id,
        organization_id=original.organization_id,
        vendor_id=original.vendor_id,
        invoice_number=original.invoice_number,
        currency=original.currency,
        amount=original.amount,
        due_date=original.due_date,
        payment_wallet_address="0x2222222222222222222222222222222222222222",
        source_document_hash=original.source_document_hash,
    )
    decision = evaluate(case, invoice=changed)
    assert decision.action == DecisionAction.HOLD
    assert "VENDOR_WALLET_CHANGED" in decision.reason_codes


def test_recently_replaced_matching_wallet_is_held_during_cooldown(case):
    decision = evaluate(
        case,
        evaluation_date=date(2026, 9, 20),
        vendor_wallet_event_type="REPLACED",
        vendor_wallet_verified_date=date(2026, 9, 20),
    )

    assert decision.action == DecisionAction.HOLD
    assert "WALLET_CHANGE_COOLDOWN_ACTIVE" in decision.reason_codes


def test_replaced_matching_wallet_can_pay_after_cooldown(case):
    decision = evaluate(
        case,
        evaluation_date=date(2026, 9, 22),
        vendor_wallet_event_type="REPLACED",
        vendor_wallet_verified_date=date(2026, 9, 20),
    )

    assert decision.action == DecisionAction.PAY
    assert "WALLET_CHANGE_COOLDOWN_ACTIVE" not in decision.reason_codes


def test_invoice_above_po_is_held(case):
    original = case[1]
    oversized = Invoice(
        id=original.id,
        organization_id=original.organization_id,
        vendor_id=original.vendor_id,
        invoice_number=original.invoice_number,
        currency=original.currency,
        amount=Decimal("1300"),
        due_date=original.due_date,
        payment_wallet_address=original.payment_wallet_address,
        source_document_hash=original.source_document_hash,
    )
    decision = evaluate(case, invoice=oversized)
    assert decision.action == DecisionAction.HOLD
    assert "INVOICE_EXCEEDS_PO" in decision.reason_codes


def test_large_valid_invoice_escalates(case):
    vendor, original, _, delivery, treasury, policy = case
    amount = Decimal("2200")
    invoice = Invoice(
        id=original.id,
        organization_id=original.organization_id,
        vendor_id=original.vendor_id,
        invoice_number=original.invoice_number,
        currency=original.currency,
        amount=amount,
        due_date=original.due_date,
        payment_wallet_address=original.payment_wallet_address,
        source_document_hash=original.source_document_hash,
    )
    po = PurchaseOrder(
        id="po-1",
        organization_id="org-1",
        vendor_id=vendor.id,
        po_number="PO-204",
        currency="USDC",
        authorized_amount=amount,
    )
    delivery = DeliveryEvidence(
        id=delivery.id,
        organization_id="org-1",
        purchase_order_id=po.id,
        delivered_value=amount,
        source_document_hash=delivery.source_document_hash,
    )
    decision = PolicyEngine().evaluate(
        invoice=invoice,
        vendor=vendor,
        purchase_order=po,
        delivery=delivery,
        treasury=treasury,
        policy=policy,
    )
    assert decision.action == DecisionAction.ESCALATE
    assert "AUTONOMY_LIMIT_EXCEEDED" in decision.reason_codes


def test_daily_no_touch_ceiling_routes_an_otherwise_valid_payment_to_human_review(case):
    policy = replace(
        case[-1],
        daily_autonomous_payment_limit_usdc=Decimal("1500"),
    )

    decision = evaluate(case, policy=policy)

    assert decision.action == DecisionAction.ESCALATE
    assert "DAILY_AUTONOMY_LIMIT_EXCEEDED" in decision.reason_codes
    assert "DAILY_LIMIT_EXCEEDED" not in decision.reason_codes


def test_manual_spend_does_not_exhaust_separate_no_touch_ceiling(case):
    vendor, invoice, po, delivery, treasury, policy = case
    decision = evaluate(
        case,
        invoice=replace(invoice, amount=Decimal("40")),
        purchase_order=replace(po, authorized_amount=Decimal("40")),
        delivery=replace(delivery, delivered_value=Decimal("40")),
        treasury=replace(treasury, spent_today_usdc=Decimal("1486.25")),
        policy=replace(
            policy,
            maximum_autonomous_payment_usdc=Decimal("50"),
            daily_autonomous_payment_limit_usdc=Decimal("450"),
        ),
        autonomous_spent_today_usdc=Decimal("0"),
    )
    assert decision.action == DecisionAction.PAY


def test_minimum_reserve_blocks_payment(case):
    treasury = TreasurySnapshot(
        organization_id="org-1",
        available_usdc=Decimal("4000"),
        spent_today_usdc=Decimal("0"),
    )
    decision = evaluate(case, treasury=treasury)
    assert decision.action == DecisionAction.HOLD
    assert "MINIMUM_RESERVE_BREACH" in decision.reason_codes


def test_kill_switch_blocks_otherwise_clean_invoice(case):
    original = case[-1]
    policy = Policy(
        version=original.version,
        organization_id=original.organization_id,
        daily_payment_limit_usdc=original.daily_payment_limit_usdc,
        minimum_cash_reserve_usdc=original.minimum_cash_reserve_usdc,
        maximum_autonomous_payment_usdc=original.maximum_autonomous_payment_usdc,
        kill_switch_enabled=True,
    )
    decision = evaluate(case, policy=policy)
    assert decision.action == DecisionAction.HOLD
    assert "KILL_SWITCH_ACTIVE" in decision.reason_codes


def test_cross_tenant_evidence_is_rejected(case):
    foreign_delivery = replace(case[3], organization_id="org-2")
    decision = evaluate(case, delivery=foreign_delivery)
    assert decision.action == DecisionAction.REJECT
    assert "TENANT_BOUNDARY_VIOLATION" in decision.reason_codes


def test_missing_purchase_order_is_held(case):
    decision = evaluate(case, purchase_order=None)
    assert decision.action == DecisionAction.HOLD
    assert "MISSING_PURCHASE_ORDER" in decision.reason_codes
    assert "DELIVERY_WITHOUT_PURCHASE_ORDER" in decision.reason_codes


def test_missing_delivery_evidence_is_held(case):
    decision = evaluate(case, delivery=None)
    assert decision.action == DecisionAction.HOLD
    assert "MISSING_DELIVERY_EVIDENCE" in decision.reason_codes


def test_valid_future_invoice_can_be_scheduled(case):
    policy = replace(case[-1], schedule_payments_before_due_days=3)
    decision = evaluate(
        case,
        policy=policy,
        evaluation_date=date(2026, 9, 20),
    )
    assert decision.action == DecisionAction.SCHEDULE
    assert "PAYMENT_SCHEDULED_FOR_DUE_DATE" in decision.reason_codes


def test_hold_takes_priority_over_scheduling(case):
    policy = replace(case[-1], schedule_payments_before_due_days=3)
    decision = evaluate(
        case,
        policy=policy,
        delivery=None,
        evaluation_date=date(2026, 9, 20),
    )
    assert decision.action == DecisionAction.HOLD

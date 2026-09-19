from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from tallyguard.approvals import (
    ApprovalError,
    ApprovalInbox,
    ApprovalStatus,
    apply_approved_escalation,
)
from tallyguard.auth import Principal, Role
from tallyguard.decisions import DecisionService
from tallyguard.models import DeliveryEvidence, Invoice, PurchaseOrder, TreasurySnapshot, Vendor
from tallyguard.normalization import NormalizedEvidence
from tallyguard.policy import DecisionAction, Policy


WALLET = "0x1111111111111111111111111111111111111111"


def escalated_decision():
    invoice = Invoice(
        id="invoice-1",
        organization_id="org-1",
        vendor_id="vendor-1",
        invoice_number="INV-1007",
        currency="USDC",
        amount=Decimal("2200"),
        due_date=date(2026, 10, 8),
        payment_wallet_address=WALLET,
        source_document_hash="a" * 64,
    )
    evidence = NormalizedEvidence(
        package_id="package-1",
        manifest_hash="c" * 64,
        invoice=invoice,
        purchase_order=PurchaseOrder(
            id="po-1",
            organization_id="org-1",
            vendor_id="vendor-1",
            po_number="PO-204",
            currency="USDC",
            authorized_amount=Decimal("2200"),
        ),
        delivery=DeliveryEvidence(
            id="delivery-1",
            organization_id="org-1",
            purchase_order_id="po-1",
            delivered_value=Decimal("2200"),
            source_document_hash="b" * 64,
        ),
    )
    return DecisionService().evaluate(
        evidence=evidence,
        vendor=Vendor(
            id="vendor-1",
            organization_id="org-1",
            legal_name="Acme",
            approved_wallet_address=WALLET,
            autopay_limit=Decimal("2500"),
        ),
        treasury=TreasurySnapshot(
            organization_id="org-1",
            available_usdc=Decimal("10000"),
            spent_today_usdc=Decimal("0"),
        ),
        policy=Policy(
            version="v1",
            organization_id="org-1",
            daily_payment_limit_usdc=Decimal("5000"),
            minimum_cash_reserve_usdc=Decimal("1000"),
            maximum_autonomous_payment_usdc=Decimal("2000"),
        ),
    )


def operator(user_id="operator-1"):
    return Principal(user_id=user_id, organization_id="org-1", roles=(Role.FINANCE_OPERATOR,))


def approver(user_id="approver-1"):
    return Principal(user_id=user_id, organization_id="org-1", roles=(Role.APPROVER,))


def test_escalation_requires_separate_approver_and_binds_authorization():
    decision = escalated_decision()
    assert decision.final_action == DecisionAction.ESCALATE
    inbox = ApprovalInbox()
    request = inbox.request(decision, requested_by=operator())
    resolved = inbox.resolve(
        organization_id="org-1",
        approval_id=request.id,
        approver=approver(),
        approve=True,
        resolution_note="Contract and delivery evidence reviewed.",
        expected_version=1,
    )
    authorized = apply_approved_escalation(decision, resolved)
    assert resolved.status == ApprovalStatus.APPROVED
    assert authorized.action == DecisionAction.PAY
    assert authorized.approval_reference == resolved.id
    assert any(result.code == "APPROVED_AUTONOMY_LIMIT_EXCEEDED" for result in authorized.rule_results)


def test_requester_cannot_approve_own_exception():
    decision = escalated_decision()
    inbox = ApprovalInbox()
    requester = Principal(
        user_id="same-user",
        organization_id="org-1",
        roles=(Role.FINANCE_OPERATOR, Role.APPROVER),
    )
    request = inbox.request(decision, requested_by=requester)
    with pytest.raises(ApprovalError, match="different users"):
        inbox.resolve(
            organization_id="org-1",
            approval_id=request.id,
            approver=requester,
            approve=True,
            resolution_note="Self approval attempt.",
            expected_version=1,
        )


def test_hold_cannot_enter_approval_inbox():
    decision = escalated_decision()
    held = replace(decision, final_action=DecisionAction.HOLD)
    with pytest.raises(ApprovalError, match="Only an ESCALATE"):
        ApprovalInbox().request(held, requested_by=operator())


def test_rejected_approval_cannot_authorize_payment():
    decision = escalated_decision()
    inbox = ApprovalInbox()
    request = inbox.request(decision, requested_by=operator())
    rejected = inbox.resolve(
        organization_id="org-1",
        approval_id=request.id,
        approver=approver(),
        approve=False,
        resolution_note="Amount needs a new contract.",
        expected_version=1,
    )
    with pytest.raises(ApprovalError, match="not approved"):
        apply_approved_escalation(decision, rejected)


def test_optimistic_version_prevents_double_resolution():
    decision = escalated_decision()
    inbox = ApprovalInbox()
    request = inbox.request(decision, requested_by=operator())
    inbox.resolve(
        organization_id="org-1",
        approval_id=request.id,
        approver=approver(),
        approve=True,
        resolution_note="Reviewed.",
        expected_version=1,
    )
    with pytest.raises(ApprovalError, match="already been resolved"):
        inbox.resolve(
            organization_id="org-1",
            approval_id=request.id,
            approver=approver("approver-2"),
            approve=False,
            resolution_note="Stale competing response.",
            expected_version=1,
        )

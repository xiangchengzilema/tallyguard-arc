from datetime import date
from decimal import Decimal

import pytest

from tallyguard.models import Invoice
from tallyguard.network import ArcNetwork, ArcNetworkConfig
from tallyguard.payments import PaymentOrchestrator
from tallyguard.persistence import SqliteRepository
from tallyguard.policy import Decision, DecisionAction
from tallyguard.settlement import (
    PaymentIntent,
    ProviderSubmission,
    SettlementDenied,
    SettlementService,
    SimulatedArcAdapter,
)
from tallyguard.workflow import InvoiceStatus, WorkflowError


WALLET = "0x1111111111111111111111111111111111111111"


def authorized_decision() -> Decision:
    return Decision(
        action=DecisionAction.PAY,
        policy_version="v1",
        invoice_fingerprint="a" * 64,
        rule_results=(),
    )


def ready_repository(database) -> tuple[SqliteRepository, object]:
    repo = SqliteRepository(database)
    repo.create_organization(organization_id="org-1", name="Northwind AI")
    stored = repo.create_invoice(
        Invoice(
            id="invoice-1",
            organization_id="org-1",
            vendor_id="vendor-1",
            invoice_number="INV-1007",
            currency="USDC",
            amount=Decimal("1.25"),
            due_date=date(2026, 10, 8),
            payment_wallet_address=WALLET,
            source_document_hash="a" * 64,
        ),
        status=InvoiceStatus.READY,
    )
    return repo, stored


def orchestrator(repo, adapter):
    config = ArcNetworkConfig.for_network(ArcNetwork.TESTNET)
    return PaymentOrchestrator(
        repository=repo,
        settlement_service=SettlementService(config=config, adapter=adapter),
        network=config,
    )


def test_payment_workflow_reaches_reconciled_and_retry_is_exactly_once(tmp_path):
    repo, stored = ready_repository(tmp_path / "payments.sqlite3")
    adapter = SimulatedArcAdapter()
    service = orchestrator(repo, adapter)

    first = service.settle(
        invoice=stored,
        decision_id="decision-1",
        decision=authorized_decision(),
        actor_user_id="approver-1",
        correlation_id="request-1",
    )
    second = service.settle(
        invoice=stored,
        decision_id="decision-1",
        decision=authorized_decision(),
        actor_user_id="approver-1",
        correlation_id="request-2",
    )

    assert first.invoice.status == InvoiceStatus.RECONCILED
    assert second.reused_receipt is True
    assert second.receipt == first.receipt
    assert first.intent.idempotency_key == second.intent.idempotency_key
    assert adapter.submission_count == 1
    assert [item.to_status for item in repo.transitions(
        organization_id="org-1", invoice_id="invoice-1"
    )] == [
        InvoiceStatus.SUBMITTING,
        InvoiceStatus.SUBMITTED,
        InvoiceStatus.CONFIRMED,
        InvoiceStatus.RECONCILED,
    ]


def test_persisted_receipt_prevents_resubmission_after_process_restart(tmp_path):
    database = tmp_path / "payments.sqlite3"
    repo, stored = ready_repository(database)
    first_adapter = SimulatedArcAdapter()
    first = orchestrator(repo, first_adapter).settle(
        invoice=stored,
        decision_id="decision-1",
        decision=authorized_decision(),
        actor_user_id="approver-1",
        correlation_id="request-1",
    )
    repo.close()

    reopened = SqliteRepository(database)
    second_adapter = SimulatedArcAdapter()
    second = orchestrator(reopened, second_adapter).settle(
        invoice=reopened.get_invoice(organization_id="org-1", invoice_id="invoice-1"),
        decision_id="decision-1",
        decision=authorized_decision(),
        actor_user_id="approver-1",
        correlation_id="request-2",
    )

    assert second.receipt == first.receipt
    assert second.reused_receipt is True
    assert second_adapter.submission_count == 0


class WrongRecipientAdapter:
    name = "fault-injection"

    def submit(self, intent: PaymentIntent) -> ProviderSubmission:
        return ProviderSubmission(
            provider_reference="bad-1",
            transaction_hash="0x" + "b" * 64,
            recipient="0x2222222222222222222222222222222222222222",
            amount_usdc=intent.amount_usdc,
            network=intent.network,
            block_number=1,
        )


def test_failed_submission_is_retryable_with_the_same_persisted_intent(tmp_path):
    repo, stored = ready_repository(tmp_path / "payments.sqlite3")
    with pytest.raises(SettlementDenied, match="wrong recipient"):
        orchestrator(repo, WrongRecipientAdapter()).settle(
            invoice=stored,
            decision_id="decision-1",
            decision=authorized_decision(),
            actor_user_id="approver-1",
            correlation_id="request-1",
        )
    failed = repo.get_invoice(organization_id="org-1", invoice_id="invoice-1")
    assert failed.status == InvoiceStatus.SUBMISSION_FAILED
    original_intent = repo.get_payment_intent_for_decision(
        organization_id="org-1", decision_id="decision-1"
    )

    success = orchestrator(repo, SimulatedArcAdapter()).settle(
        invoice=failed,
        decision_id="decision-1",
        decision=authorized_decision(),
        actor_user_id="approver-1",
        correlation_id="request-2",
    )
    assert success.invoice.status == InvoiceStatus.RECONCILED
    assert success.intent.idempotency_key == original_intent.idempotency_key


def test_non_usdc_invoice_never_creates_payment_intent(tmp_path):
    repo, stored = ready_repository(tmp_path / "payments.sqlite3")
    euro_invoice = Invoice(
        id=stored.invoice.id,
        organization_id=stored.invoice.organization_id,
        vendor_id=stored.invoice.vendor_id,
        invoice_number=stored.invoice.invoice_number,
        currency="EUR",
        amount=stored.invoice.amount,
        due_date=stored.invoice.due_date,
        payment_wallet_address=stored.invoice.payment_wallet_address,
        source_document_hash=stored.invoice.source_document_hash,
    )
    euro_stored = type(stored)(
        invoice=euro_invoice,
        status=stored.status,
        version=stored.version,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
    )

    with pytest.raises(WorkflowError, match="Only USDC"):
        orchestrator(repo, SimulatedArcAdapter()).settle(
            invoice=euro_stored,
            decision_id="decision-1",
            decision=authorized_decision(),
            actor_user_id="approver-1",
            correlation_id="request-1",
        )

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier

import pytest

from tallyguard.auth import Role
from tallyguard.models import Invoice, TreasurySnapshot
from tallyguard.network import ArcNetwork, ArcNetworkConfig
from tallyguard.payments import PaymentOrchestrator
from tallyguard.persistence import PersistenceError, SettlementExecutionBlocked, SqliteRepository
from tallyguard.policy import Decision, DecisionAction, Policy
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


def test_atomic_treasury_reservation_prevents_concurrent_daily_limit_overspend(tmp_path):
    database = tmp_path / "concurrent-reservations.sqlite3"
    recorded_at = datetime.now(timezone.utc)
    setup = SqliteRepository(database)
    setup.create_organization(organization_id="org-1", name="Northwind AI")
    setup.create_user(
        organization_id="org-1",
        user_id="admin-1",
        display_name="Policy Admin",
        roles=(Role.ADMIN.value,),
    )
    setup.create_user(
        organization_id="org-1",
        user_id="operator-1",
        display_name="Finance Operator",
        roles=(Role.FINANCE_OPERATOR.value,),
    )
    setup.activate_policy(
        Policy(
            version="v1",
            organization_id="org-1",
            daily_payment_limit_usdc=Decimal("100"),
            minimum_cash_reserve_usdc=Decimal("20"),
            maximum_autonomous_payment_usdc=Decimal("100"),
        ),
        activated_by_user_id="admin-1",
        activated_at=recorded_at,
    )
    setup.record_treasury_snapshot(
        TreasurySnapshot(
            organization_id="org-1",
            available_usdc=Decimal("140"),
            spent_today_usdc=Decimal("0"),
        ),
        source_reference="circle-balance-before-race",
        recorded_by_user_id="operator-1",
        recorded_at=recorded_at,
    )
    for index in (1, 2):
        setup.create_invoice(
            Invoice(
                id=f"invoice-{index}",
                organization_id="org-1",
                vendor_id="vendor-1",
                invoice_number=f"INV-{index}",
                currency="USDC",
                amount=Decimal("70"),
                due_date=date(2026, 10, 8),
                payment_wallet_address=WALLET,
                source_document_hash=str(index) * 64,
            ),
            status=InvoiceStatus.READY,
            created_at=recorded_at,
        )
    setup.close()

    barrier = Barrier(2)

    def reserve(index: int) -> str:
        repo = SqliteRepository(database)
        intent = PaymentIntent(
            id=f"payment-{index}",
            organization_id="org-1",
            invoice_id=f"invoice-{index}",
            decision_id=f"decision-{index}",
            recipient=WALLET,
            amount_usdc=Decimal("70"),
            network=ArcNetwork.TESTNET,
            idempotency_key=f"key-{index}",
        )
        barrier.wait()
        try:
            repo.create_or_get_payment_intent(
                intent,
                created_at=recorded_at + timedelta(seconds=index),
                enforce_active_controls=True,
            )
            return "ACCEPTED"
        except SettlementExecutionBlocked as exc:
            return exc.control_code
        finally:
            repo.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(reserve, (1, 2)))

    assert sorted(outcomes) == ["ACCEPTED", "DAILY_LIMIT_EXCEEDED"]


def test_treasury_reservation_fails_closed_on_stale_snapshot(tmp_path):
    repo, stored = ready_repository(tmp_path / "stale-treasury.sqlite3")
    observed_at = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)
    repo.create_user(
        organization_id="org-1",
        user_id="admin-1",
        display_name="Policy Admin",
        roles=(Role.ADMIN.value,),
    )
    repo.create_user(
        organization_id="org-1",
        user_id="operator-1",
        display_name="Finance Operator",
        roles=(Role.FINANCE_OPERATOR.value,),
    )
    repo.activate_policy(
        Policy(
            version="v1",
            organization_id="org-1",
            daily_payment_limit_usdc=Decimal("100"),
            minimum_cash_reserve_usdc=Decimal("20"),
            maximum_autonomous_payment_usdc=Decimal("100"),
        ),
        activated_by_user_id="admin-1",
        activated_at=observed_at,
    )
    repo.record_treasury_snapshot(
        TreasurySnapshot(
            organization_id="org-1",
            available_usdc=Decimal("140"),
            spent_today_usdc=Decimal("0"),
        ),
        source_reference="stale-circle-balance",
        recorded_by_user_id="operator-1",
        recorded_at=observed_at,
    )
    intent = PaymentIntent(
        id="payment-stale",
        organization_id="org-1",
        invoice_id=stored.invoice.id,
        decision_id="decision-stale",
        recipient=WALLET,
        amount_usdc=Decimal("1.25"),
        network=ArcNetwork.TESTNET,
        idempotency_key="key-stale",
    )

    with pytest.raises(SettlementExecutionBlocked) as blocked:
        repo.create_or_get_payment_intent(
            intent,
            created_at=observed_at + timedelta(minutes=16),
            enforce_active_controls=True,
            maximum_snapshot_age=timedelta(minutes=15),
        )

    assert blocked.value.control_code == "TREASURY_SNAPSHOT_STALE"
    with pytest.raises(PersistenceError, match="not found"):
        repo.get_payment_intent_for_decision(
            organization_id="org-1",
            decision_id="decision-stale",
        )

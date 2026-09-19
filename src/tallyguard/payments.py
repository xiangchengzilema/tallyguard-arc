"""Durable orchestration from an authorized invoice to a reconciled receipt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import uuid4

from .network import ArcNetworkConfig
from .persistence import SqliteRepository, StoredInvoice
from .policy import Decision
from .settlement import (
    PaymentIntent,
    SettlementAttempt,
    SettlementAttemptOutcome,
    SettlementReceipt,
    SettlementReconciliationMismatch,
    SettlementService,
    SettlementUnavailable,
)
from .workflow import InvoiceStatus, WorkflowError


@dataclass(frozen=True, slots=True)
class PaymentOutcome:
    intent: PaymentIntent
    receipt: SettlementReceipt
    invoice: StoredInvoice
    reused_receipt: bool


class PaymentOrchestrator:
    """Coordinates crash-safe local state around provider-level idempotency."""

    def __init__(
        self,
        *,
        repository: SqliteRepository,
        settlement_service: SettlementService,
        network: ArcNetworkConfig,
        enforce_execution_controls: bool = False,
        maximum_snapshot_age: timedelta = timedelta(minutes=15),
    ) -> None:
        if maximum_snapshot_age <= timedelta(0):
            raise ValueError("Treasury snapshot maximum age must be positive.")
        self.repository = repository
        self.settlement_service = settlement_service
        self.network = network
        self.enforce_execution_controls = enforce_execution_controls
        self.maximum_snapshot_age = maximum_snapshot_age

    def settle(
        self,
        *,
        invoice: StoredInvoice,
        decision_id: str,
        decision: Decision,
        actor_user_id: str,
        correlation_id: str,
    ) -> PaymentOutcome:
        if invoice.invoice.currency != "USDC":
            raise WorkflowError("Only USDC invoices can enter Arc settlement.")
        if not decision_id.strip():
            raise WorkflowError("A payment must be bound to a decision ID.")

        proposed = PaymentIntent(
            id=self._payment_id(invoice.invoice.organization_id, decision_id),
            organization_id=invoice.invoice.organization_id,
            invoice_id=invoice.invoice.id,
            decision_id=decision_id,
            recipient=invoice.invoice.payment_wallet_address,
            amount_usdc=invoice.invoice.amount,
            network=self.network.name,
            idempotency_key=str(uuid4()),
            approval_reference=decision.approval_reference,
        )
        intent, _ = self.repository.create_or_get_payment_intent(
            proposed,
            enforce_active_controls=self.enforce_execution_controls,
            maximum_snapshot_age=self.maximum_snapshot_age,
        )
        existing = self.repository.find_settlement_receipt(
            organization_id=intent.organization_id,
            payment_intent_id=intent.id,
        )
        if existing is not None:
            return self._outcome_from_existing(
                intent=intent,
                receipt=existing,
                actor_user_id=actor_user_id,
                correlation_id=correlation_id,
            )

        submitting = self._enter_submitting(
            organization_id=intent.organization_id,
            invoice_id=intent.invoice_id,
            payment_intent_id=intent.id,
            actor_user_id=actor_user_id,
            correlation_id=correlation_id,
        )
        raced_receipt = self.repository.find_settlement_receipt(
            organization_id=intent.organization_id,
            payment_intent_id=intent.id,
        )
        if raced_receipt is not None:
            return self._outcome_from_existing(
                intent=intent,
                receipt=raced_receipt,
                actor_user_id=actor_user_id,
                correlation_id=correlation_id,
            )
        if submitting.status != InvoiceStatus.SUBMITTING:
            raise WorkflowError(
                "Invoice advanced without a durable settlement receipt."
            )
        try:
            receipt = self.settlement_service.execute(intent=intent, decision=decision)
        except SettlementUnavailable as exc:
            self._record_attempt(
                intent=intent,
                outcome=SettlementAttemptOutcome.FAILED_RETRYABLE,
                retryable=True,
                error_code="SETTLEMENT_UNAVAILABLE",
                error_message=str(exc),
                correlation_id=correlation_id,
            )
            self._mark_submission_failed(
                organization_id=intent.organization_id,
                invoice_id=intent.invoice_id,
                actor_user_id=actor_user_id,
                correlation_id=correlation_id,
            )
            raise
        except SettlementReconciliationMismatch as exc:
            self._record_attempt(
                intent=intent,
                outcome=SettlementAttemptOutcome.RECONCILIATION_MISMATCH,
                retryable=False,
                error_code="RECONCILIATION_MISMATCH",
                error_message=str(exc),
                correlation_id=correlation_id,
            )
            self._mark_reconciliation_mismatch(
                organization_id=intent.organization_id,
                invoice_id=intent.invoice_id,
                actor_user_id=actor_user_id,
                correlation_id=correlation_id,
            )
            raise
        except Exception:
            self._record_attempt(
                intent=intent,
                outcome=SettlementAttemptOutcome.FAILED_LOCKED,
                retryable=False,
                error_code="UNCLASSIFIED_PROVIDER_ERROR",
                error_message="Settlement failed before a verified receipt was persisted.",
                correlation_id=correlation_id,
            )
            self._mark_submission_failed(
                organization_id=intent.organization_id,
                invoice_id=intent.invoice_id,
                actor_user_id=actor_user_id,
                correlation_id=correlation_id,
            )
            raise

        stored_receipt, created = self.repository.save_settlement_receipt(receipt)
        if created:
            self._record_attempt(
                intent=intent,
                outcome=SettlementAttemptOutcome.CONFIRMED,
                retryable=False,
                error_code=None,
                error_message=None,
                correlation_id=correlation_id,
            )
        completed = self._advance_to_reconciled(
            organization_id=intent.organization_id,
            invoice_id=intent.invoice_id,
            actor_user_id=actor_user_id,
            correlation_id=correlation_id,
        )
        return PaymentOutcome(
            intent=intent,
            receipt=stored_receipt,
            invoice=completed,
            reused_receipt=not created,
        )

    def _enter_submitting(
        self,
        *,
        organization_id: str,
        invoice_id: str,
        payment_intent_id: str,
        actor_user_id: str,
        correlation_id: str,
    ) -> StoredInvoice:
        in_flight = {
            InvoiceStatus.SUBMITTING,
            InvoiceStatus.SUBMITTED,
            InvoiceStatus.CONFIRMED,
            InvoiceStatus.RECONCILED,
        }
        for _ in range(8):
            current = self.repository.get_invoice(
                organization_id=organization_id,
                invoice_id=invoice_id,
            )
            if current.status in in_flight:
                return current
            if current.status not in {InvoiceStatus.READY, InvoiceStatus.SUBMISSION_FAILED}:
                raise WorkflowError(
                    f"Invoice must be READY or retryable before settlement, not {current.status.value}."
                )
            if current.status == InvoiceStatus.SUBMISSION_FAILED:
                latest = self.repository.latest_settlement_attempt(
                    organization_id=organization_id,
                    payment_intent_id=payment_intent_id,
                )
                if latest is None or not latest.retryable:
                    raise WorkflowError(
                        "Settlement failure is locked for manual investigation and cannot be retried automatically."
                    )
            try:
                return self.repository.transition_invoice(
                    organization_id=organization_id,
                    invoice_id=invoice_id,
                    target_status=InvoiceStatus.SUBMITTING,
                    expected_version=current.version,
                    actor_user_id=actor_user_id,
                    correlation_id=correlation_id,
                )
            except WorkflowError as exc:
                if "another operation" not in str(exc):
                    raise
        raise WorkflowError("Invoice did not converge on a submitting state.")

    def _outcome_from_existing(
        self,
        *,
        intent: PaymentIntent,
        receipt: SettlementReceipt,
        actor_user_id: str,
        correlation_id: str,
    ) -> PaymentOutcome:
        completed = self._advance_to_reconciled(
            organization_id=intent.organization_id,
            invoice_id=intent.invoice_id,
            actor_user_id=actor_user_id,
            correlation_id=correlation_id,
        )
        return PaymentOutcome(
            intent=intent,
            receipt=receipt,
            invoice=completed,
            reused_receipt=True,
        )

    def _mark_submission_failed(
        self,
        *,
        organization_id: str,
        invoice_id: str,
        actor_user_id: str,
        correlation_id: str,
    ) -> None:
        current = self.repository.get_invoice(
            organization_id=organization_id,
            invoice_id=invoice_id,
        )
        if current.status != InvoiceStatus.SUBMITTING:
            return
        try:
            self.repository.transition_invoice(
                organization_id=organization_id,
                invoice_id=invoice_id,
                target_status=InvoiceStatus.SUBMISSION_FAILED,
                expected_version=current.version,
                actor_user_id=actor_user_id,
                correlation_id=correlation_id,
            )
        except WorkflowError:
            # A concurrent retry may already have advanced the same invoice.
            return

    def _mark_reconciliation_mismatch(
        self,
        *,
        organization_id: str,
        invoice_id: str,
        actor_user_id: str,
        correlation_id: str,
    ) -> None:
        current = self.repository.get_invoice(
            organization_id=organization_id,
            invoice_id=invoice_id,
        )
        if current.status != InvoiceStatus.SUBMITTING:
            return
        try:
            self.repository.transition_invoice(
                organization_id=organization_id,
                invoice_id=invoice_id,
                target_status=InvoiceStatus.RECONCILIATION_MISMATCH,
                expected_version=current.version,
                actor_user_id=actor_user_id,
                correlation_id=correlation_id,
            )
        except WorkflowError:
            return

    def _record_attempt(
        self,
        *,
        intent: PaymentIntent,
        outcome: SettlementAttemptOutcome,
        retryable: bool,
        error_code: str | None,
        error_message: str | None,
        correlation_id: str,
    ) -> SettlementAttempt:
        return self.repository.record_settlement_attempt(
            SettlementAttempt(
                sequence=None,
                organization_id=intent.organization_id,
                payment_intent_id=intent.id,
                invoice_id=intent.invoice_id,
                provider=self.settlement_service.adapter.name,
                outcome=outcome,
                retryable=retryable,
                error_code=error_code,
                error_message=error_message,
                correlation_id=correlation_id,
                created_at=datetime.now(timezone.utc),
            )
        )

    def _advance_to_reconciled(
        self,
        *,
        organization_id: str,
        invoice_id: str,
        actor_user_id: str,
        correlation_id: str,
    ) -> StoredInvoice:
        next_status = {
            InvoiceStatus.SUBMITTING: InvoiceStatus.SUBMITTED,
            InvoiceStatus.SUBMITTED: InvoiceStatus.CONFIRMED,
            InvoiceStatus.CONFIRMED: InvoiceStatus.RECONCILED,
        }
        for _ in range(8):
            current = self.repository.get_invoice(
                organization_id=organization_id,
                invoice_id=invoice_id,
            )
            if current.status == InvoiceStatus.RECONCILED:
                return current
            target = next_status.get(current.status)
            if target is None:
                raise WorkflowError(
                    f"Invoice cannot reconcile from state {current.status.value}."
                )
            try:
                self.repository.transition_invoice(
                    organization_id=organization_id,
                    invoice_id=invoice_id,
                    target_status=target,
                    expected_version=current.version,
                    actor_user_id=actor_user_id,
                    correlation_id=correlation_id,
                )
            except WorkflowError as exc:
                if "another operation" not in str(exc):
                    raise
        raise WorkflowError("Invoice reconciliation did not converge after concurrent updates.")

    @staticmethod
    def _payment_id(organization_id: str, decision_id: str) -> str:
        digest = hashlib.sha256(f"{organization_id}:{decision_id}".encode("utf-8")).hexdigest()
        return "payment_" + digest[:24]

"""Invoice lifecycle transitions used by the API and workers."""

from __future__ import annotations

from enum import StrEnum

from .policy import DecisionAction


class InvoiceStatus(StrEnum):
    DRAFT = "DRAFT"
    EVIDENCE_PENDING = "EVIDENCE_PENDING"
    EVALUATING = "EVALUATING"
    READY = "READY"
    HOLD = "HOLD"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
    SCHEDULED = "SCHEDULED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    RECONCILED = "RECONCILED"
    SUBMISSION_FAILED = "SUBMISSION_FAILED"
    CONFIRMATION_TIMEOUT = "CONFIRMATION_TIMEOUT"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    CANCELLED = "CANCELLED"


ALLOWED_TRANSITIONS: dict[InvoiceStatus, frozenset[InvoiceStatus]] = {
    InvoiceStatus.DRAFT: frozenset(
        {InvoiceStatus.EVIDENCE_PENDING, InvoiceStatus.EVALUATING, InvoiceStatus.CANCELLED}
    ),
    InvoiceStatus.EVIDENCE_PENDING: frozenset({InvoiceStatus.EVALUATING, InvoiceStatus.CANCELLED}),
    InvoiceStatus.EVALUATING: frozenset(
        {
            InvoiceStatus.READY,
            InvoiceStatus.HOLD,
            InvoiceStatus.REJECTED,
            InvoiceStatus.ESCALATED,
            InvoiceStatus.SCHEDULED,
        }
    ),
    InvoiceStatus.HOLD: frozenset(
        {InvoiceStatus.EVIDENCE_PENDING, InvoiceStatus.EVALUATING, InvoiceStatus.CANCELLED}
    ),
    InvoiceStatus.ESCALATED: frozenset(
        {InvoiceStatus.READY, InvoiceStatus.REJECTED, InvoiceStatus.CANCELLED}
    ),
    InvoiceStatus.SCHEDULED: frozenset({InvoiceStatus.READY, InvoiceStatus.CANCELLED}),
    InvoiceStatus.READY: frozenset({InvoiceStatus.SUBMITTING, InvoiceStatus.CANCELLED}),
    InvoiceStatus.SUBMITTING: frozenset(
        {InvoiceStatus.SUBMITTED, InvoiceStatus.SUBMISSION_FAILED}
    ),
    InvoiceStatus.SUBMISSION_FAILED: frozenset(
        {InvoiceStatus.SUBMITTING, InvoiceStatus.CANCELLED}
    ),
    InvoiceStatus.SUBMITTED: frozenset(
        {
            InvoiceStatus.CONFIRMED,
            InvoiceStatus.CONFIRMATION_TIMEOUT,
            InvoiceStatus.RECONCILIATION_MISMATCH,
        }
    ),
    InvoiceStatus.CONFIRMATION_TIMEOUT: frozenset(
        {InvoiceStatus.SUBMITTED, InvoiceStatus.CANCELLED}
    ),
    InvoiceStatus.CONFIRMED: frozenset(
        {InvoiceStatus.RECONCILED, InvoiceStatus.RECONCILIATION_MISMATCH}
    ),
    InvoiceStatus.REJECTED: frozenset(),
    InvoiceStatus.RECONCILED: frozenset(),
    InvoiceStatus.RECONCILIATION_MISMATCH: frozenset(),
    InvoiceStatus.CANCELLED: frozenset(),
}


DECISION_STATUS: dict[DecisionAction, InvoiceStatus] = {
    DecisionAction.PAY: InvoiceStatus.READY,
    DecisionAction.SCHEDULE: InvoiceStatus.SCHEDULED,
    DecisionAction.HOLD: InvoiceStatus.HOLD,
    DecisionAction.REJECT: InvoiceStatus.REJECTED,
    DecisionAction.ESCALATE: InvoiceStatus.ESCALATED,
}


class WorkflowError(RuntimeError):
    """Raised when an invoice transition is illegal or stale."""


def require_transition(current: InvoiceStatus, target: InvoiceStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise WorkflowError(f"Illegal invoice transition: {current.value} -> {target.value}")


def status_for_decision(action: DecisionAction) -> InvoiceStatus:
    return DECISION_STATUS[action]

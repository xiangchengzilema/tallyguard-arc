"""Bounded autonomous accounts-payable planning primitives.

The planner is intentionally deterministic.  An AI analyst may explain evidence,
but only persisted workflow state and policy decisions can place a settlement in
an executable plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from .audit import canonical_json
from .workflow import InvoiceStatus


class AgentAction(StrEnum):
    SETTLE = "SETTLE"
    RETRY_SETTLEMENT = "RETRY_SETTLEMENT"
    WAIT_SCHEDULE = "WAIT_SCHEDULE"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    REMEDIATE = "REMEDIATE"
    INVESTIGATE = "INVESTIGATE"
    COLLECT_EVIDENCE = "COLLECT_EVIDENCE"


class AgentRunStatus(StrEnum):
    PLANNED = "PLANNED"
    EXECUTED = "EXECUTED"
    PARTIAL = "PARTIAL"


EXECUTABLE_ACTIONS = frozenset({AgentAction.SETTLE, AgentAction.RETRY_SETTLEMENT})


@dataclass(frozen=True, slots=True)
class AgentCandidate:
    invoice_id: str
    invoice_number: str
    amount_usdc: str
    due_date: date
    status: InvoiceStatus
    version: int
    decision_id: str | None
    decision_action: str | None
    scheduled_for: date | None
    settlement_retryable: bool

    def state_payload(self) -> dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "status": self.status.value,
            "version": self.version,
            "decision_id": self.decision_id,
            "decision_action": self.decision_action,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "settlement_retryable": self.settlement_retryable,
        }


@dataclass(frozen=True, slots=True)
class AgentPlanItem:
    invoice_id: str
    invoice_number: str
    amount_usdc: str
    due_date: date
    invoice_status: InvoiceStatus
    invoice_version: int
    decision_id: str | None
    decision_action: str | None
    action: AgentAction
    reason_code: str
    explanation: str
    executable: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "invoice_number": self.invoice_number,
            "amount_usdc": self.amount_usdc,
            "due_date": self.due_date.isoformat(),
            "invoice_status": self.invoice_status.value,
            "invoice_version": self.invoice_version,
            "decision_id": self.decision_id,
            "decision_action": self.decision_action,
            "action": self.action.value,
            "reason_code": self.reason_code,
            "explanation": self.explanation,
            "executable": self.executable,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AgentPlanItem":
        return cls(
            invoice_id=str(payload["invoice_id"]),
            invoice_number=str(payload["invoice_number"]),
            amount_usdc=str(payload["amount_usdc"]),
            due_date=date.fromisoformat(str(payload["due_date"])),
            invoice_status=InvoiceStatus(str(payload["invoice_status"])),
            invoice_version=int(payload["invoice_version"]),
            decision_id=(str(payload["decision_id"]) if payload.get("decision_id") else None),
            decision_action=(
                str(payload["decision_action"]) if payload.get("decision_action") else None
            ),
            action=AgentAction(str(payload["action"])),
            reason_code=str(payload["reason_code"]),
            explanation=str(payload["explanation"]),
            executable=bool(payload["executable"]),
        )


@dataclass(frozen=True, slots=True)
class AgentRun:
    id: str
    organization_id: str
    status: AgentRunStatus
    as_of: date
    state_hash: str
    plan_hash: str
    items: tuple[AgentPlanItem, ...]
    created_by_user_id: str
    created_at: datetime
    executed_by_user_id: str | None = None
    executed_at: datetime | None = None
    results: tuple[dict[str, Any], ...] = ()


def plan_candidate(candidate: AgentCandidate) -> AgentPlanItem:
    action: AgentAction
    reason: str
    explanation: str

    if (
        candidate.status == InvoiceStatus.READY
        and candidate.decision_action == "PAY"
        and candidate.decision_id
    ):
        action = AgentAction.SETTLE
        reason = "POLICY_CLEARED_FOR_SETTLEMENT"
        explanation = "Evidence and policy evaluation cleared this invoice for idempotent settlement."
    elif (
        candidate.status in {InvoiceStatus.SUBMISSION_FAILED, InvoiceStatus.CONFIRMATION_TIMEOUT}
        and candidate.decision_action == "PAY"
        and candidate.decision_id
        and candidate.settlement_retryable
    ):
        action = AgentAction.RETRY_SETTLEMENT
        reason = "LATEST_ATTEMPT_EXPLICITLY_RETRYABLE"
        explanation = "The durable provider ledger permits a retry with the original payment intent."
    elif candidate.status == InvoiceStatus.SCHEDULED:
        action = AgentAction.WAIT_SCHEDULE
        reason = "SCHEDULE_BOUNDARY_NOT_RELEASED"
        explanation = (
            f"Payment remains scheduled for {candidate.scheduled_for.isoformat()}."
            if candidate.scheduled_for
            else "Payment remains governed by its deterministic schedule."
        )
    elif candidate.status == InvoiceStatus.ESCALATED:
        action = AgentAction.REQUIRE_APPROVAL
        reason = "SEGREGATED_APPROVAL_REQUIRED"
        explanation = "A separate approver must resolve the escalation before settlement."
    elif candidate.status == InvoiceStatus.HOLD:
        action = AgentAction.REMEDIATE
        reason = "POLICY_HOLD_REQUIRES_REMEDIATION"
        explanation = "The agent cannot move funds until the blocking evidence or policy issue is remediated."
    elif candidate.status == InvoiceStatus.RECONCILIATION_MISMATCH:
        action = AgentAction.INVESTIGATE
        reason = "TERMINAL_RECONCILIATION_MISMATCH"
        explanation = "Receipt evidence conflicts with the intent and is locked for manual investigation."
    elif candidate.status in {InvoiceStatus.SUBMISSION_FAILED, InvoiceStatus.CONFIRMATION_TIMEOUT}:
        action = AgentAction.INVESTIGATE
        reason = "SETTLEMENT_RETRY_LOCKED"
        explanation = "The latest settlement state does not explicitly authorize an automatic retry."
    else:
        action = AgentAction.COLLECT_EVIDENCE
        reason = "WORKFLOW_NOT_POLICY_CLEARED"
        explanation = "The invoice must complete evidence intake and deterministic evaluation first."

    return AgentPlanItem(
        invoice_id=candidate.invoice_id,
        invoice_number=candidate.invoice_number,
        amount_usdc=candidate.amount_usdc,
        due_date=candidate.due_date,
        invoice_status=candidate.status,
        invoice_version=candidate.version,
        decision_id=candidate.decision_id,
        decision_action=candidate.decision_action,
        action=action,
        reason_code=reason,
        explanation=explanation,
        executable=action in EXECUTABLE_ACTIONS,
    )


def plan_payload(items: tuple[AgentPlanItem, ...], *, as_of: date) -> dict[str, Any]:
    return {
        "as_of": as_of.isoformat(),
        "items": [item.to_payload() for item in items],
    }


def state_payload(candidates: tuple[AgentCandidate, ...], *, as_of: date) -> dict[str, Any]:
    return {
        "as_of": as_of.isoformat(),
        "candidates": [candidate.state_payload() for candidate in candidates],
    }


def canonical_payload(value: dict[str, Any]) -> str:
    return canonical_json(value)

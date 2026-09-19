"""Segregated human approval for policy escalations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
from threading import Lock

from .auth import Permission, Principal, authorize
from .decisions import DecisionRecord
from .policy import Decision, DecisionAction, RuleDisposition, RuleResult


class ApprovalError(RuntimeError):
    """Raised when an approval invariant is violated."""


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    id: str
    organization_id: str
    invoice_id: str
    decision_id: str
    requested_by_user_id: str
    requested_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    version: int = 1
    resolved_by_user_id: str | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None


class ApprovalInbox:
    def __init__(self) -> None:
        self._guard = Lock()
        self._requests: dict[tuple[str, str], ApprovalRequest] = {}
        self._by_decision: dict[tuple[str, str], str] = {}

    def request(
        self,
        decision: DecisionRecord,
        *,
        requested_by: Principal,
        requested_at: datetime | None = None,
    ) -> ApprovalRequest:
        authorize(
            requested_by,
            permission=Permission.DECISION_RUN,
            resource_organization_id=decision.organization_id,
        )
        if decision.final_action != DecisionAction.ESCALATE:
            raise ApprovalError("Only an ESCALATE decision can enter the approval inbox.")
        timestamp = requested_at or datetime.now(timezone.utc)
        identity = f"{decision.organization_id}:{decision.id}:{requested_by.user_id}:{timestamp.isoformat()}"
        approval_id = "approval_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        request = ApprovalRequest(
            id=approval_id,
            organization_id=decision.organization_id,
            invoice_id=decision.invoice_id,
            decision_id=decision.id,
            requested_by_user_id=requested_by.user_id,
            requested_at=timestamp,
        )
        decision_scope = (decision.organization_id, decision.id)
        with self._guard:
            existing_id = self._by_decision.get(decision_scope)
            if existing_id is not None:
                return self._requests[(decision.organization_id, existing_id)]
            self._requests[(decision.organization_id, request.id)] = request
            self._by_decision[decision_scope] = request.id
        return request

    def resolve(
        self,
        *,
        organization_id: str,
        approval_id: str,
        approver: Principal,
        approve: bool,
        resolution_note: str,
        expected_version: int,
        resolved_at: datetime | None = None,
    ) -> ApprovalRequest:
        authorize(
            approver,
            permission=Permission.PAYMENT_APPROVE,
            resource_organization_id=organization_id,
        )
        if not resolution_note.strip():
            raise ApprovalError("Approval resolution note is required.")
        scope = (organization_id, approval_id)
        with self._guard:
            try:
                current = self._requests[scope]
            except KeyError as exc:
                raise ApprovalError("Approval request was not found in this organization.") from exc
            if current.requested_by_user_id == approver.user_id:
                raise ApprovalError("Requester and approver must be different users.")
            if current.status != ApprovalStatus.PENDING:
                raise ApprovalError("Approval request has already been resolved.")
            if current.version != expected_version:
                raise ApprovalError("Approval request was updated by another operation.")
            resolved = replace(
                current,
                status=ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED,
                version=current.version + 1,
                resolved_by_user_id=approver.user_id,
                resolved_at=resolved_at or datetime.now(timezone.utc),
                resolution_note=resolution_note.strip(),
            )
            self._requests[scope] = resolved
            return resolved

    def get(self, *, organization_id: str, approval_id: str) -> ApprovalRequest:
        with self._guard:
            try:
                return self._requests[(organization_id, approval_id)]
            except KeyError as exc:
                raise ApprovalError("Approval request was not found in this organization.") from exc

    def pending(self, *, organization_id: str) -> tuple[ApprovalRequest, ...]:
        with self._guard:
            return tuple(
                request
                for (tenant_id, _), request in self._requests.items()
                if tenant_id == organization_id and request.status == ApprovalStatus.PENDING
            )


def apply_approved_escalation(
    decision_record: DecisionRecord,
    approval: ApprovalRequest,
) -> Decision:
    """Produce a PAY authorization only for an approved, pure escalation."""

    if approval.organization_id != decision_record.organization_id:
        raise ApprovalError("Approval belongs to another organization.")
    if approval.decision_id != decision_record.id:
        raise ApprovalError("Approval is not bound to this decision.")
    if approval.status != ApprovalStatus.APPROVED:
        raise ApprovalError("Approval request is not approved.")
    original = decision_record.policy_decision
    if original.action != DecisionAction.ESCALATE:
        raise ApprovalError("Only an ESCALATE decision can be authorized.")
    if any(
        result.disposition in {RuleDisposition.HOLD, RuleDisposition.REJECT}
        for result in original.rule_results
    ):
        raise ApprovalError("A hold or rejection cannot be overridden by approval.")

    approved_results = tuple(
        RuleResult(
            code=f"APPROVED_{result.code}",
            disposition=RuleDisposition.PASS,
            message=f"Human approval satisfied escalation: {result.message}",
        )
        if result.disposition == RuleDisposition.ESCALATE
        else result
        for result in original.rule_results
    )
    return Decision(
        action=DecisionAction.PAY,
        policy_version=original.policy_version,
        invoice_fingerprint=original.invoice_fingerprint,
        rule_results=approved_results,
        approval_reference=approval.id,
    )

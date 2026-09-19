"""Evidence-bound decision orchestration with a hard AI/control boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from threading import Lock
from typing import Iterable, Protocol

from .audit import AuditChain, canonical_json
from .models import TreasurySnapshot, Vendor
from .normalization import NormalizedEvidence
from .policies import policy_content_hash
from .policy import Decision, DecisionAction, Policy, PolicyEngine


@dataclass(frozen=True, slots=True)
class AgentRecommendation:
    """Non-authoritative interpretation; never a payment payload."""

    action: DecisionAction
    summary: str
    reason_codes: tuple[str, ...]
    confidence: Decimal

    def __post_init__(self) -> None:
        confidence = self.confidence if isinstance(self.confidence, Decimal) else Decimal(str(self.confidence))
        if confidence < 0 or confidence > 1:
            raise ValueError("Agent confidence must be between 0 and 1.")
        if not self.summary.strip():
            raise ValueError("Agent recommendation summary is required.")
        object.__setattr__(self, "confidence", confidence)


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    id: str
    organization_id: str
    invoice_id: str
    evidence_manifest_hash: str
    policy_version: str
    policy_content_hash: str
    agent_recommendation: AgentRecommendation | None
    policy_decision: Decision
    final_action: DecisionAction
    created_at: datetime

    @property
    def agent_disagreed(self) -> bool:
        return self.agent_recommendation is not None and self.agent_recommendation.action != self.final_action


class DecisionStore(Protocol):
    def store_decision(self, record: DecisionRecord) -> tuple[DecisionRecord, bool]: ...

    def get_decision(self, *, organization_id: str, decision_id: str) -> DecisionRecord: ...


class DecisionRepository:
    def __init__(self) -> None:
        self._guard = Lock()
        self._records: dict[tuple[str, str], DecisionRecord] = {}

    def store_decision(self, record: DecisionRecord) -> tuple[DecisionRecord, bool]:
        scope = (record.organization_id, record.id)
        with self._guard:
            existing = self._records.get(scope)
            if existing is not None:
                return existing, False
            self._records[scope] = record
        return record, True

    def get_decision(self, *, organization_id: str, decision_id: str) -> DecisionRecord:
        with self._guard:
            try:
                return self._records[(organization_id, decision_id)]
            except KeyError as exc:
                raise KeyError("Decision was not found in this organization.") from exc


class DecisionService:
    def __init__(
        self,
        *,
        policy_engine: PolicyEngine | None = None,
        repository: DecisionStore | None = None,
        audit_chain: AuditChain | None = None,
    ) -> None:
        self.policy_engine = policy_engine or PolicyEngine()
        self.repository = repository or DecisionRepository()
        self.audit_chain = audit_chain or AuditChain()

    def evaluate(
        self,
        *,
        evidence: NormalizedEvidence,
        vendor: Vendor,
        treasury: TreasurySnapshot,
        policy: Policy,
        agent_recommendation: AgentRecommendation | None = None,
        known_invoice_fingerprints: Iterable[str] = (),
        asset: str = "USDC",
        network: str = "ARC-TESTNET",
        evaluation_date: date | None = None,
        created_at: datetime | None = None,
    ) -> DecisionRecord:
        organization_id = evidence.invoice.organization_id
        if vendor.organization_id != organization_id or treasury.organization_id != organization_id:
            raise ValueError("Decision inputs cross an organization boundary.")
        if policy.organization_id != organization_id:
            raise ValueError("Policy belongs to another organization.")

        decision = self.policy_engine.evaluate(
            invoice=evidence.invoice,
            vendor=vendor,
            purchase_order=evidence.purchase_order,
            delivery=evidence.delivery,
            treasury=treasury,
            policy=policy,
            known_invoice_fingerprints=known_invoice_fingerprints,
            asset=asset,
            network=network,
            evaluation_date=evaluation_date,
        )
        policy_hash = policy_content_hash(policy)
        decision_id = self._decision_id(
            organization_id=organization_id,
            invoice_id=evidence.invoice.id,
            evidence_manifest_hash=evidence.manifest_hash,
            policy_content_hash_value=policy_hash,
            decision=decision,
        )
        record = DecisionRecord(
            id=decision_id,
            organization_id=organization_id,
            invoice_id=evidence.invoice.id,
            evidence_manifest_hash=evidence.manifest_hash,
            policy_version=policy.version,
            policy_content_hash=policy_hash,
            agent_recommendation=agent_recommendation,
            policy_decision=decision,
            final_action=decision.action,
            created_at=created_at or datetime.now(timezone.utc),
        )
        stored, created = self.repository.store_decision(record)
        if created:
            self.audit_chain.append(
                aggregate_type="decision",
                aggregate_id=record.id,
                event_type="POLICY_DECISION_RECORDED",
                payload={
                    "organization_id": organization_id,
                    "invoice_id": record.invoice_id,
                    "evidence_manifest_hash": record.evidence_manifest_hash,
                    "policy_version": record.policy_version,
                    "policy_content_hash": record.policy_content_hash,
                    "agent_action": (
                        record.agent_recommendation.action.value
                        if record.agent_recommendation is not None
                        else None
                    ),
                    "final_action": record.final_action.value,
                    "reason_codes": record.policy_decision.reason_codes,
                },
                created_at=record.created_at,
            )
        return stored

    @staticmethod
    def _decision_id(
        *,
        organization_id: str,
        invoice_id: str,
        evidence_manifest_hash: str,
        policy_content_hash_value: str,
        decision: Decision,
    ) -> str:
        payload = {
            "organization_id": organization_id,
            "invoice_id": invoice_id,
            "evidence_manifest_hash": evidence_manifest_hash,
            "policy_content_hash": policy_content_hash_value,
            "action": decision.action,
            "rule_results": decision.rule_results,
        }
        return "decision_" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:24]

"""Evidence-bound decision orchestration with a hard AI/control boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from threading import Lock
from typing import Iterable, Protocol

from .audit import AuditChain, canonical_json
from .models import DeliveryEvidence, Invoice, PurchaseOrder, TreasurySnapshot, Vendor
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
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        confidence = self.confidence if isinstance(self.confidence, Decimal) else Decimal(str(self.confidence))
        if confidence < 0 or confidence > 1:
            raise ValueError("Agent confidence must be between 0 and 1.")
        if not self.summary.strip():
            raise ValueError("Agent recommendation summary is required.")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("Agent evidence references must be unique.")
        if any(not reference.strip() for reference in self.evidence_refs):
            raise ValueError("Agent evidence references must not be empty.")
        object.__setattr__(self, "confidence", confidence)


@dataclass(frozen=True, slots=True)
class DecisionReplayInputs:
    """Canonical, point-in-time inputs required to reproduce a decision exactly."""

    evidence: NormalizedEvidence
    vendor: Vendor
    treasury: TreasurySnapshot
    policy: Policy
    known_invoice_fingerprints: tuple[str, ...]
    asset: str
    network: str
    evaluation_date: date
    vendor_wallet_event_type: str | None = None
    vendor_wallet_verified_date: date | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(canonical_json(self).encode("utf-8")).hexdigest()

    def to_payload(self) -> dict[str, object]:
        invoice = self.evidence.invoice
        purchase_order = self.evidence.purchase_order
        delivery = self.evidence.delivery
        return {
            "evidence": {
                "package_id": self.evidence.package_id,
                "manifest_hash": self.evidence.manifest_hash,
                "invoice": {
                    "id": invoice.id,
                    "organization_id": invoice.organization_id,
                    "vendor_id": invoice.vendor_id,
                    "invoice_number": invoice.invoice_number,
                    "currency": invoice.currency,
                    "amount": format(invoice.amount, "f"),
                    "due_date": invoice.due_date.isoformat(),
                    "payment_wallet_address": invoice.payment_wallet_address,
                    "source_document_hash": invoice.source_document_hash,
                },
                "purchase_order": (
                    {
                        "id": purchase_order.id,
                        "organization_id": purchase_order.organization_id,
                        "vendor_id": purchase_order.vendor_id,
                        "po_number": purchase_order.po_number,
                        "currency": purchase_order.currency,
                        "authorized_amount": format(purchase_order.authorized_amount, "f"),
                    }
                    if purchase_order is not None
                    else None
                ),
                "delivery": (
                    {
                        "id": delivery.id,
                        "organization_id": delivery.organization_id,
                        "purchase_order_id": delivery.purchase_order_id,
                        "delivered_value": format(delivery.delivered_value, "f"),
                        "source_document_hash": delivery.source_document_hash,
                    }
                    if delivery is not None
                    else None
                ),
            },
            "vendor": {
                "id": self.vendor.id,
                "organization_id": self.vendor.organization_id,
                "legal_name": self.vendor.legal_name,
                "approved_wallet_address": self.vendor.approved_wallet_address,
                "autopay_limit": format(self.vendor.autopay_limit, "f"),
                "risk_tier": self.vendor.risk_tier,
                "active": self.vendor.active,
            },
            "treasury": {
                "organization_id": self.treasury.organization_id,
                "available_usdc": format(self.treasury.available_usdc, "f"),
                "spent_today_usdc": format(self.treasury.spent_today_usdc, "f"),
            },
            "policy": {
                "version": self.policy.version,
                "organization_id": self.policy.organization_id,
                "daily_payment_limit_usdc": format(self.policy.daily_payment_limit_usdc, "f"),
                "minimum_cash_reserve_usdc": format(self.policy.minimum_cash_reserve_usdc, "f"),
                "maximum_autonomous_payment_usdc": format(
                    self.policy.maximum_autonomous_payment_usdc, "f"
                ),
                "po_amount_tolerance_usdc": format(self.policy.po_amount_tolerance_usdc, "f"),
                "allowed_asset": self.policy.allowed_asset,
                "allowed_network": self.policy.allowed_network,
                "kill_switch_enabled": self.policy.kill_switch_enabled,
                "schedule_payments_before_due_days": self.policy.schedule_payments_before_due_days,
            },
            "known_invoice_fingerprints": list(self.known_invoice_fingerprints),
            "asset": self.asset,
            "network": self.network,
            "evaluation_date": self.evaluation_date.isoformat(),
            "vendor_wallet_trust": {
                "event_type": self.vendor_wallet_event_type,
                "verified_date": (
                    self.vendor_wallet_verified_date.isoformat()
                    if self.vendor_wallet_verified_date is not None
                    else None
                ),
            },
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> DecisionReplayInputs:
        evidence_data = dict(payload["evidence"])  # type: ignore[arg-type]
        invoice_data = dict(evidence_data["invoice"])  # type: ignore[arg-type]
        po_value = evidence_data.get("purchase_order")
        delivery_value = evidence_data.get("delivery")
        vendor_data = dict(payload["vendor"])  # type: ignore[arg-type]
        treasury_data = dict(payload["treasury"])  # type: ignore[arg-type]
        policy_data = dict(payload["policy"])  # type: ignore[arg-type]
        purchase_order = (
            PurchaseOrder(
                id=str(dict(po_value)["id"]),  # type: ignore[arg-type]
                organization_id=str(dict(po_value)["organization_id"]),  # type: ignore[arg-type]
                vendor_id=str(dict(po_value)["vendor_id"]),  # type: ignore[arg-type]
                po_number=str(dict(po_value)["po_number"]),  # type: ignore[arg-type]
                currency=str(dict(po_value)["currency"]),  # type: ignore[arg-type]
                authorized_amount=Decimal(str(dict(po_value)["authorized_amount"])),  # type: ignore[arg-type]
            )
            if po_value is not None
            else None
        )
        delivery = (
            DeliveryEvidence(
                id=str(dict(delivery_value)["id"]),  # type: ignore[arg-type]
                organization_id=str(dict(delivery_value)["organization_id"]),  # type: ignore[arg-type]
                purchase_order_id=str(dict(delivery_value)["purchase_order_id"]),  # type: ignore[arg-type]
                delivered_value=Decimal(str(dict(delivery_value)["delivered_value"])),  # type: ignore[arg-type]
                source_document_hash=str(dict(delivery_value)["source_document_hash"]),  # type: ignore[arg-type]
            )
            if delivery_value is not None
            else None
        )
        evidence = NormalizedEvidence(
            package_id=str(evidence_data["package_id"]),
            manifest_hash=str(evidence_data["manifest_hash"]),
            invoice=Invoice(
                id=str(invoice_data["id"]),
                organization_id=str(invoice_data["organization_id"]),
                vendor_id=str(invoice_data["vendor_id"]),
                invoice_number=str(invoice_data["invoice_number"]),
                currency=str(invoice_data["currency"]),
                amount=Decimal(str(invoice_data["amount"])),
                due_date=date.fromisoformat(str(invoice_data["due_date"])),
                payment_wallet_address=str(invoice_data["payment_wallet_address"]),
                source_document_hash=str(invoice_data["source_document_hash"]),
            ),
            purchase_order=purchase_order,
            delivery=delivery,
        )
        wallet_trust_value = payload.get("vendor_wallet_trust")
        wallet_trust = dict(wallet_trust_value) if isinstance(wallet_trust_value, dict) else {}
        wallet_verified_date = wallet_trust.get("verified_date")
        return cls(
            evidence=evidence,
            vendor=Vendor(
                id=str(vendor_data["id"]),
                organization_id=str(vendor_data["organization_id"]),
                legal_name=str(vendor_data["legal_name"]),
                approved_wallet_address=str(vendor_data["approved_wallet_address"]),
                autopay_limit=Decimal(str(vendor_data["autopay_limit"])),
                risk_tier=str(vendor_data["risk_tier"]),
                active=bool(vendor_data["active"]),
            ),
            treasury=TreasurySnapshot(
                organization_id=str(treasury_data["organization_id"]),
                available_usdc=Decimal(str(treasury_data["available_usdc"])),
                spent_today_usdc=Decimal(str(treasury_data["spent_today_usdc"])),
            ),
            policy=Policy(
                version=str(policy_data["version"]),
                organization_id=str(policy_data["organization_id"]),
                daily_payment_limit_usdc=Decimal(str(policy_data["daily_payment_limit_usdc"])),
                minimum_cash_reserve_usdc=Decimal(str(policy_data["minimum_cash_reserve_usdc"])),
                maximum_autonomous_payment_usdc=Decimal(
                    str(policy_data["maximum_autonomous_payment_usdc"])
                ),
                po_amount_tolerance_usdc=Decimal(str(policy_data["po_amount_tolerance_usdc"])),
                allowed_asset=str(policy_data["allowed_asset"]),
                allowed_network=str(policy_data["allowed_network"]),
                kill_switch_enabled=bool(policy_data["kill_switch_enabled"]),
                schedule_payments_before_due_days=(
                    int(policy_data["schedule_payments_before_due_days"])
                    if policy_data.get("schedule_payments_before_due_days") is not None
                    else None
                ),
            ),
            known_invoice_fingerprints=tuple(
                str(item) for item in payload.get("known_invoice_fingerprints", [])  # type: ignore[arg-type]
            ),
            asset=str(payload["asset"]),
            network=str(payload["network"]),
            evaluation_date=date.fromisoformat(str(payload["evaluation_date"])),
            vendor_wallet_event_type=(
                str(wallet_trust["event_type"])
                if wallet_trust.get("event_type") is not None
                else None
            ),
            vendor_wallet_verified_date=(
                date.fromisoformat(str(wallet_verified_date))
                if wallet_verified_date is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ReplayCheck:
    code: str
    passed: bool
    expected: str
    actual: str


@dataclass(frozen=True, slots=True)
class DecisionReplayVerification:
    verified: bool
    original_decision_id: str
    replayed_decision_id: str
    input_snapshot_hash: str
    replayed_decision: Decision
    checks: tuple[ReplayCheck, ...]


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
    replay_inputs: DecisionReplayInputs | None
    replay_input_hash: str | None
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
        vendor_wallet_event_type: str | None = None,
        vendor_wallet_verified_date: date | None = None,
        created_at: datetime | None = None,
    ) -> DecisionRecord:
        organization_id = evidence.invoice.organization_id
        if vendor.organization_id != organization_id or treasury.organization_id != organization_id:
            raise ValueError("Decision inputs cross an organization boundary.")
        if policy.organization_id != organization_id:
            raise ValueError("Policy belongs to another organization.")

        resolved_evaluation_date = evaluation_date or date.today()
        fingerprints = tuple(sorted(set(known_invoice_fingerprints)))
        replay_inputs = DecisionReplayInputs(
            evidence=evidence,
            vendor=vendor,
            treasury=treasury,
            policy=policy,
            known_invoice_fingerprints=fingerprints,
            asset=asset.strip().upper(),
            network=network.strip().upper(),
            evaluation_date=resolved_evaluation_date,
            vendor_wallet_event_type=vendor_wallet_event_type,
            vendor_wallet_verified_date=vendor_wallet_verified_date,
        )
        replay_input_hash = replay_inputs.content_hash
        decision = self.policy_engine.evaluate(
            invoice=evidence.invoice,
            vendor=vendor,
            purchase_order=evidence.purchase_order,
            delivery=evidence.delivery,
            treasury=treasury,
            policy=policy,
            known_invoice_fingerprints=fingerprints,
            asset=replay_inputs.asset,
            network=replay_inputs.network,
            evaluation_date=resolved_evaluation_date,
            vendor_wallet_event_type=replay_inputs.vendor_wallet_event_type,
            vendor_wallet_verified_date=replay_inputs.vendor_wallet_verified_date,
        )
        policy_hash = policy_content_hash(policy)
        decision_id = self._decision_id(
            organization_id=organization_id,
            invoice_id=evidence.invoice.id,
            evidence_manifest_hash=evidence.manifest_hash,
            policy_content_hash_value=policy_hash,
            replay_input_hash=replay_input_hash,
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
            replay_inputs=replay_inputs,
            replay_input_hash=replay_input_hash,
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

    def verify_replay(self, record: DecisionRecord) -> DecisionReplayVerification:
        """Re-evaluate a stored snapshot without reading mutable current state."""

        inputs = record.replay_inputs
        if inputs is None or record.replay_input_hash is None:
            raise ValueError("Decision predates replay snapshots and cannot be reproduced exactly.")
        replayed = self.policy_engine.evaluate(
            invoice=inputs.evidence.invoice,
            vendor=inputs.vendor,
            purchase_order=inputs.evidence.purchase_order,
            delivery=inputs.evidence.delivery,
            treasury=inputs.treasury,
            policy=inputs.policy,
            known_invoice_fingerprints=inputs.known_invoice_fingerprints,
            asset=inputs.asset,
            network=inputs.network,
            evaluation_date=inputs.evaluation_date,
            vendor_wallet_event_type=inputs.vendor_wallet_event_type,
            vendor_wallet_verified_date=inputs.vendor_wallet_verified_date,
        )
        snapshot_hash = inputs.content_hash
        replayed_id = self._decision_id(
            organization_id=inputs.evidence.invoice.organization_id,
            invoice_id=inputs.evidence.invoice.id,
            evidence_manifest_hash=inputs.evidence.manifest_hash,
            policy_content_hash_value=policy_content_hash(inputs.policy),
            replay_input_hash=snapshot_hash,
            decision=replayed,
        )
        checks = (
            ReplayCheck("INPUT_SNAPSHOT_HASH", snapshot_hash == record.replay_input_hash, record.replay_input_hash, snapshot_hash),
            ReplayCheck("ORGANIZATION_BINDING", inputs.evidence.invoice.organization_id == record.organization_id, record.organization_id, inputs.evidence.invoice.organization_id),
            ReplayCheck("INVOICE_BINDING", inputs.evidence.invoice.id == record.invoice_id, record.invoice_id, inputs.evidence.invoice.id),
            ReplayCheck("EVIDENCE_MANIFEST", inputs.evidence.manifest_hash == record.evidence_manifest_hash, record.evidence_manifest_hash, inputs.evidence.manifest_hash),
            ReplayCheck("POLICY_VERSION", inputs.policy.version == record.policy_version, record.policy_version, inputs.policy.version),
            ReplayCheck("POLICY_CONTENT_HASH", policy_content_hash(inputs.policy) == record.policy_content_hash, record.policy_content_hash, policy_content_hash(inputs.policy)),
            ReplayCheck("DECISION_ACTION", replayed.action == record.policy_decision.action, record.policy_decision.action.value, replayed.action.value),
            ReplayCheck("FINAL_ACTION", replayed.action == record.final_action, record.final_action.value, replayed.action.value),
            ReplayCheck("INVOICE_FINGERPRINT", replayed.invoice_fingerprint == record.policy_decision.invoice_fingerprint, record.policy_decision.invoice_fingerprint, replayed.invoice_fingerprint),
            ReplayCheck("RULE_RESULTS", replayed.rule_results == record.policy_decision.rule_results, canonical_json(record.policy_decision.rule_results), canonical_json(replayed.rule_results)),
            ReplayCheck("DECISION_ID", replayed_id == record.id, record.id, replayed_id),
        )
        return DecisionReplayVerification(
            verified=all(item.passed for item in checks),
            original_decision_id=record.id,
            replayed_decision_id=replayed_id,
            input_snapshot_hash=snapshot_hash,
            replayed_decision=replayed,
            checks=checks,
        )

    @staticmethod
    def _decision_id(
        *,
        organization_id: str,
        invoice_id: str,
        evidence_manifest_hash: str,
        policy_content_hash_value: str,
        replay_input_hash: str,
        decision: Decision,
    ) -> str:
        payload = {
            "organization_id": organization_id,
            "invoice_id": invoice_id,
            "evidence_manifest_hash": evidence_manifest_hash,
            "policy_content_hash": policy_content_hash_value,
            "replay_input_hash": replay_input_hash,
            "action": decision.action,
            "rule_results": decision.rule_results,
        }
        return "decision_" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:24]

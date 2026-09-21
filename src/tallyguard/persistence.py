"""Durable tenant-scoped SQLite persistence for evidence and invoice workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import base64
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock

from .audit import AuditEvent, GENESIS_HASH, audit_event_hash, canonical_json
from .approvals import ApprovalError, ApprovalRequest, ApprovalStatus
from .autonomy import AgentPlanItem, AgentRun, AgentRunStatus
from .auth import Principal, Role, Session
from .decisions import AgentRecommendation, DecisionRecord, DecisionReplayInputs
from .evidence import (
    EvidenceDocument,
    EvidenceRecord,
    EvidenceType,
    ExtractedField,
    ExtractionMethod,
    SourceLocation,
)
from .models import Invoice, TreasurySnapshot, Vendor, normalize_wallet
from .network import ArcNetwork
from .policies import (
    PolicyFieldChange,
    PolicyRepositoryError,
    StoredPolicy,
    policy_content_hash,
)
from .policy import Decision, DecisionAction, Policy, RuleDisposition, RuleResult
from .settlement import (
    PaymentIntent,
    SettlementAttempt,
    SettlementAttemptOutcome,
    SettlementReceipt,
    SettlementStatus,
)
from .workflow import InvoiceStatus, WorkflowError, require_transition
from .vendors import (
    VendorDirectoryError,
    VendorWalletEvent,
    WalletEventType,
    WalletVerificationMethod,
)


class PersistenceError(RuntimeError):
    """Raised when a durable record is missing, duplicated, or stale."""


class SettlementExecutionBlocked(PersistenceError):
    """Raised when the active treasury controls reject an intent reservation."""

    def __init__(
        self,
        control_code: str,
        message: str,
        *,
        details: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.control_code = control_code
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class StoredInvoice:
    invoice: Invoice
    status: InvoiceStatus
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class InvoiceTransition:
    sequence: int
    organization_id: str
    invoice_id: str
    from_status: InvoiceStatus
    to_status: InvoiceStatus
    actor_user_id: str
    correlation_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class InvoicePage:
    items: tuple[StoredInvoice, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class AccountingLedgerRow:
    invoice_id: str
    invoice_number: str
    vendor_id: str
    vendor_legal_name: str
    currency: str
    amount_usdc: Decimal
    due_date: date
    invoice_status: str
    decision_id: str
    evidence_manifest_hash: str
    policy_version: str
    policy_content_hash: str
    final_action: str
    approval_id: str | None
    approval_status: str | None
    approval_resolved_by: str | None
    approval_resolved_at: datetime | None
    payment_intent_id: str
    recipient: str
    network: str
    provider: str
    provider_reference: str
    transaction_hash: str
    block_number: int
    settlement_status: str
    confirmed_at: datetime


@dataclass(frozen=True, slots=True)
class StoredTreasurySnapshot:
    sequence: int
    snapshot: TreasurySnapshot
    source_reference: str
    recorded_by_user_id: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class OperationsOverview:
    organization_id: str
    as_of: date
    invoice_count: int
    status_counts: dict[str, int]
    open_exposure_usdc: Decimal
    blocked_exposure_usdc: Decimal
    due_next_7_days_usdc: Decimal
    due_next_7_days_count: int
    overdue_usdc: Decimal
    overdue_count: int
    reconciled_usdc: Decimal
    treasury_available_usdc: Decimal | None
    treasury_committed_since_snapshot_usdc: Decimal | None
    unreserved_open_exposure_usdc: Decimal | None
    minimum_reserve_usdc: Decimal | None
    projected_after_open_usdc: Decimal | None
    work_queue: tuple[StoredInvoice, ...]


@dataclass(frozen=True, slots=True)
class SettlementCapacity:
    organization_id: str
    active_policy_version: str
    active_policy_hash: str
    kill_switch_enabled: bool
    treasury_snapshot_sequence: int
    treasury_snapshot_recorded_at: datetime
    snapshot_age_seconds: int
    snapshot_fresh: bool
    snapshot_available_usdc: Decimal
    snapshot_spent_today_usdc: Decimal
    committed_since_snapshot_usdc: Decimal
    effective_available_usdc: Decimal
    daily_payment_limit_usdc: Decimal
    daily_remaining_usdc: Decimal
    daily_autonomous_payment_limit_usdc: Decimal
    autonomous_daily_remaining_usdc: Decimal
    minimum_cash_reserve_usdc: Decimal
    maximum_new_payment_usdc: Decimal


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    organization_id TEXT NOT NULL,
    id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    roles_json TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, id),
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    roles_json TEXT NOT NULL,
    principal_active INTEGER NOT NULL CHECK (principal_active IN (0, 1)),
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (organization_id, user_id) REFERENCES users(organization_id, id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_expiry
    ON sessions (expires_at);

CREATE TABLE IF NOT EXISTS policies (
    organization_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    daily_payment_limit_usdc TEXT NOT NULL,
    daily_autonomous_payment_limit_usdc TEXT NOT NULL,
    autonomous_payments_enabled INTEGER NOT NULL CHECK (autonomous_payments_enabled IN (0, 1)),
    minimum_cash_reserve_usdc TEXT NOT NULL,
    maximum_autonomous_payment_usdc TEXT NOT NULL,
    po_amount_tolerance_usdc TEXT NOT NULL,
    allowed_asset TEXT NOT NULL,
    allowed_network TEXT NOT NULL,
    kill_switch_enabled INTEGER NOT NULL CHECK (kill_switch_enabled IN (0, 1)),
    schedule_payments_before_due_days INTEGER,
    activated_by_user_id TEXT NOT NULL,
    activated_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, version),
    UNIQUE (organization_id, content_hash),
    FOREIGN KEY (organization_id, activated_by_user_id)
        REFERENCES users(organization_id, id)
);

CREATE TABLE IF NOT EXISTS active_policies (
    organization_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    activated_at TEXT NOT NULL,
    FOREIGN KEY (organization_id, version)
        REFERENCES policies(organization_id, version)
);

CREATE TABLE IF NOT EXISTS treasury_snapshots (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id TEXT NOT NULL,
    available_usdc TEXT NOT NULL,
    spent_today_usdc TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    recorded_by_user_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    FOREIGN KEY (organization_id, recorded_by_user_id)
        REFERENCES users(organization_id, id)
);

CREATE INDEX IF NOT EXISTS idx_treasury_snapshots_tenant
    ON treasury_snapshots (organization_id, sequence DESC);

CREATE TABLE IF NOT EXISTS vendors (
    organization_id TEXT NOT NULL,
    id TEXT NOT NULL,
    legal_name TEXT NOT NULL,
    approved_wallet_address TEXT NOT NULL,
    autopay_limit TEXT NOT NULL,
    risk_tier TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, id),
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE TABLE IF NOT EXISTS vendor_wallet_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id TEXT NOT NULL,
    vendor_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    wallet_address TEXT NOT NULL,
    previous_wallet_address TEXT,
    verification_method TEXT NOT NULL,
    verification_reference TEXT NOT NULL,
    verified_by_user_id TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    FOREIGN KEY (organization_id, vendor_id) REFERENCES vendors(organization_id, id),
    FOREIGN KEY (organization_id, verified_by_user_id) REFERENCES users(organization_id, id)
);

CREATE INDEX IF NOT EXISTS idx_vendor_wallet_history
    ON vendor_wallet_events (organization_id, vendor_id, sequence);

CREATE TABLE IF NOT EXISTS evidence_documents (
    organization_id TEXT NOT NULL,
    id TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    ingested_at TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    PRIMARY KEY (organization_id, id),
    UNIQUE (organization_id, content_sha256),
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE TABLE IF NOT EXISTS evidence_blobs (
    organization_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    content BLOB NOT NULL,
    PRIMARY KEY (organization_id, document_id),
    FOREIGN KEY (organization_id, document_id)
        REFERENCES evidence_documents(organization_id, id)
);

CREATE TABLE IF NOT EXISTS invoice_evidence (
    organization_id TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    linked_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, invoice_id, document_id),
    UNIQUE (organization_id, invoice_id, evidence_type),
    FOREIGN KEY (organization_id, invoice_id)
        REFERENCES invoices(organization_id, id),
    FOREIGN KEY (organization_id, document_id)
        REFERENCES evidence_documents(organization_id, id)
);

CREATE TABLE IF NOT EXISTS invoices (
    organization_id TEXT NOT NULL,
    id TEXT NOT NULL,
    vendor_id TEXT NOT NULL,
    invoice_number TEXT NOT NULL,
    currency TEXT NOT NULL,
    amount TEXT NOT NULL,
    due_date TEXT NOT NULL,
    payment_wallet_address TEXT NOT NULL,
    source_document_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, id),
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE INDEX IF NOT EXISTS idx_invoices_tenant_status
    ON invoices (organization_id, status, updated_at, id);

CREATE TABLE IF NOT EXISTS decisions (
    organization_id TEXT NOT NULL,
    id TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    evidence_manifest_hash TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    policy_content_hash TEXT NOT NULL,
    agent_recommendation_json TEXT,
    policy_decision_json TEXT NOT NULL,
    final_action TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, id),
    FOREIGN KEY (organization_id, invoice_id) REFERENCES invoices(organization_id, id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_tenant_invoice
    ON decisions (organization_id, invoice_id, created_at);

CREATE TABLE IF NOT EXISTS approvals (
    organization_id TEXT NOT NULL,
    id TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    requested_by_user_id TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    resolved_by_user_id TEXT,
    resolved_at TEXT,
    resolution_note TEXT,
    PRIMARY KEY (organization_id, id),
    UNIQUE (organization_id, decision_id),
    FOREIGN KEY (organization_id, invoice_id) REFERENCES invoices(organization_id, id),
    FOREIGN KEY (organization_id, decision_id) REFERENCES decisions(organization_id, id),
    FOREIGN KEY (organization_id, requested_by_user_id) REFERENCES users(organization_id, id),
    FOREIGN KEY (organization_id, resolved_by_user_id) REFERENCES users(organization_id, id)
);

CREATE INDEX IF NOT EXISTS idx_approvals_tenant_status
    ON approvals (organization_id, status, requested_at);

CREATE TABLE IF NOT EXISTS audit_events (
    organization_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, sequence),
    UNIQUE (organization_id, aggregate_type, aggregate_id, event_type),
    UNIQUE (organization_id, event_hash),
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_aggregate
    ON audit_events (organization_id, aggregate_type, aggregate_id, sequence);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_event_type
    ON audit_events (organization_id, event_type, sequence DESC);

CREATE TABLE IF NOT EXISTS invoice_transitions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    actor_user_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (organization_id, invoice_id) REFERENCES invoices(organization_id, id)
);

CREATE TABLE IF NOT EXISTS payment_intents (
    organization_id TEXT NOT NULL,
    id TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    amount_usdc TEXT NOT NULL,
    network TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    approval_reference TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, id),
    UNIQUE (organization_id, decision_id),
    UNIQUE (organization_id, idempotency_key),
    FOREIGN KEY (organization_id, invoice_id) REFERENCES invoices(organization_id, id)
);

CREATE TABLE IF NOT EXISTS settlement_receipts (
    organization_id TEXT NOT NULL,
    payment_intent_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_reference TEXT NOT NULL,
    transaction_hash TEXT NOT NULL,
    block_number INTEGER NOT NULL,
    confirmed_recipient TEXT NOT NULL,
    confirmed_amount_usdc TEXT NOT NULL,
    network TEXT NOT NULL,
    status TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, payment_intent_id),
    UNIQUE (organization_id, provider, provider_reference),
    UNIQUE (organization_id, transaction_hash),
    FOREIGN KEY (organization_id, payment_intent_id)
        REFERENCES payment_intents(organization_id, id)
);

CREATE TABLE IF NOT EXISTS settlement_attempts (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id TEXT NOT NULL,
    payment_intent_id TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    outcome TEXT NOT NULL,
    retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
    error_code TEXT,
    error_message TEXT,
    correlation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (organization_id, payment_intent_id)
        REFERENCES payment_intents(organization_id, id),
    FOREIGN KEY (organization_id, invoice_id)
        REFERENCES invoices(organization_id, id)
);

CREATE INDEX IF NOT EXISTS idx_settlement_attempts_tenant
    ON settlement_attempts (organization_id, sequence DESC);

CREATE TABLE IF NOT EXISTS agent_runs (
    organization_id TEXT NOT NULL,
    id TEXT NOT NULL,
    status TEXT NOT NULL,
    as_of TEXT NOT NULL,
    state_hash TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    executed_by_user_id TEXT,
    executed_at TEXT,
    results_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (organization_id, id),
    FOREIGN KEY (organization_id, created_by_user_id)
        REFERENCES users(organization_id, id),
    FOREIGN KEY (organization_id, executed_by_user_id)
        REFERENCES users(organization_id, id)
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_tenant_created
    ON agent_runs (organization_id, created_at DESC, id DESC);
"""


class SqliteRepository:
    """Small synchronous repository with serialized writes and optimistic versions."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._guard = RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        with self._guard:
            if self.path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.executescript(SCHEMA)
            policy_columns = {
                str(row["name"])
                for row in self._connection.execute("PRAGMA table_info(policies)").fetchall()
            }
            if "daily_autonomous_payment_limit_usdc" not in policy_columns:
                self._connection.execute(
                    "ALTER TABLE policies ADD COLUMN "
                    "daily_autonomous_payment_limit_usdc TEXT NOT NULL DEFAULT '1000'"
                )
            if "autonomous_payments_enabled" not in policy_columns:
                self._connection.execute(
                    "ALTER TABLE policies ADD COLUMN "
                    "autonomous_payments_enabled INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (autonomous_payments_enabled IN (0, 1))"
                )

    def close(self) -> None:
        with self._guard:
            self._connection.close()

    def create_organization(
        self,
        *,
        organization_id: str,
        name: str,
        created_at: datetime | None = None,
    ) -> None:
        timestamp = created_at or datetime.now(timezone.utc)
        if not organization_id.strip() or not name.strip():
            raise PersistenceError("Organization ID and name are required.")
        with self._guard:
            try:
                self._connection.execute(
                    "INSERT INTO organizations (id, name, created_at) VALUES (?, ?, ?)",
                    (organization_id, name, timestamp.isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise PersistenceError("Organization ID is already in use.") from exc

    def ensure_organization(
        self,
        *,
        organization_id: str,
        name: str,
        created_at: datetime | None = None,
    ) -> bool:
        """Create an organization or verify that the durable identity is unchanged."""

        timestamp = created_at or datetime.now(timezone.utc)
        if not organization_id.strip() or not name.strip():
            raise PersistenceError("Organization ID and name are required.")
        with self._guard:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT name FROM organizations WHERE id = ?",
                    (organization_id,),
                ).fetchone()
                if row is None:
                    self._connection.execute(
                        "INSERT INTO organizations (id, name, created_at) VALUES (?, ?, ?)",
                        (organization_id, name, timestamp.isoformat()),
                    )
                    self._connection.execute("COMMIT")
                    return True
                if row["name"] != name:
                    raise PersistenceError(
                        "Existing organization name does not match the requested identity."
                    )
                self._connection.execute("COMMIT")
                return False
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def create_user(
        self,
        *,
        organization_id: str,
        user_id: str,
        display_name: str,
        roles: tuple[str, ...],
        active: bool = True,
        created_at: datetime | None = None,
    ) -> None:
        timestamp = created_at or datetime.now(timezone.utc)
        if not user_id.strip() or not display_name.strip() or not roles:
            raise PersistenceError("User ID, display name, and roles are required.")
        with self._guard:
            try:
                self._connection.execute(
                    """
                    INSERT INTO users
                        (organization_id, id, display_name, roles_json, active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        organization_id,
                        user_id,
                        display_name,
                        canonical_json(roles),
                        int(active),
                        timestamp.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PersistenceError("User cannot be created in this organization.") from exc

    def ensure_user(
        self,
        *,
        organization_id: str,
        user_id: str,
        display_name: str,
        roles: tuple[str, ...],
        active: bool = True,
        created_at: datetime | None = None,
    ) -> bool:
        """Create a user or fail closed if an existing identity differs."""

        timestamp = created_at or datetime.now(timezone.utc)
        if not user_id.strip() or not display_name.strip() or not roles:
            raise PersistenceError("User ID, display name, and roles are required.")
        canonical_roles = canonical_json(roles)
        with self._guard:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT display_name, roles_json, active
                    FROM users
                    WHERE organization_id = ? AND id = ?
                    """,
                    (organization_id, user_id),
                ).fetchone()
                if row is None:
                    self._connection.execute(
                        """
                        INSERT INTO users
                            (organization_id, id, display_name, roles_json, active, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            organization_id,
                            user_id,
                            display_name,
                            canonical_roles,
                            int(active),
                            timestamp.isoformat(),
                        ),
                    )
                    self._connection.execute("COMMIT")
                    return True
                if (
                    row["display_name"] != display_name
                    or row["roles_json"] != canonical_roles
                    or bool(row["active"]) is not active
                ):
                    raise PersistenceError(
                        "Existing user does not match the requested identity, roles, or state."
                    )
                self._connection.execute("COMMIT")
                return False
            except sqlite3.IntegrityError as exc:
                self._connection.execute("ROLLBACK")
                raise PersistenceError(
                    "User cannot be created in this organization."
                ) from exc
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def save_session(
        self,
        session: Session,
        *,
        maximum_active_sessions_per_principal: int,
    ) -> None:
        if maximum_active_sessions_per_principal < 1:
            raise ValueError("Maximum active sessions per principal must be positive.")
        principal = session.principal
        with self._guard:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    DELETE FROM sessions
                    WHERE revoked_at IS NOT NULL
                       OR julianday(expires_at) <= julianday(?)
                    """,
                    (session.issued_at.isoformat(),),
                )
                self._connection.execute(
                    """
                    DELETE FROM sessions
                    WHERE token_hash IN (
                        SELECT token_hash
                        FROM sessions
                        WHERE organization_id = ?
                          AND user_id = ?
                        ORDER BY julianday(issued_at) DESC, token_hash DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (
                        principal.organization_id,
                        principal.user_id,
                        maximum_active_sessions_per_principal - 1,
                    ),
                )
                self._connection.execute(
                    """
                    INSERT INTO sessions
                        (token_hash, organization_id, user_id, roles_json,
                         principal_active, issued_at, expires_at, revoked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.token_hash,
                        principal.organization_id,
                        principal.user_id,
                        canonical_json(tuple(role.value for role in principal.roles)),
                        int(principal.active),
                        session.issued_at.isoformat(),
                        session.expires_at.isoformat(),
                        session.revoked_at.isoformat() if session.revoked_at else None,
                    ),
                )
                self._connection.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                self._connection.execute("ROLLBACK")
                raise PersistenceError("Session could not be stored for this user.") from exc
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def get_session(self, token_hash: str) -> Session | None:
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        return self._session(row) if row is not None else None

    def revoke_session(self, token_hash: str, revoked_at: datetime) -> Session | None:
        with self._guard:
            self._connection.execute(
                """
                UPDATE sessions SET revoked_at = COALESCE(revoked_at, ?)
                WHERE token_hash = ?
                """,
                (revoked_at.isoformat(), token_hash),
            )
        return self.get_session(token_hash)

    def activate_policy(
        self,
        policy: Policy,
        *,
        activated_by_user_id: str,
        activated_at: datetime | None = None,
    ) -> StoredPolicy:
        timestamp = activated_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise PolicyRepositoryError("Policy activation timestamp must be timezone-aware.")
        if not activated_by_user_id.strip():
            raise PolicyRepositoryError("Policy activator is required.")
        stored = StoredPolicy(
            policy=policy,
            content_hash=policy_content_hash(policy),
            activated_by_user_id=activated_by_user_id,
            activated_at=timestamp,
        )
        with self._guard:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    """
                    SELECT 1 FROM policies
                    WHERE organization_id = ? AND version = ?
                    """,
                    (policy.organization_id, policy.version),
                ).fetchone()
                if existing is not None:
                    raise PolicyRepositoryError(
                        "Policy versions are immutable and cannot be overwritten."
                    )
                self._connection.execute(
                    """
                    INSERT INTO policies
                        (organization_id, version, content_hash,
                         daily_payment_limit_usdc, daily_autonomous_payment_limit_usdc,
                         autonomous_payments_enabled,
                         minimum_cash_reserve_usdc,
                         maximum_autonomous_payment_usdc, po_amount_tolerance_usdc,
                         allowed_asset, allowed_network, kill_switch_enabled,
                         schedule_payments_before_due_days, activated_by_user_id,
                         activated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        policy.organization_id,
                        policy.version,
                        stored.content_hash,
                        format(policy.daily_payment_limit_usdc, "f"),
                        format(policy.daily_autonomous_payment_limit_usdc, "f"),
                        int(policy.autonomous_payments_enabled),
                        format(policy.minimum_cash_reserve_usdc, "f"),
                        format(policy.maximum_autonomous_payment_usdc, "f"),
                        format(policy.po_amount_tolerance_usdc, "f"),
                        policy.allowed_asset,
                        policy.allowed_network,
                        int(policy.kill_switch_enabled),
                        policy.schedule_payments_before_due_days,
                        activated_by_user_id,
                        timestamp.isoformat(),
                    ),
                )
                self._connection.execute(
                    """
                    INSERT INTO active_policies (organization_id, version, activated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(organization_id) DO UPDATE SET
                        version = excluded.version,
                        activated_at = excluded.activated_at
                    """,
                    (policy.organization_id, policy.version, timestamp.isoformat()),
                )
                self._connection.execute("COMMIT")
                return stored
            except sqlite3.IntegrityError as exc:
                self._connection.execute("ROLLBACK")
                message = str(exc).lower()
                if "policies.organization_id, policies.version" in message:
                    raise PolicyRepositoryError(
                        "Policy versions are immutable and cannot be overwritten."
                    ) from exc
                if "policies.organization_id, policies.content_hash" in message:
                    raise PolicyRepositoryError(
                        "A new policy version must change at least one control field."
                    ) from exc
                raise PolicyRepositoryError(
                    "Policy activation requires a valid organization and activator."
                ) from exc
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def get_policy(self, *, organization_id: str, version: str) -> StoredPolicy:
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM policies WHERE organization_id = ? AND version = ?",
                (organization_id, version),
            ).fetchone()
        if row is None:
            raise PolicyRepositoryError("Policy version was not found in this organization.")
        return self._stored_policy(row)

    def active_policy(self, *, organization_id: str) -> StoredPolicy:
        with self._guard:
            row = self._connection.execute(
                """
                SELECT p.* FROM active_policies a
                JOIN policies p
                  ON p.organization_id = a.organization_id AND p.version = a.version
                WHERE a.organization_id = ?
                """,
                (organization_id,),
            ).fetchone()
        if row is None:
            raise PolicyRepositoryError("No active policy exists for this organization.")
        return self._stored_policy(row)

    def policy_history(self, *, organization_id: str) -> tuple[StoredPolicy, ...]:
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT * FROM policies WHERE organization_id = ?
                ORDER BY activated_at, version
                """,
                (organization_id,),
            ).fetchall()
        return tuple(self._stored_policy(row) for row in rows)

    def policy_diff(
        self,
        *,
        organization_id: str,
        from_version: str,
        to_version: str,
    ) -> tuple[PolicyFieldChange, ...]:
        before = asdict(
            self.get_policy(organization_id=organization_id, version=from_version).policy
        )
        after = asdict(
            self.get_policy(organization_id=organization_id, version=to_version).policy
        )
        return tuple(
            PolicyFieldChange(field=field, before=before[field], after=after[field])
            for field in sorted(before)
            if before[field] != after[field]
        )

    def record_treasury_snapshot(
        self,
        snapshot: TreasurySnapshot,
        *,
        source_reference: str,
        recorded_by_user_id: str,
        recorded_at: datetime | None = None,
    ) -> StoredTreasurySnapshot:
        timestamp = recorded_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise PersistenceError("Treasury snapshot timestamp must be timezone-aware.")
        if not source_reference.strip() or not recorded_by_user_id.strip():
            raise PersistenceError("Treasury source reference and recorder are required.")
        with self._guard:
            try:
                cursor = self._connection.execute(
                    """
                    INSERT INTO treasury_snapshots
                        (organization_id, available_usdc, spent_today_usdc,
                         source_reference, recorded_by_user_id, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.organization_id,
                        format(snapshot.available_usdc, "f"),
                        format(snapshot.spent_today_usdc, "f"),
                        source_reference,
                        recorded_by_user_id,
                        timestamp.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PersistenceError(
                    "Treasury snapshot requires a valid organization and recorder."
                ) from exc
        return StoredTreasurySnapshot(
            sequence=int(cursor.lastrowid),
            snapshot=snapshot,
            source_reference=source_reference,
            recorded_by_user_id=recorded_by_user_id,
            recorded_at=timestamp,
        )

    def latest_treasury_snapshot(self, *, organization_id: str) -> StoredTreasurySnapshot:
        with self._guard:
            row = self._connection.execute(
                """
                SELECT * FROM treasury_snapshots
                WHERE organization_id = ? ORDER BY sequence DESC LIMIT 1
                """,
                (organization_id,),
            ).fetchone()
        if row is None:
            raise PersistenceError("No treasury snapshot exists for this organization.")
        return self._treasury_snapshot(row)

    def settlement_capacity(
        self,
        *,
        organization_id: str,
        as_of: datetime | None = None,
        maximum_snapshot_age: timedelta = timedelta(minutes=15),
    ) -> SettlementCapacity:
        timestamp = as_of or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise PersistenceError("Settlement capacity timestamp must be timezone-aware.")
        if maximum_snapshot_age <= timedelta(0):
            raise PersistenceError("Treasury snapshot maximum age must be positive.")
        with self._guard:
            policy_row = self._connection.execute(
                """
                SELECT p.* FROM active_policies a
                JOIN policies p
                  ON p.organization_id = a.organization_id AND p.version = a.version
                WHERE a.organization_id = ?
                """,
                (organization_id,),
            ).fetchone()
            snapshot_row = self._connection.execute(
                """
                SELECT * FROM treasury_snapshots
                WHERE organization_id = ? ORDER BY sequence DESC LIMIT 1
                """,
                (organization_id,),
            ).fetchone()
            if policy_row is None or snapshot_row is None:
                raise PersistenceError(
                    "Settlement capacity requires an active policy and treasury snapshot."
                )
            recorded_at = datetime.fromisoformat(str(snapshot_row["recorded_at"]))
            reservation_rows = self._connection.execute(
                """
                SELECT amount_usdc FROM payment_intents
                WHERE organization_id = ? AND created_at > ?
                """,
                (organization_id, recorded_at.isoformat()),
            ).fetchall()

        committed = sum(
            (Decimal(str(row["amount_usdc"])) for row in reservation_rows),
            Decimal("0"),
        )
        available = Decimal(str(snapshot_row["available_usdc"]))
        spent_today = Decimal(str(snapshot_row["spent_today_usdc"]))
        daily_limit = Decimal(str(policy_row["daily_payment_limit_usdc"]))
        daily_autonomous_limit = Decimal(
            str(policy_row["daily_autonomous_payment_limit_usdc"])
        )
        reserve_floor = Decimal(str(policy_row["minimum_cash_reserve_usdc"]))
        effective_available = available - committed
        daily_remaining = max(daily_limit - spent_today - committed, Decimal("0"))
        autonomous_daily_remaining = max(
            daily_autonomous_limit - spent_today - committed,
            Decimal("0"),
        )
        balance_headroom = max(effective_available - reserve_floor, Decimal("0"))
        age = timestamp.astimezone(timezone.utc) - recorded_at.astimezone(timezone.utc)
        return SettlementCapacity(
            organization_id=organization_id,
            active_policy_version=str(policy_row["version"]),
            active_policy_hash=str(policy_row["content_hash"]),
            kill_switch_enabled=bool(policy_row["kill_switch_enabled"]),
            treasury_snapshot_sequence=int(snapshot_row["sequence"]),
            treasury_snapshot_recorded_at=recorded_at,
            snapshot_age_seconds=max(0, int(age.total_seconds())),
            snapshot_fresh=timedelta(0) <= age <= maximum_snapshot_age,
            snapshot_available_usdc=available,
            snapshot_spent_today_usdc=spent_today,
            committed_since_snapshot_usdc=committed,
            effective_available_usdc=effective_available,
            daily_payment_limit_usdc=daily_limit,
            daily_remaining_usdc=daily_remaining,
            daily_autonomous_payment_limit_usdc=daily_autonomous_limit,
            autonomous_daily_remaining_usdc=autonomous_daily_remaining,
            minimum_cash_reserve_usdc=reserve_floor,
            maximum_new_payment_usdc=min(daily_remaining, balance_headroom),
        )

    def onboard_vendor(
        self,
        vendor: Vendor,
        *,
        verification_method: WalletVerificationMethod,
        verification_reference: str,
        verified_by_user_id: str,
        verified_at: datetime | None = None,
    ) -> Vendor:
        timestamp = verified_at or datetime.now(timezone.utc)
        event = VendorWalletEvent(
            organization_id=vendor.organization_id,
            vendor_id=vendor.id,
            event_type=WalletEventType.VERIFIED,
            wallet_address=vendor.approved_wallet_address,
            previous_wallet_address=None,
            verification_method=verification_method,
            verification_reference=verification_reference,
            verified_by_user_id=verified_by_user_id,
            verified_at=timestamp,
        )
        with self._guard:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO vendors
                        (organization_id, id, legal_name, approved_wallet_address,
                         autopay_limit, risk_tier, active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        vendor.organization_id,
                        vendor.id,
                        vendor.legal_name,
                        vendor.approved_wallet_address,
                        format(vendor.autopay_limit, "f"),
                        vendor.risk_tier,
                        int(vendor.active),
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                    ),
                )
                self._insert_wallet_event(event)
                self._connection.execute("COMMIT")
                return vendor
            except sqlite3.IntegrityError as exc:
                self._connection.execute("ROLLBACK")
                raise PersistenceError(
                    "Vendor ID must be unique and the verifier must belong to this organization."
                ) from exc
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def get_vendor(self, *, organization_id: str, vendor_id: str) -> Vendor:
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM vendors WHERE organization_id = ? AND id = ?",
                (organization_id, vendor_id),
            ).fetchone()
        if row is None:
            raise VendorDirectoryError("Vendor was not found in this organization.")
        return self._vendor(row)

    def list_vendors(self, *, organization_id: str) -> tuple[Vendor, ...]:
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT * FROM vendors WHERE organization_id = ?
                ORDER BY legal_name, id
                """,
                (organization_id,),
            ).fetchall()
        return tuple(self._vendor(row) for row in rows)

    def replace_vendor_wallet(
        self,
        *,
        organization_id: str,
        vendor_id: str,
        expected_current_wallet: str,
        new_wallet: str,
        verification_method: WalletVerificationMethod,
        verification_reference: str,
        verified_by_user_id: str,
        verified_at: datetime | None = None,
    ) -> Vendor:
        expected = normalize_wallet(expected_current_wallet)
        replacement = normalize_wallet(new_wallet)
        timestamp = verified_at or datetime.now(timezone.utc)
        with self._guard:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM vendors WHERE organization_id = ? AND id = ?",
                    (organization_id, vendor_id),
                ).fetchone()
                if row is None:
                    raise VendorDirectoryError("Vendor was not found in this organization.")
                current = self._vendor(row)
                if current.approved_wallet_address != expected:
                    raise VendorDirectoryError("Vendor wallet changed since it was last read.")
                if replacement == expected:
                    raise VendorDirectoryError("Replacement wallet must differ from the current wallet.")
                event = VendorWalletEvent(
                    organization_id=organization_id,
                    vendor_id=vendor_id,
                    event_type=WalletEventType.REPLACED,
                    wallet_address=replacement,
                    previous_wallet_address=expected,
                    verification_method=verification_method,
                    verification_reference=verification_reference,
                    verified_by_user_id=verified_by_user_id,
                    verified_at=timestamp,
                )
                self._connection.execute(
                    """
                    UPDATE vendors SET approved_wallet_address = ?, updated_at = ?
                    WHERE organization_id = ? AND id = ? AND approved_wallet_address = ?
                    """,
                    (replacement, timestamp.isoformat(), organization_id, vendor_id, expected),
                )
                self._insert_wallet_event(event)
                self._connection.execute("COMMIT")
                return Vendor(
                    id=current.id,
                    organization_id=current.organization_id,
                    legal_name=current.legal_name,
                    approved_wallet_address=replacement,
                    autopay_limit=current.autopay_limit,
                    risk_tier=current.risk_tier,
                    active=current.active,
                )
            except sqlite3.IntegrityError as exc:
                self._connection.execute("ROLLBACK")
                raise PersistenceError(
                    "Wallet verification could not be recorded for this vendor."
                ) from exc
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def vendor_wallet_history(
        self, *, organization_id: str, vendor_id: str
    ) -> tuple[VendorWalletEvent, ...]:
        self.get_vendor(organization_id=organization_id, vendor_id=vendor_id)
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT * FROM vendor_wallet_events
                WHERE organization_id = ? AND vendor_id = ? ORDER BY sequence
                """,
                (organization_id, vendor_id),
            ).fetchall()
        return tuple(self._wallet_event(row) for row in rows)

    def save_evidence(
        self,
        record: EvidenceRecord,
        *,
        content: bytes | None = None,
    ) -> EvidenceRecord:
        document = record.document
        if content is not None:
            if len(content) != document.byte_size:
                raise PersistenceError("Evidence byte size does not match its immutable metadata.")
            if hashlib.sha256(content).hexdigest() != document.content_sha256:
                raise PersistenceError("Evidence bytes do not match the immutable content hash.")
        fields_json = canonical_json(record.fields)
        with self._guard:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO evidence_documents
                        (organization_id, id, evidence_type, filename, mime_type,
                         content_sha256, byte_size, ingested_at, fields_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document.organization_id,
                        document.id,
                        document.evidence_type.value,
                        document.filename,
                        document.mime_type,
                        document.content_sha256,
                        document.byte_size,
                        document.ingested_at.isoformat(),
                        fields_json,
                    ),
                )
                if content is not None:
                    self._connection.execute(
                        """
                        INSERT INTO evidence_blobs (organization_id, document_id, content)
                        VALUES (?, ?, ?)
                        """,
                        (document.organization_id, document.id, content),
                    )
                self._connection.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                self._connection.execute("ROLLBACK")
                raise PersistenceError("Evidence ID or content already exists in this organization.") from exc
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return record

    def get_evidence(self, *, organization_id: str, document_id: str) -> EvidenceRecord:
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM evidence_documents WHERE organization_id = ? AND id = ?",
                (organization_id, document_id),
            ).fetchone()
        if row is None:
            raise PersistenceError("Evidence was not found in this organization.")
        document = EvidenceDocument(
            id=row["id"],
            organization_id=row["organization_id"],
            evidence_type=EvidenceType(row["evidence_type"]),
            filename=row["filename"],
            mime_type=row["mime_type"],
            content_sha256=row["content_sha256"],
            byte_size=row["byte_size"],
            ingested_at=datetime.fromisoformat(row["ingested_at"]),
        )
        fields_data = json.loads(row["fields_json"])
        fields = tuple(
            ExtractedField(
                name=item["name"],
                raw_value=item["raw_value"],
                normalized_value=item["normalized_value"],
                confidence=Decimal(item["confidence"]),
                method=ExtractionMethod(item["method"]),
                source=SourceLocation(
                    document_id=item["source"]["document_id"],
                    page_number=item["source"].get("page_number"),
                    bounding_box=(
                        tuple(item["source"]["bounding_box"])
                        if item["source"].get("bounding_box") is not None
                        else None
                    ),
                    json_pointer=item["source"].get("json_pointer"),
                ),
            )
            for item in fields_data
        )
        return EvidenceRecord(document=document, fields=fields)

    def get_evidence_content(self, *, organization_id: str, document_id: str) -> bytes:
        with self._guard:
            row = self._connection.execute(
                """
                SELECT content FROM evidence_blobs
                WHERE organization_id = ? AND document_id = ?
                """,
                (organization_id, document_id),
            ).fetchone()
        if row is None:
            raise PersistenceError("Evidence content was not found in this organization.")
        return bytes(row["content"])

    def link_invoice_evidence(
        self,
        *,
        organization_id: str,
        invoice_id: str,
        document_id: str,
        evidence_type: EvidenceType,
        linked_at: datetime | None = None,
    ) -> None:
        timestamp = linked_at or datetime.now(timezone.utc)
        with self._guard:
            try:
                self._connection.execute(
                    """
                    INSERT INTO invoice_evidence
                        (organization_id, invoice_id, document_id, evidence_type, linked_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        organization_id,
                        invoice_id,
                        document_id,
                        evidence_type.value,
                        timestamp.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PersistenceError(
                    "Invoice evidence must exist in the same organization and each type may be linked once."
                ) from exc

    def save_and_link_invoice_evidence(
        self,
        record: EvidenceRecord,
        *,
        content: bytes,
        invoice_id: str,
        linked_at: datetime | None = None,
    ) -> EvidenceRecord:
        document = record.document
        if len(content) != document.byte_size:
            raise PersistenceError("Evidence byte size does not match its immutable metadata.")
        if hashlib.sha256(content).hexdigest() != document.content_sha256:
            raise PersistenceError("Evidence bytes do not match the immutable content hash.")
        timestamp = linked_at or datetime.now(timezone.utc)
        with self._guard:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO evidence_documents
                        (organization_id, id, evidence_type, filename, mime_type,
                         content_sha256, byte_size, ingested_at, fields_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document.organization_id,
                        document.id,
                        document.evidence_type.value,
                        document.filename,
                        document.mime_type,
                        document.content_sha256,
                        document.byte_size,
                        document.ingested_at.isoformat(),
                        canonical_json(record.fields),
                    ),
                )
                self._connection.execute(
                    """
                    INSERT INTO evidence_blobs (organization_id, document_id, content)
                    VALUES (?, ?, ?)
                    """,
                    (document.organization_id, document.id, content),
                )
                self._connection.execute(
                    """
                    INSERT INTO invoice_evidence
                        (organization_id, invoice_id, document_id, evidence_type, linked_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        document.organization_id,
                        invoice_id,
                        document.id,
                        document.evidence_type.value,
                        timestamp.isoformat(),
                    ),
                )
                self._connection.execute("COMMIT")
                return record
            except sqlite3.IntegrityError as exc:
                self._connection.execute("ROLLBACK")
                raise PersistenceError(
                    "Evidence must be unique, tenant-bound, and the only linked document of its type."
                ) from exc
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def list_invoice_evidence(
        self,
        *,
        organization_id: str,
        invoice_id: str,
    ) -> tuple[EvidenceRecord, ...]:
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT document_id FROM invoice_evidence
                WHERE organization_id = ? AND invoice_id = ?
                ORDER BY evidence_type, linked_at, document_id
                """,
                (organization_id, invoice_id),
            ).fetchall()
        return tuple(
            self.get_evidence(
                organization_id=organization_id,
                document_id=row["document_id"],
            )
            for row in rows
        )

    def create_invoice(
        self,
        invoice: Invoice,
        *,
        status: InvoiceStatus = InvoiceStatus.DRAFT,
        created_at: datetime | None = None,
    ) -> StoredInvoice:
        timestamp = created_at or datetime.now(timezone.utc)
        with self._guard:
            try:
                self._connection.execute(
                    """
                    INSERT INTO invoices
                        (organization_id, id, vendor_id, invoice_number, currency, amount,
                         due_date, payment_wallet_address, source_document_hash, status,
                         version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        invoice.organization_id,
                        invoice.id,
                        invoice.vendor_id,
                        invoice.invoice_number,
                        invoice.currency,
                        format(invoice.amount, "f"),
                        invoice.due_date.isoformat(),
                        invoice.payment_wallet_address,
                        invoice.source_document_hash,
                        status.value,
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PersistenceError("Invoice ID is already in use in this organization.") from exc
        return StoredInvoice(invoice=invoice, status=status, version=1, created_at=timestamp, updated_at=timestamp)

    def get_invoice(self, *, organization_id: str, invoice_id: str) -> StoredInvoice:
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM invoices WHERE organization_id = ? AND id = ?",
                (organization_id, invoice_id),
            ).fetchone()
        if row is None:
            raise PersistenceError("Invoice was not found in this organization.")
        return self._stored_invoice(row)

    def list_invoices(
        self,
        *,
        organization_id: str,
        status: InvoiceStatus | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> InvoicePage:
        if limit < 1 or limit > 100:
            raise PersistenceError("Invoice page limit must be between 1 and 100.")
        clauses = ["organization_id = ?"]
        values: list[object] = [organization_id]
        if status is not None:
            clauses.append("status = ?")
            values.append(status.value)
        if cursor is not None:
            cursor_updated_at, cursor_id = self._decode_cursor(cursor)
            clauses.append("(updated_at < ? OR (updated_at = ? AND id < ?))")
            values.extend((cursor_updated_at, cursor_updated_at, cursor_id))
        values.append(limit + 1)
        query = f"""
            SELECT * FROM invoices
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
        """
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        next_cursor = None
        if has_more and visible_rows:
            next_cursor = self._encode_cursor(visible_rows[-1]["updated_at"], visible_rows[-1]["id"])
        return InvoicePage(
            items=tuple(self._stored_invoice(row) for row in visible_rows),
            next_cursor=next_cursor,
        )

    def operations_overview(
        self,
        *,
        organization_id: str,
        as_of: date | None = None,
        queue_limit: int = 12,
    ) -> OperationsOverview:
        if queue_limit < 1 or queue_limit > 50:
            raise PersistenceError("Operations queue limit must be between 1 and 50.")
        effective_date = as_of or date.today()
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM invoices WHERE organization_id = ? ORDER BY due_date, updated_at DESC",
                (organization_id,),
            ).fetchall()
        invoices = tuple(self._stored_invoice(row) for row in rows)
        terminal = {
            InvoiceStatus.RECONCILED,
            InvoiceStatus.REJECTED,
            InvoiceStatus.CANCELLED,
            InvoiceStatus.RECONCILIATION_MISMATCH,
        }
        blocked = {
            InvoiceStatus.HOLD,
            InvoiceStatus.REJECTED,
            InvoiceStatus.ESCALATED,
            InvoiceStatus.SUBMISSION_FAILED,
            InvoiceStatus.CONFIRMATION_TIMEOUT,
            InvoiceStatus.RECONCILIATION_MISMATCH,
        }
        open_items = tuple(item for item in invoices if item.status not in terminal)
        status_counts = {
            status.value: sum(1 for item in invoices if item.status == status)
            for status in InvoiceStatus
        }
        week_end = effective_date + timedelta(days=7)
        due_soon = tuple(
            item
            for item in open_items
            if effective_date <= item.invoice.due_date <= week_end
        )
        overdue = tuple(item for item in open_items if item.invoice.due_date < effective_date)

        def total(items: tuple[StoredInvoice, ...]) -> Decimal:
            return sum((item.invoice.amount for item in items), Decimal("0"))

        treasury_available: Decimal | None = None
        committed_since_snapshot: Decimal | None = None
        reserved_invoice_ids: set[str] = set()
        minimum_reserve: Decimal | None = None
        try:
            latest_snapshot = self.latest_treasury_snapshot(
                organization_id=organization_id
            )
            treasury_available = latest_snapshot.snapshot.available_usdc
            with self._guard:
                reservation_rows = self._connection.execute(
                    """
                    SELECT invoice_id, amount_usdc FROM payment_intents
                    WHERE organization_id = ? AND created_at > ?
                    """,
                    (organization_id, latest_snapshot.recorded_at.isoformat()),
                ).fetchall()
            committed_since_snapshot = sum(
                (Decimal(str(row["amount_usdc"])) for row in reservation_rows),
                Decimal("0"),
            )
            reserved_invoice_ids = {
                str(row["invoice_id"]) for row in reservation_rows
            }
        except PersistenceError:
            pass
        try:
            minimum_reserve = self.active_policy(
                organization_id=organization_id
            ).policy.minimum_cash_reserve_usdc
        except PolicyRepositoryError:
            pass
        open_exposure = total(open_items)
        unreserved_open_exposure = (
            total(
                tuple(
                    item
                    for item in open_items
                    if item.invoice.id not in reserved_invoice_ids
                )
            )
            if treasury_available is not None
            else None
        )
        return OperationsOverview(
            organization_id=organization_id,
            as_of=effective_date,
            invoice_count=len(invoices),
            status_counts=status_counts,
            open_exposure_usdc=open_exposure,
            blocked_exposure_usdc=total(
                tuple(item for item in invoices if item.status in blocked)
            ),
            due_next_7_days_usdc=total(due_soon),
            due_next_7_days_count=len(due_soon),
            overdue_usdc=total(overdue),
            overdue_count=len(overdue),
            reconciled_usdc=total(
                tuple(item for item in invoices if item.status == InvoiceStatus.RECONCILED)
            ),
            treasury_available_usdc=treasury_available,
            treasury_committed_since_snapshot_usdc=committed_since_snapshot,
            unreserved_open_exposure_usdc=unreserved_open_exposure,
            minimum_reserve_usdc=minimum_reserve,
            projected_after_open_usdc=(
                treasury_available
                - (committed_since_snapshot or Decimal("0"))
                - (unreserved_open_exposure or Decimal("0"))
                if treasury_available is not None
                else None
            ),
            work_queue=open_items[:queue_limit],
        )

    def create_agent_run(self, run: AgentRun) -> AgentRun:
        plan_json = canonical_json([item.to_payload() for item in run.items])
        with self._guard:
            try:
                self._connection.execute(
                    """
                    INSERT INTO agent_runs
                        (organization_id, id, status, as_of, state_hash, plan_hash,
                         plan_json, created_by_user_id, created_at,
                         executed_by_user_id, executed_at, results_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.organization_id,
                        run.id,
                        run.status.value,
                        run.as_of.isoformat(),
                        run.state_hash,
                        run.plan_hash,
                        plan_json,
                        run.created_by_user_id,
                        run.created_at.isoformat(),
                        run.executed_by_user_id,
                        run.executed_at.isoformat() if run.executed_at else None,
                        canonical_json(run.results),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PersistenceError("Agent run ID is already in use in this organization.") from exc
        return run

    def get_agent_run(self, *, organization_id: str, run_id: str) -> AgentRun:
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM agent_runs WHERE organization_id = ? AND id = ?",
                (organization_id, run_id),
            ).fetchone()
        if row is None:
            raise PersistenceError("Agent run was not found in this organization.")
        return self._agent_run(row)

    def latest_agent_run(self, *, organization_id: str) -> AgentRun | None:
        with self._guard:
            row = self._connection.execute(
                """
                SELECT * FROM agent_runs WHERE organization_id = ?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (organization_id,),
            ).fetchone()
        return self._agent_run(row) if row is not None else None

    def claim_agent_run_execution(
        self,
        *,
        organization_id: str,
        run_id: str,
        executed_by_user_id: str,
        claimed_at: datetime | None = None,
        lease_timeout: timedelta = timedelta(minutes=5),
    ) -> tuple[AgentRun, bool]:
        """Atomically grant one worker authority to execute a planned run.

        A durable EXECUTING state prevents concurrent API workers from repeating
        approval routing or settlement work. The payment layer remains
        independently idempotent; this claim protects the orchestration layer.
        """

        if lease_timeout <= timedelta(0):
            raise PersistenceError("Agent run execution lease timeout must be positive.")
        timestamp = claimed_at or datetime.now(timezone.utc)
        stale_before = timestamp - lease_timeout
        with self._guard:
            cursor = self._connection.execute(
                """
                UPDATE agent_runs
                SET status = ?, executed_by_user_id = ?, executed_at = ?
                WHERE organization_id = ? AND id = ? AND status = ?
                """,
                (
                    AgentRunStatus.EXECUTING.value,
                    executed_by_user_id,
                    timestamp.isoformat(),
                    organization_id,
                    run_id,
                    AgentRunStatus.PLANNED.value,
                ),
            )
            if cursor.rowcount != 1:
                cursor = self._connection.execute(
                    """
                    UPDATE agent_runs
                    SET executed_by_user_id = ?, executed_at = ?
                    WHERE organization_id = ? AND id = ? AND status = ?
                      AND executed_at IS NOT NULL AND executed_at <= ?
                    """,
                    (
                        executed_by_user_id,
                        timestamp.isoformat(),
                        organization_id,
                        run_id,
                        AgentRunStatus.EXECUTING.value,
                        stale_before.isoformat(),
                    ),
                )
        if cursor.rowcount == 1:
            return (
                self.get_agent_run(organization_id=organization_id, run_id=run_id),
                True,
            )
        return self.get_agent_run(organization_id=organization_id, run_id=run_id), False

    def complete_agent_run(
        self,
        *,
        organization_id: str,
        run_id: str,
        status: AgentRunStatus,
        executed_by_user_id: str,
        results: tuple[dict[str, object], ...],
        executed_at: datetime | None = None,
    ) -> AgentRun:
        timestamp = executed_at or datetime.now(timezone.utc)
        if status in {AgentRunStatus.PLANNED, AgentRunStatus.EXECUTING}:
            raise PersistenceError("A completed agent run requires a terminal status.")
        with self._guard:
            cursor = self._connection.execute(
                """
                UPDATE agent_runs
                SET status = ?, executed_by_user_id = ?, executed_at = ?, results_json = ?
                WHERE organization_id = ? AND id = ? AND status = ?
                """,
                (
                    status.value,
                    executed_by_user_id,
                    timestamp.isoformat(),
                    canonical_json(results),
                    organization_id,
                    run_id,
                    AgentRunStatus.EXECUTING.value,
                ),
            )
        if cursor.rowcount != 1:
            existing = self.get_agent_run(organization_id=organization_id, run_id=run_id)
            if existing.status in {AgentRunStatus.EXECUTED, AgentRunStatus.PARTIAL}:
                return existing
            raise PersistenceError("Agent run could not be completed.")
        return self.get_agent_run(organization_id=organization_id, run_id=run_id)

    def known_invoice_fingerprints(
        self,
        *,
        organization_id: str,
        exclude_invoice_id: str | None = None,
    ) -> tuple[str, ...]:
        query = "SELECT * FROM invoices WHERE organization_id = ?"
        values: list[object] = [organization_id]
        if exclude_invoice_id is not None:
            query += " AND id <> ?"
            values.append(exclude_invoice_id)
        query += " ORDER BY id"
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        return tuple(self._stored_invoice(row).invoice.fingerprint for row in rows)

    def transition_invoice(
        self,
        *,
        organization_id: str,
        invoice_id: str,
        target_status: InvoiceStatus,
        expected_version: int,
        actor_user_id: str,
        correlation_id: str,
        created_at: datetime | None = None,
    ) -> StoredInvoice:
        timestamp = created_at or datetime.now(timezone.utc)
        if not actor_user_id.strip() or not correlation_id.strip():
            raise WorkflowError("Actor and correlation IDs are required for transitions.")
        with self._guard:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM invoices WHERE organization_id = ? AND id = ?",
                    (organization_id, invoice_id),
                ).fetchone()
                if row is None:
                    raise PersistenceError("Invoice was not found in this organization.")
                current = InvoiceStatus(row["status"])
                if row["version"] != expected_version:
                    raise WorkflowError("Invoice was updated by another operation.")
                require_transition(current, target_status)
                next_version = expected_version + 1
                self._connection.execute(
                    """
                    UPDATE invoices SET status = ?, version = ?, updated_at = ?
                    WHERE organization_id = ? AND id = ? AND version = ?
                    """,
                    (
                        target_status.value,
                        next_version,
                        timestamp.isoformat(),
                        organization_id,
                        invoice_id,
                        expected_version,
                    ),
                )
                self._connection.execute(
                    """
                    INSERT INTO invoice_transitions
                        (organization_id, invoice_id, from_status, to_status,
                         actor_user_id, correlation_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        organization_id,
                        invoice_id,
                        current.value,
                        target_status.value,
                        actor_user_id,
                        correlation_id,
                        timestamp.isoformat(),
                    ),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return self.get_invoice(organization_id=organization_id, invoice_id=invoice_id)

    def transitions(self, *, organization_id: str, invoice_id: str) -> tuple[InvoiceTransition, ...]:
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT * FROM invoice_transitions
                WHERE organization_id = ? AND invoice_id = ? ORDER BY sequence
                """,
                (organization_id, invoice_id),
            ).fetchall()
        return tuple(
            InvoiceTransition(
                sequence=row["sequence"],
                organization_id=row["organization_id"],
                invoice_id=row["invoice_id"],
                from_status=InvoiceStatus(row["from_status"]),
                to_status=InvoiceStatus(row["to_status"]),
                actor_user_id=row["actor_user_id"],
                correlation_id=row["correlation_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        )

    def store_decision(self, record: DecisionRecord) -> tuple[DecisionRecord, bool]:
        agent_json = (
            canonical_json(
                {
                    "action": record.agent_recommendation.action.value,
                    "summary": record.agent_recommendation.summary,
                    "reason_codes": record.agent_recommendation.reason_codes,
                    "confidence": format(record.agent_recommendation.confidence, "f"),
                    "evidence_refs": record.agent_recommendation.evidence_refs,
                }
            )
            if record.agent_recommendation is not None
            else None
        )
        policy_json = canonical_json(
            {
                "action": record.policy_decision.action.value,
                "policy_version": record.policy_decision.policy_version,
                "invoice_fingerprint": record.policy_decision.invoice_fingerprint,
                "approval_reference": record.policy_decision.approval_reference,
                "rule_results": [
                    {
                        "code": result.code,
                        "disposition": result.disposition.value,
                        "message": result.message,
                        "remediation": result.remediation,
                    }
                    for result in record.policy_decision.rule_results
                ],
                "replay_inputs": (
                    record.replay_inputs.to_payload()
                    if record.replay_inputs is not None
                    else None
                ),
                "replay_input_hash": record.replay_input_hash,
            }
        )
        with self._guard:
            try:
                self._connection.execute(
                    """
                    INSERT INTO decisions
                        (organization_id, id, invoice_id, evidence_manifest_hash,
                         policy_version, policy_content_hash, agent_recommendation_json,
                         policy_decision_json, final_action, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.organization_id,
                        record.id,
                        record.invoice_id,
                        record.evidence_manifest_hash,
                        record.policy_version,
                        record.policy_content_hash,
                        agent_json,
                        policy_json,
                        record.final_action.value,
                        record.created_at.isoformat(),
                    ),
                )
                return record, True
            except sqlite3.IntegrityError as exc:
                existing = self.get_decision(
                    organization_id=record.organization_id,
                    decision_id=record.id,
                )
                if not self._same_decision_content(existing, record):
                    raise PersistenceError("Decision ID is already bound to different content.") from exc
                return existing, False

    def get_decision(self, *, organization_id: str, decision_id: str) -> DecisionRecord:
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM decisions WHERE organization_id = ? AND id = ?",
                (organization_id, decision_id),
            ).fetchone()
        if row is None:
            raise KeyError("Decision was not found in this organization.")
        return self._decision_record(row)

    def latest_decision_for_invoice(
        self,
        *,
        organization_id: str,
        invoice_id: str,
    ) -> DecisionRecord:
        with self._guard:
            row = self._connection.execute(
                """
                SELECT * FROM decisions
                WHERE organization_id = ? AND invoice_id = ?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (organization_id, invoice_id),
            ).fetchone()
        if row is None:
            raise PersistenceError("No decision exists for this invoice in this organization.")
        return self._decision_record(row)

    def create_or_get_approval(self, request: ApprovalRequest) -> ApprovalRequest:
        with self._guard:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    """
                    SELECT * FROM approvals
                    WHERE organization_id = ? AND decision_id = ?
                    """,
                    (request.organization_id, request.decision_id),
                ).fetchone()
                if existing is not None:
                    self._connection.execute("COMMIT")
                    return self._approval(existing)
                self._connection.execute(
                    """
                    INSERT INTO approvals
                        (organization_id, id, invoice_id, decision_id,
                         requested_by_user_id, requested_at, status, version,
                         resolved_by_user_id, resolved_at, resolution_note)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.organization_id,
                        request.id,
                        request.invoice_id,
                        request.decision_id,
                        request.requested_by_user_id,
                        request.requested_at.isoformat(),
                        request.status.value,
                        request.version,
                        request.resolved_by_user_id,
                        request.resolved_at.isoformat() if request.resolved_at else None,
                        request.resolution_note,
                    ),
                )
                self._connection.execute("COMMIT")
                return request
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def resolve_approval(
        self,
        *,
        organization_id: str,
        approval_id: str,
        approver_user_id: str,
        approve: bool,
        resolution_note: str,
        expected_version: int,
        resolved_at: datetime,
    ) -> ApprovalRequest:
        with self._guard:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM approvals WHERE organization_id = ? AND id = ?",
                    (organization_id, approval_id),
                ).fetchone()
                if row is None:
                    raise ApprovalError("Approval request was not found in this organization.")
                current = self._approval(row)
                if current.requested_by_user_id == approver_user_id:
                    raise ApprovalError("Requester and approver must be different users.")
                if current.status != ApprovalStatus.PENDING:
                    raise ApprovalError("Approval request has already been resolved.")
                if current.version != expected_version:
                    raise ApprovalError("Approval request was updated by another operation.")
                status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
                self._connection.execute(
                    """
                    UPDATE approvals
                    SET status = ?, version = ?, resolved_by_user_id = ?,
                        resolved_at = ?, resolution_note = ?
                    WHERE organization_id = ? AND id = ? AND version = ?
                    """,
                    (
                        status.value,
                        expected_version + 1,
                        approver_user_id,
                        resolved_at.isoformat(),
                        resolution_note,
                        organization_id,
                        approval_id,
                        expected_version,
                    ),
                )
                updated = self._connection.execute(
                    "SELECT * FROM approvals WHERE organization_id = ? AND id = ?",
                    (organization_id, approval_id),
                ).fetchone()
                self._connection.execute("COMMIT")
                return self._approval(updated)
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def get_approval(
        self, *, organization_id: str, approval_id: str
    ) -> ApprovalRequest | None:
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM approvals WHERE organization_id = ? AND id = ?",
                (organization_id, approval_id),
            ).fetchone()
        return self._approval(row) if row is not None else None

    def find_approval_for_decision(
        self, *, organization_id: str, decision_id: str
    ) -> ApprovalRequest | None:
        with self._guard:
            row = self._connection.execute(
                """
                SELECT * FROM approvals
                WHERE organization_id = ? AND decision_id = ?
                """,
                (organization_id, decision_id),
            ).fetchone()
        return self._approval(row) if row is not None else None

    def pending_approvals(self, *, organization_id: str) -> tuple[ApprovalRequest, ...]:
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT * FROM approvals
                WHERE organization_id = ? AND status = ?
                ORDER BY requested_at, id
                """,
                (organization_id, ApprovalStatus.PENDING.value),
            ).fetchall()
        return tuple(self._approval(row) for row in rows)

    def append(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
        created_at: datetime | None = None,
    ) -> AuditEvent:
        organization_id = str(payload.get("organization_id", "")).strip()
        if not organization_id:
            raise PersistenceError("Audit payload requires an organization_id.")
        timestamp = created_at or datetime.now(timezone.utc)
        with self._guard:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    """
                    SELECT * FROM audit_events
                    WHERE organization_id = ? AND aggregate_type = ?
                      AND aggregate_id = ? AND event_type = ?
                    """,
                    (organization_id, aggregate_type, aggregate_id, event_type),
                ).fetchone()
                if existing is not None:
                    self._connection.execute("COMMIT")
                    return self._audit_event(existing)
                previous = self._connection.execute(
                    """
                    SELECT sequence, event_hash FROM audit_events
                    WHERE organization_id = ? ORDER BY sequence DESC LIMIT 1
                    """,
                    (organization_id,),
                ).fetchone()
                sequence = (previous["sequence"] + 1) if previous is not None else 1
                previous_hash = previous["event_hash"] if previous is not None else GENESIS_HASH
                event_hash = audit_event_hash(
                    sequence=sequence,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    event_type=event_type,
                    payload=payload,
                    previous_hash=previous_hash,
                    created_at=timestamp,
                )
                self._connection.execute(
                    """
                    INSERT INTO audit_events
                        (organization_id, sequence, aggregate_type, aggregate_id,
                         event_type, payload_json, previous_hash, event_hash, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        organization_id,
                        sequence,
                        aggregate_type,
                        aggregate_id,
                        event_type,
                        canonical_json(payload),
                        previous_hash,
                        event_hash,
                        timestamp.isoformat(),
                    ),
                )
                self._connection.execute("COMMIT")
                return AuditEvent(
                    sequence=sequence,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    event_type=event_type,
                    payload=payload,
                    previous_hash=previous_hash,
                    event_hash=event_hash,
                    created_at=timestamp,
                )
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def audit_events(self, *, organization_id: str) -> tuple[AuditEvent, ...]:
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT * FROM audit_events
                WHERE organization_id = ? ORDER BY sequence
                """,
                (organization_id,),
            ).fetchall()
        return tuple(self._audit_event(row) for row in rows)

    def search_audit_events(
        self,
        *,
        organization_id: str,
        event_type: str | None = None,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        query: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        before_sequence: int | None = None,
        limit: int = 50,
    ) -> tuple[AuditEvent, ...]:
        """Return a tenant-scoped, newest-first audit page.

        ``query`` searches identifiers, event names, and the canonical JSON
        payload. SQL wildcards are escaped so user text remains literal.
        """

        if not organization_id.strip():
            raise PersistenceError("Audit search requires an organization ID.")
        if not 1 <= limit <= 201:
            raise PersistenceError("Audit search limit must be between 1 and 201.")
        if before_sequence is not None and before_sequence < 1:
            raise PersistenceError("Audit search cursor must be positive.")

        clauses = ["organization_id = ?"]
        parameters: list[object] = [organization_id]
        for column, value in (
            ("event_type", event_type),
            ("aggregate_type", aggregate_type),
            ("aggregate_id", aggregate_id),
        ):
            normalized = (value or "").strip()
            if normalized:
                clauses.append(f"{column} = ?")
                parameters.append(normalized)
        if before_sequence is not None:
            clauses.append("sequence < ?")
            parameters.append(before_sequence)
        if created_after is not None:
            clauses.append("julianday(created_at) >= julianday(?)")
            parameters.append(created_after.isoformat())
        if created_before is not None:
            clauses.append("julianday(created_at) <= julianday(?)")
            parameters.append(created_before.isoformat())
        normalized_query = (query or "").strip()
        if normalized_query:
            escaped = (
                normalized_query.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            pattern = f"%{escaped}%"
            clauses.append(
                "("
                "event_type LIKE ? ESCAPE '\\' OR "
                "aggregate_type LIKE ? ESCAPE '\\' OR "
                "aggregate_id LIKE ? ESCAPE '\\' OR "
                "payload_json LIKE ? ESCAPE '\\'"
                ")"
            )
            parameters.extend((pattern, pattern, pattern, pattern))
        parameters.append(limit)

        with self._guard:
            rows = self._connection.execute(
                f"""
                SELECT * FROM audit_events
                WHERE {' AND '.join(clauses)}
                ORDER BY sequence DESC
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
        return tuple(self._audit_event(row) for row in rows)

    def verify_audit_chain(self, *, organization_id: str) -> bool:
        previous_hash = GENESIS_HASH
        for expected_sequence, event in enumerate(
            self.audit_events(organization_id=organization_id), start=1
        ):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                return False
            if event.event_hash != audit_event_hash(
                sequence=event.sequence,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                event_type=event.event_type,
                payload=event.payload,
                previous_hash=event.previous_hash,
                created_at=event.created_at,
            ):
                return False
            previous_hash = event.event_hash
        return True

    def create_or_get_payment_intent(
        self,
        intent: PaymentIntent,
        *,
        created_at: datetime | None = None,
        enforce_active_controls: bool = False,
        maximum_snapshot_age: timedelta = timedelta(minutes=15),
    ) -> tuple[PaymentIntent, bool]:
        """Persist one immutable intent per tenant/decision, safe under races.

        When active controls are enabled, the policy read, treasury reservation
        calculation, and intent insert share one ``BEGIN IMMEDIATE`` transaction.
        This makes competing workers serialize on the same durable limits instead
        of each trusting the same stale in-memory balance.
        """

        timestamp = created_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise PersistenceError("Payment intent timestamp must be timezone-aware.")
        if maximum_snapshot_age <= timedelta(0):
            raise PersistenceError("Treasury snapshot maximum age must be positive.")
        with self._guard:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing_row = self._connection.execute(
                    """
                    SELECT * FROM payment_intents
                    WHERE organization_id = ? AND decision_id = ?
                    """,
                    (intent.organization_id, intent.decision_id),
                ).fetchone()
                if existing_row is not None:
                    existing = self._payment_intent(existing_row)
                    self._ensure_same_payment(existing, intent)
                    receipt_row = self._connection.execute(
                        """
                        SELECT 1 FROM settlement_receipts
                        WHERE organization_id = ? AND payment_intent_id = ?
                        """,
                        (intent.organization_id, existing.id),
                    ).fetchone()
                    if receipt_row is not None:
                        self._connection.execute("COMMIT")
                        return existing, False
                if enforce_active_controls:
                    self._assert_payment_reservation_allowed(
                        intent=intent,
                        timestamp=timestamp,
                        maximum_snapshot_age=maximum_snapshot_age,
                    )
                if existing_row is not None:
                    self._connection.execute("COMMIT")
                    return existing, False
                self._connection.execute(
                    """
                    INSERT INTO payment_intents
                        (organization_id, id, invoice_id, decision_id, recipient,
                         amount_usdc, network, idempotency_key, approval_reference, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent.organization_id,
                        intent.id,
                        intent.invoice_id,
                        intent.decision_id,
                        intent.recipient,
                        format(intent.amount_usdc, "f"),
                        intent.network.value,
                        intent.idempotency_key,
                        intent.approval_reference,
                        timestamp.isoformat(),
                    ),
                )
                self._connection.execute("COMMIT")
                return intent, True
            except sqlite3.IntegrityError as exc:
                self._connection.execute("ROLLBACK")
                raise PersistenceError("Payment intent violates a tenant or idempotency constraint.") from exc
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def _assert_payment_reservation_allowed(
        self,
        *,
        intent: PaymentIntent,
        timestamp: datetime,
        maximum_snapshot_age: timedelta,
    ) -> None:
        policy_row = self._connection.execute(
            """
            SELECT p.* FROM active_policies a
            JOIN policies p
              ON p.organization_id = a.organization_id AND p.version = a.version
            WHERE a.organization_id = ?
            """,
            (intent.organization_id,),
        ).fetchone()
        if policy_row is None:
            raise SettlementExecutionBlocked(
                "NO_ACTIVE_POLICY",
                "Settlement requires an active organization policy.",
            )
        policy_details = {
            "active_policy_version": str(policy_row["version"]),
            "active_policy_hash": str(policy_row["content_hash"]),
        }
        if bool(policy_row["kill_switch_enabled"]):
            raise SettlementExecutionBlocked(
                "KILL_SWITCH_ENABLED",
                "The active organization policy has engaged the settlement kill switch.",
                details=policy_details,
            )
        if str(policy_row["allowed_asset"]).upper() != "USDC":
            raise SettlementExecutionBlocked(
                "ASSET_NOT_ALLOWED",
                "The active policy does not allow USDC settlement.",
                details=policy_details,
            )
        if str(policy_row["allowed_network"]).upper() != intent.network.value.upper():
            raise SettlementExecutionBlocked(
                "NETWORK_NOT_ALLOWED",
                "The active policy does not allow settlement on the configured Arc network.",
                details=policy_details,
            )

        autonomous_limit = Decimal(str(policy_row["maximum_autonomous_payment_usdc"]))
        if not bool(policy_row["autonomous_payments_enabled"]) and not intent.approval_reference:
            raise SettlementExecutionBlocked(
                "AUTONOMOUS_PAYMENTS_DISABLED",
                "No-touch settlement is disabled and this payment has no approval reference.",
                details=policy_details,
            )
        if intent.amount_usdc > autonomous_limit and not intent.approval_reference:
            raise SettlementExecutionBlocked(
                "AUTONOMY_LIMIT_EXCEEDED",
                "Settlement exceeds the current autonomous limit and has no approval reference.",
                details={
                    **policy_details,
                    "amount_usdc": format(intent.amount_usdc, "f"),
                    "maximum_autonomous_payment_usdc": format(autonomous_limit, "f"),
                },
            )

        snapshot_row = self._connection.execute(
            """
            SELECT * FROM treasury_snapshots
            WHERE organization_id = ? ORDER BY sequence DESC LIMIT 1
            """,
            (intent.organization_id,),
        ).fetchone()
        if snapshot_row is None:
            raise SettlementExecutionBlocked(
                "TREASURY_SNAPSHOT_MISSING",
                "Settlement requires a recent treasury snapshot.",
                details=policy_details,
            )
        recorded_at = datetime.fromisoformat(str(snapshot_row["recorded_at"]))
        snapshot_age = timestamp.astimezone(timezone.utc) - recorded_at.astimezone(timezone.utc)
        if snapshot_age < timedelta(0) or snapshot_age > maximum_snapshot_age:
            raise SettlementExecutionBlocked(
                "TREASURY_SNAPSHOT_STALE",
                "The latest treasury snapshot is outside the settlement freshness window.",
                details={
                    **policy_details,
                    "treasury_snapshot_sequence": str(snapshot_row["sequence"]),
                    "treasury_snapshot_recorded_at": recorded_at.isoformat(),
                    "maximum_snapshot_age_seconds": str(int(maximum_snapshot_age.total_seconds())),
                },
            )

        reservation_rows = self._connection.execute(
            """
            SELECT id, amount_usdc FROM payment_intents
            WHERE organization_id = ? AND created_at > ? AND id <> ?
            """,
            (intent.organization_id, recorded_at.isoformat(), intent.id),
        ).fetchall()
        committed_since_snapshot = sum(
            (Decimal(str(row["amount_usdc"])) for row in reservation_rows),
            Decimal("0"),
        )
        available = Decimal(str(snapshot_row["available_usdc"]))
        spent_today = Decimal(str(snapshot_row["spent_today_usdc"]))
        daily_limit = Decimal(str(policy_row["daily_payment_limit_usdc"]))
        daily_autonomous_limit = Decimal(
            str(policy_row["daily_autonomous_payment_limit_usdc"])
        )
        reserve_floor = Decimal(str(policy_row["minimum_cash_reserve_usdc"]))
        projected_daily_spend = spent_today + committed_since_snapshot + intent.amount_usdc
        projected_available = available - committed_since_snapshot - intent.amount_usdc
        treasury_details = {
            **policy_details,
            "treasury_snapshot_sequence": str(snapshot_row["sequence"]),
            "treasury_snapshot_recorded_at": recorded_at.isoformat(),
            "committed_since_snapshot_usdc": format(committed_since_snapshot, "f"),
            "requested_amount_usdc": format(intent.amount_usdc, "f"),
            "projected_daily_spend_usdc": format(projected_daily_spend, "f"),
            "daily_payment_limit_usdc": format(daily_limit, "f"),
            "daily_autonomous_payment_limit_usdc": format(
                daily_autonomous_limit, "f"
            ),
            "projected_available_usdc": format(projected_available, "f"),
            "minimum_cash_reserve_usdc": format(reserve_floor, "f"),
        }
        if projected_daily_spend > daily_limit:
            raise SettlementExecutionBlocked(
                "DAILY_LIMIT_EXCEEDED",
                "Settlement would exceed the active policy's durable daily payment limit.",
                details=treasury_details,
            )
        if (
            not intent.approval_reference
            and projected_daily_spend > daily_autonomous_limit
        ):
            raise SettlementExecutionBlocked(
                "DAILY_AUTONOMY_LIMIT_EXCEEDED",
                "Settlement exceeds the daily no-touch ceiling and has no approval reference.",
                details=treasury_details,
            )
        if projected_available < reserve_floor:
            raise SettlementExecutionBlocked(
                "MINIMUM_RESERVE_BREACH",
                "Settlement would reduce reserved treasury funds below the active minimum.",
                details=treasury_details,
            )

    def get_payment_intent(
        self,
        *,
        organization_id: str,
        payment_intent_id: str,
    ) -> PaymentIntent:
        with self._guard:
            row = self._connection.execute(
                """
                SELECT * FROM payment_intents
                WHERE organization_id = ? AND id = ?
                """,
                (organization_id, payment_intent_id),
            ).fetchone()
        if row is None:
            raise PersistenceError("Payment intent was not found in this organization.")
        return self._payment_intent(row)

    def get_payment_intent_for_decision(
        self,
        *,
        organization_id: str,
        decision_id: str,
    ) -> PaymentIntent:
        with self._guard:
            row = self._connection.execute(
                """
                SELECT * FROM payment_intents
                WHERE organization_id = ? AND decision_id = ?
                """,
                (organization_id, decision_id),
            ).fetchone()
        if row is None:
            raise PersistenceError("Payment intent was not found in this organization.")
        return self._payment_intent(row)

    def save_settlement_receipt(self, receipt: SettlementReceipt) -> tuple[SettlementReceipt, bool]:
        with self._guard:
            try:
                self._connection.execute(
                    """
                    INSERT INTO settlement_receipts
                        (organization_id, payment_intent_id, provider, provider_reference,
                         transaction_hash, block_number, confirmed_recipient,
                         confirmed_amount_usdc, network, status, confirmed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt.organization_id,
                        receipt.payment_intent_id,
                        receipt.provider,
                        receipt.provider_reference,
                        receipt.transaction_hash,
                        receipt.block_number,
                        receipt.confirmed_recipient,
                        format(receipt.confirmed_amount_usdc, "f"),
                        receipt.network.value,
                        receipt.status.value,
                        receipt.confirmed_at.isoformat(),
                    ),
                )
                return receipt, True
            except sqlite3.IntegrityError as exc:
                row = self._connection.execute(
                    """
                    SELECT * FROM settlement_receipts
                    WHERE organization_id = ? AND payment_intent_id = ?
                    """,
                    (receipt.organization_id, receipt.payment_intent_id),
                ).fetchone()
                if row is None:
                    raise PersistenceError("Settlement receipt violates a uniqueness constraint.") from exc
                existing = self._settlement_receipt(row)
                if existing != receipt:
                    raise PersistenceError("Payment intent is already bound to another settlement receipt.") from exc
                return existing, False

    def record_settlement_attempt(
        self,
        attempt: SettlementAttempt,
    ) -> SettlementAttempt:
        if not attempt.correlation_id.strip():
            raise PersistenceError("Settlement attempt requires a correlation ID.")
        message = attempt.error_message
        if message is not None:
            message = " ".join(message.split())[:500]
        with self._guard:
            cursor = self._connection.execute(
                """
                INSERT INTO settlement_attempts
                    (organization_id, payment_intent_id, invoice_id, provider,
                     outcome, retryable, error_code, error_message,
                     correlation_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.organization_id,
                    attempt.payment_intent_id,
                    attempt.invoice_id,
                    attempt.provider,
                    attempt.outcome.value,
                    1 if attempt.retryable else 0,
                    attempt.error_code,
                    message,
                    attempt.correlation_id,
                    attempt.created_at.isoformat(),
                ),
            )
        return SettlementAttempt(
            sequence=int(cursor.lastrowid),
            organization_id=attempt.organization_id,
            payment_intent_id=attempt.payment_intent_id,
            invoice_id=attempt.invoice_id,
            provider=attempt.provider,
            outcome=attempt.outcome,
            retryable=attempt.retryable,
            error_code=attempt.error_code,
            error_message=message,
            correlation_id=attempt.correlation_id,
            created_at=attempt.created_at,
        )

    def settlement_attempts(
        self,
        *,
        organization_id: str,
        payment_intent_id: str | None = None,
        limit: int = 100,
    ) -> tuple[SettlementAttempt, ...]:
        if limit < 1 or limit > 500:
            raise PersistenceError("Settlement attempt limit must be between 1 and 500.")
        query = "SELECT * FROM settlement_attempts WHERE organization_id = ?"
        values: list[object] = [organization_id]
        if payment_intent_id is not None:
            query += " AND payment_intent_id = ?"
            values.append(payment_intent_id)
        query += " ORDER BY sequence DESC LIMIT ?"
        values.append(limit)
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        return tuple(self._settlement_attempt(row) for row in rows)

    def latest_settlement_attempt(
        self,
        *,
        organization_id: str,
        payment_intent_id: str,
    ) -> SettlementAttempt | None:
        attempts = self.settlement_attempts(
            organization_id=organization_id,
            payment_intent_id=payment_intent_id,
            limit=1,
        )
        return attempts[0] if attempts else None

    def get_settlement_receipt(
        self,
        *,
        organization_id: str,
        payment_intent_id: str,
    ) -> SettlementReceipt:
        with self._guard:
            row = self._connection.execute(
                """
                SELECT * FROM settlement_receipts
                WHERE organization_id = ? AND payment_intent_id = ?
                """,
                (organization_id, payment_intent_id),
            ).fetchone()
        if row is None:
            raise PersistenceError("Settlement receipt was not found in this organization.")
        return self._settlement_receipt(row)

    def find_settlement_receipt(
        self,
        *,
        organization_id: str,
        payment_intent_id: str,
    ) -> SettlementReceipt | None:
        with self._guard:
            row = self._connection.execute(
                """
                SELECT * FROM settlement_receipts
                WHERE organization_id = ? AND payment_intent_id = ?
                """,
                (organization_id, payment_intent_id),
            ).fetchone()
        return self._settlement_receipt(row) if row is not None else None

    def accounting_ledger(self, *, organization_id: str) -> tuple[AccountingLedgerRow, ...]:
        """Return reconciled payments as a tenant-scoped accounting ledger."""

        with self._guard:
            rows = self._connection.execute(
                """
                SELECT
                    i.id AS invoice_id,
                    i.invoice_number,
                    i.vendor_id,
                    v.legal_name AS vendor_legal_name,
                    i.currency,
                    i.amount AS amount_usdc,
                    i.due_date,
                    i.status AS invoice_status,
                    d.id AS decision_id,
                    d.evidence_manifest_hash,
                    d.policy_version,
                    d.policy_content_hash,
                    d.final_action,
                    a.id AS approval_id,
                    a.status AS approval_status,
                    a.resolved_by_user_id AS approval_resolved_by,
                    a.resolved_at AS approval_resolved_at,
                    p.id AS payment_intent_id,
                    p.recipient,
                    p.network,
                    r.provider,
                    r.provider_reference,
                    r.transaction_hash,
                    r.block_number,
                    r.status AS settlement_status,
                    r.confirmed_at
                FROM settlement_receipts AS r
                INNER JOIN payment_intents AS p
                    ON p.organization_id = r.organization_id
                    AND p.id = r.payment_intent_id
                INNER JOIN invoices AS i
                    ON i.organization_id = p.organization_id
                    AND i.id = p.invoice_id
                INNER JOIN vendors AS v
                    ON v.organization_id = i.organization_id
                    AND v.id = i.vendor_id
                INNER JOIN decisions AS d
                    ON d.organization_id = p.organization_id
                    AND d.id = p.decision_id
                LEFT JOIN approvals AS a
                    ON a.organization_id = d.organization_id
                    AND a.decision_id = d.id
                WHERE r.organization_id = ?
                ORDER BY r.confirmed_at ASC, i.id ASC
                """,
                (organization_id,),
            ).fetchall()
        return tuple(
            AccountingLedgerRow(
                invoice_id=str(row["invoice_id"]),
                invoice_number=str(row["invoice_number"]),
                vendor_id=str(row["vendor_id"]),
                vendor_legal_name=str(row["vendor_legal_name"]),
                currency=str(row["currency"]),
                amount_usdc=Decimal(str(row["amount_usdc"])),
                due_date=date.fromisoformat(str(row["due_date"])),
                invoice_status=str(row["invoice_status"]),
                decision_id=str(row["decision_id"]),
                evidence_manifest_hash=str(row["evidence_manifest_hash"]),
                policy_version=str(row["policy_version"]),
                policy_content_hash=str(row["policy_content_hash"]),
                final_action=str(row["final_action"]),
                approval_id=str(row["approval_id"]) if row["approval_id"] is not None else None,
                approval_status=(
                    str(row["approval_status"]) if row["approval_status"] is not None else None
                ),
                approval_resolved_by=(
                    str(row["approval_resolved_by"])
                    if row["approval_resolved_by"] is not None
                    else None
                ),
                approval_resolved_at=(
                    datetime.fromisoformat(str(row["approval_resolved_at"]))
                    if row["approval_resolved_at"] is not None
                    else None
                ),
                payment_intent_id=str(row["payment_intent_id"]),
                recipient=str(row["recipient"]),
                network=str(row["network"]),
                provider=str(row["provider"]),
                provider_reference=str(row["provider_reference"]),
                transaction_hash=str(row["transaction_hash"]),
                block_number=int(row["block_number"]),
                settlement_status=str(row["settlement_status"]),
                confirmed_at=datetime.fromisoformat(str(row["confirmed_at"])),
            )
            for row in rows
        )

    def _insert_wallet_event(self, event: VendorWalletEvent) -> None:
        self._connection.execute(
            """
            INSERT INTO vendor_wallet_events
                (organization_id, vendor_id, event_type, wallet_address,
                 previous_wallet_address, verification_method,
                 verification_reference, verified_by_user_id, verified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.organization_id,
                event.vendor_id,
                event.event_type.value,
                event.wallet_address,
                event.previous_wallet_address,
                event.verification_method.value,
                event.verification_reference,
                event.verified_by_user_id,
                event.verified_at.isoformat(),
            ),
        )

    @staticmethod
    def _stored_invoice(row: sqlite3.Row) -> StoredInvoice:
        invoice = Invoice(
            id=row["id"],
            organization_id=row["organization_id"],
            vendor_id=row["vendor_id"],
            invoice_number=row["invoice_number"],
            currency=row["currency"],
            amount=Decimal(row["amount"]),
            due_date=date.fromisoformat(row["due_date"]),
            payment_wallet_address=row["payment_wallet_address"],
            source_document_hash=row["source_document_hash"],
        )
        return StoredInvoice(
            invoice=invoice,
            status=InvoiceStatus(row["status"]),
            version=row["version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _stored_policy(row: sqlite3.Row) -> StoredPolicy:
        return StoredPolicy(
            policy=Policy(
                version=row["version"],
                organization_id=row["organization_id"],
                daily_payment_limit_usdc=Decimal(row["daily_payment_limit_usdc"]),
                daily_autonomous_payment_limit_usdc=Decimal(
                    row["daily_autonomous_payment_limit_usdc"]
                ),
                autonomous_payments_enabled=bool(row["autonomous_payments_enabled"]),
                minimum_cash_reserve_usdc=Decimal(row["minimum_cash_reserve_usdc"]),
                maximum_autonomous_payment_usdc=Decimal(
                    row["maximum_autonomous_payment_usdc"]
                ),
                po_amount_tolerance_usdc=Decimal(row["po_amount_tolerance_usdc"]),
                allowed_asset=row["allowed_asset"],
                allowed_network=row["allowed_network"],
                kill_switch_enabled=bool(row["kill_switch_enabled"]),
                schedule_payments_before_due_days=row[
                    "schedule_payments_before_due_days"
                ],
            ),
            content_hash=row["content_hash"],
            activated_by_user_id=row["activated_by_user_id"],
            activated_at=datetime.fromisoformat(row["activated_at"]),
        )

    @staticmethod
    def _treasury_snapshot(row: sqlite3.Row) -> StoredTreasurySnapshot:
        return StoredTreasurySnapshot(
            sequence=row["sequence"],
            snapshot=TreasurySnapshot(
                organization_id=row["organization_id"],
                available_usdc=Decimal(row["available_usdc"]),
                spent_today_usdc=Decimal(row["spent_today_usdc"]),
            ),
            source_reference=row["source_reference"],
            recorded_by_user_id=row["recorded_by_user_id"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )

    @staticmethod
    def _vendor(row: sqlite3.Row) -> Vendor:
        return Vendor(
            id=row["id"],
            organization_id=row["organization_id"],
            legal_name=row["legal_name"],
            approved_wallet_address=row["approved_wallet_address"],
            autopay_limit=Decimal(row["autopay_limit"]),
            risk_tier=row["risk_tier"],
            active=bool(row["active"]),
        )

    @staticmethod
    def _wallet_event(row: sqlite3.Row) -> VendorWalletEvent:
        return VendorWalletEvent(
            organization_id=row["organization_id"],
            vendor_id=row["vendor_id"],
            event_type=WalletEventType(row["event_type"]),
            wallet_address=row["wallet_address"],
            previous_wallet_address=row["previous_wallet_address"],
            verification_method=WalletVerificationMethod(row["verification_method"]),
            verification_reference=row["verification_reference"],
            verified_by_user_id=row["verified_by_user_id"],
            verified_at=datetime.fromisoformat(row["verified_at"]),
        )

    @staticmethod
    def _session(row: sqlite3.Row) -> Session:
        return Session(
            token_hash=row["token_hash"],
            principal=Principal(
                user_id=row["user_id"],
                organization_id=row["organization_id"],
                roles=tuple(Role(value) for value in json.loads(row["roles_json"])),
                active=bool(row["principal_active"]),
            ),
            issued_at=datetime.fromisoformat(row["issued_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            revoked_at=(
                datetime.fromisoformat(row["revoked_at"])
                if row["revoked_at"] is not None
                else None
            ),
        )

    @staticmethod
    def _decision_record(row: sqlite3.Row) -> DecisionRecord:
        policy_data = json.loads(row["policy_decision_json"])
        agent_data = (
            json.loads(row["agent_recommendation_json"])
            if row["agent_recommendation_json"] is not None
            else None
        )
        decision = Decision(
            action=DecisionAction(policy_data["action"]),
            policy_version=policy_data["policy_version"],
            invoice_fingerprint=policy_data["invoice_fingerprint"],
            rule_results=tuple(
                RuleResult(
                    code=item["code"],
                    disposition=RuleDisposition(item["disposition"]),
                    message=item["message"],
                    remediation=item.get("remediation"),
                )
                for item in policy_data["rule_results"]
            ),
            approval_reference=policy_data.get("approval_reference"),
        )
        recommendation = (
            AgentRecommendation(
                action=DecisionAction(agent_data["action"]),
                summary=agent_data["summary"],
                reason_codes=tuple(agent_data["reason_codes"]),
                confidence=Decimal(agent_data["confidence"]),
                evidence_refs=tuple(agent_data.get("evidence_refs", ())),
            )
            if agent_data is not None
            else None
        )
        replay_inputs = (
            DecisionReplayInputs.from_payload(policy_data["replay_inputs"])
            if policy_data.get("replay_inputs") is not None
            else None
        )
        return DecisionRecord(
            id=row["id"],
            organization_id=row["organization_id"],
            invoice_id=row["invoice_id"],
            evidence_manifest_hash=row["evidence_manifest_hash"],
            policy_version=row["policy_version"],
            policy_content_hash=row["policy_content_hash"],
            agent_recommendation=recommendation,
            policy_decision=decision,
            final_action=DecisionAction(row["final_action"]),
            replay_inputs=replay_inputs,
            replay_input_hash=policy_data.get("replay_input_hash"),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _approval(row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest(
            id=row["id"],
            organization_id=row["organization_id"],
            invoice_id=row["invoice_id"],
            decision_id=row["decision_id"],
            requested_by_user_id=row["requested_by_user_id"],
            requested_at=datetime.fromisoformat(row["requested_at"]),
            status=ApprovalStatus(row["status"]),
            version=row["version"],
            resolved_by_user_id=row["resolved_by_user_id"],
            resolved_at=(
                datetime.fromisoformat(row["resolved_at"])
                if row["resolved_at"] is not None
                else None
            ),
            resolution_note=row["resolution_note"],
        )

    @staticmethod
    def _audit_event(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            sequence=row["sequence"],
            aggregate_type=row["aggregate_type"],
            aggregate_id=row["aggregate_id"],
            event_type=row["event_type"],
            payload=json.loads(row["payload_json"]),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _payment_intent(row: sqlite3.Row) -> PaymentIntent:
        return PaymentIntent(
            id=row["id"],
            organization_id=row["organization_id"],
            invoice_id=row["invoice_id"],
            decision_id=row["decision_id"],
            recipient=row["recipient"],
            amount_usdc=Decimal(row["amount_usdc"]),
            network=ArcNetwork(row["network"]),
            idempotency_key=row["idempotency_key"],
            approval_reference=row["approval_reference"],
        )

    @staticmethod
    def _settlement_receipt(row: sqlite3.Row) -> SettlementReceipt:
        return SettlementReceipt(
            payment_intent_id=row["payment_intent_id"],
            organization_id=row["organization_id"],
            provider=row["provider"],
            provider_reference=row["provider_reference"],
            transaction_hash=row["transaction_hash"],
            block_number=row["block_number"],
            confirmed_recipient=row["confirmed_recipient"],
            confirmed_amount_usdc=Decimal(row["confirmed_amount_usdc"]),
            network=ArcNetwork(row["network"]),
            status=SettlementStatus(row["status"]),
            confirmed_at=datetime.fromisoformat(row["confirmed_at"]),
        )

    @staticmethod
    def _settlement_attempt(row: sqlite3.Row) -> SettlementAttempt:
        return SettlementAttempt(
            sequence=int(row["sequence"]),
            organization_id=row["organization_id"],
            payment_intent_id=row["payment_intent_id"],
            invoice_id=row["invoice_id"],
            provider=row["provider"],
            outcome=SettlementAttemptOutcome(row["outcome"]),
            retryable=bool(row["retryable"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            correlation_id=row["correlation_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _agent_run(row: sqlite3.Row) -> AgentRun:
        plan_data = json.loads(row["plan_json"])
        results_data = json.loads(row["results_json"])
        return AgentRun(
            id=row["id"],
            organization_id=row["organization_id"],
            status=AgentRunStatus(row["status"]),
            as_of=date.fromisoformat(row["as_of"]),
            state_hash=row["state_hash"],
            plan_hash=row["plan_hash"],
            items=tuple(AgentPlanItem.from_payload(item) for item in plan_data),
            created_by_user_id=row["created_by_user_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            executed_by_user_id=row["executed_by_user_id"],
            executed_at=(
                datetime.fromisoformat(row["executed_at"])
                if row["executed_at"] is not None
                else None
            ),
            results=tuple(results_data),
        )

    @staticmethod
    def _ensure_same_payment(existing: PaymentIntent, proposed: PaymentIntent) -> None:
        same = (
            existing.organization_id == proposed.organization_id
            and existing.invoice_id == proposed.invoice_id
            and existing.decision_id == proposed.decision_id
            and existing.recipient == proposed.recipient
            and existing.amount_usdc == proposed.amount_usdc
            and existing.network == proposed.network
            and existing.approval_reference == proposed.approval_reference
        )
        if not same:
            raise PersistenceError("Decision is already bound to a different payment intent.")

    @staticmethod
    def _same_decision_content(existing: DecisionRecord, proposed: DecisionRecord) -> bool:
        return (
            existing.id == proposed.id
            and existing.organization_id == proposed.organization_id
            and existing.invoice_id == proposed.invoice_id
            and existing.evidence_manifest_hash == proposed.evidence_manifest_hash
            and existing.policy_version == proposed.policy_version
            and existing.policy_content_hash == proposed.policy_content_hash
            and existing.agent_recommendation == proposed.agent_recommendation
            and existing.policy_decision == proposed.policy_decision
            and existing.final_action == proposed.final_action
            and existing.replay_inputs == proposed.replay_inputs
            and existing.replay_input_hash == proposed.replay_input_hash
        )

    @staticmethod
    def _encode_cursor(updated_at: str, invoice_id: str) -> str:
        raw = canonical_json({"updated_at": updated_at, "id": invoice_id}).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[str, str]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            return str(payload["updated_at"]), str(payload["id"])
        except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PersistenceError("Invoice cursor is invalid.") from exc

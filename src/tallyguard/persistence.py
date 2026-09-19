"""Durable tenant-scoped SQLite persistence for evidence and invoice workflow."""

from __future__ import annotations

from dataclasses import dataclass
import base64
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from threading import RLock

from .audit import canonical_json
from .evidence import (
    EvidenceDocument,
    EvidenceRecord,
    EvidenceType,
    ExtractedField,
    ExtractionMethod,
    SourceLocation,
)
from .models import Invoice
from .network import ArcNetwork
from .settlement import PaymentIntent, SettlementReceipt, SettlementStatus
from .workflow import InvoiceStatus, WorkflowError, require_transition


class PersistenceError(RuntimeError):
    """Raised when a durable record is missing, duplicated, or stale."""


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

    def save_evidence(self, record: EvidenceRecord) -> EvidenceRecord:
        document = record.document
        fields_json = canonical_json(record.fields)
        with self._guard:
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
            except sqlite3.IntegrityError as exc:
                raise PersistenceError("Evidence ID or content already exists in this organization.") from exc
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

    def create_or_get_payment_intent(
        self,
        intent: PaymentIntent,
        *,
        created_at: datetime | None = None,
    ) -> tuple[PaymentIntent, bool]:
        """Persist one immutable intent per tenant/decision, safe under races."""

        timestamp = created_at or datetime.now(timezone.utc)
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

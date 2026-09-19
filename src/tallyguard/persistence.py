"""Durable tenant-scoped SQLite persistence for evidence and invoice workflow."""

from __future__ import annotations

from dataclasses import dataclass
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

"""Immutable evidence intake, provenance, and duplicate detection.

This module belongs to the interpretation/control boundary. Extractors may be
probabilistic, but the bytes, normalized observations, and their provenance are
captured immutably before deterministic policy evaluation begins.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from enum import StrEnum
import hashlib
import json
import re
from threading import Lock
from typing import Iterable

from .audit import canonical_json


SHA256 = re.compile(r"^[a-f0-9]{64}$")
SUPPORTED_MIME_TYPES = {
    "application/json",
    "application/pdf",
    "image/jpeg",
    "image/png",
}


class EvidenceError(ValueError):
    """Raised when evidence cannot be admitted to the immutable store."""


class EvidenceType(StrEnum):
    INVOICE = "INVOICE"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    DELIVERY = "DELIVERY"


class ExtractionMethod(StrEnum):
    JSON = "JSON"
    PDF_TEXT = "PDF_TEXT"
    OCR = "OCR"
    MANUAL = "MANUAL"
    SEEDED = "SEEDED"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Location of one observation in an immutable source document."""

    document_id: str
    page_number: int | None = None
    bounding_box: tuple[float, float, float, float] | None = None
    json_pointer: str | None = None

    def __post_init__(self) -> None:
        if not self.document_id.strip():
            raise EvidenceError("Source document ID is required.")
        if self.page_number is not None and self.page_number < 1:
            raise EvidenceError("Page numbers are one-based.")
        if self.bounding_box is not None:
            if len(self.bounding_box) != 4 or any(value < 0 or value > 1 for value in self.bounding_box):
                raise EvidenceError("Bounding boxes must contain four normalized coordinates.")
            left, top, right, bottom = self.bounding_box
            if left >= right or top >= bottom:
                raise EvidenceError("Bounding box must have positive area.")
        if self.json_pointer is not None and not self.json_pointer.startswith("/"):
            raise EvidenceError("JSON pointers must start with '/'.")


@dataclass(frozen=True, slots=True)
class ExtractedField:
    """One normalized field plus enough information to reproduce its source."""

    name: str
    raw_value: str
    normalized_value: str
    confidence: Decimal
    method: ExtractionMethod
    source: SourceLocation

    def __post_init__(self) -> None:
        confidence = self.confidence if isinstance(self.confidence, Decimal) else Decimal(str(self.confidence))
        if not self.name.strip():
            raise EvidenceError("Extracted field name is required.")
        if confidence < 0 or confidence > 1:
            raise EvidenceError("Extraction confidence must be between 0 and 1.")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "confidence", confidence)


@dataclass(frozen=True, slots=True)
class EvidenceDocument:
    id: str
    organization_id: str
    evidence_type: EvidenceType
    filename: str
    mime_type: str
    content_sha256: str
    byte_size: int
    ingested_at: datetime

    def __post_init__(self) -> None:
        digest = self.content_sha256.strip().lower()
        mime_type = self.mime_type.strip().lower()
        if not self.id.strip() or not self.organization_id.strip():
            raise EvidenceError("Evidence ID and organization ID are required.")
        if not self.filename.strip():
            raise EvidenceError("Evidence filename is required.")
        if mime_type not in SUPPORTED_MIME_TYPES:
            raise EvidenceError(f"Unsupported evidence MIME type: {mime_type}")
        if not SHA256.fullmatch(digest):
            raise EvidenceError("Evidence content hash must be a SHA-256 digest.")
        if self.byte_size <= 0:
            raise EvidenceError("Evidence documents must not be empty.")
        if self.ingested_at.tzinfo is None:
            raise EvidenceError("Evidence timestamps must be timezone-aware.")
        object.__setattr__(self, "mime_type", mime_type)
        object.__setattr__(self, "content_sha256", digest)


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    document: EvidenceDocument
    fields: tuple[ExtractedField, ...]

    def __post_init__(self) -> None:
        if not self.fields:
            raise EvidenceError("At least one extracted field is required.")
        if any(field.source.document_id != self.document.id for field in self.fields):
            raise EvidenceError("Every extracted field must point to its source document.")
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise EvidenceError("Extracted field names must be unique within a document.")

    def field(self, name: str) -> ExtractedField:
        try:
            return next(field for field in self.fields if field.name == name)
        except StopIteration as exc:
            raise EvidenceError(f"Required field is missing: {name}") from exc


@dataclass(frozen=True, slots=True)
class EvidencePackage:
    """A tenant-safe, content-addressed three-way-match evidence package."""

    id: str
    organization_id: str
    records: tuple[EvidenceRecord, ...]

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.organization_id.strip():
            raise EvidenceError("Evidence package ID and organization ID are required.")
        if not self.records:
            raise EvidenceError("Evidence package must contain at least one document.")
        document_ids = [record.document.id for record in self.records]
        if len(document_ids) != len(set(document_ids)):
            raise EvidenceError("Evidence package contains duplicate document IDs.")
        if any(record.document.organization_id != self.organization_id for record in self.records):
            raise EvidenceError("Evidence package crosses an organization boundary.")

    @property
    def manifest_hash(self) -> str:
        manifest = [
            {
                "document": record.document,
                "fields": tuple(sorted(record.fields, key=lambda field: field.name)),
            }
            for record in sorted(self.records, key=lambda item: item.document.id)
        ]
        return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()

    def records_of_type(self, evidence_type: EvidenceType) -> tuple[EvidenceRecord, ...]:
        return tuple(record for record in self.records if record.document.evidence_type == evidence_type)


def _validate_content(mime_type: str, content: bytes) -> None:
    if not content:
        raise EvidenceError("Evidence documents must not be empty.")
    if mime_type == "application/pdf" and not content.startswith(b"%PDF-"):
        raise EvidenceError("PDF evidence does not have a valid PDF signature.")
    if mime_type == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise EvidenceError("PNG evidence does not have a valid PNG signature.")
    if mime_type == "image/jpeg" and not content.startswith(b"\xff\xd8\xff"):
        raise EvidenceError("JPEG evidence does not have a valid JPEG signature.")
    if mime_type == "application/json":
        try:
            json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceError("JSON evidence must contain valid UTF-8 JSON.") from exc


class EvidenceStore:
    """Thread-safe process-local immutable evidence repository.

    Persistence is deliberately abstracted behind this small surface so the API
    milestone can replace it with a database without changing evidence rules.
    """

    def __init__(self) -> None:
        self._guard = Lock()
        self._records: dict[tuple[str, str], EvidenceRecord] = {}

    def ingest(
        self,
        *,
        document_id: str,
        organization_id: str,
        evidence_type: EvidenceType,
        filename: str,
        mime_type: str,
        content: bytes,
        fields: Iterable[ExtractedField],
        ingested_at: datetime | None = None,
    ) -> EvidenceRecord:
        normalized_mime = mime_type.strip().lower()
        if normalized_mime not in SUPPORTED_MIME_TYPES:
            raise EvidenceError(f"Unsupported evidence MIME type: {normalized_mime}")
        _validate_content(normalized_mime, content)
        document = EvidenceDocument(
            id=document_id,
            organization_id=organization_id,
            evidence_type=evidence_type,
            filename=filename,
            mime_type=normalized_mime,
            content_sha256=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
            ingested_at=ingested_at or datetime.now(timezone.utc),
        )
        record = EvidenceRecord(document=document, fields=tuple(fields))
        scope = (organization_id, document_id)
        with self._guard:
            existing = self._records.get(scope)
            if existing is not None:
                if existing != record:
                    raise EvidenceError("Evidence document ID is immutable and already in use.")
                return existing
            self._records[scope] = record
        return record

    def get(self, *, organization_id: str, document_id: str) -> EvidenceRecord:
        with self._guard:
            try:
                return self._records[(organization_id, document_id)]
            except KeyError as exc:
                raise EvidenceError("Evidence document was not found in this organization.") from exc


@dataclass(frozen=True, slots=True)
class InvoiceIdentity:
    id: str
    organization_id: str
    vendor_id: str
    invoice_number: str
    amount: Decimal
    currency: str
    source_document_hash: str
    normalized_text: str

    def __post_init__(self) -> None:
        amount = self.amount if isinstance(self.amount, Decimal) else Decimal(str(self.amount))
        digest = self.source_document_hash.strip().lower()
        if amount <= 0:
            raise EvidenceError("Invoice identity amount must be positive.")
        if not SHA256.fullmatch(digest):
            raise EvidenceError("Invoice identity requires a valid source-document hash.")
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "currency", self.currency.strip().upper())
        object.__setattr__(self, "invoice_number", self.invoice_number.strip().casefold())
        object.__setattr__(self, "source_document_hash", digest)
        object.__setattr__(self, "normalized_text", normalize_document_text(self.normalized_text))


class DuplicateSignal(StrEnum):
    CONTENT_HASH = "CONTENT_HASH"
    VENDOR_INVOICE_NUMBER = "VENDOR_INVOICE_NUMBER"
    BUSINESS_KEY = "BUSINESS_KEY"
    NEAR_DUPLICATE_TEXT = "NEAR_DUPLICATE_TEXT"


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    existing_invoice_id: str
    signals: tuple[DuplicateSignal, ...]
    text_similarity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class DuplicateCheck:
    invoice_id: str
    matches: tuple[DuplicateMatch, ...]

    @property
    def is_duplicate(self) -> bool:
        return bool(self.matches)


def normalize_document_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


class DuplicateRegistry:
    """Tenant-scoped, atomic duplicate admission registry."""

    def __init__(self, *, near_duplicate_threshold: Decimal = Decimal("0.94")) -> None:
        threshold = Decimal(str(near_duplicate_threshold))
        if threshold <= 0 or threshold > 1:
            raise ValueError("Near-duplicate threshold must be in (0, 1].")
        self.threshold = threshold
        self._guard = Lock()
        self._invoices: dict[str, list[InvoiceIdentity]] = {}

    def check_and_record(self, candidate: InvoiceIdentity) -> DuplicateCheck:
        with self._guard:
            tenant_invoices = self._invoices.setdefault(candidate.organization_id, [])
            if any(existing.id == candidate.id for existing in tenant_invoices):
                raise EvidenceError("Invoice ID is immutable and already in use.")
            matches = tuple(
                match
                for existing in tenant_invoices
                if (match := self._match(candidate, existing)) is not None
            )
            if not matches:
                tenant_invoices.append(candidate)
            return DuplicateCheck(invoice_id=candidate.id, matches=matches)

    def _match(self, candidate: InvoiceIdentity, existing: InvoiceIdentity) -> DuplicateMatch | None:
        signals: list[DuplicateSignal] = []
        strong_match = False
        if candidate.source_document_hash == existing.source_document_hash:
            signals.append(DuplicateSignal.CONTENT_HASH)
            strong_match = True
        if candidate.vendor_id == existing.vendor_id and candidate.invoice_number == existing.invoice_number:
            signals.append(DuplicateSignal.VENDOR_INVOICE_NUMBER)
            strong_match = True
        if (
            candidate.vendor_id == existing.vendor_id
            and candidate.amount == existing.amount
            and candidate.currency == existing.currency
        ):
            signals.append(DuplicateSignal.BUSINESS_KEY)

        similarity: Decimal | None = None
        if candidate.normalized_text and existing.normalized_text:
            similarity = Decimal(
                str(SequenceMatcher(None, candidate.normalized_text, existing.normalized_text).ratio())
            ).quantize(Decimal("0.0001"))
            if similarity >= self.threshold:
                signals.append(DuplicateSignal.NEAR_DUPLICATE_TEXT)
                strong_match = True

        # Equal recurring charges are common. The business key is useful
        # corroboration, but it is never sufficient to reject an invoice.
        if not strong_match:
            return None
        return DuplicateMatch(
            existing_invoice_id=existing.id,
            signals=tuple(signals),
            text_similarity=similarity,
        )

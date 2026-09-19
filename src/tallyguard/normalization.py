"""Normalize provenance-bound evidence into deterministic domain records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from .evidence import EvidenceError, EvidencePackage, EvidenceRecord, EvidenceType
from .models import DeliveryEvidence, Invoice, PurchaseOrder


@dataclass(frozen=True, slots=True)
class NormalizedEvidence:
    package_id: str
    manifest_hash: str
    invoice: Invoice
    purchase_order: PurchaseOrder | None
    delivery: DeliveryEvidence | None


class EvidenceNormalizer:
    """Converts normalized fields only after confidence and cardinality checks."""

    def __init__(self, *, minimum_confidence: Decimal = Decimal("0.80")) -> None:
        confidence = Decimal(str(minimum_confidence))
        if confidence < 0 or confidence > 1:
            raise ValueError("Minimum confidence must be between 0 and 1.")
        self.minimum_confidence = confidence

    def normalize(self, package: EvidencePackage) -> NormalizedEvidence:
        invoice_record = self._exactly_one(package, EvidenceType.INVOICE, required=True)
        po_record = self._exactly_one(package, EvidenceType.PURCHASE_ORDER, required=False)
        delivery_record = self._exactly_one(package, EvidenceType.DELIVERY, required=False)
        assert invoice_record is not None

        invoice = Invoice(
            id=self._value(invoice_record, "invoice_id"),
            organization_id=package.organization_id,
            vendor_id=self._value(invoice_record, "vendor_id"),
            invoice_number=self._value(invoice_record, "invoice_number"),
            currency=self._value(invoice_record, "currency"),
            amount=self._decimal(invoice_record, "amount"),
            due_date=self._date(invoice_record, "due_date"),
            payment_wallet_address=self._value(invoice_record, "payment_wallet_address"),
            source_document_hash=invoice_record.document.content_sha256,
        )
        purchase_order = None
        if po_record is not None:
            purchase_order = PurchaseOrder(
                id=self._value(po_record, "purchase_order_id"),
                organization_id=package.organization_id,
                vendor_id=self._value(po_record, "vendor_id"),
                po_number=self._value(po_record, "po_number"),
                currency=self._value(po_record, "currency"),
                authorized_amount=self._decimal(po_record, "authorized_amount"),
            )
        delivery = None
        if delivery_record is not None:
            delivery = DeliveryEvidence(
                id=self._value(delivery_record, "delivery_id"),
                organization_id=package.organization_id,
                purchase_order_id=self._value(delivery_record, "purchase_order_id"),
                delivered_value=self._decimal(delivery_record, "delivered_value"),
                source_document_hash=delivery_record.document.content_sha256,
            )
        return NormalizedEvidence(
            package_id=package.id,
            manifest_hash=package.manifest_hash,
            invoice=invoice,
            purchase_order=purchase_order,
            delivery=delivery,
        )

    @staticmethod
    def _exactly_one(
        package: EvidencePackage,
        evidence_type: EvidenceType,
        *,
        required: bool,
    ) -> EvidenceRecord | None:
        records = package.records_of_type(evidence_type)
        if len(records) > 1:
            raise EvidenceError(f"Evidence package has multiple {evidence_type.value} documents.")
        if required and not records:
            raise EvidenceError(f"Evidence package requires one {evidence_type.value} document.")
        return records[0] if records else None

    def _value(self, record: EvidenceRecord, name: str) -> str:
        field = record.field(name)
        if field.confidence < self.minimum_confidence:
            raise EvidenceError(
                f"Field {name} confidence {field.confidence} is below {self.minimum_confidence}."
            )
        value = field.normalized_value.strip()
        if not value:
            raise EvidenceError(f"Required field is empty: {name}")
        return value

    def _decimal(self, record: EvidenceRecord, name: str) -> Decimal:
        try:
            return Decimal(self._value(record, name))
        except InvalidOperation as exc:
            raise EvidenceError(f"Field {name} is not a valid decimal.") from exc

    def _date(self, record: EvidenceRecord, name: str) -> date:
        try:
            return date.fromisoformat(self._value(record, name))
        except ValueError as exc:
            raise EvidenceError(f"Field {name} is not an ISO date.") from exc

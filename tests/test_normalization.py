from decimal import Decimal

import pytest

from tallyguard.evidence import (
    EvidenceError,
    EvidencePackage,
    EvidenceStore,
    EvidenceType,
    ExtractedField,
    ExtractionMethod,
    SourceLocation,
)
from tallyguard.normalization import EvidenceNormalizer


def record(store, document_id, evidence_type, values, *, confidence="0.99"):
    content = ("{" + ",".join(f'\"{key}\":\"{value}\"' for key, value in values.items()) + "}").encode()
    fields = tuple(
        ExtractedField(
            name=key,
            raw_value=value,
            normalized_value=value,
            confidence=Decimal(confidence),
            method=ExtractionMethod.SEEDED,
            source=SourceLocation(document_id=document_id, json_pointer=f"/{key}"),
        )
        for key, value in values.items()
    )
    return store.ingest(
        document_id=document_id,
        organization_id="org-1",
        evidence_type=evidence_type,
        filename=f"{document_id}.json",
        mime_type="application/json",
        content=content,
        fields=fields,
    )


def test_three_way_evidence_normalizes_to_domain_records():
    store = EvidenceStore()
    invoice = record(
        store,
        "invoice-doc",
        EvidenceType.INVOICE,
        {
            "invoice_id": "invoice-1",
            "vendor_id": "vendor-1",
            "invoice_number": "INV-1007",
            "currency": "USDC",
            "amount": "1200.00",
            "due_date": "2026-10-08",
            "payment_wallet_address": "0x1111111111111111111111111111111111111111",
        },
    )
    po = record(
        store,
        "po-doc",
        EvidenceType.PURCHASE_ORDER,
        {
            "purchase_order_id": "po-1",
            "vendor_id": "vendor-1",
            "po_number": "PO-204",
            "currency": "USDC",
            "authorized_amount": "1200.00",
        },
    )
    delivery = record(
        store,
        "delivery-doc",
        EvidenceType.DELIVERY,
        {
            "delivery_id": "delivery-1",
            "purchase_order_id": "po-1",
            "delivered_value": "1200.00",
        },
    )
    package = EvidencePackage(
        id="package-1",
        organization_id="org-1",
        records=(invoice, po, delivery),
    )

    normalized = EvidenceNormalizer().normalize(package)

    assert normalized.invoice.amount == Decimal("1200.00")
    assert normalized.invoice.source_document_hash == invoice.document.content_sha256
    assert normalized.purchase_order is not None
    assert normalized.purchase_order.po_number == "PO-204"
    assert normalized.delivery is not None
    assert normalized.delivery.purchase_order_id == normalized.purchase_order.id
    assert normalized.manifest_hash == package.manifest_hash


def test_missing_po_and_delivery_normalize_as_explicit_absence():
    store = EvidenceStore()
    invoice = record(
        store,
        "invoice-doc",
        EvidenceType.INVOICE,
        {
            "invoice_id": "invoice-1",
            "vendor_id": "vendor-1",
            "invoice_number": "INV-1007",
            "currency": "USDC",
            "amount": "1200",
            "due_date": "2026-10-08",
            "payment_wallet_address": "0x1111111111111111111111111111111111111111",
        },
    )
    package = EvidencePackage(id="package-1", organization_id="org-1", records=(invoice,))
    normalized = EvidenceNormalizer().normalize(package)
    assert normalized.purchase_order is None
    assert normalized.delivery is None


def test_low_confidence_financial_field_is_not_promoted_to_domain_data():
    store = EvidenceStore()
    invoice = record(
        store,
        "invoice-doc",
        EvidenceType.INVOICE,
        {
            "invoice_id": "invoice-1",
            "vendor_id": "vendor-1",
            "invoice_number": "INV-1007",
            "currency": "USDC",
            "amount": "1200",
            "due_date": "2026-10-08",
            "payment_wallet_address": "0x1111111111111111111111111111111111111111",
        },
        confidence="0.40",
    )
    package = EvidencePackage(id="package-1", organization_id="org-1", records=(invoice,))
    with pytest.raises(EvidenceError, match="below"):
        EvidenceNormalizer().normalize(package)

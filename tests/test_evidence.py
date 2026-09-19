from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tallyguard.evidence import (
    DuplicateRegistry,
    DuplicateSignal,
    EvidenceError,
    EvidencePackage,
    EvidenceStore,
    EvidenceType,
    ExtractedField,
    ExtractionMethod,
    InvoiceIdentity,
    SourceLocation,
)


def field(document_id: str, name: str, value: str, pointer: str) -> ExtractedField:
    return ExtractedField(
        name=name,
        raw_value=value,
        normalized_value=value,
        confidence=Decimal("0.99"),
        method=ExtractionMethod.JSON,
        source=SourceLocation(document_id=document_id, json_pointer=pointer),
    )


def identity(
    invoice_id: str,
    *,
    organization_id: str = "org-1",
    invoice_number: str = "INV-1007",
    digest: str = "a" * 64,
    text: str = "Invoice INV-1007 from Acme Data for 1200 USDC",
) -> InvoiceIdentity:
    return InvoiceIdentity(
        id=invoice_id,
        organization_id=organization_id,
        vendor_id="vendor-1",
        invoice_number=invoice_number,
        amount=Decimal("1200"),
        currency="usdc",
        source_document_hash=digest,
        normalized_text=text,
    )


def test_json_evidence_has_content_hash_and_field_provenance():
    content = b'{"invoice_number":"INV-1007","amount":"1200"}'
    store = EvidenceStore()
    record = store.ingest(
        document_id="doc-invoice",
        organization_id="org-1",
        evidence_type=EvidenceType.INVOICE,
        filename="invoice.json",
        mime_type="application/json",
        content=content,
        fields=(
            field("doc-invoice", "invoice_number", "INV-1007", "/invoice_number"),
            field("doc-invoice", "amount", "1200", "/amount"),
        ),
        ingested_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )

    assert record.document.content_sha256 == "289a653478e1d584f7966c4e8c14ea37d0178e3a1bd08a5f1fa7fcfd20024439"
    assert record.field("amount").source.json_pointer == "/amount"
    assert store.get(organization_id="org-1", document_id="doc-invoice") == record


def test_invalid_file_signature_is_rejected():
    store = EvidenceStore()
    with pytest.raises(EvidenceError, match="PDF signature"):
        store.ingest(
            document_id="doc-1",
            organization_id="org-1",
            evidence_type=EvidenceType.INVOICE,
            filename="not-a-pdf.pdf",
            mime_type="application/pdf",
            content=b"not a pdf",
            fields=(field("doc-1", "invoice_number", "1", "/invoice_number"),),
        )


def test_evidence_document_id_is_immutable():
    store = EvidenceStore()
    values = dict(
        document_id="doc-1",
        organization_id="org-1",
        evidence_type=EvidenceType.INVOICE,
        filename="invoice.json",
        mime_type="application/json",
        fields=(field("doc-1", "invoice_number", "1", "/invoice_number"),),
        ingested_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )
    store.ingest(content=b'{"invoice_number":"1"}', **values)
    with pytest.raises(EvidenceError, match="immutable"):
        store.ingest(content=b'{"invoice_number":"2"}', **values)


def test_package_rejects_cross_tenant_evidence():
    store = EvidenceStore()
    first = store.ingest(
        document_id="doc-1",
        organization_id="org-1",
        evidence_type=EvidenceType.INVOICE,
        filename="invoice.json",
        mime_type="application/json",
        content=b'{"invoice_number":"1"}',
        fields=(field("doc-1", "invoice_number", "1", "/invoice_number"),),
    )
    second = store.ingest(
        document_id="doc-2",
        organization_id="org-2",
        evidence_type=EvidenceType.PURCHASE_ORDER,
        filename="po.json",
        mime_type="application/json",
        content=b'{"po_number":"2"}',
        fields=(field("doc-2", "po_number", "2", "/po_number"),),
    )
    with pytest.raises(EvidenceError, match="organization boundary"):
        EvidencePackage(id="package-1", organization_id="org-1", records=(first, second))


def test_package_manifest_is_order_independent():
    store = EvidenceStore()
    records = []
    for number, evidence_type in (("1", EvidenceType.INVOICE), ("2", EvidenceType.PURCHASE_ORDER)):
        document_id = f"doc-{number}"
        records.append(
            store.ingest(
                document_id=document_id,
                organization_id="org-1",
                evidence_type=evidence_type,
                filename=f"{number}.json",
                mime_type="application/json",
                content=f'{{"number":"{number}"}}'.encode(),
                fields=(field(document_id, "number", number, "/number"),),
            )
        )
    forward = EvidencePackage(id="package-1", organization_id="org-1", records=tuple(records))
    reverse = EvidencePackage(id="package-1", organization_id="org-1", records=tuple(reversed(records)))
    assert forward.manifest_hash == reverse.manifest_hash


def test_duplicate_detector_uses_multiple_signals():
    registry = DuplicateRegistry()
    assert not registry.check_and_record(identity("invoice-1")).is_duplicate
    result = registry.check_and_record(
        identity(
            "invoice-2",
            digest="b" * 64,
            text="Invoice INV 1007 from Acme Data, total: 1200 USDC",
        )
    )
    assert result.is_duplicate
    signals = set(result.matches[0].signals)
    assert DuplicateSignal.VENDOR_INVOICE_NUMBER in signals
    assert DuplicateSignal.BUSINESS_KEY in signals


def test_duplicate_detection_is_tenant_scoped():
    registry = DuplicateRegistry()
    assert not registry.check_and_record(identity("invoice-1", organization_id="org-1")).is_duplicate
    assert not registry.check_and_record(identity("invoice-1", organization_id="org-2")).is_duplicate


def test_equal_recurring_amount_alone_is_not_a_duplicate():
    registry = DuplicateRegistry()
    assert not registry.check_and_record(identity("invoice-1")).is_duplicate
    result = registry.check_and_record(
        identity(
            "invoice-2",
            invoice_number="INV-1008",
            digest="b" * 64,
            text="Monthly cloud hosting for September service period",
        )
    )
    assert not result.is_duplicate


def test_concurrent_duplicate_admission_accepts_exactly_one():
    registry = DuplicateRegistry()

    def submit(index: int) -> bool:
        return registry.check_and_record(identity(f"invoice-{index}")).is_duplicate

    with ThreadPoolExecutor(max_workers=20) as pool:
        duplicate_results = list(pool.map(submit, range(100)))

    assert duplicate_results.count(False) == 1
    assert duplicate_results.count(True) == 99

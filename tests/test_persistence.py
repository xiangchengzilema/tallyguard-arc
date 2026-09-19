from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from tallyguard.evidence import (
    EvidenceRecord,
    EvidenceStore,
    EvidenceType,
    ExtractedField,
    ExtractionMethod,
    SourceLocation,
)
from tallyguard.models import Invoice
from tallyguard.persistence import PersistenceError, SqliteRepository
from tallyguard.workflow import InvoiceStatus, WorkflowError


WALLET = "0x1111111111111111111111111111111111111111"


def invoice(organization_id="org-1") -> Invoice:
    return Invoice(
        id="invoice-1",
        organization_id=organization_id,
        vendor_id="vendor-1",
        invoice_number="INV-1007",
        currency="USDC",
        amount=Decimal("1200"),
        due_date=date(2026, 10, 8),
        payment_wallet_address=WALLET,
        source_document_hash="a" * 64,
    )


def repository(tmp_path):
    repo = SqliteRepository(tmp_path / "tallyguard.sqlite3")
    repo.create_organization(organization_id="org-1", name="Northwind AI")
    repo.create_organization(organization_id="org-2", name="Contoso Agents")
    return repo


def evidence_record() -> EvidenceRecord:
    store = EvidenceStore()
    return store.ingest(
        document_id="doc-1",
        organization_id="org-1",
        evidence_type=EvidenceType.INVOICE,
        filename="invoice.json",
        mime_type="application/json",
        content=b'{"invoice_number":"INV-1007"}',
        fields=(
            ExtractedField(
                name="invoice_number",
                raw_value="INV-1007",
                normalized_value="INV-1007",
                confidence=Decimal("0.99"),
                method=ExtractionMethod.JSON,
                source=SourceLocation(document_id="doc-1", json_pointer="/invoice_number"),
            ),
        ),
        ingested_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )


def test_evidence_round_trips_with_provenance(tmp_path):
    repo = repository(tmp_path)
    record = evidence_record()
    repo.save_evidence(record)
    restored = repo.get_evidence(organization_id="org-1", document_id="doc-1")
    assert restored == record
    assert restored.fields[0].source.json_pointer == "/invoice_number"


def test_evidence_content_is_unique_within_tenant(tmp_path):
    repo = repository(tmp_path)
    record = evidence_record()
    repo.save_evidence(record)
    duplicate_id = EvidenceRecord(
        document=type(record.document)(
            id="doc-2",
            organization_id=record.document.organization_id,
            evidence_type=record.document.evidence_type,
            filename=record.document.filename,
            mime_type=record.document.mime_type,
            content_sha256=record.document.content_sha256,
            byte_size=record.document.byte_size,
            ingested_at=record.document.ingested_at,
        ),
        fields=(
            ExtractedField(
                name="invoice_number",
                raw_value="INV-1007",
                normalized_value="INV-1007",
                confidence=Decimal("0.99"),
                method=ExtractionMethod.JSON,
                source=SourceLocation(document_id="doc-2", json_pointer="/invoice_number"),
            ),
        ),
    )
    with pytest.raises(PersistenceError, match="content already exists"):
        repo.save_evidence(duplicate_id)


def test_invoice_workflow_is_durable_and_audited(tmp_path):
    repo = repository(tmp_path)
    created = repo.create_invoice(invoice())
    assert created.status == InvoiceStatus.DRAFT
    evaluating = repo.transition_invoice(
        organization_id="org-1",
        invoice_id="invoice-1",
        target_status=InvoiceStatus.EVALUATING,
        expected_version=1,
        actor_user_id="operator-1",
        correlation_id="request-001",
    )
    ready = repo.transition_invoice(
        organization_id="org-1",
        invoice_id="invoice-1",
        target_status=InvoiceStatus.READY,
        expected_version=evaluating.version,
        actor_user_id="worker-policy",
        correlation_id="request-001",
    )
    assert ready.status == InvoiceStatus.READY
    assert ready.version == 3
    transitions = repo.transitions(organization_id="org-1", invoice_id="invoice-1")
    assert [item.to_status for item in transitions] == [InvoiceStatus.EVALUATING, InvoiceStatus.READY]
    assert {item.correlation_id for item in transitions} == {"request-001"}


def test_illegal_and_stale_transitions_fail_closed(tmp_path):
    repo = repository(tmp_path)
    repo.create_invoice(invoice())
    with pytest.raises(WorkflowError, match="Illegal"):
        repo.transition_invoice(
            organization_id="org-1",
            invoice_id="invoice-1",
            target_status=InvoiceStatus.RECONCILED,
            expected_version=1,
            actor_user_id="operator-1",
            correlation_id="request-1",
        )
    repo.transition_invoice(
        organization_id="org-1",
        invoice_id="invoice-1",
        target_status=InvoiceStatus.EVALUATING,
        expected_version=1,
        actor_user_id="operator-1",
        correlation_id="request-2",
    )
    with pytest.raises(WorkflowError, match="another operation"):
        repo.transition_invoice(
            organization_id="org-1",
            invoice_id="invoice-1",
            target_status=InvoiceStatus.HOLD,
            expected_version=1,
            actor_user_id="worker-policy",
            correlation_id="request-2",
        )


def test_invoice_lookup_is_tenant_scoped(tmp_path):
    repo = repository(tmp_path)
    repo.create_invoice(invoice())
    with pytest.raises(PersistenceError, match="not found"):
        repo.get_invoice(organization_id="org-2", invoice_id="invoice-1")


def test_financial_records_require_existing_organization(tmp_path):
    repo = SqliteRepository(tmp_path / "tallyguard.sqlite3")
    with pytest.raises(PersistenceError, match="already in use"):
        repo.create_invoice(invoice(organization_id="missing-org"))


def test_concurrent_transitions_have_one_winner(tmp_path):
    repo = repository(tmp_path)
    repo.create_invoice(invoice())

    def advance(index):
        try:
            repo.transition_invoice(
                organization_id="org-1",
                invoice_id="invoice-1",
                target_status=InvoiceStatus.EVALUATING,
                expected_version=1,
                actor_user_id=f"operator-{index}",
                correlation_id=f"request-{index}",
            )
            return "won"
        except WorkflowError:
            return "lost"

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(advance, range(20)))
    assert results.count("won") == 1
    assert results.count("lost") == 19

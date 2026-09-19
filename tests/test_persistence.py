from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from datetime import timedelta

import pytest

from tallyguard.auth import AuthenticationDenied, Authenticator, Principal, Role
from tallyguard.evidence import (
    EvidenceRecord,
    EvidenceStore,
    EvidenceType,
    ExtractedField,
    ExtractionMethod,
    SourceLocation,
)
from tallyguard.models import Invoice
from tallyguard.network import ArcNetwork
from tallyguard.persistence import PersistenceError, SqliteRepository
from tallyguard.settlement import PaymentIntent, SettlementReceipt, SettlementStatus
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


def test_payment_intent_and_receipt_survive_repository_restart(tmp_path):
    database = tmp_path / "tallyguard.sqlite3"
    repo = repository(tmp_path)
    repo.create_invoice(invoice())
    intent = PaymentIntent(
        id="payment-1",
        organization_id="org-1",
        invoice_id="invoice-1",
        decision_id="decision-1",
        recipient=WALLET,
        amount_usdc=Decimal("1200"),
        network=ArcNetwork.TESTNET,
        idempotency_key="123e4567-e89b-42d3-a456-426614174000",
    )
    persisted, created = repo.create_or_get_payment_intent(intent)
    assert created is True
    assert persisted == intent
    receipt = SettlementReceipt(
        payment_intent_id=intent.id,
        organization_id="org-1",
        provider="arc-simulator",
        provider_reference="sim-1",
        transaction_hash="0x" + "b" * 64,
        block_number=123,
        confirmed_recipient=WALLET,
        confirmed_amount_usdc=Decimal("1200"),
        network=ArcNetwork.TESTNET,
        status=SettlementStatus.CONFIRMED,
        confirmed_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )
    stored, receipt_created = repo.save_settlement_receipt(receipt)
    assert receipt_created is True
    assert stored == receipt
    repo.close()

    reopened = SqliteRepository(database)
    assert reopened.get_payment_intent(
        organization_id="org-1", payment_intent_id="payment-1"
    ) == intent
    assert reopened.get_settlement_receipt(
        organization_id="org-1", payment_intent_id="payment-1"
    ) == receipt


def test_opaque_session_survives_restart_and_revocation_is_durable(tmp_path):
    database = tmp_path / "tallyguard.sqlite3"
    repo = repository(tmp_path)
    repo.create_user(
        organization_id="org-1",
        user_id="auditor-1",
        display_name="Auditor One",
        roles=(Role.AUDITOR.value,),
    )
    authenticator = Authenticator(store=repo)
    token, session = authenticator.issue_session(
        Principal(
            user_id="auditor-1",
            organization_id="org-1",
            roles=(Role.AUDITOR,),
        ),
        lifetime=timedelta(hours=1),
        now=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )
    assert token != session.token_hash
    repo.close()

    reopened = SqliteRepository(database)
    restarted_authenticator = Authenticator(store=reopened)
    principal = restarted_authenticator.authenticate(
        token,
        now=datetime(2026, 9, 20, 0, 30, tzinfo=timezone.utc),
    )
    assert principal.user_id == "auditor-1"

    restarted_authenticator.revoke(
        token,
        now=datetime(2026, 9, 20, 0, 31, tzinfo=timezone.utc),
    )
    reopened.close()

    final_repo = SqliteRepository(database)
    with pytest.raises(AuthenticationDenied, match="revoked"):
        Authenticator(store=final_repo).authenticate(
            token,
            now=datetime(2026, 9, 20, 0, 32, tzinfo=timezone.utc),
        )


def test_persistent_audit_chain_is_tenant_scoped_idempotent_and_tamper_evident(tmp_path):
    repo = repository(tmp_path)
    timestamp = datetime(2026, 9, 20, tzinfo=timezone.utc)
    first = repo.append(
        aggregate_type="decision",
        aggregate_id="decision-1",
        event_type="POLICY_DECISION_RECORDED",
        payload={"organization_id": "org-1", "action": "PAY"},
        created_at=timestamp,
    )
    duplicate = repo.append(
        aggregate_type="decision",
        aggregate_id="decision-1",
        event_type="POLICY_DECISION_RECORDED",
        payload={"organization_id": "org-1", "action": "PAY"},
        created_at=timestamp,
    )
    other = repo.append(
        aggregate_type="decision",
        aggregate_id="decision-2",
        event_type="POLICY_DECISION_RECORDED",
        payload={"organization_id": "org-2", "action": "HOLD"},
        created_at=timestamp,
    )

    assert duplicate == first
    assert other.sequence == 1
    assert len(repo.audit_events(organization_id="org-1")) == 1
    assert repo.verify_audit_chain(organization_id="org-1") is True

    repo._connection.execute(
        """
        UPDATE audit_events SET payload_json = ?
        WHERE organization_id = ? AND sequence = ?
        """,
        ('{"action":"REJECT","organization_id":"org-1"}', "org-1", 1),
    )
    assert repo.verify_audit_chain(organization_id="org-1") is False


def test_decision_retries_reuse_original_uuid4_and_reject_changed_payment(tmp_path):
    repo = repository(tmp_path)
    repo.create_invoice(invoice())
    original = PaymentIntent(
        id="payment-1",
        organization_id="org-1",
        invoice_id="invoice-1",
        decision_id="decision-1",
        recipient=WALLET,
        amount_usdc=Decimal("1200"),
        network=ArcNetwork.TESTNET,
        idempotency_key="123e4567-e89b-42d3-a456-426614174000",
    )
    repo.create_or_get_payment_intent(original)
    retry = PaymentIntent(
        id="payment-retry",
        organization_id="org-1",
        invoice_id="invoice-1",
        decision_id="decision-1",
        recipient=WALLET,
        amount_usdc=Decimal("1200"),
        network=ArcNetwork.TESTNET,
        idempotency_key="123e4567-e89b-42d3-a456-426614174001",
    )
    persisted, created = repo.create_or_get_payment_intent(retry)
    assert created is False
    assert persisted.idempotency_key == original.idempotency_key
    assert persisted.id == original.id

    changed = PaymentIntent(
        id="payment-retry",
        organization_id="org-1",
        invoice_id="invoice-1",
        decision_id="decision-1",
        recipient=WALLET,
        amount_usdc=Decimal("1199"),
        network=ArcNetwork.TESTNET,
        idempotency_key="123e4567-e89b-42d3-a456-426614174002",
    )
    with pytest.raises(PersistenceError, match="different payment intent"):
        repo.create_or_get_payment_intent(changed)


def test_payment_and_receipt_queries_are_tenant_scoped(tmp_path):
    repo = repository(tmp_path)
    repo.create_invoice(invoice())
    intent = PaymentIntent(
        id="payment-1",
        organization_id="org-1",
        invoice_id="invoice-1",
        decision_id="decision-1",
        recipient=WALLET,
        amount_usdc=Decimal("1200"),
        network=ArcNetwork.TESTNET,
        idempotency_key="123e4567-e89b-42d3-a456-426614174000",
    )
    repo.create_or_get_payment_intent(intent)
    with pytest.raises(PersistenceError, match="not found"):
        repo.get_payment_intent(organization_id="org-2", payment_intent_id="payment-1")
    assert repo.find_settlement_receipt(
        organization_id="org-2", payment_intent_id="payment-1"
    ) is None

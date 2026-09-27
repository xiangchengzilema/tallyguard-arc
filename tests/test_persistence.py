from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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
from tallyguard.models import Invoice, TreasurySnapshot, Vendor
from tallyguard.network import ArcNetwork
from tallyguard.persistence import PersistenceError, SqliteRepository
from tallyguard.policies import PolicyRepositoryError
from tallyguard.policy import VENDOR_INVOICE_NUMBER_SIGNAL, Policy
from tallyguard.settlement import PaymentIntent, SettlementReceipt, SettlementStatus
from tallyguard.workflow import InvoiceStatus, WorkflowError
from tallyguard.vendors import WalletVerificationMethod


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


def test_duplicate_signal_survives_reexported_invoice_and_stays_tenant_scoped(tmp_path):
    repo = repository(tmp_path)
    first = invoice()
    changed = replace(
        first,
        id="invoice-2",
        invoice_number="inv 1007",
        amount=Decimal("1201"),
        source_document_hash="b" * 64,
    )
    other_vendor = replace(
        first,
        id="invoice-3",
        vendor_id="vendor-2",
        source_document_hash="c" * 64,
    )
    repo.create_invoice(first)
    repo.create_invoice(changed)
    repo.create_invoice(other_vendor)

    signals = repo.known_invoice_fingerprints(
        organization_id="org-1",
        exclude_invoice_id=changed.id,
        candidate_invoice=changed,
    )
    assert first.fingerprint in signals
    assert f"{VENDOR_INVOICE_NUMBER_SIGNAL}{changed.fingerprint}:{first.id}" in signals
    assert not any(signal.endswith(f":{other_vendor.id}") for signal in signals)
    assert repo.known_invoice_fingerprints(
        organization_id="org-2",
        candidate_invoice=replace(changed, organization_id="org-2"),
    ) == ()


def test_evidence_round_trips_with_provenance(tmp_path):
    repo = repository(tmp_path)
    record = evidence_record()
    repo.save_evidence(record)
    restored = repo.get_evidence(organization_id="org-1", document_id="doc-1")
    assert restored == record
    assert restored.fields[0].source.json_pointer == "/invoice_number"


def test_verified_vendor_wallet_history_survives_restart(tmp_path):
    database = tmp_path / "tallyguard.sqlite3"
    repo = SqliteRepository(database)
    repo.create_organization(organization_id="org-1", name="Northwind AI")
    repo.create_user(
        organization_id="org-1",
        user_id="operator-1",
        display_name="Finance Operator",
        roles=(Role.FINANCE_OPERATOR.value,),
    )
    original_wallet = "0x2222222222222222222222222222222222222222"
    replacement_wallet = "0x3333333333333333333333333333333333333333"
    repo.onboard_vendor(
        Vendor(
            id="vendor-1",
            organization_id="org-1",
            legal_name="Verified Supplies Ltd",
            approved_wallet_address=original_wallet,
            autopay_limit=Decimal("2500"),
            risk_tier="low",
        ),
        verification_method=WalletVerificationMethod.SIGNED_CHALLENGE,
        verification_reference="challenge-001",
        verified_by_user_id="operator-1",
    )
    repo.replace_vendor_wallet(
        organization_id="org-1",
        vendor_id="vendor-1",
        expected_current_wallet=original_wallet,
        new_wallet=replacement_wallet,
        verification_method=WalletVerificationMethod.OUT_OF_BAND_CALL,
        verification_reference="call-001",
        verified_by_user_id="operator-1",
    )
    repo.close()

    restarted = SqliteRepository(database)
    vendor = restarted.get_vendor(organization_id="org-1", vendor_id="vendor-1")
    history = restarted.vendor_wallet_history(
        organization_id="org-1", vendor_id="vendor-1"
    )

    assert vendor.approved_wallet_address == replacement_wallet
    assert [item.verification_reference for item in history] == [
        "challenge-001",
        "call-001",
    ]
    assert history[1].previous_wallet_address == original_wallet


def test_active_policy_and_treasury_snapshot_survive_restart(tmp_path):
    database = tmp_path / "tallyguard.sqlite3"
    repo = SqliteRepository(database)
    repo.create_organization(organization_id="org-1", name="Northwind AI")
    repo.create_user(
        organization_id="org-1",
        user_id="admin-1",
        display_name="Policy Admin",
        roles=(Role.ADMIN.value,),
    )
    repo.create_user(
        organization_id="org-1",
        user_id="operator-1",
        display_name="Finance Operator",
        roles=(Role.FINANCE_OPERATOR.value,),
    )
    first = Policy(
        version="v1",
        organization_id="org-1",
        daily_payment_limit_usdc=Decimal("5000"),
        daily_autonomous_payment_limit_usdc=Decimal("1000"),
        minimum_cash_reserve_usdc=Decimal("1000"),
        maximum_autonomous_payment_usdc=Decimal("2000"),
    )
    second = Policy(
        version="v2",
        organization_id="org-1",
        daily_payment_limit_usdc=Decimal("7500"),
        daily_autonomous_payment_limit_usdc=Decimal("1000"),
        minimum_cash_reserve_usdc=Decimal("1000"),
        maximum_autonomous_payment_usdc=Decimal("2000"),
        kill_switch_enabled=True,
    )
    repo.activate_policy(first, activated_by_user_id="admin-1")
    repo.activate_policy(second, activated_by_user_id="admin-1")
    with pytest.raises(PolicyRepositoryError, match="immutable"):
        repo.activate_policy(second, activated_by_user_id="admin-1")
    recorded = repo.record_treasury_snapshot(
        TreasurySnapshot(
            organization_id="org-1",
            available_usdc=Decimal("12000"),
            spent_today_usdc=Decimal("450"),
        ),
        source_reference="circle-balance-2026-09-20T01:00:00Z",
        recorded_by_user_id="operator-1",
    )
    repo.close()

    restarted = SqliteRepository(database)
    active = restarted.active_policy(organization_id="org-1")
    history = restarted.policy_history(organization_id="org-1")
    changes = restarted.policy_diff(
        organization_id="org-1", from_version="v1", to_version="v2"
    )
    treasury = restarted.latest_treasury_snapshot(organization_id="org-1")

    assert active.policy == second
    assert [item.policy.version for item in history] == ["v1", "v2"]
    assert {item.field for item in changes} == {
        "daily_payment_limit_usdc",
        "kill_switch_enabled",
        "version",
    }
    assert treasury.sequence == recorded.sequence
    assert treasury.snapshot.available_usdc == Decimal("12000")
    assert treasury.source_reference == "circle-balance-2026-09-20T01:00:00Z"


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


def test_persistent_sessions_are_pruned_and_bounded_per_principal(tmp_path):
    repo = repository(tmp_path)
    repo.create_user(
        organization_id="org-1",
        user_id="auditor-1",
        display_name="Auditor One",
        roles=(Role.AUDITOR.value,),
    )
    authenticator = Authenticator(
        store=repo,
        maximum_active_sessions_per_principal=2,
    )
    identity = Principal(
        user_id="auditor-1",
        organization_id="org-1",
        roles=(Role.AUDITOR,),
    )
    expired_token, expired_session = authenticator.issue_session(
        identity,
        lifetime=timedelta(minutes=1),
        now=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )
    second_token, second_session = authenticator.issue_session(
        identity,
        now=datetime(2026, 9, 20, 0, 2, tzinfo=timezone.utc),
    )
    third_token, _ = authenticator.issue_session(
        identity,
        now=datetime(2026, 9, 20, 0, 3, tzinfo=timezone.utc),
    )
    fourth_token, _ = authenticator.issue_session(
        identity,
        now=datetime(2026, 9, 20, 0, 4, tzinfo=timezone.utc),
    )

    assert repo.get_session(expired_session.token_hash) is None
    assert repo.get_session(second_session.token_hash) is None
    for removed in (expired_token, second_token):
        with pytest.raises(AuthenticationDenied, match="invalid"):
            authenticator.authenticate(
                removed,
                now=datetime(2026, 9, 20, 0, 4, tzinfo=timezone.utc),
            )
    assert authenticator.authenticate(
        third_token,
        now=datetime(2026, 9, 20, 0, 4, tzinfo=timezone.utc),
    ).user_id == "auditor-1"
    assert authenticator.authenticate(
        fourth_token,
        now=datetime(2026, 9, 20, 0, 4, tzinfo=timezone.utc),
    ).user_id == "auditor-1"


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

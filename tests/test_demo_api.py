from datetime import date
import hashlib
from io import BytesIO
import json

import pytest

from tallyguard.api import create_app
from tallyguard.network import ArcNetwork, ArcNetworkConfig
from tallyguard.persistence import PersistenceError
from tallyguard.settlement import PaymentIntent, ProviderSubmission


class WrongRecipientAdapter:
    name = "fault-injection"

    def submit(self, intent: PaymentIntent) -> ProviderSubmission:
        return ProviderSubmission(
            provider_reference="wrong-recipient",
            transaction_hash="0x" + "b" * 64,
            recipient="0x2222222222222222222222222222222222222222",
            amount_usdc=intent.amount_usdc,
            network=ArcNetwork.TESTNET,
            block_number=55,
        )


def auth(client, role):
    token = client.post("/api/demo/session", json={"role": role}).get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _post_ok(client, path, *, headers, json_body):
    response = client.post(path, headers=headers, json=json_body)
    assert response.status_code in {200, 201}, response.get_json()
    return response.get_json()


def _seed_mainnet_pay_decision(client, *, admin_headers, operator_headers):
    invoice_id = "mainnet-invoice-1"
    vendor_id = "mainnet-vendor-1"
    recipient = "0x1111111111111111111111111111111111111111"
    invoice_document = json.dumps(
        {
            "invoice_id": invoice_id,
            "vendor_id": vendor_id,
            "invoice_number": "MAINNET-001",
            "currency": "USDC",
            "amount": "0.01",
            "due_date": "2026-10-01",
            "payment_wallet_address": recipient,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    purchase_order = json.dumps(
        {
            "purchase_order_id": "mainnet-po-1",
            "vendor_id": vendor_id,
            "po_number": "MAINNET-PO-001",
            "currency": "USDC",
            "authorized_amount": "0.01",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    delivery = json.dumps(
        {
            "delivery_id": "mainnet-delivery-1",
            "purchase_order_id": "mainnet-po-1",
            "delivered_value": "0.01",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()

    _post_ok(
        client,
        "/api/policies",
        headers=admin_headers,
        json_body={
            "version": "mainnet-policy-v1",
            "daily_payment_limit_usdc": "1",
            "minimum_cash_reserve_usdc": "0",
            "maximum_autonomous_payment_usdc": "0.10",
            "po_amount_tolerance_usdc": "0",
            "allowed_asset": "USDC",
            "allowed_network": "ARC-MAINNET",
            "kill_switch_enabled": False,
        },
    )
    _post_ok(
        client,
        "/api/treasury/snapshots",
        headers=operator_headers,
        json_body={
            "available_usdc": "1",
            "spent_today_usdc": "0",
            "source_reference": "controlled-mainnet-wallet-check",
        },
    )
    _post_ok(
        client,
        "/api/vendors",
        headers=operator_headers,
        json_body={
            "id": vendor_id,
            "legal_name": "Controlled Mainnet Recipient",
            "approved_wallet_address": recipient,
            "autopay_limit": "0.10",
            "verification_method": "MANUAL_REVIEW",
            "verification_reference": "mainnet-acceptance-review",
        },
    )
    _post_ok(
        client,
        "/api/invoices",
        headers=operator_headers,
        json_body={
            "id": invoice_id,
            "vendor_id": vendor_id,
            "invoice_number": "MAINNET-001",
            "currency": "USDC",
            "amount": "0.01",
            "due_date": "2026-10-01",
            "payment_wallet_address": recipient,
            "source_document_hash": hashlib.sha256(invoice_document).hexdigest(),
        },
    )
    for evidence_type, filename, content in (
        ("INVOICE", "invoice.json", invoice_document),
        ("PURCHASE_ORDER", "purchase-order.json", purchase_order),
        ("DELIVERY", "delivery.json", delivery),
    ):
        uploaded = client.post(
            f"/api/invoices/{invoice_id}/evidence",
            headers=operator_headers,
            data={
                "evidence_type": evidence_type,
                "file": (BytesIO(content), filename, "application/json"),
            },
            content_type="multipart/form-data",
        )
        assert uploaded.status_code == 201, uploaded.get_json()
    evaluated = client.post(
        f"/api/invoices/{invoice_id}/evaluate",
        headers=operator_headers,
        json={},
    )
    assert evaluated.status_code == 200, evaluated.get_json()
    decision = evaluated.get_json()["decision"]
    assert decision["final_action"] == "PAY"
    return invoice_id, decision["id"]


def test_all_judge_scenarios_run_through_real_policy_and_workflow(tmp_path):
    app = create_app(database_path=tmp_path / "demo.sqlite3", testing=True)
    client = app.test_client()
    operator_headers = auth(client, "operator")
    catalog = client.get("/api/demo/scenarios").get_json()["items"]
    expected = {item["key"]: item["expected_action"] for item in catalog}
    assert len(expected) == 8

    for key, expected_action in expected.items():
        response = client.post(f"/api/demo/scenarios/{key}/run", headers=operator_headers)
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["decision"]["final_action"] == expected_action
        assert payload["invoice"]["status"] in {
            "READY",
            "SCHEDULED",
            "HOLD",
            "REJECTED",
            "ESCALATED",
        }
        assert payload["decision"]["rules"]


def test_provider_timeout_retries_same_durable_intent_without_double_payment(tmp_path):
    app = create_app(database_path=tmp_path / "provider-recovery.sqlite3", testing=True)
    client = app.test_client()
    operator_headers = auth(client, "operator")
    approver_headers = auth(client, "approver")
    auditor_headers = auth(client, "auditor")
    run = client.post(
        "/api/demo/scenarios/provider-recovery/run",
        headers=operator_headers,
    ).get_json()
    invoice_id = run["invoice"]["id"]
    decision_id = run["decision"]["id"]

    first = client.post(
        f"/api/invoices/{invoice_id}/settle",
        headers=approver_headers,
        json={"decision_id": decision_id},
    )
    assert first.status_code == 503
    assert first.get_json()["error"]["code"] == "SETTLEMENT_UNAVAILABLE"
    assert first.headers["Retry-After"] == "2"
    repository = app.extensions["tallyguard_repository"]
    failed = repository.get_invoice(
        organization_id="demo-org", invoice_id=invoice_id
    )
    assert failed.status.value == "SUBMISSION_FAILED"
    original_intent = repository.get_payment_intent_for_decision(
        organization_id="demo-org", decision_id=decision_id
    )
    failed_attempts = repository.settlement_attempts(
        organization_id="demo-org", payment_intent_id=original_intent.id
    )
    assert len(failed_attempts) == 1
    assert failed_attempts[0].outcome.value == "FAILED_RETRYABLE"
    assert failed_attempts[0].retryable is True
    open_incidents = client.get(
        "/api/operations/settlement-incidents", headers=auditor_headers
    ).get_json()
    assert open_incidents["summary"]["open_retryable"] == 1
    assert open_incidents["items"][0]["state"] == "OPEN_RETRYABLE"

    second = client.post(
        f"/api/invoices/{invoice_id}/settle",
        headers=approver_headers,
        json={"decision_id": decision_id},
    )
    assert second.status_code == 200
    payment = second.get_json()["payment"]
    assert payment["invoice"]["status"] == "RECONCILED"
    assert payment["intent"]["id"] == original_intent.id
    retried_intent = repository.get_payment_intent_for_decision(
        organization_id="demo-org", decision_id=decision_id
    )
    assert retried_intent.idempotency_key == original_intent.idempotency_key
    adapter = app.extensions["tallyguard_settlement_adapter"]
    assert adapter.failed_attempt_count == 1
    assert adapter.submission_count == 1
    attempts = repository.settlement_attempts(
        organization_id="demo-org", payment_intent_id=original_intent.id
    )
    assert [attempt.outcome.value for attempt in attempts] == [
        "CONFIRMED",
        "FAILED_RETRYABLE",
    ]
    recovered = client.get(
        "/api/operations/settlement-incidents", headers=auditor_headers
    ).get_json()
    assert recovered["summary"]["resolved"] == 1
    assert recovered["items"][0]["state"] == "RESOLVED"
    assert recovered["items"][0]["attempt_count"] == 2
    assert len(recovered["items"][0]["idempotency_fingerprint"]) == 64

    audit = client.get("/api/audit/events", headers=auditor_headers).get_json()
    event_types = [item["event_type"] for item in audit["items"]]
    assert "SETTLEMENT_PROVIDER_UNAVAILABLE" in event_types
    assert event_types.count("SETTLEMENT_RECONCILED") == 1
    assert audit["chain_valid"] is True


def test_provider_retry_rechecks_new_kill_switch_before_resubmission(tmp_path):
    app = create_app(database_path=tmp_path / "provider-retry-kill.sqlite3", testing=True)
    client = app.test_client()
    operator_headers = auth(client, "operator")
    approver_headers = auth(client, "approver")
    admin_headers = auth(client, "admin")
    run = client.post(
        "/api/demo/scenarios/provider-recovery/run",
        headers=operator_headers,
    ).get_json()
    invoice_id = run["invoice"]["id"]
    decision_id = run["decision"]["id"]

    assert client.post(
        f"/api/invoices/{invoice_id}/settle",
        headers=approver_headers,
        json={"decision_id": decision_id},
    ).status_code == 503
    active = client.get(
        "/api/policies/active", headers=approver_headers
    ).get_json()["policy"]
    kill_payload = {
        key: active[key]
        for key in (
            "daily_payment_limit_usdc",
            "minimum_cash_reserve_usdc",
            "maximum_autonomous_payment_usdc",
            "po_amount_tolerance_usdc",
            "allowed_asset",
            "allowed_network",
            "schedule_payments_before_due_days",
        )
    }
    kill_payload.update(
        {"version": "provider-retry-emergency-stop", "kill_switch_enabled": True}
    )
    assert client.post(
        "/api/policies", headers=admin_headers, json=kill_payload
    ).status_code == 201

    retry = client.post(
        f"/api/invoices/{invoice_id}/settle",
        headers=approver_headers,
        json={"decision_id": decision_id},
    )
    assert retry.status_code == 409
    assert retry.get_json()["error"]["code"] == "SETTLEMENT_DENIED"
    assert "kill switch" in retry.get_json()["error"]["message"].lower()
    adapter = app.extensions["tallyguard_settlement_adapter"]
    assert adapter.failed_attempt_count == 1
    assert adapter.submission_count == 0


def test_reconciliation_mismatch_is_visible_and_locked_for_auditors(tmp_path):
    app = create_app(
        database_path=tmp_path / "provider-mismatch.sqlite3",
        testing=True,
        settlement_adapter=WrongRecipientAdapter(),
    )
    client = app.test_client()
    operator_headers = auth(client, "operator")
    approver_headers = auth(client, "approver")
    auditor_headers = auth(client, "auditor")
    run = client.post(
        "/api/demo/scenarios/clean-payment/run", headers=operator_headers
    ).get_json()

    denied = client.post(
        f"/api/invoices/{run['invoice']['id']}/settle",
        headers=approver_headers,
        json={"decision_id": run["decision"]["id"]},
    )
    assert denied.status_code == 409
    repository = app.extensions["tallyguard_repository"]
    invoice = repository.get_invoice(
        organization_id="demo-org", invoice_id=run["invoice"]["id"]
    )
    assert invoice.status.value == "RECONCILIATION_MISMATCH"

    incidents = client.get(
        "/api/operations/settlement-incidents", headers=auditor_headers
    ).get_json()
    assert incidents["summary"] == {
        "total": 1,
        "open_retryable": 0,
        "locked": 1,
        "resolved": 0,
    }
    incident = incidents["items"][0]
    assert incident["state"] == "LOCKED"
    assert incident["retryable"] is False
    assert incident["failed_attempt"]["error_code"] == "RECONCILIATION_MISMATCH"
    assert "idempotency_key" not in incident


def test_missing_delivery_scenario_explains_remediation(tmp_path):
    app = create_app(database_path=tmp_path / "demo.sqlite3", testing=True)
    client = app.test_client()
    payload = client.post(
        "/api/demo/scenarios/missing-delivery/run",
        headers=auth(client, "operator"),
    ).get_json()
    assert payload["decision"]["final_action"] == "HOLD"
    assert "MISSING_DELIVERY_EVIDENCE" in payload["decision"]["reason_codes"]
    assert any("delivery" in text.lower() for text in payload["decision"]["remediation"])


def test_schedule_runner_waits_until_release_date_then_settles_exactly_once(tmp_path):
    clock = {"today": date(2026, 9, 20)}
    app = create_app(
        database_path=tmp_path / "schedule.sqlite3",
        testing=True,
        date_provider=lambda: clock["today"],
    )
    client = app.test_client()
    operator_headers = auth(client, "operator")
    approver_headers = auth(client, "approver")
    run = client.post(
        "/api/demo/scenarios/scheduled-payment/run",
        headers=operator_headers,
    ).get_json()
    assert run["decision"]["final_action"] == "SCHEDULE"
    assert run["decision"]["scheduled_for"] == "2026-10-07"

    assert client.post("/api/schedules/run", headers=operator_headers).status_code == 403
    waiting = client.post("/api/schedules/run", headers=approver_headers)
    assert waiting.status_code == 200
    waiting_run = waiting.get_json()["schedule_run"]
    assert waiting_run["scanned"] == 1
    assert waiting_run["waiting"] == 1
    assert waiting_run["settled"] == 0
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 0

    clock["today"] = date(2026, 10, 7)
    released = client.post("/api/schedules/run", headers=approver_headers)
    assert released.status_code == 200
    release_run = released.get_json()["schedule_run"]
    assert release_run["settled"] == 1
    assert release_run["failed"] == 0
    result = release_run["results"][0]
    assert result["release_decision"]["final_action"] == "PAY"
    assert result["payment"]["invoice"]["status"] == "RECONCILED"
    assert result["payment"]["intent"]["decision_id"] == result["release_decision"]["id"]
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 1

    repeated = client.post("/api/schedules/run", headers=approver_headers)
    assert repeated.status_code == 200
    assert repeated.get_json()["schedule_run"]["scanned"] == 0
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 1


def test_schedule_release_revalidates_current_kill_switch_before_payment(tmp_path):
    app = create_app(
        database_path=tmp_path / "schedule-stop.sqlite3",
        testing=True,
        date_provider=lambda: date(2026, 10, 7),
    )
    client = app.test_client()
    run = client.post(
        "/api/demo/scenarios/scheduled-payment/run",
        headers=auth(client, "operator"),
    ).get_json()
    admin_headers = auth(client, "admin")
    activated = client.post(
        "/api/policies",
        headers=admin_headers,
        json={
            "version": "emergency-stop-1",
            "daily_payment_limit_usdc": "5000",
            "minimum_cash_reserve_usdc": "3000",
            "maximum_autonomous_payment_usdc": "2000",
            "po_amount_tolerance_usdc": "0",
            "allowed_asset": "USDC",
            "allowed_network": "ARC-TESTNET",
            "kill_switch_enabled": True,
            "schedule_payments_before_due_days": 3,
        },
    )
    assert activated.status_code == 201

    response = client.post("/api/schedules/run", headers=auth(client, "approver"))
    assert response.status_code == 200
    schedule_run = response.get_json()["schedule_run"]
    assert schedule_run["settled"] == 0
    assert schedule_run["revalidated"] == 1
    result = schedule_run["results"][0]
    assert result["release_decision"]["final_action"] == "HOLD"
    assert "KILL_SWITCH_ACTIVE" in result["release_decision"]["reason_codes"]
    assert result["invoice"]["status"] == "HOLD"
    assert result["source_decision_id"] == run["decision"]["id"]
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 0


def test_large_invoice_requires_role_separated_approval(tmp_path):
    app = create_app(database_path=tmp_path / "demo.sqlite3", testing=True)
    client = app.test_client()
    operator_headers = auth(client, "operator")
    approver_headers = auth(client, "approver")
    run = client.post(
        "/api/demo/scenarios/large-invoice/run",
        headers=operator_headers,
    ).get_json()
    assert run["invoice"]["status"] == "ESCALATED"
    decision_id = run["decision"]["id"]

    requested = client.post(
        f"/api/decisions/{decision_id}/request-approval",
        headers=operator_headers,
    )
    assert requested.status_code == 201
    approval = requested.get_json()["approval"]

    forbidden = client.post(
        f"/api/approvals/{approval['id']}/resolve",
        headers=operator_headers,
        json={"approve": True, "note": "Attempted by wrong role", "expected_version": 1},
    )
    assert forbidden.status_code == 403

    resolved = client.post(
        f"/api/approvals/{approval['id']}/resolve",
        headers=approver_headers,
        json={"approve": True, "note": "Contract and delivery checked", "expected_version": 1},
    )
    assert resolved.status_code == 200
    assert resolved.get_json()["approval"]["authorized_action"] == "PAY"

    invoice = client.get(
        f"/api/invoices/{run['invoice']['id']}",
        headers=approver_headers,
    ).get_json()["invoice"]
    assert invoice["status"] == "READY"

    settled = client.post(
        f"/api/invoices/{run['invoice']['id']}/settle",
        headers=approver_headers,
        json={"decision_id": decision_id, "approval_reference": approval["id"]},
    )
    assert settled.status_code == 200
    payment = settled.get_json()["payment"]
    assert payment["intent"]["approval_reference"] == approval["id"]
    assert payment["invoice"]["status"] == "RECONCILED"
    assert payment["receipt"]["status"] == "CONFIRMED"


def test_mainnet_pay_requires_role_separated_decision_bound_approval(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("TALLYGUARD_ALLOW_MAINNET", "true")
    app = create_app(
        database_path=tmp_path / "mainnet-approval.sqlite3",
        testing=True,
        settlement_config=ArcNetworkConfig.for_network(ArcNetwork.MAINNET),
    )
    client = app.test_client()
    admin_headers = auth(client, "admin")
    operator_headers = auth(client, "operator")
    approver_headers = auth(client, "approver")
    auditor_headers = auth(client, "auditor")
    invoice_id, decision_id = _seed_mainnet_pay_decision(
        client,
        admin_headers=admin_headers,
        operator_headers=operator_headers,
    )

    blocked = client.post(
        f"/api/invoices/{invoice_id}/settle",
        headers=approver_headers,
        json={"decision_id": decision_id},
    )
    assert blocked.status_code == 409
    assert "mainnet payment reference" in blocked.get_json()["error"]["message"]
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 0

    requested = client.post(
        f"/api/decisions/{decision_id}/request-mainnet-approval",
        headers=operator_headers,
    )
    assert requested.status_code == 201, requested.get_json()
    approval = requested.get_json()["approval"]
    assert approval["purpose"] == "MAINNET_PAYMENT"

    pending_attempt = client.post(
        f"/api/invoices/{invoice_id}/settle",
        headers=approver_headers,
        json={
            "decision_id": decision_id,
            "approval_reference": approval["id"],
        },
    )
    assert pending_attempt.status_code == 409
    assert "not approved" in pending_attempt.get_json()["error"]["message"]
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 0

    resolved = client.post(
        f"/api/approvals/{approval['id']}/resolve",
        headers=approver_headers,
        json={
            "approve": True,
            "note": "Recipient, amount, policy, and mainnet funding reviewed.",
            "expected_version": approval["version"],
        },
    )
    assert resolved.status_code == 200, resolved.get_json()
    assert resolved.get_json()["approval"]["authorized_action"] == "PAY"

    settled = client.post(
        f"/api/invoices/{invoice_id}/settle",
        headers=approver_headers,
        json={
            "decision_id": decision_id,
            "approval_reference": approval["id"],
        },
    )
    assert settled.status_code == 200, settled.get_json()
    payment = settled.get_json()["payment"]
    assert payment["intent"]["network"] == "ARC-MAINNET"
    assert payment["intent"]["approval_reference"] == approval["id"]
    assert payment["receipt"]["status"] == "CONFIRMED"
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 1

    audit = client.get("/api/audit/events", headers=auditor_headers).get_json()
    assert audit["chain_valid"] is True
    event_types = [item["event_type"] for item in audit["items"]]
    assert "MAINNET_APPROVAL_REQUESTED" in event_types
    assert "APPROVAL_RESOLVED" in event_types
    assert "SETTLEMENT_RECONCILED" in event_types


def test_mainnet_approval_endpoint_stays_locked_on_testnet(tmp_path):
    app = create_app(database_path=tmp_path / "testnet-mainnet-lock.sqlite3", testing=True)
    client = app.test_client()
    operator_headers = auth(client, "operator")
    run = client.post(
        "/api/demo/scenarios/clean-payment/run",
        headers=operator_headers,
    ).get_json()

    response = client.post(
        f"/api/decisions/{run['decision']['id']}/request-mainnet-approval",
        headers=operator_headers,
    )

    assert response.status_code == 409
    assert "explicitly enabled ARC-MAINNET" in response.get_json()["error"]["message"]


def test_global_approval_inbox_is_role_scoped_and_rejection_closes_invoice(tmp_path):
    app = create_app(database_path=tmp_path / "approval-inbox.sqlite3", testing=True)
    client = app.test_client()
    operator_headers = auth(client, "operator")
    approver_headers = auth(client, "approver")
    run = client.post(
        "/api/demo/scenarios/large-invoice/run",
        headers=operator_headers,
    ).get_json()
    approval = client.post(
        f"/api/decisions/{run['decision']['id']}/request-approval",
        headers=operator_headers,
    ).get_json()["approval"]

    assert client.get("/api/approvals/pending", headers=operator_headers).status_code == 403
    pending = client.get("/api/approvals/pending", headers=approver_headers)
    assert pending.status_code == 200
    item = pending.get_json()["items"][0]
    assert item["approval"]["id"] == approval["id"]
    assert item["invoice"]["amount"] == "2200"
    assert item["decision"]["final_action"] == "ESCALATE"
    assert "AUTONOMY_LIMIT_EXCEEDED" in item["decision"]["reason_codes"]

    rejected = client.post(
        f"/api/approvals/{approval['id']}/resolve",
        headers=approver_headers,
        json={
            "approve": False,
            "note": "Contract owner rejected this exception.",
            "expected_version": approval["version"],
        },
    )
    assert rejected.status_code == 200
    assert rejected.get_json()["approval"]["status"] == "REJECTED"
    invoice = client.get(
        f"/api/invoices/{run['invoice']['id']}",
        headers=approver_headers,
    ).get_json()["invoice"]
    assert invoice["status"] == "REJECTED"
    assert client.get(
        "/api/approvals/pending", headers=approver_headers
    ).get_json()["items"] == []


def test_governance_overview_treats_missing_policy_as_empty_state(tmp_path):
    app = create_app(database_path=tmp_path / "governance-empty.sqlite3", testing=True)
    client = app.test_client()
    approver_headers = auth(client, "approver")

    response = client.get("/api/governance/overview", headers=approver_headers)

    assert response.status_code == 200
    assert response.get_json()["active_policy"] is None
    assert response.get_json()["settlement_capacity"] is None
    assert response.get_json()["pending_approvals"] == []


def test_approved_escalation_can_settle_after_service_restart(tmp_path):
    database = tmp_path / "demo.sqlite3"
    first_app = create_app(database_path=database, testing=True)
    first_client = first_app.test_client()
    operator_headers = auth(first_client, "operator")
    approver_headers = auth(first_client, "approver")
    run = first_client.post(
        "/api/demo/scenarios/large-invoice/run",
        headers=operator_headers,
    ).get_json()
    approval = first_client.post(
        f"/api/decisions/{run['decision']['id']}/request-approval",
        headers=operator_headers,
    ).get_json()["approval"]
    resolved = first_client.post(
        f"/api/approvals/{approval['id']}/resolve",
        headers=approver_headers,
        json={"approve": True, "note": "Durable approval", "expected_version": 1},
    )
    assert resolved.status_code == 200
    first_app.extensions["tallyguard_repository"].close()

    restarted_app = create_app(database_path=database, testing=True)
    restarted = restarted_app.test_client()
    settled = restarted.post(
        f"/api/invoices/{run['invoice']['id']}/settle",
        headers=approver_headers,
        json={
            "decision_id": run["decision"]["id"],
            "approval_reference": approval["id"],
        },
    )

    assert settled.status_code == 200
    payment = settled.get_json()["payment"]
    assert payment["intent"]["approval_reference"] == approval["id"]
    assert payment["invoice"]["status"] == "RECONCILED"
    audit = restarted.get(
        "/api/audit/events",
        headers=approver_headers,
    ).get_json()
    assert audit["chain_valid"] is True
    assert [item["event_type"] for item in audit["items"]] == [
        "POLICY_DECISION_RECORDED",
        "APPROVAL_REQUESTED",
        "APPROVAL_RESOLVED",
        "SETTLEMENT_RECONCILED",
    ]


def test_clean_scenario_settles_once_and_exposes_auditor_receipt(tmp_path):
    app = create_app(database_path=tmp_path / "demo.sqlite3", testing=True)
    client = app.test_client()
    operator_headers = auth(client, "operator")
    approver_headers = auth(client, "approver")
    auditor_headers = auth(client, "auditor")
    run = client.post(
        "/api/demo/scenarios/clean-payment/run",
        headers=operator_headers,
    ).get_json()
    invoice_id = run["invoice"]["id"]
    decision_id = run["decision"]["id"]

    forbidden = client.post(
        f"/api/invoices/{invoice_id}/settle",
        headers=operator_headers,
        json={"decision_id": decision_id},
    )
    assert forbidden.status_code == 403

    first = client.post(
        f"/api/invoices/{invoice_id}/settle",
        headers=approver_headers,
        json={"decision_id": decision_id},
    )
    assert first.status_code == 200
    first_payment = first.get_json()["payment"]
    assert first_payment["invoice"]["status"] == "RECONCILED"
    assert first_payment["reused_receipt"] is False
    assert first_payment["receipt"]["explorer_url"].startswith(
        "https://explorer.testnet.arc.io/tx/0x"
    )
    capacity = client.get(
        "/api/governance/overview", headers=approver_headers
    ).get_json()["settlement_capacity"]
    assert capacity["snapshot_fresh"] is True
    assert capacity["committed_since_snapshot_usdc"] == "1200"
    assert capacity["effective_available_usdc"] == "8800"
    assert capacity["daily_remaining_usdc"] == "3400"
    assert capacity["maximum_new_payment_usdc"] == "3400"

    second = client.post(
        f"/api/invoices/{invoice_id}/settle",
        headers=approver_headers,
        json={"decision_id": decision_id},
    )
    assert second.status_code == 200
    second_payment = second.get_json()["payment"]
    assert second_payment["reused_receipt"] is True
    assert second_payment["receipt"]["transaction_hash"] == first_payment["receipt"]["transaction_hash"]
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 1

    fetched = client.get(
        f"/api/payments/{first_payment['intent']['id']}",
        headers=auditor_headers,
    )
    assert fetched.status_code == 200
    assert fetched.get_json()["payment"]["receipt"] == first_payment["receipt"]

    audit = client.get("/api/audit/events", headers=auditor_headers).get_json()
    assert audit["chain_valid"] is True
    assert [item["event_type"] for item in audit["items"]].count(
        "SETTLEMENT_RECONCILED"
    ) == 1
    assert all(len(item["event_hash"]) == 64 for item in audit["items"])


def test_active_kill_switch_blocks_ready_invoice_but_not_completed_receipt_replay(tmp_path):
    app = create_app(database_path=tmp_path / "execution-kill-switch.sqlite3", testing=True)
    client = app.test_client()
    operator_headers = auth(client, "operator")
    approver_headers = auth(client, "approver")
    admin_headers = auth(client, "admin")
    auditor_headers = auth(client, "auditor")

    blocked_run = client.post(
        "/api/demo/scenarios/clean-payment/run",
        headers=operator_headers,
    ).get_json()
    active = client.get(
        "/api/policies/active", headers=approver_headers
    ).get_json()["policy"]
    kill_payload = {
        key: active[key]
        for key in (
            "daily_payment_limit_usdc",
            "minimum_cash_reserve_usdc",
            "maximum_autonomous_payment_usdc",
            "po_amount_tolerance_usdc",
            "allowed_asset",
            "allowed_network",
            "schedule_payments_before_due_days",
        )
    }
    kill_payload.update({"version": "emergency-stop-v1", "kill_switch_enabled": True})
    assert client.post(
        "/api/policies", json=kill_payload, headers=admin_headers
    ).status_code == 201

    blocked = client.post(
        f"/api/invoices/{blocked_run['invoice']['id']}/settle",
        json={"decision_id": blocked_run["decision"]["id"]},
        headers=approver_headers,
    )
    assert blocked.status_code == 409
    assert "kill switch" in blocked.get_json()["error"]["message"].lower()
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 0
    with pytest.raises(PersistenceError, match="not found"):
        app.extensions["tallyguard_repository"].get_payment_intent_for_decision(
            organization_id="demo-org",
            decision_id=blocked_run["decision"]["id"],
        )
    blocked_audit = client.get(
        "/api/audit/events", headers=auditor_headers
    ).get_json()
    assert blocked_audit["chain_valid"] is True
    assert blocked_audit["items"][-1]["event_type"] == (
        "SETTLEMENT_BLOCKED_BY_ACTIVE_KILL_SWITCH"
    )

    paid_app = create_app(database_path=tmp_path / "paid-replay.sqlite3", testing=True)
    paid_client = paid_app.test_client()
    paid_operator = auth(paid_client, "operator")
    paid_approver = auth(paid_client, "approver")
    paid_admin = auth(paid_client, "admin")
    paid_run = paid_client.post(
        "/api/demo/scenarios/clean-payment/run", headers=paid_operator
    ).get_json()
    first = paid_client.post(
        f"/api/invoices/{paid_run['invoice']['id']}/settle",
        json={"decision_id": paid_run["decision"]["id"]},
        headers=paid_approver,
    )
    assert first.status_code == 200
    paid_active = paid_client.get(
        "/api/policies/active", headers=paid_approver
    ).get_json()["policy"]
    paid_kill_payload = {
        key: paid_active[key]
        for key in (
            "daily_payment_limit_usdc",
            "minimum_cash_reserve_usdc",
            "maximum_autonomous_payment_usdc",
            "po_amount_tolerance_usdc",
            "allowed_asset",
            "allowed_network",
            "schedule_payments_before_due_days",
        )
    }
    paid_kill_payload.update({"version": "post-payment-stop-v1", "kill_switch_enabled": True})
    assert paid_client.post(
        "/api/policies", json=paid_kill_payload, headers=paid_admin
    ).status_code == 201
    replay = paid_client.post(
        f"/api/invoices/{paid_run['invoice']['id']}/settle",
        json={"decision_id": paid_run["decision"]["id"]},
        headers=paid_approver,
    )
    assert replay.status_code == 200
    assert replay.get_json()["payment"]["reused_receipt"] is True
    assert paid_app.extensions["tallyguard_settlement_adapter"].submission_count == 1


def test_execution_time_daily_limit_blocks_an_older_pay_decision(tmp_path):
    app = create_app(database_path=tmp_path / "execution-daily-limit.sqlite3", testing=True)
    client = app.test_client()
    operator_headers = auth(client, "operator")
    approver_headers = auth(client, "approver")
    admin_headers = auth(client, "admin")
    auditor_headers = auth(client, "auditor")
    run = client.post(
        "/api/demo/scenarios/clean-payment/run",
        headers=operator_headers,
    ).get_json()
    active = client.get(
        "/api/policies/active", headers=approver_headers
    ).get_json()["policy"]
    reduced_payload = {
        key: active[key]
        for key in (
            "minimum_cash_reserve_usdc",
            "maximum_autonomous_payment_usdc",
            "po_amount_tolerance_usdc",
            "allowed_asset",
            "allowed_network",
            "kill_switch_enabled",
            "schedule_payments_before_due_days",
        )
    }
    reduced_payload.update(
        {"version": "reduced-daily-limit-v1", "daily_payment_limit_usdc": "1000"}
    )
    assert client.post(
        "/api/policies", json=reduced_payload, headers=admin_headers
    ).status_code == 201

    blocked = client.post(
        f"/api/invoices/{run['invoice']['id']}/settle",
        json={"decision_id": run["decision"]["id"]},
        headers=approver_headers,
    )

    assert blocked.status_code == 409
    assert "daily payment limit" in blocked.get_json()["error"]["message"].lower()
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 0
    with pytest.raises(PersistenceError, match="not found"):
        app.extensions["tallyguard_repository"].get_payment_intent_for_decision(
            organization_id="demo-org",
            decision_id=run["decision"]["id"],
        )
    audit = client.get(
        "/api/audit/events", headers=auditor_headers
    ).get_json()
    assert audit["chain_valid"] is True
    assert audit["items"][-1]["event_type"] == "SETTLEMENT_BLOCKED_BY_EXECUTION_CONTROL"
    assert audit["items"][-1]["payload"]["control_code"] == "DAILY_LIMIT_EXCEEDED"


def test_non_pay_decision_cannot_enter_settlement(tmp_path):
    app = create_app(database_path=tmp_path / "demo.sqlite3", testing=True)
    client = app.test_client()
    run = client.post(
        "/api/demo/scenarios/missing-delivery/run",
        headers=auth(client, "operator"),
    ).get_json()

    response = client.post(
        f"/api/invoices/{run['invoice']['id']}/settle",
        headers=auth(client, "approver"),
        json={"decision_id": run["decision"]["id"]},
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "APPROVAL_ERROR"


def test_approval_cannot_be_rebound_to_another_invoice(tmp_path):
    app = create_app(database_path=tmp_path / "demo.sqlite3", testing=True)
    client = app.test_client()
    operator_headers = auth(client, "operator")
    approver_headers = auth(client, "approver")
    first = client.post(
        "/api/demo/scenarios/large-invoice/run", headers=operator_headers
    ).get_json()
    second = client.post(
        "/api/demo/scenarios/large-invoice/run", headers=operator_headers
    ).get_json()
    approval = client.post(
        f"/api/decisions/{first['decision']['id']}/request-approval",
        headers=operator_headers,
    ).get_json()["approval"]
    assert client.post(
        f"/api/approvals/{approval['id']}/resolve",
        headers=approver_headers,
        json={"approve": True, "note": "Approved only for first invoice", "expected_version": 1},
    ).status_code == 200

    response = client.post(
        f"/api/invoices/{second['invoice']['id']}/settle",
        headers=approver_headers,
        json={
            "decision_id": second["decision"]["id"],
            "approval_reference": approval["id"],
        },
    )
    assert response.status_code == 409
    assert "not bound" in response.get_json()["error"]["message"]

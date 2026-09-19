from datetime import date

import pytest

from tallyguard.api import create_app
from tallyguard.persistence import PersistenceError


def auth(client, role):
    token = client.post("/api/demo/session", json={"role": role}).get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_all_judge_scenarios_run_through_real_policy_and_workflow(tmp_path):
    app = create_app(database_path=tmp_path / "demo.sqlite3", testing=True)
    client = app.test_client()
    operator_headers = auth(client, "operator")
    catalog = client.get("/api/demo/scenarios").get_json()["items"]
    expected = {item["key"]: item["expected_action"] for item in catalog}
    assert len(expected) == 7

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

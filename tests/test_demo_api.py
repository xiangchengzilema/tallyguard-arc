from tallyguard.api import create_app


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

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

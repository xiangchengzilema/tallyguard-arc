from tallyguard.api import create_app
from tallyguard.auth import Principal, Role


def headers(token: str, correlation_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Correlation-ID": correlation_id,
    }


def session(client, role: str) -> str:
    return client.post("/api/demo/session", json={"role": role}).get_json()["access_token"]


def test_agent_run_plans_mixed_work_and_executes_only_policy_cleared_items(tmp_path):
    app = create_app(database_path=tmp_path / "agent-runs.sqlite3", testing=True)
    client = app.test_client()
    operator = session(client, "operator")
    approver = session(client, "approver")
    auditor = session(client, "auditor")

    clean = client.post(
        "/api/demo/scenarios/clean-payment/run",
        headers=headers(operator, "agent-seed-clean"),
    ).get_json()
    held = client.post(
        "/api/demo/scenarios/wallet-change/run",
        headers=headers(operator, "agent-seed-hold"),
    ).get_json()

    denied = client.post(
        "/api/agent-runs",
        json={"max_items": 25},
        headers=headers(auditor, "agent-plan-denied"),
    )
    assert denied.status_code == 403

    planned = client.post(
        "/api/agent-runs",
        json={"max_items": 25},
        headers=headers(operator, "agent-plan"),
    )
    assert planned.status_code == 201
    run = planned.get_json()["agent_run"]
    assert run["status"] == "PLANNED"
    assert run["summary"] == {
        "scanned": 2,
        "executable": 1,
        "requires_attention": 1,
        "actions": {"REMEDIATE": 1, "SETTLE": 1},
    }
    assert len(run["state_hash"]) == 64
    assert len(run["plan_hash"]) == 64
    actions = {item["invoice_id"]: item for item in run["items"]}
    assert actions[clean["invoice"]["id"]]["action"] == "SETTLE"
    assert actions[clean["invoice"]["id"]]["executable"] is True
    assert actions[held["invoice"]["id"]]["action"] == "REMEDIATE"
    assert actions[held["invoice"]["id"]]["executable"] is False

    executed = client.post(
        f"/api/agent-runs/{run['id']}/execute",
        headers=headers(approver, "agent-execute"),
    )
    assert executed.status_code == 200
    completed = executed.get_json()["agent_run"]
    assert completed["status"] == "EXECUTED"
    results = {item["invoice_id"]: item for item in completed["results"]}
    assert results[clean["invoice"]["id"]]["status"] == "SETTLED"
    assert results[held["invoice"]["id"]]["status"] == "SKIPPED"

    repeated = client.post(
        f"/api/agent-runs/{run['id']}/execute",
        headers=headers(approver, "agent-execute-again"),
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["reused_result"] is True
    assert repeated.get_json()["agent_run"]["results"] == completed["results"]

    latest = client.get(
        "/api/agent-runs/latest",
        headers=headers(auditor, "agent-latest"),
    ).get_json()["agent_run"]
    assert latest["id"] == run["id"]
    audit = client.get(
        "/api/audit/events",
        headers=headers(auditor, "agent-audit"),
    ).get_json()
    run_events = [item for item in audit["items"] if item["aggregate_id"] == run["id"]]
    assert [item["event_type"] for item in run_events] == [
        "AGENT_RUN_PLANNED",
        "AGENT_RUN_EXECUTED",
    ]


def test_agent_run_revalidates_stale_invoice_state_before_moving_funds(tmp_path):
    app = create_app(database_path=tmp_path / "agent-stale.sqlite3", testing=True)
    client = app.test_client()
    operator = session(client, "operator")
    approver = session(client, "approver")

    clean = client.post(
        "/api/demo/scenarios/clean-payment/run",
        headers=headers(operator, "stale-seed"),
    ).get_json()
    planned = client.post(
        "/api/agent-runs",
        json={},
        headers=headers(operator, "stale-plan"),
    ).get_json()["agent_run"]
    assert planned["summary"]["executable"] == 1

    settled = client.post(
        f"/api/invoices/{clean['invoice']['id']}/settle",
        json={"decision_id": clean["decision"]["id"]},
        headers=headers(approver, "stale-external-settle"),
    )
    assert settled.status_code == 200

    executed = client.post(
        f"/api/agent-runs/{planned['id']}/execute",
        headers=headers(approver, "stale-execute"),
    )
    assert executed.status_code == 207
    completed = executed.get_json()["agent_run"]
    assert completed["status"] == "PARTIAL"
    assert completed["results"][0]["status"] == "STALE"
    assert "INVOICE_VERSION_CHANGED" in completed["results"][0]["reason_codes"]
    assert "WORKFLOW_STATUS_CHANGED" in completed["results"][0]["reason_codes"]


def test_agent_runs_are_durable_and_tenant_scoped(tmp_path):
    database = tmp_path / "agent-durable.sqlite3"
    app = create_app(database_path=database, testing=True)
    client = app.test_client()
    operator = session(client, "operator")
    auditor = session(client, "auditor")
    run = client.post(
        "/api/agent-runs",
        json={},
        headers=headers(operator, "durable-plan"),
    ).get_json()["agent_run"]
    app.extensions["tallyguard_repository"].close()

    restarted = create_app(database_path=database, testing=True)
    fetched = restarted.test_client().get(
        f"/api/agent-runs/{run['id']}",
        headers=headers(auditor, "durable-read"),
    )
    assert fetched.status_code == 200
    assert fetched.get_json()["agent_run"]["plan_hash"] == run["plan_hash"]

    repository = restarted.extensions["tallyguard_repository"]
    repository.create_organization(organization_id="other-org", name="Other Organization")
    repository.create_user(
        organization_id="other-org",
        user_id="other-auditor",
        display_name="Other Auditor",
        roles=(Role.AUDITOR.value,),
    )
    other_token, _ = restarted.extensions["tallyguard_authenticator"].issue_session(
        Principal(
            user_id="other-auditor",
            organization_id="other-org",
            roles=(Role.AUDITOR,),
        )
    )
    hidden = restarted.test_client().get(
        f"/api/agent-runs/{run['id']}",
        headers=headers(other_token, "tenant-hidden"),
    )
    assert hidden.status_code == 404

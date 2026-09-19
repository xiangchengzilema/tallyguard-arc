from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json

from tallyguard.audit import canonical_json

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

    packet_response = client.get(
        f"/api/agent-runs/{run['id']}/proof-packet",
        headers=headers(auditor, "agent-proof"),
    )
    assert packet_response.status_code == 200
    assert packet_response.headers["Content-Disposition"].endswith("-proof-packet.json\"")
    envelope = json.loads(packet_response.data)
    assert envelope["packet"]["agent_run"]["id"] == run["id"]
    assert envelope["packet"]["integrity"]["plan_hash_verified"] is True
    assert envelope["packet"]["audit"]["tenant_chain_valid"] is True
    assert {event["event_type"] for event in envelope["packet"]["audit"]["related_events"]} >= {
        "AGENT_RUN_PLANNED",
        "AGENT_RUN_EXECUTED",
    }
    packet_hash = sha256(canonical_json(envelope["packet"]).encode("utf-8")).hexdigest()
    assert envelope["packet_sha256"] == packet_hash
    assert packet_response.headers["X-TallyGuard-Packet-SHA256"] == packet_hash


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
    hidden_packet = restarted.test_client().get(
        f"/api/agent-runs/{run['id']}/proof-packet",
        headers=headers(other_token, "tenant-proof-hidden"),
    )
    assert hidden_packet.status_code == 404


def test_agent_run_routes_escalation_then_settles_only_after_separate_approval(tmp_path):
    app = create_app(database_path=tmp_path / "agent-approval.sqlite3", testing=True)
    client = app.test_client()
    operator = session(client, "operator")
    approver = session(client, "approver")

    invoice = client.post(
        "/api/demo/scenarios/large-invoice/run",
        headers=headers(operator, "agent-escalation-seed"),
    ).get_json()
    first_plan = client.post(
        "/api/agent-runs",
        json={},
        headers=headers(operator, "agent-escalation-plan"),
    ).get_json()["agent_run"]
    assert first_plan["items"][0]["action"] == "REQUIRE_APPROVAL"
    routed = client.post(
        f"/api/agent-runs/{first_plan['id']}/execute",
        headers=headers(approver, "agent-route-approval"),
    )
    assert routed.status_code == 200
    assert routed.get_json()["agent_run"]["results"][0]["status"] == "ROUTED"

    governance = client.get(
        "/api/governance/overview",
        headers=headers(approver, "agent-approval-inbox"),
    ).get_json()
    approval = governance["pending_approvals"][0]["approval"]
    resolved = client.post(
        f"/api/approvals/{approval['id']}/resolve",
        json={
            "approve": True,
            "note": "Independent reviewer approved the bounded policy exception.",
            "expected_version": approval["version"],
        },
        headers=headers(approver, "agent-approval-resolve"),
    )
    assert resolved.status_code == 200

    second_plan = client.post(
        "/api/agent-runs",
        json={},
        headers=headers(operator, "agent-approved-plan"),
    ).get_json()["agent_run"]
    assert second_plan["items"][0]["action"] == "SETTLE_APPROVED"
    assert second_plan["items"][0]["approval_reference"] == approval["id"]
    settled = client.post(
        f"/api/agent-runs/{second_plan['id']}/execute",
        headers=headers(approver, "agent-approved-execute"),
    )
    assert settled.status_code == 200
    result = settled.get_json()["agent_run"]["results"][0]
    assert result["status"] == "SETTLED"
    assert result["payment"]["intent"]["approval_reference"] == approval["id"]
    assert result["payment"]["invoice"]["id"] == invoice["invoice"]["id"]


def test_agent_run_releases_due_schedule_through_current_policy_revalidation(tmp_path):
    app = create_app(
        database_path=tmp_path / "agent-schedule.sqlite3",
        testing=True,
        date_provider=lambda: date(2026, 10, 7),
    )
    client = app.test_client()
    operator = session(client, "operator")
    approver = session(client, "approver")
    scheduled = client.post(
        "/api/demo/scenarios/scheduled-payment/run",
        headers=headers(operator, "agent-schedule-seed"),
    ).get_json()
    planned = client.post(
        "/api/agent-runs",
        json={},
        headers=headers(operator, "agent-schedule-plan"),
    ).get_json()["agent_run"]
    assert planned["items"][0]["action"] == "RELEASE_SCHEDULE"
    assert planned["items"][0]["executable"] is True

    executed = client.post(
        f"/api/agent-runs/{planned['id']}/execute",
        headers=headers(approver, "agent-schedule-execute"),
    )
    assert executed.status_code == 200
    result = executed.get_json()["agent_run"]["results"][0]
    assert result["status"] == "SETTLED"
    assert result["schedule_result"]["release_decision"]["final_action"] == "PAY"
    assert result["schedule_result"]["payment"]["invoice"]["id"] == scheduled["invoice"]["id"]
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 1


def test_agent_run_execution_lease_fails_fast_and_recovers_after_timeout(tmp_path):
    app = create_app(database_path=tmp_path / "agent-lease.sqlite3", testing=True)
    client = app.test_client()
    operator = session(client, "operator")
    approver = session(client, "approver")
    principal = app.extensions["tallyguard_authenticator"].authenticate(approver)

    client.post(
        "/api/demo/scenarios/clean-payment/run",
        headers=headers(operator, "lease-seed-current"),
    )
    current_plan = client.post(
        "/api/agent-runs",
        json={},
        headers=headers(operator, "lease-plan-current"),
    ).get_json()["agent_run"]
    claimed, won = app.extensions["tallyguard_repository"].claim_agent_run_execution(
        organization_id=principal.organization_id,
        run_id=current_plan["id"],
        executed_by_user_id=principal.user_id,
    )
    assert won is True
    assert claimed.status.value == "EXECUTING"

    in_progress = client.post(
        f"/api/agent-runs/{current_plan['id']}/execute",
        headers=headers(approver, "lease-current-contender"),
    )
    assert in_progress.status_code == 202
    assert in_progress.headers["Retry-After"] == "1"
    assert in_progress.get_json()["execution_in_progress"] is True

    recovery_plan = client.post(
        "/api/agent-runs",
        json={},
        headers=headers(operator, "lease-plan-recovery"),
    ).get_json()["agent_run"]
    stale_claim, won = app.extensions["tallyguard_repository"].claim_agent_run_execution(
        organization_id=principal.organization_id,
        run_id=recovery_plan["id"],
        executed_by_user_id=principal.user_id,
        claimed_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    assert won is True
    assert stale_claim.status.value == "EXECUTING"

    recovered = client.post(
        f"/api/agent-runs/{recovery_plan['id']}/execute",
        headers=headers(approver, "lease-recovered"),
    )
    assert recovered.status_code == 200
    assert recovered.get_json()["agent_run"]["status"] == "EXECUTED"
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 1

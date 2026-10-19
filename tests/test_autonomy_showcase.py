from datetime import date, timedelta

import pytest

from tallyguard.api import create_app


def headers(token: str, correlation_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Correlation-ID": correlation_id,
    }


def session(client, role: str) -> str:
    return client.post("/api/demo/session", json={"role": role}).get_json()["access_token"]


@pytest.mark.parametrize("seed_date", [
    None, date(2026, 9, 20), date(2026, 10, 19),
    date(2027, 12, 24), date(2028, 2, 22),
])
def test_showcase_seeds_mixed_queue_for_one_click_agent_planning(tmp_path, seed_date):
    app = create_app(
        database_path=tmp_path / "showcase.sqlite3", testing=True,
        date_provider=(lambda: seed_date) if seed_date is not None else None,
    )
    client = app.test_client()
    operator = session(client, "operator")

    response = client.post(
        "/api/demo/autonomy-showcase",
        headers=headers(operator, "showcase-seed"),
    )
    assert response.status_code == 201
    showcase = response.get_json()["showcase"]
    assert len(showcase["items"]) == 4
    assert [item["scenario"]["key"] for item in showcase["items"]] == [
        "clean-payment",
        "large-invoice",
        "wallet-change",
        "scheduled-payment",
    ]
    assert len(showcase["approvals"]) == 1
    assert showcase["approvals"][0]["status"] == "PENDING"
    assert showcase["approvals"][0]["invoice_id"] == showcase["items"][1]["invoice"]["id"]

    approver = session(client, "approver")
    pending = client.get(
        "/api/governance/overview",
        headers=headers(approver, "showcase-governance"),
    ).get_json()
    assert len(pending["pending_approvals"]) == 1
    assert pending["pending_approvals"][0]["invoice"]["invoice_number"] == showcase["items"][1]["invoice"]["invoice_number"]

    plan = client.post(
        "/api/agent-runs",
        json={"max_items": 25},
        headers=headers(operator, "showcase-plan"),
    ).get_json()["agent_run"]
    assert plan["summary"] == {
        "scanned": 4,
        "executable": 2,
        "requires_attention": 2,
        "actions": {
            "REMEDIATE": 1,
            "REQUIRE_APPROVAL": 1,
            "SETTLE": 1,
            "WAIT_SCHEDULE": 1,
        },
    }
    assert {item["action"] for item in plan["items"]} == {
        "SETTLE",
        "REQUIRE_APPROVAL",
        "REMEDIATE",
        "WAIT_SCHEDULE",
    }
    if seed_date is not None:
        scheduled = showcase["items"][3]
        assert scheduled["invoice"]["due_date"] == (seed_date + timedelta(days=20)).isoformat()
        assert scheduled["decision"]["scheduled_for"] == (seed_date + timedelta(days=17)).isoformat()


def test_showcase_requires_decision_run_permission(tmp_path):
    app = create_app(database_path=tmp_path / "showcase-auth.sqlite3", testing=True)
    client = app.test_client()
    auditor = session(client, "auditor")

    denied = client.post(
        "/api/demo/autonomy-showcase",
        headers=headers(auditor, "showcase-denied"),
    )
    assert denied.status_code == 403


def test_existing_demo_schedule_stays_sealed_when_new_samples_use_a_later_date(tmp_path):
    database = tmp_path / "durable-schedule.sqlite3"
    clock = {"today": date(2026, 9, 20)}
    app = create_app(database_path=database, testing=True, date_provider=lambda: clock["today"])
    client = app.test_client()
    operator = session(client, "operator")
    auditor = session(client, "auditor")
    original = client.post(
        "/api/demo/scenarios/scheduled-payment/run",
        headers=headers(operator, "old-demo-seed"),
    ).get_json()
    assert original["decision"]["scheduled_for"] == "2026-10-07"
    app.extensions["tallyguard_repository"].close()

    clock["today"] = date(2027, 1, 1)
    app = create_app(database_path=database, testing=True, date_provider=lambda: clock["today"])
    client = app.test_client()
    fresh = client.post(
        "/api/demo/scenarios/scheduled-payment/run",
        headers=headers(operator, "fresh-demo-seed"),
    ).get_json()
    assert fresh["decision"]["scheduled_for"] == "2027-01-18"
    stored_decision = client.get(
        f"/api/decisions/{original['decision']['id']}",
        headers=headers(auditor, "old-decision-read"),
    )
    assert stored_decision.status_code == 200
    assert stored_decision.get_json()["decision"] == original["decision"]
    replay = client.get(
        f"/api/decisions/{original['decision']['id']}/replay",
        headers=headers(auditor, "old-decision-replay"),
    )
    assert replay.status_code == 200
    assert replay.get_json()["verification"]["verified"] is True

    plan = client.post(
        "/api/agent-runs", json={},
        headers=headers(operator, "old-and-new-plan"),
    ).get_json()["agent_run"]
    assert {item["invoice_id"]: item["action"] for item in plan["items"]} == {
        original["invoice"]["id"]: "RELEASE_SCHEDULE",
        fresh["invoice"]["id"]: "WAIT_SCHEDULE",
    }
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 0

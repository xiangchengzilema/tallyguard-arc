from tallyguard.api import create_app


def headers(token: str, correlation_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Correlation-ID": correlation_id,
    }


def session(client, role: str) -> str:
    return client.post("/api/demo/session", json={"role": role}).get_json()["access_token"]


def test_showcase_seeds_mixed_queue_for_one_click_agent_planning(tmp_path):
    app = create_app(database_path=tmp_path / "showcase.sqlite3", testing=True)
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


def test_showcase_requires_decision_run_permission(tmp_path):
    app = create_app(database_path=tmp_path / "showcase-auth.sqlite3", testing=True)
    client = app.test_client()
    auditor = session(client, "auditor")

    denied = client.post(
        "/api/demo/autonomy-showcase",
        headers=headers(auditor, "showcase-denied"),
    )
    assert denied.status_code == 403

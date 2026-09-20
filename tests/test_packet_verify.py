import copy

from tallyguard.api import create_app
from tallyguard.packet_verify import verify_evidence_packet


def headers(token: str, correlation_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Correlation-ID": correlation_id,
    }


def settled_packet(tmp_path):
    app = create_app(database_path=tmp_path / "packet-verifier.sqlite3", testing=True)
    client = app.test_client()
    operator = client.post("/api/demo/session", json={"role": "operator"}).get_json()[
        "access_token"
    ]
    approver = client.post("/api/demo/session", json={"role": "approver"}).get_json()[
        "access_token"
    ]
    auditor = client.post("/api/demo/session", json={"role": "auditor"}).get_json()[
        "access_token"
    ]
    run = client.post(
        "/api/demo/scenarios/clean-payment/run",
        headers=headers(operator, "verify-run"),
    ).get_json()
    settled = client.post(
        f"/api/invoices/{run['invoice']['id']}/settle",
        json={"decision_id": run["decision"]["id"]},
        headers=headers(approver, "verify-settle"),
    )
    assert settled.status_code == 200
    response = client.get(
        f"/api/invoices/{run['invoice']['id']}/evidence-packet",
        headers=headers(auditor, "verify-export"),
    )
    assert response.status_code == 200
    envelope = response.get_json()
    app.extensions["tallyguard_repository"].close()
    return envelope


def test_standalone_packet_verifier_replays_decision_and_bindings(tmp_path):
    result = verify_evidence_packet(settled_packet(tmp_path))

    assert result.verified is True
    assert {check.code for check in result.checks} >= {
        "PACKET_CONTENT_HASH",
        "DETERMINISTIC_RULE_TRACE",
        "DECISION_ID",
        "PAYMENT_BINDING",
        "AUDIT_EVENT_HASHES",
    }
    assert all(check.passed for check in result.checks)


def test_packet_verifier_rejects_changed_payment_recipient(tmp_path):
    envelope = settled_packet(tmp_path)
    tampered = copy.deepcopy(envelope)
    tampered["packet"]["payment"]["receipt"]["confirmed_recipient"] = (
        "0x9999999999999999999999999999999999999999"
    )

    result = verify_evidence_packet(tampered)

    assert result.verified is False
    failed = {check.code for check in result.checks if not check.passed}
    assert "PACKET_CONTENT_HASH" in failed
    assert "PAYMENT_BINDING" in failed


def test_packet_verifier_rejects_tampered_sealed_policy(tmp_path):
    envelope = settled_packet(tmp_path)
    tampered = copy.deepcopy(envelope)
    tampered["packet"]["decision"]["sealed_replay_inputs"]["policy"][
        "maximum_autonomous_payment_usdc"
    ] = "1"

    result = verify_evidence_packet(tampered)

    assert result.verified is False
    failed = {check.code for check in result.checks if not check.passed}
    assert "PACKET_CONTENT_HASH" in failed
    assert "REPLAY_INPUT_HASH" in failed
    assert "POLICY_CONTENT_HASH" in failed
    assert "DECISION_ACTION" in failed

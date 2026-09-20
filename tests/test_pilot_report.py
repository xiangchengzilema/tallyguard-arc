import copy

import pytest

from tallyguard.api import create_app
from tallyguard.audit import canonical_json
from tallyguard.pilot_report import (
    PilotReportError,
    build_pilot_report,
    render_pilot_markdown,
)


def headers(token: str, correlation_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Correlation-ID": correlation_id,
    }


def settled_packet(tmp_path):
    app = create_app(database_path=tmp_path / "pilot.sqlite3", testing=True)
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
        headers=headers(operator, "pilot-run"),
    ).get_json()
    settled = client.post(
        f"/api/invoices/{run['invoice']['id']}/settle",
        json={"decision_id": run["decision"]["id"]},
        headers=headers(approver, "pilot-settle"),
    )
    assert settled.status_code == 200
    packet = client.get(
        f"/api/invoices/{run['invoice']['id']}/evidence-packet",
        headers=headers(auditor, "pilot-packet"),
    ).get_json()
    app.extensions["tallyguard_repository"].close()
    return packet


def valid_attestation():
    return {
        "schema_version": "1.0",
        "pilot_id": "pilot-self-001",
        "classification": "self-operated",
        "organization_alias": "operator-a",
        "operator_role": "owner-operator",
        "started_at": "2026-09-20T09:00:00+08:00",
        "completed_at": "2026-09-20T09:05:00+08:00",
        "workflow": {
            "baseline_process": "Manual invoice, PO, and receipt comparison.",
            "baseline_minutes": "12",
            "tallyguard_minutes": "5",
            "acceptance_criteria": [
                "Decision matches the written policy.",
                "Evidence packet verifies offline.",
            ],
            "criteria_met": [
                "Decision matches the written policy.",
                "Evidence packet verifies offline.",
            ],
        },
        "consent": {
            "authorized_for_anonymized_hackathon_reporting": True,
            "attested_by": "owner-operator",
            "attested_at": "2026-09-20T09:06:00+08:00",
        },
    }


def test_pilot_report_binds_attestation_to_verified_packet_without_overclaiming(tmp_path):
    report = build_pilot_report(settled_packet(tmp_path), valid_attestation())

    body = report["report"]
    assert report["report_id"].startswith("pilot_report_")
    assert len(report["report_sha256"]) == 64
    assert body["usage_measurement"]["time_saved_minutes"] == "7"
    assert body["usage_measurement"]["time_saved_percent"] == "58.3"
    assert body["usage_measurement"]["criteria_completion"] == "2/2"
    assert body["product_evidence"]["packet_verified"] is True
    assert body["product_evidence"]["settlement_evidence"] == "simulation"
    assert body["product_evidence"]["funds_claim"] == "no funds moved"
    assert body["usage_measurement"]["independently_verified_customer_claim"] is False
    assert len(body["attestation"]["sha256"]) == 64
    assert "not an external customer claim" in render_pilot_markdown(report)


def test_pilot_report_rejects_tampered_packet_and_missing_consent(tmp_path):
    packet = settled_packet(tmp_path)
    tampered = copy.deepcopy(packet)
    tampered["packet"]["invoice"]["status"] = "READY"
    with pytest.raises(PilotReportError, match="verification failed"):
        build_pilot_report(tampered, valid_attestation())

    attestation = valid_attestation()
    attestation["consent"]["authorized_for_anonymized_hackathon_reporting"] = False
    with pytest.raises(PilotReportError, match="consent is required"):
        build_pilot_report(packet, attestation)


def test_pilot_report_is_content_addressed_and_requires_exact_criteria(tmp_path):
    packet = settled_packet(tmp_path)
    attestation = valid_attestation()
    first = build_pilot_report(packet, attestation)
    second = build_pilot_report(packet, attestation)
    assert canonical_json(first) == canonical_json(second)

    attestation["workflow"]["criteria_met"] = ["A criterion chosen after the run."]
    with pytest.raises(PilotReportError, match="exactly match"):
        build_pilot_report(packet, attestation)

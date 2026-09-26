"""Public activity exposes real-run progress without private wallet controls."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random

import pytest

from tallyguard.api import create_app
from tallyguard.public_activity import ActivityFeedError, public_activity_snapshot, published_activity_snapshot
from tallyguard.testnet_campaign import make_plan


def test_checked_in_final_campaign_snapshot_is_complete():
    snapshot = published_activity_snapshot(
        Path(__file__).resolve().parents[1] / "docs" / "reports" / "arc-testnet-public-activity.json"
    )
    assert snapshot["network"] == "ARC-TESTNET"
    assert (snapshot["planned"], snapshot["processed"], snapshot["queued"]) == (50, 50, 0)
    assert (snapshot["confirmed_payments"], snapshot["declined"], snapshot["held"]) == (40, 5, 5)
    assert snapshot["confirmed_principal_usdc"] == "0.40"
    assert len(snapshot["entries"]) == len({item["invoice_number"] for item in snapshot["entries"]}) == 50
    hashes = [item["transaction_hash"] for item in snapshot["entries"] if item["status"] == "PAID"]
    assert len(hashes) == len(set(hashes)) == 40


def _sources(tmp_path):
    plan = make_plan(
        now=datetime(2026, 9, 25, 12, tzinfo=timezone.utc),
        treasury="0x" + "f" * 40,
        recipients=tuple(f"0x{index:040x}" for index in range(1, 38)),
        rng=random.Random(55),
        campaign_id="public-feed",
    )
    first = plan["jobs"][0]
    report = {
        "campaign_id": plan["campaign_id"],
        "network": "ARC-TESTNET",
        "plan_sha256": plan["plan_sha256"],
        "updated_at": (datetime.fromisoformat(first["submit_at"]) + timedelta(minutes=1)).isoformat(),
        "planned_agents": 50,
        "processed_agents": 1,
        "counts": {"PAID": 1, "DECLINED": 0, "HELD": 0, "AWAITING_REVIEW": 0},
        "confirmed_transfer_principal_usdc_excluding_fees": "0.01",
        "items": [{
            "agent_id": first["agent_id"],
            "scenario": first["scenario"],
            "status": "PAID",
            "invoice_id": first["invoice_id"],
            "transaction_hash": "0x" + "a" * 64,
            "block_number": 600001,
            "amount_usdc": "0.01",
        }],
    }
    plan_path = tmp_path / "plan.json"
    report_path = tmp_path / "report.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return plan, report, plan_path, report_path


def test_public_activity_has_exact_statuses_but_no_wallets_or_private_fields(tmp_path):
    plan, _report, plan_path, report_path = _sources(tmp_path)
    snapshot = public_activity_snapshot(
        plan_path=plan_path,
        report_path=report_path,
        now=datetime(2026, 9, 25, 12, tzinfo=timezone.utc),
    )
    assert snapshot["planned"] == 50
    assert snapshot["processed"] == 1
    assert snapshot["confirmed_payments"] == 1
    assert snapshot["queued"] == 49
    assert snapshot["confirmed_principal_usdc"] == "0.01"
    assert len(snapshot["entries"]) == 50
    paid = next(entry for entry in snapshot["entries"] if entry["status"] == "PAID")
    assert paid["id"] == "FLOW-001"
    assert paid["invoice_number"] == "TG-20260925-001"
    assert paid["route"] == "AUTOMATIC"
    assert paid["explorer_url"] == "https://explorer.testnet.arc.io/tx/" + "0x" + "a" * 64
    serialized = json.dumps(snapshot)
    assert plan["treasury"] not in serialized
    assert all(job["recipient"] not in serialized for job in plan["jobs"])
    assert "agent-" not in serialized.lower()
    assert "AGENT50" not in serialized
    assert "CIRCLE_" not in serialized


def test_public_activity_fails_closed_on_false_payment_or_modified_plan(tmp_path):
    plan, report, plan_path, report_path = _sources(tmp_path)
    report["items"][0]["amount_usdc"] = "0.10"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ActivityFeedError, match="exact Arc receipt"):
        public_activity_snapshot(plan_path=plan_path, report_path=report_path)
    report["items"][0]["amount_usdc"] = "0.01"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    plan["jobs"][0]["amount_usdc"] = "1.00"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ActivityFeedError, match="locked Arc Testnet campaign"):
        public_activity_snapshot(plan_path=plan_path, report_path=report_path)


def test_public_activity_rejects_inconsistent_counts_and_bad_principal(tmp_path):
    _plan, report, plan_path, report_path = _sources(tmp_path)
    report["counts"] = None
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ActivityFeedError, match="counts"):
        public_activity_snapshot(plan_path=plan_path, report_path=report_path)
    report["counts"] = {"PAID": 1, "DECLINED": 0, "HELD": 0, "AWAITING_REVIEW": 0}
    report["confirmed_transfer_principal_usdc_excluding_fees"] = "not-a-number"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ActivityFeedError, match="principal"):
        public_activity_snapshot(plan_path=plan_path, report_path=report_path)


def test_public_route_is_read_only_and_unavailable_without_sources(tmp_path, monkeypatch):
    _plan, _report, plan_path, report_path = _sources(tmp_path)
    monkeypatch.setenv("TALLYGUARD_PUBLIC_ACTIVITY_PLAN", str(plan_path))
    monkeypatch.setenv("TALLYGUARD_PUBLIC_ACTIVITY_REPORT", str(report_path))
    monkeypatch.setenv("TALLYGUARD_PUBLIC_ACTIVITY_SNAPSHOT", str(tmp_path / "missing-snapshot.json"))
    app = create_app(database_path=tmp_path / "web.sqlite3", testing=True)
    try:
        with app.test_client() as client:
            response = client.get("/api/public/arc-activity")
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert response.get_json()["confirmed_payments"] == 1
            operation = client.get("/api/openapi.json").get_json()["paths"]["/api/public/arc-activity"]["get"]
            assert "security" not in operation
            monkeypatch.setenv("TALLYGUARD_PUBLIC_ACTIVITY_REPORT", str(tmp_path / "missing.json"))
            missing = client.get("/api/public/arc-activity")
            assert missing.status_code == 200
            assert missing.get_json() == {"available": False}
    finally:
        app.extensions["tallyguard_repository"].close()


def test_published_snapshot_fallback_is_redacted_and_read_only(tmp_path, monkeypatch):
    plan, _report, plan_path, report_path = _sources(tmp_path)
    snapshot_path = tmp_path / "published.json"
    snapshot_path.write_text(
        json.dumps(public_activity_snapshot(plan_path=plan_path, report_path=report_path)),
        encoding="utf-8",
    )
    monkeypatch.setenv("TALLYGUARD_PUBLIC_ACTIVITY_PLAN", str(tmp_path / "private-plan-missing.json"))
    monkeypatch.setenv("TALLYGUARD_PUBLIC_ACTIVITY_REPORT", str(tmp_path / "private-report-missing.json"))
    monkeypatch.setenv("TALLYGUARD_PUBLIC_ACTIVITY_SNAPSHOT", str(snapshot_path))
    app = create_app(database_path=tmp_path / "web.sqlite3", testing=True)
    try:
        with app.test_client() as client:
            response = client.get("/api/public/arc-activity")
            assert response.status_code == 200
            assert response.get_json()["confirmed_payments"] == 1
            assert plan["treasury"] not in response.get_data(as_text=True)
            assert all(job["recipient"] not in response.get_data(as_text=True) for job in plan["jobs"])
    finally:
        app.extensions["tallyguard_repository"].close()


def test_public_route_uses_service_working_directory_for_checked_in_snapshot(tmp_path, monkeypatch):
    _plan, _report, plan_path, report_path = _sources(tmp_path)
    published = tmp_path / "docs" / "reports" / "arc-testnet-public-activity.json"
    published.parent.mkdir(parents=True)
    published.write_text(
        json.dumps(public_activity_snapshot(plan_path=plan_path, report_path=report_path)),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TALLYGUARD_PUBLIC_ACTIVITY_SNAPSHOT", raising=False)
    app = create_app(database_path=tmp_path / "web.sqlite3", testing=True)
    try:
        with app.test_client() as client:
            response = client.get("/api/public/arc-activity")
            assert response.status_code == 200
            assert response.get_json()["confirmed_payments"] == 1
    finally:
        app.extensions["tallyguard_repository"].close()


def test_published_snapshot_rejects_extra_fields_and_forged_receipts(tmp_path):
    _plan, _report, plan_path, report_path = _sources(tmp_path)
    payload = public_activity_snapshot(plan_path=plan_path, report_path=report_path)
    snapshot_path = tmp_path / "published.json"
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    assert published_activity_snapshot(snapshot_path)["confirmed_payments"] == 1
    payload["entries"][0]["recipient"] = "private-wallet"
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ActivityFeedError, match="unexpected entry"):
        published_activity_snapshot(snapshot_path)
    del payload["entries"][0]["recipient"]
    paid = next(entry for entry in payload["entries"] if entry["status"] == "PAID")
    paid["explorer_url"] = "https://example.com/fake"
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ActivityFeedError, match="exact Arc receipt"):
        published_activity_snapshot(snapshot_path)

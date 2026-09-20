from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import logging

from tallyguard.api import create_app
from tallyguard.audit import canonical_json
from tallyguard.auth import Principal, Role
from tallyguard.network import ArcNetwork, ArcNetworkConfig
from tallyguard.settlement import PaymentIntent, ProviderSubmission


WALLET = "0x1111111111111111111111111111111111111111"


def headers(token: str, correlation_id: str = "test-request-1") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Correlation-ID": correlation_id,
    }


def invoice_payload(invoice_id: str = "invoice-1", source_document_hash: str = "a" * 64):
    return {
        "id": invoice_id,
        "vendor_id": "vendor-1",
        "invoice_number": f"INV-{invoice_id}",
        "currency": "USDC",
        "amount": "1200.00",
        "due_date": "2026-10-08",
        "payment_wallet_address": WALLET,
        "source_document_hash": source_document_hash,
    }


def test_health_and_readiness_are_public(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    client = app.test_client()
    assert client.get("/api/health").get_json()["status"] == "ok"
    readiness = client.get("/api/readiness").get_json()
    assert readiness["database"] == "ok"
    assert readiness["evidence_analyst"] == "deterministic-evidence-analyst"
    assert readiness["settlement_mode"] == "simulation"
    assert readiness["funds_movement"] == "disabled"
    assert readiness["arc_rpc_verification"] == "simulated"
    assert readiness["mainnet_enabled"] is False
    assert readiness["demo_sessions_enabled"] is True


def test_public_pdf_judge_sample_is_served_from_built_frontend(tmp_path):
    app = create_app(database_path=tmp_path / "sample.sqlite3", testing=True)

    response = app.test_client().get("/samples/evidence/invoice.pdf")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF-")


def test_request_log_is_structured_and_omits_sensitive_request_data(tmp_path, caplog):
    app = create_app(
        database_path=tmp_path / "request-logs.sqlite3",
        testing=True,
        request_logging_enabled=True,
    )
    caplog.set_level(logging.INFO, logger=app.logger.name)

    response = app.test_client().get(
        "/api/health?private_invoice=INV-SECRET",
        headers={
            "Authorization": "Bearer super-secret-token",
            "X-Correlation-ID": "judge-request-7",
        },
    )

    assert response.headers["X-Correlation-ID"] == "judge-request-7"
    event = next(
        json.loads(record.message)
        for record in reversed(caplog.records)
        if '"event":"http_request"' in record.message
    )
    assert event == {
        "correlation_id": "judge-request-7",
        "duration_ms": event["duration_ms"],
        "endpoint": "health",
        "event": "http_request",
        "method": "GET",
        "status_code": 200,
    }
    assert event["duration_ms"] >= 0
    assert "super-secret-token" not in caplog.text
    assert "INV-SECRET" not in caplog.text


def test_untrusted_correlation_id_is_replaced(tmp_path):
    app = create_app(database_path=tmp_path / "correlation.sqlite3", testing=True)

    response = app.test_client().get(
        "/api/health",
        headers={"X-Correlation-ID": "x" * 129},
    )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"].startswith("req_")
    assert len(response.headers["X-Correlation-ID"]) == 36


def test_live_settlement_adapter_disables_demo_identities_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("TALLYGUARD_ENABLE_DEMO_SESSIONS", raising=False)

    class LiveAdapter:
        name = "circle-developer-wallets+arc-rpc"

        def submit(self, intent: PaymentIntent) -> ProviderSubmission:
            raise AssertionError("Login gate must not call settlement.")

    app = create_app(
        database_path=tmp_path / "live.sqlite3",
        settlement_adapter=LiveAdapter(),
        settlement_config=ArcNetworkConfig.for_network(ArcNetwork.TESTNET),
    )

    response = app.test_client().post("/api/demo/session", json={"role": "admin"})

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "DEMO_SESSIONS_DISABLED"


def test_metrics_report_aggregate_requests_without_financial_labels(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    client = app.test_client()
    client.get("/api/health")
    client.get("/api/readiness")

    response = client.get("/api/metrics")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["requests_total"] == 2
    assert payload["responses_by_class"] == {"2xx": 2}
    assert "tenant" not in " ".join(payload["responses_by_endpoint"]).lower()
    assert "No tenant" in payload["labels"]


def test_authenticated_endpoints_enforce_per_tenant_rate_limit(tmp_path):
    app = create_app(
        database_path=tmp_path / "api.sqlite3",
        testing=True,
        rate_limit_per_minute=2,
    )
    client = app.test_client()
    token = client.post("/api/demo/session", json={"role": "auditor"}).get_json()["access_token"]

    assert client.get("/api/invoices", headers=headers(token)).status_code == 200
    assert client.get("/api/invoices", headers=headers(token)).status_code == 200
    limited = client.get("/api/invoices", headers=headers(token))

    assert limited.status_code == 429
    assert limited.get_json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert int(limited.headers["Retry-After"]) >= 1


def test_demo_session_authentication_survives_api_restart(tmp_path):
    database = tmp_path / "api.sqlite3"
    first_app = create_app(database_path=database, testing=True)
    first_client = first_app.test_client()
    token = first_client.post(
        "/api/demo/session", json={"role": "auditor"}
    ).get_json()["access_token"]
    first_app.extensions["tallyguard_repository"].close()

    restarted_app = create_app(database_path=database, testing=True)
    response = restarted_app.test_client().get(
        "/api/invoices",
        headers=headers(token, "after-restart"),
    )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "after-restart"


def test_api_responses_include_browser_security_headers(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    response = app.test_client().get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_frontend_bundle_path_can_be_overridden_for_packaged_deployments(tmp_path, monkeypatch):
    frontend_dist = tmp_path / "frontend"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text("<h1>TallyGuard deployed</h1>", encoding="utf-8")
    monkeypatch.setenv("TALLYGUARD_FRONTEND_DIST", str(frontend_dist))

    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"TallyGuard deployed" in response.data


def test_invoice_endpoints_require_authentication(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    response = app.test_client().get("/api/invoices")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "AUTHENTICATION_DENIED"


def test_operator_creates_and_reads_tenant_invoice(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    client = app.test_client()
    session = client.post("/api/demo/session", json={"role": "operator"}).get_json()
    token = session["access_token"]
    created = client.post("/api/invoices", json=invoice_payload(), headers=headers(token))
    assert created.status_code == 201
    assert created.get_json()["invoice"]["organization_id"] == "demo-org"
    assert created.headers["X-Correlation-ID"] == "test-request-1"

    fetched = client.get("/api/invoices/invoice-1", headers=headers(token))
    assert fetched.status_code == 200
    assert fetched.get_json()["invoice"]["amount"] == "1200.00"


def test_json_evidence_upload_persists_bytes_provenance_and_audit(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    client = app.test_client()
    token = client.post("/api/demo/session", json={"role": "operator"}).get_json()["access_token"]
    content = json.dumps(
        {
            "invoice_id": "invoice-evidence",
            "vendor_id": "vendor-1",
            "invoice_number": "INV-EVIDENCE",
            "currency": "USDC",
            "amount": "1200.00",
            "due_date": "2026-10-08",
            "payment_wallet_address": WALLET,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    created = client.post(
        "/api/invoices",
        json=invoice_payload("invoice-evidence", digest),
        headers=headers(token),
    )
    assert created.status_code == 201

    uploaded = client.post(
        "/api/invoices/invoice-evidence/evidence",
        data={
            "evidence_type": "INVOICE",
            "file": (BytesIO(content), "invoice evidence.json", "application/json"),
        },
        content_type="multipart/form-data",
        headers=headers(token, "upload-evidence"),
    )

    assert uploaded.status_code == 201
    evidence = uploaded.get_json()["evidence"]
    assert evidence["content_sha256"] == digest
    assert evidence["filename"] == "invoice_evidence.json"
    assert evidence["byte_size"] == len(content)
    invoice_id_field = next(field for field in evidence["fields"] if field["name"] == "invoice_id")
    assert invoice_id_field["source"]["json_pointer"] == "/invoice_id"

    listed = client.get(
        "/api/invoices/invoice-evidence/evidence",
        headers=headers(token),
    ).get_json()["items"]
    assert [item["id"] for item in listed] == [evidence["id"]]

    downloaded = client.get(
        f"/api/evidence/{evidence['id']}/content",
        headers=headers(token),
    )
    assert downloaded.status_code == 200
    assert downloaded.data == content
    assert downloaded.mimetype == "application/json"
    assert "attachment" in downloaded.headers["Content-Disposition"]

    repository = app.extensions["tallyguard_repository"]
    repository.create_organization(organization_id="evidence-other", name="Evidence Other")
    repository.create_user(
        organization_id="evidence-other",
        user_id="other-auditor",
        display_name="Other Auditor",
        roles=(Role.AUDITOR.value,),
    )
    other_token, _ = app.extensions["tallyguard_authenticator"].issue_session(
        Principal(
            user_id="other-auditor",
            organization_id="evidence-other",
            roles=(Role.AUDITOR,),
        )
    )
    cross_tenant = client.get(
        f"/api/evidence/{evidence['id']}/content",
        headers=headers(other_token),
    )
    assert cross_tenant.status_code == 404

    audit = client.get("/api/audit/events", headers=headers(token)).get_json()
    assert audit["chain_valid"] is True
    assert audit["items"][-1]["event_type"] == "EVIDENCE_INGESTED"


def test_evidence_upload_rejects_bad_signature_and_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLYGUARD_MAX_EVIDENCE_BYTES", "64")
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    client = app.test_client()
    token = client.post("/api/demo/session", json={"role": "operator"}).get_json()["access_token"]
    assert client.post(
        "/api/invoices",
        json=invoice_payload("invoice-limits"),
        headers=headers(token),
    ).status_code == 201
    fields = json.dumps(
        [
            {
                "name": "purchase_order_id",
                "normalized_value": "po-1",
                "confidence": "1",
                "method": "OCR",
                "source": {"page_number": 1, "bounding_box": [0.1, 0.1, 0.9, 0.2]},
            }
        ]
    )

    bad_signature = client.post(
        "/api/invoices/invoice-limits/evidence",
        data={
            "evidence_type": "PURCHASE_ORDER",
            "fields": fields,
            "file": (BytesIO(b"not a pdf"), "po.pdf", "application/pdf"),
        },
        content_type="multipart/form-data",
        headers=headers(token),
    )
    assert bad_signature.status_code == 400
    assert bad_signature.get_json()["error"]["code"] == "VALIDATION_ERROR"

    oversized = client.post(
        "/api/invoices/invoice-limits/evidence",
        data={
            "evidence_type": "PURCHASE_ORDER",
            "fields": fields,
            "file": (BytesIO(b"%PDF-" + b"x" * 100), "large.pdf", "application/pdf"),
        },
        content_type="multipart/form-data",
        headers=headers(token),
    )
    assert oversized.status_code == 413
    assert oversized.get_json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_real_evidence_policy_and_treasury_produce_idempotent_pay_decision(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    client = app.test_client()
    admin = client.post(
        "/api/demo/session", json={"role": "admin"}
    ).get_json()["access_token"]
    operator = client.post(
        "/api/demo/session", json={"role": "operator"}
    ).get_json()["access_token"]
    auditor = client.post(
        "/api/demo/session", json={"role": "auditor"}
    ).get_json()["access_token"]
    vendor_wallet = "0x2222222222222222222222222222222222222222"

    assert client.post(
        "/api/policies",
        json={
            "version": "payables-v1",
            "daily_payment_limit_usdc": "5000",
            "minimum_cash_reserve_usdc": "1000",
            "maximum_autonomous_payment_usdc": "2000",
            "po_amount_tolerance_usdc": "0",
            "allowed_asset": "USDC",
            "allowed_network": "ARC-TESTNET",
            "kill_switch_enabled": False,
        },
        headers=headers(admin),
    ).status_code == 201
    assert client.post(
        "/api/treasury/snapshots",
        json={
            "available_usdc": "10000",
            "spent_today_usdc": "500",
            "source_reference": "circle-test-wallet-balance-001",
        },
        headers=headers(operator),
    ).status_code == 201
    assert client.post(
        "/api/vendors",
        json={
            "id": "vendor-live",
            "legal_name": "Live Evidence Vendor",
            "approved_wallet_address": vendor_wallet,
            "autopay_limit": "2000",
            "verification_method": "SIGNED_CHALLENGE",
            "verification_reference": "wallet-proof-001",
        },
        headers=headers(operator),
    ).status_code == 201

    invoice_document = json.dumps(
        {
            "invoice_id": "invoice-live",
            "vendor_id": "vendor-live",
            "invoice_number": "INV-LIVE-001",
            "currency": "USDC",
            "amount": "1200",
            "due_date": "2026-10-08",
            "payment_wallet_address": vendor_wallet,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    assert client.post(
        "/api/invoices",
        json={
            "id": "invoice-live",
            "vendor_id": "vendor-live",
            "invoice_number": "INV-LIVE-001",
            "currency": "USDC",
            "amount": "1200",
            "due_date": "2026-10-08",
            "payment_wallet_address": vendor_wallet,
            "source_document_hash": hashlib.sha256(invoice_document).hexdigest(),
        },
        headers=headers(operator),
    ).status_code == 201

    evidence_documents = (
        ("INVOICE", "invoice.json", invoice_document),
        (
            "PURCHASE_ORDER",
            "purchase-order.json",
            json.dumps(
                {
                    "purchase_order_id": "po-live-001",
                    "vendor_id": "vendor-live",
                    "po_number": "PO-LIVE-001",
                    "currency": "USDC",
                    "authorized_amount": "1200",
                },
                separators=(",", ":"),
            ).encode("utf-8"),
        ),
        (
            "DELIVERY",
            "delivery.json",
            json.dumps(
                {
                    "delivery_id": "delivery-live-001",
                    "purchase_order_id": "po-live-001",
                    "delivered_value": "1200",
                },
                separators=(",", ":"),
            ).encode("utf-8"),
        ),
    )
    for evidence_type, filename, content in evidence_documents:
        uploaded = client.post(
            "/api/invoices/invoice-live/evidence",
            data={
                "evidence_type": evidence_type,
                "file": (BytesIO(content), filename, "application/json"),
            },
            content_type="multipart/form-data",
            headers=headers(operator),
        )
        assert uploaded.status_code == 201

    first = client.post(
        "/api/invoices/invoice-live/evaluate", headers=headers(operator, "evaluate-1")
    )
    assert first.status_code == 200
    result = first.get_json()
    assert result["decision"]["final_action"] == "PAY"
    assert result["invoice"]["status"] == "READY"
    assert result["decision"]["reason_codes"] == []
    assert result["decision"]["agent_recommendation"]["action"] == "PAY"
    assert result["decision"]["agent_recommendation"]["evidence_refs"] == [
        "package:invoice-live",
        result["decision"]["evidence_manifest_hash"],
    ]

    assert client.post(
        "/api/policies",
        json={
            "version": "payables-v2-emergency-stop",
            "daily_payment_limit_usdc": "5000",
            "minimum_cash_reserve_usdc": "1000",
            "maximum_autonomous_payment_usdc": "2000",
            "po_amount_tolerance_usdc": "0",
            "allowed_asset": "USDC",
            "allowed_network": "ARC-TESTNET",
            "kill_switch_enabled": True,
        },
        headers=headers(admin),
    ).status_code == 201

    repeated = client.post(
        "/api/invoices/invoice-live/evaluate", headers=headers(operator, "evaluate-2")
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["decision"]["id"] == result["decision"]["id"]
    assert repeated.get_json()["decision"]["policy_version"] == "payables-v1"
    fetched = client.get(
        f"/api/decisions/{result['decision']['id']}", headers=headers(auditor)
    )
    assert fetched.status_code == 200
    assert fetched.get_json()["decision"]["evidence_manifest_hash"] == result[
        "decision"
    ]["evidence_manifest_hash"]
    assert fetched.get_json()["decision"]["replayable"] is True
    replayed = client.get(
        f"/api/decisions/{result['decision']['id']}/replay",
        headers=headers(auditor, "replay-1"),
    )
    assert replayed.status_code == 200
    verification = replayed.get_json()["verification"]
    assert verification["verified"] is True
    assert verification["replayed_decision_id"] == result["decision"]["id"]
    assert all(check["passed"] for check in verification["checks"])

    locked = client.post(
        "/api/invoices/invoice-live/evidence",
        data={
            "evidence_type": "DELIVERY",
            "file": (BytesIO(b'{"delivery_id":"other"}'), "other.json", "application/json"),
        },
        content_type="multipart/form-data",
        headers=headers(operator),
    )
    assert locked.status_code == 409
    assert locked.get_json()["error"]["code"] == "WORKFLOW_ERROR"


def test_auditor_cannot_create_invoice(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    client = app.test_client()
    token = client.post("/api/demo/session", json={"role": "auditor"}).get_json()["access_token"]
    response = client.post("/api/invoices", json=invoice_payload(), headers=headers(token))
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "AUTHORIZATION_DENIED"


def test_operations_overview_aggregates_persisted_work_queue_by_tenant(tmp_path):
    app = create_app(database_path=tmp_path / "operations.sqlite3", testing=True)
    client = app.test_client()
    operator = client.post(
        "/api/demo/session", json={"role": "operator"}
    ).get_json()["access_token"]
    auditor = client.post(
        "/api/demo/session", json={"role": "auditor"}
    ).get_json()["access_token"]
    results = {}
    for key in ("clean-payment", "wallet-change", "duplicate-invoice"):
        response = client.post(
            f"/api/demo/scenarios/{key}/run",
            headers=headers(operator, f"run-{key}"),
        )
        assert response.status_code == 200
        results[key] = response.get_json()

    response = client.get(
        "/api/operations/overview?as_of=2026-10-05&queue_limit=10",
        headers=headers(auditor, "overview-1"),
    )
    assert response.status_code == 200
    overview = response.get_json()["overview"]
    clean_amount = Decimal(results["clean-payment"]["invoice"]["amount"])
    wallet_amount = Decimal(results["wallet-change"]["invoice"]["amount"])
    duplicate_amount = Decimal(results["duplicate-invoice"]["invoice"]["amount"])
    assert overview["invoice_count"] == 3
    assert overview["status_counts"]["READY"] == 1
    assert overview["status_counts"]["HOLD"] == 1
    assert overview["status_counts"]["REJECTED"] == 1
    assert Decimal(overview["open_exposure_usdc"]) == clean_amount + wallet_amount
    assert Decimal(overview["blocked_exposure_usdc"]) == wallet_amount + duplicate_amount
    assert {item["status"] for item in overview["work_queue"]} == {"READY", "HOLD"}
    assert overview["treasury_available_usdc"] == "10000"
    assert overview["minimum_reserve_usdc"] == "3000"


def test_reliability_report_is_auditor_visible_and_content_addressed(tmp_path):
    app = create_app(database_path=tmp_path / "reliability.sqlite3", testing=True)
    client = app.test_client()
    auditor = client.post(
        "/api/demo/session", json={"role": "auditor"}
    ).get_json()["access_token"]
    denied = client.get(
        "/api/reliability/report",
        headers={"X-Correlation-ID": "reliability-denied"},
    )
    assert denied.status_code == 401

    response = client.get(
        "/api/reliability/report",
        headers=headers(auditor, "reliability-read"),
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["report"]["summary"]["successful_workflows"] == 10_000
    assert payload["report"]["summary"]["duplicate_payment_count"] == 0
    assert payload["report"]["configuration"]["slow_provider_delay_ms"] == 500
    assert payload["report"]["summary"]["duplicate_storm_delayed_provider_attempts"] == 1
    assert payload["report"]["summary"]["slow_provider_idempotency_preserved"] is True
    assert payload["report"]["methodology"]["classification"] == (
        "synthetic multi-tenant engineering load test"
    )
    assert len(payload["artifact"]["sha256"]) == 64
    assert payload["artifact"]["immutable"] is True
    assert payload["agent_report"]["summary"]["successful_agent_workflows"] == 50
    assert payload["agent_report"]["summary"]["orchestration_single_execution_preserved"] is True
    assert payload["agent_report"]["summary"]["cross_tenant_attempts_denied"] == 50
    assert len(payload["agent_artifact"]["sha256"]) == 64
    assert payload["agent_artifact"]["immutable"] is True


def test_payment_evidence_packet_binds_replay_settlement_and_audit(tmp_path):
    app = create_app(database_path=tmp_path / "packet.sqlite3", testing=True)
    client = app.test_client()
    operator = client.post(
        "/api/demo/session", json={"role": "operator"}
    ).get_json()["access_token"]
    approver = client.post(
        "/api/demo/session", json={"role": "approver"}
    ).get_json()["access_token"]
    auditor = client.post(
        "/api/demo/session", json={"role": "auditor"}
    ).get_json()["access_token"]

    run = client.post(
        "/api/demo/scenarios/clean-payment/run",
        headers=headers(operator, "packet-run"),
    ).get_json()
    before_settlement = client.get(
        f"/api/invoices/{run['invoice']['id']}/evidence-packet",
        headers=headers(auditor, "packet-before-settlement"),
    )
    assert before_settlement.status_code == 200
    assert before_settlement.get_json()["packet"]["payment"] is None

    settled = client.post(
        f"/api/invoices/{run['invoice']['id']}/settle",
        json={"decision_id": run["decision"]["id"]},
        headers=headers(approver, "packet-settle"),
    )
    assert settled.status_code == 200

    response = client.get(
        f"/api/invoices/{run['invoice']['id']}/evidence-packet",
        headers=headers(auditor, "packet-download"),
    )
    assert response.status_code == 200
    assert response.headers["Content-Disposition"].endswith("-evidence-packet.json\"")
    envelope = response.get_json()
    packet = envelope["packet"]
    recomputed = hashlib.sha256(canonical_json(packet).encode("utf-8")).hexdigest()
    assert envelope["packet_sha256"] == recomputed
    assert envelope["packet_sha256"] != before_settlement.get_json()["packet_sha256"]
    assert response.headers["X-TallyGuard-Packet-SHA256"] == recomputed
    assert packet["invoice"]["status"] == "RECONCILED"
    assert packet["decision"]["sealed_replay_inputs"] is not None
    assert packet["replay_verification"]["verified"] is True
    assert len(packet["replay_verification"]["checks"]) == 11
    assert packet["payment"]["receipt"]["status"] == "CONFIRMED"
    assert packet["audit"]["tenant_chain_valid"] is True
    assert packet["audit"]["last_invoice_event_hash"] is not None


def test_policy_simulation_reuses_sealed_inputs_without_mutating_decision(tmp_path):
    app = create_app(database_path=tmp_path / "simulation.sqlite3", testing=True)
    client = app.test_client()
    operator = client.post(
        "/api/demo/session", json={"role": "operator"}
    ).get_json()["access_token"]
    admin = client.post(
        "/api/demo/session", json={"role": "admin"}
    ).get_json()["access_token"]
    run = client.post(
        "/api/demo/scenarios/clean-payment/run",
        headers=headers(operator, "simulate-run"),
    ).get_json()
    decision_id = run["decision"]["id"]

    denied = client.post(
        f"/api/decisions/{decision_id}/policy-simulation",
        json={"kill_switch_enabled": True},
        headers=headers(operator, "simulate-denied"),
    )
    assert denied.status_code == 403

    capped = client.post(
        f"/api/decisions/{decision_id}/policy-simulation",
        json={"maximum_autonomous_payment_usdc": "500"},
        headers=headers(admin, "simulate-cap"),
    )
    assert capped.status_code == 200
    cap_result = capped.get_json()["simulation"]
    assert cap_result["persisted"] is False
    assert cap_result["original_action"] == "PAY"
    assert cap_result["simulated_action"] == "ESCALATE"
    assert "AUTONOMY_LIMIT_EXCEEDED" in cap_result["reason_codes"]

    stopped = client.post(
        f"/api/decisions/{decision_id}/policy-simulation",
        json={"kill_switch_enabled": True},
        headers=headers(admin, "simulate-stop"),
    )
    assert stopped.status_code == 200
    assert stopped.get_json()["simulation"]["simulated_action"] == "HOLD"

    invalid_decimal = client.post(
        f"/api/decisions/{decision_id}/policy-simulation",
        json={"maximum_autonomous_payment_usdc": "not-a-number"},
        headers=headers(admin, "simulate-invalid-decimal"),
    )
    assert invalid_decimal.status_code == 400

    unknown_field = client.post(
        f"/api/decisions/{decision_id}/policy-simulation",
        json={"recipient_wallet": "0x0000000000000000000000000000000000000000"},
        headers=headers(admin, "simulate-unknown"),
    )
    assert unknown_field.status_code == 400

    original = client.get(
        f"/api/decisions/{decision_id}",
        headers=headers(operator, "simulate-original"),
    ).get_json()["decision"]
    assert original["final_action"] == "PAY"
    assert original["policy_version"] == run["decision"]["policy_version"]


def test_batch_settlement_isolates_failures_and_reuses_each_receipt(tmp_path):
    app = create_app(database_path=tmp_path / "batch.sqlite3", testing=True)
    client = app.test_client()
    operator = client.post(
        "/api/demo/session", json={"role": "operator"}
    ).get_json()["access_token"]
    approver = client.post(
        "/api/demo/session", json={"role": "approver"}
    ).get_json()["access_token"]
    auditor = client.post(
        "/api/demo/session", json={"role": "auditor"}
    ).get_json()["access_token"]
    runs = []
    for index, key in enumerate(("clean-payment", "clean-payment", "wallet-change")):
        response = client.post(
            f"/api/demo/scenarios/{key}/run",
            headers=headers(operator, f"batch-seed-{index}"),
        )
        assert response.status_code == 200
        runs.append(response.get_json())
    batch_items = [
        {
            "invoice_id": run["invoice"]["id"],
            "decision_id": run["decision"]["id"],
        }
        for run in runs
    ]

    first = client.post(
        "/api/payment-batches/settle",
        json={"items": batch_items},
        headers=headers(approver, "batch-first"),
    )
    assert first.status_code == 207
    first_batch = first.get_json()["batch"]
    assert first_batch["requested"] == 3
    assert first_batch["succeeded"] == 2
    assert first_batch["failed"] == 1
    assert [item["status"] for item in first_batch["results"]] == [
        "SETTLED",
        "SETTLED",
        "FAILED",
    ]
    assert all(
        item["payment"]["reused_receipt"] is False
        for item in first_batch["results"][:2]
    )

    repeated = client.post(
        "/api/payment-batches/settle",
        json={"items": batch_items},
        headers=headers(approver, "batch-retry"),
    )
    assert repeated.status_code == 207
    repeated_batch = repeated.get_json()["batch"]
    assert repeated_batch["succeeded"] == 2
    assert all(
        item["payment"]["reused_receipt"] is True
        for item in repeated_batch["results"][:2]
    )

    overview = client.get(
        "/api/operations/overview",
        headers=headers(auditor, "batch-overview"),
    ).get_json()["overview"]
    assert overview["status_counts"]["RECONCILED"] == 2
    assert overview["status_counts"]["HOLD"] == 1
    assert len(overview["work_queue"]) == 1


def test_vendor_wallet_verification_history_is_durable_tenant_scoped_and_audited(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    client = app.test_client()
    operator = client.post(
        "/api/demo/session", json={"role": "operator"}
    ).get_json()["access_token"]
    auditor = client.post(
        "/api/demo/session", json={"role": "auditor"}
    ).get_json()["access_token"]
    original_wallet = "0x2222222222222222222222222222222222222222"
    replacement_wallet = "0x3333333333333333333333333333333333333333"

    created = client.post(
        "/api/vendors",
        json={
            "id": "vendor-verified",
            "legal_name": "Verified Supplies Ltd",
            "approved_wallet_address": original_wallet,
            "autopay_limit": "2500.00",
            "risk_tier": "low",
            "verification_method": "signed_challenge",
            "verification_reference": "challenge-2026-09-20-001",
        },
        headers=headers(operator, "vendor-onboard"),
    )
    assert created.status_code == 201
    assert created.get_json()["vendor"]["organization_id"] == "demo-org"

    forbidden = client.post(
        "/api/vendors",
        json={
            "id": "auditor-vendor",
            "legal_name": "Must Fail",
            "approved_wallet_address": original_wallet,
            "autopay_limit": "1",
            "verification_method": "MANUAL_REVIEW",
            "verification_reference": "forbidden",
        },
        headers=headers(auditor, "vendor-forbidden"),
    )
    assert forbidden.status_code == 403

    stale = client.patch(
        "/api/vendors/vendor-verified/wallet",
        json={
            "expected_current_wallet": WALLET,
            "new_wallet": replacement_wallet,
            "verification_method": "OUT_OF_BAND_CALL",
            "verification_reference": "call-001",
        },
        headers=headers(operator, "vendor-stale"),
    )
    assert stale.status_code == 409
    assert stale.get_json()["error"]["code"] == "VENDOR_ERROR"

    replaced = client.patch(
        "/api/vendors/vendor-verified/wallet",
        json={
            "expected_current_wallet": original_wallet,
            "new_wallet": replacement_wallet,
            "verification_method": "OUT_OF_BAND_CALL",
            "verification_reference": "call-001",
        },
        headers=headers(operator, "vendor-replace"),
    )
    assert replaced.status_code == 200
    assert replaced.get_json()["vendor"]["approved_wallet_address"] == replacement_wallet

    listed = client.get("/api/vendors", headers=headers(auditor)).get_json()["items"]
    assert [item["id"] for item in listed] == ["vendor-verified"]
    history = client.get(
        "/api/vendors/vendor-verified/wallet-history",
        headers=headers(auditor),
    ).get_json()["items"]
    assert [item["event_type"] for item in history] == ["VERIFIED", "REPLACED"]
    assert history[0]["verification_reference"] == "challenge-2026-09-20-001"
    assert history[1]["previous_wallet_address"] == original_wallet

    repository = app.extensions["tallyguard_repository"]
    repository.create_organization(organization_id="vendor-other", name="Vendor Other")
    repository.create_user(
        organization_id="vendor-other",
        user_id="other-auditor",
        display_name="Other Auditor",
        roles=(Role.AUDITOR.value,),
    )
    other_token, _ = app.extensions["tallyguard_authenticator"].issue_session(
        Principal(
            user_id="other-auditor",
            organization_id="vendor-other",
            roles=(Role.AUDITOR,),
        )
    )
    assert client.get("/api/vendors", headers=headers(other_token)).get_json()["items"] == []
    invisible = client.get(
        "/api/vendors/vendor-verified/wallet-history",
        headers=headers(other_token),
    )
    assert invisible.status_code == 404

    audit = client.get("/api/audit/events", headers=headers(auditor)).get_json()
    assert audit["chain_valid"] is True
    event_types = [item["event_type"] for item in audit["items"]]
    assert event_types[-2:] == ["VENDOR_ONBOARDED", "VENDOR_WALLET_REPLACED"]


def test_policy_and_treasury_apis_are_versioned_role_scoped_and_audited(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    client = app.test_client()
    admin = client.post(
        "/api/demo/session", json={"role": "admin"}
    ).get_json()["access_token"]
    operator = client.post(
        "/api/demo/session", json={"role": "operator"}
    ).get_json()["access_token"]
    auditor = client.post(
        "/api/demo/session", json={"role": "auditor"}
    ).get_json()["access_token"]

    base = {
        "daily_payment_limit_usdc": "5000",
        "minimum_cash_reserve_usdc": "1000",
        "maximum_autonomous_payment_usdc": "2000",
        "po_amount_tolerance_usdc": "10",
        "allowed_asset": "USDC",
        "allowed_network": "ARC-TESTNET",
        "kill_switch_enabled": False,
    }
    first = client.post(
        "/api/policies",
        json={"version": "v1", **base},
        headers=headers(admin, "policy-v1"),
    )
    second = client.post(
        "/api/policies",
        json={
            "version": "v2",
            **base,
            "daily_payment_limit_usdc": "7500",
            "kill_switch_enabled": True,
        },
        headers=headers(admin, "policy-v2"),
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.get_json()["policy"]["content_hash"] != second.get_json()["policy"][
        "content_hash"
    ]

    forbidden = client.post(
        "/api/policies",
        json={"version": "operator-policy", **base},
        headers=headers(operator, "policy-forbidden"),
    )
    assert forbidden.status_code == 403

    active = client.get("/api/policies/active", headers=headers(auditor)).get_json()
    assert active["policy"]["version"] == "v2"
    history = client.get("/api/policies", headers=headers(auditor)).get_json()
    assert history["active_version"] == "v2"
    assert [item["version"] for item in history["items"]] == ["v1", "v2"]
    diff = client.get(
        "/api/policies/diff?from=v1&to=v2", headers=headers(auditor)
    ).get_json()["changes"]
    assert {item["field"] for item in diff} == {
        "daily_payment_limit_usdc",
        "kill_switch_enabled",
        "version",
    }

    treasury = client.post(
        "/api/treasury/snapshots",
        json={
            "available_usdc": "12000",
            "spent_today_usdc": "450",
            "source_reference": "circle-balance-2026-09-20T01:00:00Z",
        },
        headers=headers(operator, "treasury-record"),
    )
    assert treasury.status_code == 201
    summary = client.get(
        "/api/treasury/summary", headers=headers(auditor)
    ).get_json()["treasury"]
    assert summary["available_usdc"] == "12000"
    assert summary["source_reference"] == "circle-balance-2026-09-20T01:00:00Z"

    audit = client.get("/api/audit/events", headers=headers(auditor)).get_json()
    assert audit["chain_valid"] is True
    assert [item["event_type"] for item in audit["items"]][-3:] == [
        "POLICY_ACTIVATED",
        "POLICY_ACTIVATED",
        "TREASURY_SNAPSHOT_RECORDED",
    ]


def test_cross_tenant_invoice_is_invisible(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    client = app.test_client()
    operator = client.post("/api/demo/session", json={"role": "operator"}).get_json()["access_token"]
    assert client.post("/api/invoices", json=invoice_payload(), headers=headers(operator)).status_code == 201

    repository = app.extensions["tallyguard_repository"]
    repository.create_organization(organization_id="other-org", name="Other Organization")
    repository.create_user(
        organization_id="other-org",
        user_id="other-auditor",
        display_name="Other Auditor",
        roles=(Role.AUDITOR.value,),
    )
    authenticator = app.extensions["tallyguard_authenticator"]
    other_token, _ = authenticator.issue_session(
        Principal(user_id="other-auditor", organization_id="other-org", roles=(Role.AUDITOR,))
    )
    response = client.get("/api/invoices/invoice-1", headers=headers(other_token))
    assert response.status_code == 404


def test_invoice_list_has_stable_cursor_pagination(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    client = app.test_client()
    token = client.post("/api/demo/session", json={"role": "operator"}).get_json()["access_token"]
    for index in range(3):
        assert client.post(
            "/api/invoices",
            json=invoice_payload(f"invoice-{index}"),
            headers=headers(token, f"create-{index}"),
        ).status_code == 201

    first = client.get("/api/invoices?limit=2", headers=headers(token)).get_json()
    second = client.get(
        f"/api/invoices?limit=2&cursor={first['next_cursor']}",
        headers=headers(token),
    ).get_json()
    ids = [item["id"] for item in first["items"] + second["items"]]
    assert len(ids) == 3
    assert len(set(ids)) == 3
    assert second["next_cursor"] is None

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import logging
import re

from tallyguard.api import create_app
from tallyguard.audit import canonical_json
from tallyguard.auth import Principal, Role
from tallyguard.circle_arc import ArcNetworkStatus, CircleWalletSnapshot
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


def test_public_responses_apply_browser_security_and_safe_cache_headers(tmp_path):
    app = create_app(database_path=tmp_path / "headers.sqlite3", testing=True)
    client = app.test_client()

    api_response = client.get("/api/health", base_url="https://judge.example")

    assert api_response.headers["Cache-Control"] == "no-store"
    assert api_response.headers["Content-Security-Policy"].startswith(
        "default-src 'self'"
    )
    assert "frame-ancestors 'none'" in api_response.headers["Content-Security-Policy"]
    assert api_response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert api_response.headers["Referrer-Policy"] == "no-referrer"
    assert api_response.headers["X-Content-Type-Options"] == "nosniff"
    assert api_response.headers["X-Frame-Options"] == "DENY"
    assert api_response.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )

    asset_name = next(
        path.name for path in (app.extensions["tallyguard_frontend_dist"] / "assets").iterdir()
    )
    asset_response = client.get(f"/assets/{asset_name}")
    assert asset_response.status_code == 200
    assert asset_response.headers["Cache-Control"] == (
        "public, max-age=31536000, immutable"
    )
    assert "Strict-Transport-Security" not in asset_response.headers


def test_openapi_contract_covers_every_registered_api_operation(tmp_path):
    app = create_app(database_path=tmp_path / "openapi.sqlite3", testing=True)
    response = app.test_client().get("/api/openapi.json")

    assert response.status_code == 200
    contract = response.get_json()
    assert contract["openapi"] == "3.1.0"
    assert contract["servers"] == [{"url": "http://localhost"}]
    documented = {
        (path, method.upper())
        for path, path_item in contract["paths"].items()
        for method in path_item
    }
    registered = {
        (
            re.sub(r"<(?:(?:[^:>]+):)?([^>]+)>", r"{\1}", rule.rule),
            method,
        )
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/")
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }
    assert documented == registered
    operation_ids = [
        operation["operationId"]
        for path_item in contract["paths"].values()
        for operation in path_item.values()
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert "security" not in contract["paths"]["/api/demo/session"]["post"]
    assert "security" not in contract["paths"]["/api/demo/workspace"]["post"]
    assert contract["paths"]["/api/auth/session"]["delete"]["security"] == [
        {"bearerAuth": []}
    ]
    assert contract["paths"]["/api/invoices/{invoice_id}/settle"]["post"][
        "security"
    ] == [{"bearerAuth": []}]


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
    workspace_response = app.test_client().post("/api/demo/workspace")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "DEMO_SESSIONS_DISABLED"
    assert workspace_response.status_code == 404
    assert workspace_response.get_json()["error"]["code"] == "DEMO_SESSIONS_DISABLED"


def test_live_treasury_refresh_records_circle_balance_and_arc_rpc_proof(tmp_path):
    class InspectableLiveAdapter:
        name = "circle-developer-wallets+arc-rpc"

        def submit(self, intent: PaymentIntent) -> ProviderSubmission:
            raise AssertionError("Treasury refresh must not submit a transfer.")

        def inspect_treasury_wallet(self) -> CircleWalletSnapshot:
            return CircleWalletSnapshot(
                wallet_id="wallet-treasury",
                address="0x21f19dae0e6e6d20657f9c8d03bce02c7d476b99",
                blockchain="ARC-TESTNET",
                state="LIVE",
                usdc_balance=Decimal("578.680587"),
            )

        def inspect_arc_network(self) -> ArcNetworkStatus:
            return ArcNetworkStatus(
                chain_id=5042002,
                latest_block=63412867,
                usdc_contract_has_code=True,
            )

    app = create_app(
        database_path=tmp_path / "live-treasury.sqlite3",
        testing=True,
        settlement_adapter=InspectableLiveAdapter(),
        settlement_config=ArcNetworkConfig.for_network(ArcNetwork.TESTNET),
    )
    client = app.test_client()
    operator = client.post(
        "/api/demo/session", json={"role": "operator"}
    ).get_json()["access_token"]

    response = client.post(
        "/api/treasury/snapshots/refresh",
        headers=headers(operator),
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["treasury"]["available_usdc"] == "578.680587"
    assert payload["treasury"]["spent_today_usdc"] == "0"
    assert payload["verification"]["chain_id"] == 5042002
    assert payload["verification"]["latest_block"] == 63412867
    assert payload["verification"]["wallet_address_redacted"].startswith("0x21f19d")
    assert "0x21f19dae0e6e6d20657f9c8d03bce02c7d476b99" not in response.get_data(as_text=True)


def test_simulation_cannot_claim_a_live_treasury_refresh(tmp_path):
    app = create_app(database_path=tmp_path / "sim-treasury.sqlite3", testing=True)
    client = app.test_client()
    operator = client.post(
        "/api/demo/session", json={"role": "operator"}
    ).get_json()["access_token"]

    response = client.post(
        "/api/treasury/snapshots/refresh",
        headers=headers(operator),
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "SETTLEMENT_DENIED"


def test_demo_workspaces_issue_isolated_role_bundles(tmp_path):
    app = create_app(database_path=tmp_path / "isolated-workspaces.sqlite3", testing=True)
    client = app.test_client()

    first = client.post("/api/demo/workspace")
    second = client.post("/api/demo/workspace")

    assert first.status_code == 201
    assert second.status_code == 201
    first_payload = first.get_json()
    second_payload = second.get_json()
    assert first_payload["workspace_id"].startswith("demo-ws-")
    assert first_payload["workspace_id"] != second_payload["workspace_id"]
    assert set(first_payload["sessions"]) == {
        "admin",
        "operator",
        "approver",
        "auditor",
    }
    assert (
        first_payload["isolation"]
        == "one browser workspace; four role-separated sessions"
    )

    expected_roles = {
        "admin": "ADMIN",
        "operator": "FINANCE_OPERATOR",
        "approver": "APPROVER",
        "auditor": "AUDITOR",
    }
    for role_name, expected_role in expected_roles.items():
        inspected = client.get(
            "/api/auth/session",
            headers=headers(
                first_payload["sessions"][role_name],
                f"inspect-{role_name}",
            ),
        )
        assert inspected.status_code == 200
        principal = inspected.get_json()["principal"]
        assert principal["organization_id"] == first_payload["workspace_id"]
        assert principal["roles"] == [expected_role]

    run = client.post(
        "/api/demo/scenarios/clean-payment/run",
        headers=headers(
            first_payload["sessions"]["operator"],
            "workspace-one-run",
        ),
    )
    assert run.status_code == 200
    first_invoices = client.get(
        "/api/invoices",
        headers=headers(
            first_payload["sessions"]["auditor"],
            "workspace-one-list",
        ),
    ).get_json()["items"]
    second_invoices = client.get(
        "/api/invoices",
        headers=headers(
            second_payload["sessions"]["auditor"],
            "workspace-two-list",
        ),
    ).get_json()["items"]
    assert len(first_invoices) == 1
    assert second_invoices == []


def test_authenticated_session_inspection_returns_only_principal_identity(tmp_path):
    app = create_app(database_path=tmp_path / "session-inspection.sqlite3", testing=True)
    client = app.test_client()
    issued = client.post("/api/demo/session", json={"role": "operator"}).get_json()

    response = client.get(
        "/api/auth/session",
        headers=headers(issued["access_token"]),
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "principal": {
            "user_id": "demo-operator",
            "organization_id": "demo-org",
            "roles": ["FINANCE_OPERATOR"],
        }
    }
    assert issued["access_token"] not in response.get_data(as_text=True)


def test_session_inspection_rejects_invalid_bearer(tmp_path):
    app = create_app(database_path=tmp_path / "session-inspection.sqlite3", testing=True)

    response = app.test_client().get(
        "/api/auth/session",
        headers=headers("not-a-valid-session"),
    )

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "AUTHENTICATION_DENIED"


def test_current_session_can_revoke_itself_without_echoing_token(tmp_path):
    app = create_app(database_path=tmp_path / "session-revocation.sqlite3", testing=True)
    client = app.test_client()
    token = client.post("/api/demo/session", json={"role": "auditor"}).get_json()[
        "access_token"
    ]

    revoked = client.delete("/api/auth/session", headers=headers(token))

    assert revoked.status_code == 200
    assert revoked.get_json() == {"status": "revoked"}
    assert token not in revoked.get_data(as_text=True)
    denied = client.get("/api/auth/session", headers=headers(token))
    assert denied.status_code == 401
    assert denied.get_json()["error"]["code"] == "AUTHENTICATION_DENIED"


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
    assert limited.get_json()["error"]["message"] == "Tenant request limit exceeded."


def test_demo_session_issuance_has_a_separate_anonymous_rate_limit(tmp_path):
    app = create_app(
        database_path=tmp_path / "demo-rate-limit.sqlite3",
        testing=True,
        demo_session_rate_limit_per_minute=2,
    )
    client = app.test_client()

    assert client.post("/api/demo/session", json={"role": "operator"}).status_code == 200
    assert client.post("/api/demo/session", json={"role": "auditor"}).status_code == 200
    limited = client.post("/api/demo/session", json={"role": "admin"})

    assert limited.status_code == 429
    assert limited.get_json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert limited.get_json()["error"]["message"] == "Demo session request limit exceeded."
    assert int(limited.headers["Retry-After"]) >= 1

    independent_source = client.post(
        "/api/demo/session",
        json={"role": "operator"},
        environ_base={"REMOTE_ADDR": "192.0.2.25"},
    )
    assert independent_source.status_code == 200


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
    assert response.headers["Referrer-Policy"] == "no-referrer"
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


def test_real_evidence_policy_and_treasury_can_autopay_an_idempotent_pay_decision(tmp_path):
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
            "daily_autonomous_payment_limit_usdc": "2000",
            "autonomous_payments_enabled": True,
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
        "/api/invoices/invoice-live/evaluate?auto_settle=true",
        headers=headers(operator, "evaluate-1"),
    )
    assert first.status_code == 200
    result = first.get_json()
    assert result["decision"]["final_action"] == "PAY"
    assert result["invoice"]["status"] == "RECONCILED"
    assert result["autopay"]["status"] == "SETTLED"
    assert result["autopay"]["payment"]["receipt"]["confirmed_amount_usdc"] == "1200"
    assert result["autopay"]["payment"]["intent"]["approval_reference"] is None
    assert result["autopay"]["daily_autonomous_remaining_usdc"] == "800"
    assert result["autopay"]["hard_daily_payment_limit_usdc"] == "5000"
    assert result["decision"]["reason_codes"] == []
    assert result["decision"]["agent_recommendation"]["action"] == "PAY"
    assert result["decision"]["agent_recommendation"]["evidence_refs"] == [
        "package:invoice-live",
        result["decision"]["evidence_manifest_hash"],
    ]

    # Re-exporting a document with a changed amount must not bypass the
    # existing vendor invoice number and trigger another automatic transfer.
    reexported_invoice = {
        "invoice_id": "invoice-reexported",
        "vendor_id": "vendor-live",
        "invoice_number": "inv live 001",
        "currency": "USDC",
        "amount": "1201",
        "due_date": "2026-10-08",
        "payment_wallet_address": vendor_wallet,
    }
    reexported_bytes = json.dumps(reexported_invoice, separators=(",", ":")).encode("utf-8")
    assert client.post(
        "/api/invoices",
        json={
            "id": "invoice-reexported",
            "vendor_id": "vendor-live",
            "invoice_number": "inv live 001",
            "currency": "USDC",
            "amount": "1201",
            "due_date": "2026-10-08",
            "payment_wallet_address": vendor_wallet,
            "source_document_hash": hashlib.sha256(reexported_bytes).hexdigest(),
        },
        headers=headers(operator),
    ).status_code == 201
    reexported_documents = (
        ("INVOICE", reexported_bytes),
        (
            "PURCHASE_ORDER",
            json.dumps({
                "purchase_order_id": "po-live-002",
                "vendor_id": "vendor-live",
                "po_number": "PO-LIVE-002",
                "currency": "USDC",
                "authorized_amount": "1201",
            }, separators=(",", ":")).encode("utf-8"),
        ),
        (
            "DELIVERY",
            json.dumps({
                "delivery_id": "delivery-live-002",
                "purchase_order_id": "po-live-002",
                "delivered_value": "1201",
            }, separators=(",", ":")).encode("utf-8"),
        ),
    )
    for evidence_type, content in reexported_documents:
        assert client.post(
            "/api/invoices/invoice-reexported/evidence",
            data={
                "evidence_type": evidence_type,
                "file": (BytesIO(content), f"{evidence_type.lower()}.json", "application/json"),
            },
            content_type="multipart/form-data",
            headers=headers(operator),
        ).status_code == 201
    duplicate_review = client.post(
        "/api/invoices/invoice-reexported/evaluate?auto_settle=true",
        headers=headers(operator, "evaluate-duplicate-reexport"),
    )
    assert duplicate_review.status_code == 200
    duplicate_result = duplicate_review.get_json()
    assert duplicate_result["decision"]["final_action"] == "ESCALATE"
    assert "VENDOR_INVOICE_NUMBER_REUSED" in duplicate_result["decision"]["reason_codes"]
    assert duplicate_result["autopay"]["payment"] is None
    assert client.get(
        f"/api/decisions/{duplicate_result['decision']['id']}/replay",
        headers=headers(auditor, "replay-duplicate-reexport"),
    ).get_json()["verification"]["verified"] is True

    def evaluate_related_invoice(
        *, invoice_id: str, number: str, amount: str,
        po_number: str, delivery_id: str,
    ) -> dict:
        po_id = f"po-{invoice_id}"
        invoice_fields = {
            "invoice_id": invoice_id,
            "vendor_id": "vendor-live",
            "invoice_number": number,
            "currency": "USDC",
            "amount": amount,
            "due_date": "2026-10-08",
            "payment_wallet_address": vendor_wallet,
        }
        invoice_bytes = json.dumps(invoice_fields, separators=(",", ":")).encode("utf-8")
        assert client.post(
            "/api/invoices",
            json={**invoice_fields, "id": invoice_id,
                  "source_document_hash": hashlib.sha256(invoice_bytes).hexdigest()},
            headers=headers(operator),
        ).status_code == 201
        documents = (
            ("INVOICE", invoice_bytes),
            ("PURCHASE_ORDER", json.dumps({
                "purchase_order_id": po_id, "vendor_id": "vendor-live",
                "po_number": po_number, "currency": "USDC", "authorized_amount": "1200",
            }, separators=(",", ":")).encode("utf-8")),
            ("DELIVERY", json.dumps({
                "delivery_id": delivery_id, "purchase_order_id": po_id,
                "delivered_value": "1200",
            }, separators=(",", ":")).encode("utf-8")),
        )
        for evidence_type, content in documents:
            assert client.post(
                f"/api/invoices/{invoice_id}/evidence",
                data={"evidence_type": evidence_type,
                      "file": (BytesIO(content), f"{evidence_type.lower()}.json", "application/json")},
                content_type="multipart/form-data",
                headers=headers(operator),
            ).status_code == 201
        response = client.post(
            f"/api/invoices/{invoice_id}/evaluate?auto_settle=true",
            headers=headers(operator, f"evaluate-{invoice_id}"),
        )
        assert response.status_code == 200
        result = response.get_json()
        assert result["autopay"]["payment"] is None
        assert client.get(
            f"/api/decisions/{result['decision']['id']}/replay",
            headers=headers(auditor, f"replay-{invoice_id}"),
        ).get_json()["verification"]["verified"] is True
        return result

    near_match = evaluate_related_invoice(
        invoice_id="invoice-near", number="INV-LIVE-002", amount="1200",
        po_number="PO-OTHER-002", delivery_id="delivery-other-002",
    )
    assert near_match["decision"]["final_action"] == "ESCALATE"
    assert "NEAR_DUPLICATE_INVOICE_FIELDS" in near_match["decision"]["reason_codes"]

    overused_evidence = evaluate_related_invoice(
        invoice_id="invoice-overused", number="INV-OTHER-900", amount="100",
        po_number="PO-LIVE-001", delivery_id="delivery-live-001",
    )
    assert overused_evidence["decision"]["final_action"] == "HOLD"
    assert "PO_CUMULATIVE_EXCEEDED" in overused_evidence["decision"]["reason_codes"]
    assert "DELIVERY_CUMULATIVE_EXCEEDED" in overused_evidence["decision"]["reason_codes"]

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
    assert {item["status"] for item in overview["recent_requests"]} == {
        "READY",
        "HOLD",
        "REJECTED",
    }
    rejected = next(item for item in overview["recent_requests"] if item["status"] == "REJECTED")
    assert rejected["decision_reason_codes"]
    assert rejected["decision_findings"]
    assert all(finding["message"] for finding in rejected["decision_findings"])
    assert rejected["decision_remediation"]
    assert all(item["settlement_status"] == "NOT_STARTED" for item in overview["recent_requests"])
    assert all(item["settled_amount_usdc"] is None for item in overview["recent_requests"])
    assert all(item["settlement_payment_intent_id"] is None for item in overview["recent_requests"])
    assert all(item["settlement_transaction_hash"] is None for item in overview["recent_requests"])
    assert all(item["settlement_provider"] is None for item in overview["recent_requests"])
    assert all(item["settlement_network"] is None for item in overview["recent_requests"])
    assert all(item["settlement_block_number"] is None for item in overview["recent_requests"])
    assert all(item["settlement_explorer_url"] is None for item in overview["recent_requests"])
    assert overview["treasury_available_usdc"] == "10000"
    assert overview["treasury_committed_since_snapshot_usdc"] == "0"
    assert Decimal(overview["unreserved_open_exposure_usdc"]) == (
        clean_amount + wallet_amount
    )
    assert Decimal(overview["projected_after_open_usdc"]) == (
        Decimal("10000") - clean_amount - wallet_amount
    )
    assert overview["minimum_reserve_usdc"] == "3000"


def test_optional_treasury_summary_returns_null_for_empty_workspace(tmp_path):
    app = create_app(database_path=tmp_path / "optional-treasury.sqlite3", testing=True)
    client = app.test_client()
    auditor = client.post(
        "/api/demo/session", json={"role": "auditor"}
    ).get_json()["access_token"]

    optional = client.get(
        "/api/treasury/summary?optional=true",
        headers=headers(auditor, "optional-treasury"),
    )

    assert optional.status_code == 200
    assert optional.get_json() == {"treasury": None}


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
    settled_requests = [
        item
        for item in overview["recent_requests"]
        if item["settlement_status"] == "CONFIRMED"
    ]
    assert len(settled_requests) == 2
    assert all(item["settlement_provider"] == "arc-simulator" for item in settled_requests)
    assert all(item["settlement_payment_intent_id"] for item in settled_requests)
    assert all(item["settlement_network"] == "ARC-TESTNET" for item in settled_requests)
    assert all(item["settlement_block_number"] is not None for item in settled_requests)
    assert all(
        item["settlement_explorer_url"].startswith("https://explorer.testnet.arc.io/tx/")
        for item in settled_requests
    )
    for item in settled_requests:
        persisted = client.get(
            f"/api/payments/{item['settlement_payment_intent_id']}",
            headers=headers(auditor, f"reopen-{item['id']}"),
        )
        assert persisted.status_code == 200
        assert persisted.get_json()["payment"]["receipt"]["transaction_hash"] == item["settlement_transaction_hash"]
    settled_amount = sum(
        (Decimal(item["invoice"]["amount"]) for item in runs[:2]),
        Decimal("0"),
    )
    held_amount = Decimal(runs[2]["invoice"]["amount"])
    assert Decimal(overview["treasury_committed_since_snapshot_usdc"]) == (
        settled_amount
    )
    assert Decimal(overview["unreserved_open_exposure_usdc"]) == held_amount
    assert Decimal(overview["projected_after_open_usdc"]) == (
        Decimal("10000") - settled_amount - held_amount
    )


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
        "daily_autonomous_payment_limit_usdc": "1000",
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
    assert first.get_json()["policy"]["autonomous_payments_enabled"] is False
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

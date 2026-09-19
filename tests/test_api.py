from datetime import datetime, timedelta, timezone

from tallyguard.api import create_app
from tallyguard.auth import Principal, Role


WALLET = "0x1111111111111111111111111111111111111111"


def headers(token: str, correlation_id: str = "test-request-1") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Correlation-ID": correlation_id,
    }


def invoice_payload(invoice_id: str = "invoice-1"):
    return {
        "id": invoice_id,
        "vendor_id": "vendor-1",
        "invoice_number": f"INV-{invoice_id}",
        "currency": "USDC",
        "amount": "1200.00",
        "due_date": "2026-10-08",
        "payment_wallet_address": WALLET,
        "source_document_hash": "a" * 64,
    }


def test_health_and_readiness_are_public(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    client = app.test_client()
    assert client.get("/api/health").get_json()["status"] == "ok"
    assert client.get("/api/readiness").get_json()["database"] == "ok"


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


def test_auditor_cannot_create_invoice(tmp_path):
    app = create_app(database_path=tmp_path / "api.sqlite3", testing=True)
    client = app.test_client()
    token = client.post("/api/demo/session", json={"role": "auditor"}).get_json()["access_token"]
    response = client.post("/api/invoices", json=invoice_payload(), headers=headers(token))
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "AUTHORIZATION_DENIED"


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

from datetime import datetime, timezone

from tallyguard.api import create_app
from tallyguard.auth import Principal, Role


def headers(token: str, correlation_id: str = "audit-search") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Correlation-ID": correlation_id,
    }


def session(app, *, organization_id: str, user_id: str) -> str:
    repository = app.extensions["tallyguard_repository"]
    authenticator = app.extensions["tallyguard_authenticator"]
    repository.create_organization(organization_id=organization_id, name=organization_id)
    repository.create_user(
        organization_id=organization_id,
        user_id=user_id,
        display_name=user_id,
        roles=(Role.AUDITOR.value,),
    )
    token, _ = authenticator.issue_session(
        Principal(
            user_id=user_id,
            organization_id=organization_id,
            roles=(Role.AUDITOR,),
        )
    )
    return token


def append_event(
    app,
    *,
    organization_id: str,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    marker: str,
    created_at: datetime | None = None,
) -> None:
    app.extensions["tallyguard_repository"].append(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload={
            "organization_id": organization_id,
            "invoice_id": aggregate_id,
            "correlation_id": marker,
        },
        created_at=created_at,
    )


def test_audit_search_is_tenant_scoped_filterable_and_cursor_paginated(tmp_path):
    app = create_app(database_path=tmp_path / "audit-search.sqlite3", testing=True)
    client = app.test_client()
    auditor = session(app, organization_id="org-a", user_id="auditor-a")
    session(app, organization_id="org-b", user_id="auditor-b")

    append_event(
        app,
        organization_id="org-a",
        aggregate_type="invoice",
        aggregate_id="invoice-alpha",
        event_type="INVOICE_INGESTED",
        marker="needle-one",
        created_at=datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc),
    )
    append_event(
        app,
        organization_id="org-a",
        aggregate_type="decision",
        aggregate_id="decision-alpha",
        event_type="POLICY_EVALUATED",
        marker="needle-two",
        created_at=datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc),
    )
    append_event(
        app,
        organization_id="org-a",
        aggregate_type="invoice",
        aggregate_id="invoice-beta",
        event_type="INVOICE_INGESTED",
        marker="literal%_marker",
        created_at=datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc),
    )
    append_event(
        app,
        organization_id="org-b",
        aggregate_type="invoice",
        aggregate_id="foreign-invoice",
        event_type="INVOICE_INGESTED",
        marker="needle-foreign",
        created_at=datetime(2026, 9, 19, 11, 0, tzinfo=timezone.utc),
    )

    first = client.get(
        "/api/audit/events?event_type=INVOICE_INGESTED&limit=1",
        headers=headers(auditor, "audit-page-1"),
    )
    assert first.status_code == 200
    first_payload = first.get_json()
    assert first_payload["chain_valid"] is True
    assert [item["aggregate_id"] for item in first_payload["items"]] == [
        "invoice-beta"
    ]
    assert first_payload["page"]["has_more"] is True
    cursor = first_payload["page"]["next_before_sequence"]

    second = client.get(
        f"/api/audit/events?event_type=INVOICE_INGESTED&limit=1&before_sequence={cursor}",
        headers=headers(auditor, "audit-page-2"),
    ).get_json()
    assert [item["aggregate_id"] for item in second["items"]] == [
        "invoice-alpha"
    ]
    assert second["page"]["has_more"] is False

    searched = client.get(
        "/api/audit/events?q=needle-two",
        headers=headers(auditor, "audit-search-query"),
    ).get_json()
    assert [item["aggregate_id"] for item in searched["items"]] == [
        "decision-alpha"
    ]
    assert all(item["aggregate_id"] != "foreign-invoice" for item in searched["items"])

    literal = client.get(
        "/api/audit/events?q=literal%25_marker",
        headers=headers(auditor, "audit-search-literal"),
    ).get_json()
    assert [item["aggregate_id"] for item in literal["items"]] == [
        "invoice-beta"
    ]

    window = client.get(
        "/api/audit/events?created_after=2026-09-19T08:30:00Z&created_before=2026-09-19T09:30:00Z",
        headers=headers(auditor, "audit-search-window"),
    ).get_json()
    assert [item["aggregate_id"] for item in window["items"]] == [
        "decision-alpha"
    ]


def test_audit_search_rejects_unbounded_or_invalid_filters(tmp_path):
    app = create_app(database_path=tmp_path / "audit-search-invalid.sqlite3", testing=True)
    client = app.test_client()
    auditor = client.post("/api/demo/session", json={"role": "auditor"}).get_json()[
        "access_token"
    ]

    assert client.get(
        "/api/audit/events?limit=201",
        headers=headers(auditor, "audit-limit"),
    ).status_code == 400
    assert client.get(
        "/api/audit/events?before_sequence=0",
        headers=headers(auditor, "audit-cursor"),
    ).status_code == 400
    assert client.get(
        "/api/audit/events?q=" + "x" * 201,
        headers=headers(auditor, "audit-query"),
    ).status_code == 400
    assert client.get(
        "/api/audit/events?created_after=2026-09-20T12:00:00&created_before=2026-09-20T13:00:00Z",
        headers=headers(auditor, "audit-naive-time"),
    ).status_code == 400
    assert client.get(
        "/api/audit/events?created_after=2026-09-20T14:00:00Z&created_before=2026-09-20T13:00:00Z",
        headers=headers(auditor, "audit-inverted-time"),
    ).status_code == 400

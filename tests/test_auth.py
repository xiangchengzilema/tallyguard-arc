from datetime import datetime, timedelta, timezone

import pytest

from tallyguard.auth import (
    AuthenticationDenied,
    Authenticator,
    InMemorySessionStore,
    AuthorizationDenied,
    Permission,
    Principal,
    Role,
    authorize,
)


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def principal(*roles: Role, organization_id: str = "org-1") -> Principal:
    return Principal(
        user_id="user-1",
        organization_id=organization_id,
        roles=roles,
    )


def test_session_authenticates_before_expiry_and_stores_only_digest():
    store = InMemorySessionStore()
    authenticator = Authenticator(store=store)
    raw_token, session = authenticator.issue_session(
        principal(Role.FINANCE_OPERATOR),
        lifetime=timedelta(minutes=30),
        now=NOW,
    )
    assert raw_token != session.token_hash
    assert store.get_session(raw_token) is None
    assert store.get_session(session.token_hash) == session
    assert authenticator.authenticate(raw_token, now=NOW + timedelta(minutes=29)).user_id == "user-1"


def test_expired_and_revoked_sessions_are_rejected():
    authenticator = Authenticator()
    token, _ = authenticator.issue_session(
        principal(Role.AUDITOR),
        lifetime=timedelta(minutes=30),
        now=NOW,
    )
    with pytest.raises(AuthenticationDenied, match="expired"):
        authenticator.authenticate(token, now=NOW + timedelta(minutes=30))

    token, _ = authenticator.issue_session(principal(Role.AUDITOR), now=NOW)
    authenticator.revoke(token, now=NOW + timedelta(minutes=1))
    with pytest.raises(AuthenticationDenied, match="revoked"):
        authenticator.authenticate(token, now=NOW + timedelta(minutes=2))


def test_session_store_prunes_inactive_records_and_bounds_active_sessions():
    store = InMemorySessionStore()
    authenticator = Authenticator(
        store=store,
        maximum_active_sessions_per_principal=2,
    )
    expired_token, expired_session = authenticator.issue_session(
        principal(Role.AUDITOR),
        lifetime=timedelta(minutes=1),
        now=NOW,
    )
    second_token, _ = authenticator.issue_session(
        principal(Role.AUDITOR),
        now=NOW + timedelta(minutes=2),
    )
    third_token, _ = authenticator.issue_session(
        principal(Role.AUDITOR),
        now=NOW + timedelta(minutes=3),
    )
    fourth_token, _ = authenticator.issue_session(
        principal(Role.AUDITOR),
        now=NOW + timedelta(minutes=4),
    )

    assert store.get_session(expired_session.token_hash) is None
    with pytest.raises(AuthenticationDenied, match="invalid"):
        authenticator.authenticate(expired_token, now=NOW + timedelta(minutes=4))
    with pytest.raises(AuthenticationDenied, match="invalid"):
        authenticator.authenticate(second_token, now=NOW + timedelta(minutes=4))
    assert authenticator.authenticate(
        third_token, now=NOW + timedelta(minutes=4)
    ).user_id == "user-1"
    assert authenticator.authenticate(
        fourth_token, now=NOW + timedelta(minutes=4)
    ).user_id == "user-1"


def test_operator_cannot_approve_or_settle():
    operator = principal(Role.FINANCE_OPERATOR)
    authorize(
        operator,
        permission=Permission.EVIDENCE_WRITE,
        resource_organization_id="org-1",
    )
    with pytest.raises(AuthorizationDenied, match="PAYMENT_APPROVE"):
        authorize(
            operator,
            permission=Permission.PAYMENT_APPROVE,
            resource_organization_id="org-1",
        )


def test_approver_can_approve_and_settle_but_not_change_policy():
    approver = principal(Role.APPROVER)
    authorize(
        approver,
        permission=Permission.PAYMENT_APPROVE,
        resource_organization_id="org-1",
    )
    authorize(
        approver,
        permission=Permission.SETTLEMENT_EXECUTE,
        resource_organization_id="org-1",
    )
    with pytest.raises(AuthorizationDenied, match="POLICY_WRITE"):
        authorize(
            approver,
            permission=Permission.POLICY_WRITE,
            resource_organization_id="org-1",
        )


def test_admin_cannot_cross_tenant_boundary():
    admin = principal(Role.ADMIN, organization_id="org-1")
    with pytest.raises(AuthorizationDenied, match="Cross-organization"):
        authorize(
            admin,
            permission=Permission.AUDIT_READ,
            resource_organization_id="org-2",
        )


def test_multi_role_permissions_are_combined():
    combined = principal(Role.FINANCE_OPERATOR, Role.APPROVER)
    assert Permission.EVIDENCE_WRITE in combined.permissions
    assert Permission.PAYMENT_APPROVE in combined.permissions
    assert Permission.POLICY_WRITE not in combined.permissions

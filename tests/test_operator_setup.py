from datetime import datetime, timezone
import hashlib

import pytest

from tallyguard.api import create_app
from tallyguard.auth import Authenticator, Role
from tallyguard.operator_setup import (
    CONFIRMATION_PHRASE,
    OperatorSetupError,
    provision_operator_access,
)
from tallyguard.persistence import PersistenceError, SqliteRepository


def test_operator_setup_requires_exact_confirmation_before_creating_database(tmp_path):
    database = tmp_path / "operators.sqlite3"

    with pytest.raises(OperatorSetupError, match=CONFIRMATION_PHRASE):
        provision_operator_access(
            database_path=database,
            organization_id="atlas-finance",
            organization_name="Atlas Finance",
            confirmation="yes",
        )

    assert not database.exists()


def test_operator_setup_creates_role_separated_durable_sessions(tmp_path):
    database = tmp_path / "operators.sqlite3"

    bundle = provision_operator_access(
        database_path=database,
        organization_id="atlas-finance",
        organization_name="Atlas Finance",
        confirmation=CONFIRMATION_PHRASE,
        session_hours=2,
    )

    assert bundle.organization_created is True
    assert len(bundle.sessions) == 4
    assert {session.role for session in bundle.sessions} == {role.value for role in Role}
    assert len({session.bearer_token for session in bundle.sessions}) == 4
    assert all(session.user_created for session in bundle.sessions)
    assert all(session.bearer_token not in database.read_bytes().decode("latin-1") for session in bundle.sessions)

    repository = SqliteRepository(database)
    authenticator = Authenticator(store=repository)
    try:
        for issued in bundle.sessions:
            principal = authenticator.authenticate(
                issued.bearer_token,
                now=datetime.now(timezone.utc),
            )
            assert principal.organization_id == "atlas-finance"
            assert principal.user_id == issued.user_id
            assert principal.roles == (Role(issued.role),)
            stored = repository.get_session(
                hashlib.sha256(issued.bearer_token.encode("utf-8")).hexdigest()
            )
            assert stored is not None
            assert stored.token_hash != issued.bearer_token
    finally:
        repository.close()


def test_operator_session_authenticates_against_api_restart(tmp_path):
    database = tmp_path / "operators.sqlite3"
    bundle = provision_operator_access(
        database_path=database,
        organization_id="atlas-finance",
        organization_name="Atlas Finance",
        confirmation=CONFIRMATION_PHRASE,
    )
    operator_token = next(
        session.bearer_token
        for session in bundle.sessions
        if session.role == Role.FINANCE_OPERATOR.value
    )

    app = create_app(database_path=database, testing=True)
    response = app.test_client().get(
        "/api/operations/overview",
        headers={"Authorization": f"Bearer {operator_token}"},
    )

    assert response.status_code == 200
    assert response.get_json()["overview"]["organization_id"] == "atlas-finance"
    assert response.get_json()["overview"]["invoice_count"] == 0
    assert response.get_json()["overview"]["work_queue"] == []


def test_operator_setup_is_idempotent_for_identity_and_rotates_sessions(tmp_path):
    database = tmp_path / "operators.sqlite3"
    first = provision_operator_access(
        database_path=database,
        organization_id="atlas-finance",
        organization_name="Atlas Finance",
        confirmation=CONFIRMATION_PHRASE,
    )

    second = provision_operator_access(
        database_path=database,
        organization_id="atlas-finance",
        organization_name="Atlas Finance",
        confirmation=CONFIRMATION_PHRASE,
    )

    assert second.organization_created is False
    assert all(session.user_created is False for session in second.sessions)
    assert {session.bearer_token for session in first.sessions}.isdisjoint(
        session.bearer_token for session in second.sessions
    )


def test_operator_setup_rejects_identity_drift(tmp_path):
    database = tmp_path / "operators.sqlite3"
    provision_operator_access(
        database_path=database,
        organization_id="atlas-finance",
        organization_name="Atlas Finance",
        confirmation=CONFIRMATION_PHRASE,
    )

    with pytest.raises(PersistenceError, match="organization name"):
        provision_operator_access(
            database_path=database,
            organization_id="atlas-finance",
            organization_name="Renamed Behind the Operator's Back",
            confirmation=CONFIRMATION_PHRASE,
        )


def test_operator_setup_rejects_existing_user_role_drift(tmp_path):
    database = tmp_path / "operators.sqlite3"
    repository = SqliteRepository(database)
    repository.create_organization(
        organization_id="atlas-finance",
        name="Atlas Finance",
    )
    repository.create_user(
        organization_id="atlas-finance",
        user_id="atlas-finance-admin",
        display_name="Policy Administrator",
        roles=(Role.AUDITOR.value,),
    )
    repository.close()

    with pytest.raises(PersistenceError, match="requested identity"):
        provision_operator_access(
            database_path=database,
            organization_id="atlas-finance",
            organization_name="Atlas Finance",
            confirmation=CONFIRMATION_PHRASE,
        )


@pytest.mark.parametrize("hours", [0, 25])
def test_operator_setup_bounds_session_lifetime(tmp_path, hours):
    with pytest.raises(OperatorSetupError, match="between 1 and 24"):
        provision_operator_access(
            database_path=tmp_path / "operators.sqlite3",
            organization_id="atlas-finance",
            organization_name="Atlas Finance",
            confirmation=CONFIRMATION_PHRASE,
            session_hours=hours,
        )


@pytest.mark.parametrize(
    "organization_id",
    ["UPPERCASE", "ab", "spaces are unsafe", "tenant/escape", "-leading"],
)
def test_operator_setup_rejects_unsafe_organization_ids(tmp_path, organization_id):
    with pytest.raises(OperatorSetupError, match="Organization ID"):
        provision_operator_access(
            database_path=tmp_path / "operators.sqlite3",
            organization_id=organization_id,
            organization_name="Atlas Finance",
            confirmation=CONFIRMATION_PHRASE,
        )

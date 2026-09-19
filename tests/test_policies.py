from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tallyguard.policies import PolicyRepository, PolicyRepositoryError, policy_content_hash
from tallyguard.policy import Policy


def policy(version: str = "v1") -> Policy:
    return Policy(
        version=version,
        organization_id="org-1",
        daily_payment_limit_usdc=Decimal("5000"),
        minimum_cash_reserve_usdc=Decimal("1000"),
        maximum_autonomous_payment_usdc=Decimal("2000"),
    )


def test_policy_versions_are_immutable_and_active_version_advances():
    repository = PolicyRepository()
    first = repository.activate(policy(), activated_by_user_id="admin-1")
    second = repository.activate(
        replace(policy(), version="v2", daily_payment_limit_usdc=Decimal("7500")),
        activated_by_user_id="admin-1",
    )

    assert repository.active(organization_id="org-1") == second
    assert repository.get(organization_id="org-1", version="v1") == first
    with pytest.raises(PolicyRepositoryError, match="immutable"):
        repository.activate(policy(), activated_by_user_id="admin-1")


def test_policy_diff_is_field_level_and_content_hash_is_stable():
    repository = PolicyRepository()
    first_policy = policy()
    second_policy = replace(
        first_policy,
        version="v2",
        daily_payment_limit_usdc=Decimal("7500"),
        kill_switch_enabled=True,
    )
    repository.activate(
        first_policy,
        activated_by_user_id="admin-1",
        activated_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )
    repository.activate(second_policy, activated_by_user_id="admin-1")

    changes = repository.diff(organization_id="org-1", from_version="v1", to_version="v2")
    assert {change.field for change in changes} == {
        "daily_payment_limit_usdc",
        "kill_switch_enabled",
        "version",
    }
    assert policy_content_hash(first_policy) == policy_content_hash(policy())
    assert policy_content_hash(first_policy) != policy_content_hash(second_policy)


def test_policy_history_is_tenant_scoped():
    repository = PolicyRepository()
    repository.activate(policy(), activated_by_user_id="admin-1")
    with pytest.raises(PolicyRepositoryError, match="not found"):
        repository.get(organization_id="org-2", version="v1")

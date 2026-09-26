"""The 50-agent campaign uses a simulator in CI and never needs Circle secrets."""

from datetime import datetime, timedelta, timezone
import random

import pytest

from tallyguard.api import create_app
from tallyguard.network import ArcNetwork, ArcNetworkConfig
from tallyguard.operator_setup import CONFIRMATION_PHRASE, provision_operator_access
from tallyguard.settlement import SimulatedArcAdapter
from tallyguard.testnet_campaign import (
    AGENT_COUNT,
    CampaignError,
    make_plan,
    run_due,
    validate_plan,
)


TREASURY = "0x" + "f" * 40
RECIPIENTS = tuple(f"0x{index:040x}" for index in range(1, 38))


def _plan():
    return make_plan(
        now=datetime(2026, 9, 25, 12, tzinfo=timezone.utc),
        treasury=TREASURY,
        recipients=RECIPIENTS,
        rng=random.Random(1402),
        campaign_id="test-campaign",
    )


def test_plan_requires_confirmed_wallet_count_and_immutable_caps():
    plan = _plan()
    assert len(plan["jobs"]) == AGENT_COUNT
    assert len({job["agent_id"] for job in plan["jobs"]}) == AGENT_COUNT
    assert len({job["recipient"] for job in plan["jobs"]}) == len(RECIPIENTS)
    validate_plan(plan, treasury=TREASURY, recipients=RECIPIENTS)
    plan["jobs"][0]["amount_usdc"] = "1.00"
    with pytest.raises(CampaignError, match="hash mismatch"):
        validate_plan(plan, treasury=TREASURY, recipients=RECIPIENTS)
    with pytest.raises(CampaignError, match="50 distinct recipients"):
        make_plan(
            now=datetime.now(timezone.utc),
            treasury=TREASURY,
            recipients=RECIPIENTS,
            rng=random.Random(1),
            campaign_id="unique-wallets",
            unique_wallets=True,
        )


def test_due_jobs_cover_realistic_approval_mix_without_duplicate_settlements(tmp_path):
    plan = _plan()
    database = tmp_path / "campaign.sqlite3"
    bundle = provision_operator_access(
        database_path=database,
        organization_id="tg-test-campaign",
        organization_name="TallyGuard Controlled Agent Testnet Campaign",
        confirmation=CONFIRMATION_PHRASE,
    )
    sessions = {
        entry.role.lower().replace("finance_operator", "operator"): entry.bearer_token
        for entry in bundle.sessions
    }
    adapter = SimulatedArcAdapter()
    app = create_app(
        database_path=database,
        testing=True,
        settlement_adapter=adapter,
        settlement_config=ArcNetworkConfig.for_network(ArcNetwork.TESTNET),
    )
    repository = app.extensions["tallyguard_repository"]
    now = datetime.fromisoformat(plan["created_at"]) + timedelta(hours=26)
    try:
        with app.test_client() as client:
            first = run_due(
                plan=plan,
                now=now,
                client=client,
                repository=repository,
                sessions=sessions,
                organization_id="tg-test-campaign",
                live=False,
            )
        counts = {status: sum(item["status"] == status for item in first) for status in ("PAID", "DECLINED", "HELD")}
        assert counts == {"PAID": 40, "DECLINED": 5, "HELD": 5}
        assert adapter.submission_count == 40
        assert len({item["transaction_hash"] for item in first if item["status"] == "PAID"}) == 40

        with app.test_client() as client:
            second = run_due(
                plan=plan,
                now=now + timedelta(minutes=1),
                client=client,
                repository=repository,
                sessions=sessions,
                organization_id="tg-test-campaign",
                live=False,
            )
        assert second == first
        assert adapter.submission_count == 40
    finally:
        repository.close()

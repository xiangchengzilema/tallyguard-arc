from decimal import Decimal

import pytest

from tallyguard.acceptance import run_testnet_acceptance
from tallyguard.network import ArcNetwork, ArcNetworkConfig
from tallyguard.settlement import PaymentIntent, ProviderSubmission


RECIPIENT = "0x1111111111111111111111111111111111111111"


class CountingAdapter:
    name = "acceptance-fake"

    def __init__(self) -> None:
        self.submissions = 0

    def submit(self, intent: PaymentIntent) -> ProviderSubmission:
        self.submissions += 1
        return ProviderSubmission(
            provider_reference="provider-1",
            transaction_hash="0x" + "a" * 64,
            recipient=intent.recipient,
            amount_usdc=intent.amount_usdc,
            network=intent.network,
            block_number=42,
        )


def test_acceptance_exercises_real_api_and_proves_exactly_once_replay(tmp_path):
    adapter = CountingAdapter()
    report = run_testnet_acceptance(
        database_path=tmp_path / "acceptance.sqlite3",
        config=ArcNetworkConfig.for_network(ArcNetwork.TESTNET),
        settlement_adapter=adapter,
        recipient=RECIPIENT,
        amount_usdc=Decimal("0.01"),
        run_id="test-run",
    )

    assert report["decision_action"] == "PAY"
    assert report["receipt"]["status"] == "CONFIRMED"
    assert report["idempotent_replay"]["second_request_reused_receipt"] is True
    assert report["audit_chain_valid"] is True
    assert adapter.submissions == 1


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("0.100001")])
def test_acceptance_refuses_zero_or_above_ten_cents(tmp_path, amount):
    with pytest.raises(ValueError, match="at most"):
        run_testnet_acceptance(
            database_path=tmp_path / "acceptance.sqlite3",
            config=ArcNetworkConfig.for_network(ArcNetwork.TESTNET),
            settlement_adapter=CountingAdapter(),
            recipient=RECIPIENT,
            amount_usdc=amount,
        )


def test_acceptance_refuses_mainnet_even_with_fake_adapter(tmp_path):
    with pytest.raises(ValueError, match="locked"):
        run_testnet_acceptance(
            database_path=tmp_path / "acceptance.sqlite3",
            config=ArcNetworkConfig.for_network(ArcNetwork.MAINNET),
            settlement_adapter=CountingAdapter(),
            recipient=RECIPIENT,
            amount_usdc=Decimal("0.01"),
        )

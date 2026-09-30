from decimal import Decimal

import pytest

from tallyguard.mainnet_acceptance import (
    CONFIRMATION_PHRASE,
    run_mainnet_acceptance,
    validate_mainnet_acceptance_authorization,
    write_mainnet_acceptance_artifacts,
)
from tallyguard.network import ArcNetwork, ArcNetworkConfig
from tallyguard.settlement import PaymentIntent, ProviderSubmission, SimulatedArcAdapter


RECIPIENT = "0x1111111111111111111111111111111111111111"


class CountingMainnetAdapter(SimulatedArcAdapter):
    name = "mainnet-acceptance-fake"

    def __init__(self) -> None:
        self.submissions = 0

    def submit(self, intent: PaymentIntent) -> ProviderSubmission:
        self.submissions += 1
        return ProviderSubmission(
            provider_reference="provider-mainnet-1",
            transaction_hash="0x" + "d" * 64,
            recipient=intent.recipient,
            amount_usdc=intent.amount_usdc,
            network=intent.network,
            block_number=5042,
        )


def test_mainnet_acceptance_proves_approval_and_exactly_once_replay(tmp_path):
    adapter = CountingMainnetAdapter()

    report = run_mainnet_acceptance(
        database_path=tmp_path / "mainnet.sqlite3",
        config=ArcNetworkConfig.for_network(ArcNetwork.MAINNET),
        settlement_adapter=adapter,
        recipient=RECIPIENT,
        amount_usdc=Decimal("0.01"),
        treasury_available_usdc=Decimal("0.05"),
        run_id="mainnet-test-run",
    )

    assert report["classification"] == "synthetic-mainnet-path-test"
    assert report["network"] == "ARC-MAINNET"
    assert report["decision_action"] == "PAY"
    assert report["approval"]["status"] == "APPROVED"
    assert report["approval"]["requested_by_user_id"] != report["approval"]["resolved_by_user_id"]
    assert report["intent"]["approval_reference"] == report["approval"]["id"]
    assert report["intent"]["network"] == "ARC-MAINNET"
    assert report["receipt"]["status"] == "CONFIRMED"
    assert report["idempotent_replay"]["second_request_reused_receipt"] is True
    assert report["audit_chain_valid"] is True
    assert adapter.submissions == 1


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("0.010001")])
def test_mainnet_acceptance_refuses_zero_or_above_one_cent(tmp_path, amount):
    with pytest.raises(ValueError, match="at most"):
        run_mainnet_acceptance(
            database_path=tmp_path / "mainnet.sqlite3",
            config=ArcNetworkConfig.for_network(ArcNetwork.MAINNET),
            settlement_adapter=CountingMainnetAdapter(),
            recipient=RECIPIENT,
            amount_usdc=amount,
            treasury_available_usdc=Decimal("1"),
        )


def test_mainnet_acceptance_refuses_testnet(tmp_path):
    with pytest.raises(ValueError, match="locked"):
        run_mainnet_acceptance(
            database_path=tmp_path / "mainnet.sqlite3",
            config=ArcNetworkConfig.for_network(ArcNetwork.TESTNET),
            settlement_adapter=CountingMainnetAdapter(),
            recipient=RECIPIENT,
            amount_usdc=Decimal("0.01"),
            treasury_available_usdc=Decimal("1"),
        )


def test_mainnet_acceptance_requires_observed_balance(tmp_path):
    with pytest.raises(ValueError, match="balance"):
        run_mainnet_acceptance(
            database_path=tmp_path / "mainnet.sqlite3",
            config=ArcNetworkConfig.for_network(ArcNetwork.MAINNET),
            settlement_adapter=CountingMainnetAdapter(),
            recipient=RECIPIENT,
            amount_usdc=Decimal("0.01"),
            treasury_available_usdc=Decimal("0.009999"),
        )


def test_mainnet_artifact_labels_fake_path_as_synthetic(tmp_path):
    report = run_mainnet_acceptance(
        database_path=tmp_path / "mainnet.sqlite3",
        config=ArcNetworkConfig.for_network(ArcNetwork.MAINNET),
        settlement_adapter=CountingMainnetAdapter(),
        recipient=RECIPIENT,
        amount_usdc=Decimal("0.01"),
        treasury_available_usdc=Decimal("0.05"),
        run_id="artifact-test",
    )

    json_path, markdown_path, report_hash = write_mainnet_acceptance_artifacts(
        output_dir=tmp_path / "artifacts",
        acceptance=report,
    )

    assert len(report_hash) == 64
    assert '"classification": "synthetic-mainnet-path-test"' in json_path.read_text(
        encoding="utf-8"
    )
    summary = markdown_path.read_text(encoding="utf-8")
    assert "not customer traction" in summary
    assert report_hash in summary


def test_mainnet_cli_authorization_requires_every_independent_gate():
    mainnet = ArcNetworkConfig.for_network(ArcNetwork.MAINNET)
    valid = {
        "config": mainnet,
        "allow_mainnet": True,
        "confirmation": CONFIRMATION_PHRASE,
        "recipient": RECIPIENT,
        "amount_usdc": Decimal("0.01"),
        "adapter_cap_usdc": Decimal("0.10"),
    }
    validate_mainnet_acceptance_authorization(**valid)

    invalid_cases = (
        ({**valid, "confirmation": "MOVE-TESTNET-USDC"}, "confirm"),
        (
            {
                **valid,
                "config": ArcNetworkConfig.for_network(ArcNetwork.TESTNET),
            },
            "locked",
        ),
        ({**valid, "allow_mainnet": False}, "ALLOW_MAINNET"),
        ({**valid, "recipient": "not-an-address"}, "20-byte"),
        ({**valid, "amount_usdc": Decimal("0.02")}, "at most"),
        ({**valid, "amount_usdc": Decimal("0.0000001")}, "six decimal"),
        ({**valid, "adapter_cap_usdc": Decimal("0.11")}, "at most 0.10"),
        (
            {
                **valid,
                "amount_usdc": Decimal("0.01"),
                "adapter_cap_usdc": Decimal("0.009"),
            },
            "exceeds",
        ),
    )
    for values, message in invalid_cases:
        with pytest.raises(ValueError, match=message):
            validate_mainnet_acceptance_authorization(**values)

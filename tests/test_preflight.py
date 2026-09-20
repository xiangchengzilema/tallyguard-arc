from decimal import Decimal

from tallyguard.circle_arc import ArcRpcClient, CircleWalletSnapshot
from tallyguard.network import ArcNetwork, ArcNetworkConfig
from tallyguard.preflight import CheckStatus, run_preflight


WALLET = "0x1111111111111111111111111111111111111111"


def rpc_transport(chain_id: int):
    def transport(method: str, params: list[object]):
        if method == "eth_chainId":
            return hex(chain_id)
        if method == "eth_blockNumber":
            return hex(12345)
        if method == "eth_getCode":
            assert params[1] == "latest"
            return "0x6001600055"
        raise AssertionError(method)

    return transport


class FakeCircle:
    def __init__(self, snapshot: CircleWalletSnapshot) -> None:
        self.snapshot = snapshot

    def inspect_wallet(self) -> CircleWalletSnapshot:
        return self.snapshot


def wallet(**changes) -> CircleWalletSnapshot:
    values = {
        "wallet_id": "wallet-id",
        "address": WALLET,
        "blockchain": "ARC-TESTNET",
        "state": "LIVE",
        "usdc_balance": Decimal("4.50"),
    }
    values.update(changes)
    return CircleWalletSnapshot(**values)


def test_full_testnet_preflight_is_ready_without_any_write_operation():
    config = ArcNetworkConfig.for_network(ArcNetwork.TESTNET)
    report = run_preflight(
        config=config,
        arc_rpc=ArcRpcClient(config=config, transport=rpc_transport(config.chain_id)),
        circle=FakeCircle(wallet()),
        allow_mainnet=False,
        max_transfer_usdc=Decimal("0.10"),
    )

    assert report.safe_to_enable_live_adapter is True
    assert all(check.status == CheckStatus.PASS for check in report.checks)
    assert WALLET not in " ".join(check.detail for check in report.checks)


def test_network_only_never_claims_live_adapter_is_ready():
    config = ArcNetworkConfig.for_network(ArcNetwork.TESTNET)
    report = run_preflight(
        config=config,
        arc_rpc=ArcRpcClient(config=config, transport=rpc_transport(config.chain_id)),
        circle=None,
        allow_mainnet=False,
        max_transfer_usdc=Decimal("0.10"),
        network_only=True,
    )

    assert report.safe_to_enable_live_adapter is False
    assert report.checks[-1].status == CheckStatus.SKIP


def test_preflight_fails_closed_on_wrong_rpc_chain():
    config = ArcNetworkConfig.for_network(ArcNetwork.TESTNET)
    report = run_preflight(
        config=config,
        arc_rpc=ArcRpcClient(config=config, transport=rpc_transport(5042)),
        circle=FakeCircle(wallet()),
        allow_mainnet=False,
        max_transfer_usdc=Decimal("0.10"),
    )

    assert report.safe_to_enable_live_adapter is False
    assert next(check for check in report.checks if check.name == "arc_rpc").status == CheckStatus.FAIL


def test_mainnet_requires_explicit_gate_and_low_hard_cap():
    config = ArcNetworkConfig.for_network(ArcNetwork.MAINNET)
    report = run_preflight(
        config=config,
        arc_rpc=ArcRpcClient(config=config, transport=rpc_transport(config.chain_id)),
        circle=FakeCircle(wallet(blockchain="ARC")),
        allow_mainnet=False,
        max_transfer_usdc=Decimal("6"),
    )

    assert report.safe_to_enable_live_adapter is False
    failed = {check.name for check in report.checks if check.status == CheckStatus.FAIL}
    assert failed == {"transfer_cap", "mainnet_gate"}


def test_testnet_preflight_also_rejects_an_oversized_live_adapter_cap():
    config = ArcNetworkConfig.for_network(ArcNetwork.TESTNET)
    report = run_preflight(
        config=config,
        arc_rpc=ArcRpcClient(config=config, transport=rpc_transport(config.chain_id)),
        circle=FakeCircle(wallet()),
        allow_mainnet=False,
        max_transfer_usdc=Decimal("0.100001"),
    )

    assert report.safe_to_enable_live_adapter is False
    transfer_cap = next(check for check in report.checks if check.name == "transfer_cap")
    assert transfer_cap.status == CheckStatus.FAIL


def test_mainnet_can_be_ready_only_with_explicit_gate_and_safe_cap():
    config = ArcNetworkConfig.for_network(ArcNetwork.MAINNET)
    report = run_preflight(
        config=config,
        arc_rpc=ArcRpcClient(config=config, transport=rpc_transport(config.chain_id)),
        circle=FakeCircle(wallet(blockchain="ARC", usdc_balance=Decimal("0.25"))),
        allow_mainnet=True,
        max_transfer_usdc=Decimal("0.05"),
    )

    assert report.safe_to_enable_live_adapter is True


def test_wallet_network_state_and_canonical_balance_must_match():
    config = ArcNetworkConfig.for_network(ArcNetwork.TESTNET)
    cases = [
        wallet(blockchain="ARC"),
        wallet(state="FROZEN"),
        wallet(usdc_balance=None),
    ]

    for snapshot in cases:
        report = run_preflight(
            config=config,
            arc_rpc=ArcRpcClient(config=config, transport=rpc_transport(config.chain_id)),
            circle=FakeCircle(snapshot),
            allow_mainnet=False,
            max_transfer_usdc=Decimal("0.10"),
        )
        assert report.safe_to_enable_live_adapter is False
        assert next(check for check in report.checks if check.name == "circle_wallet").status == CheckStatus.FAIL

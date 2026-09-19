import pytest

from tallyguard.network import ArcNetwork, ArcNetworkConfig, MainnetSafetyError, require_mainnet_authorization


def test_official_arc_network_configuration():
    mainnet = ArcNetworkConfig.for_network(ArcNetwork.MAINNET)
    testnet = ArcNetworkConfig.for_network(ArcNetwork.TESTNET)
    assert mainnet.chain_id == 5042
    assert mainnet.rpc_url == "https://rpc.mainnet.arc.io"
    assert testnet.chain_id == 5042002
    assert testnet.rpc_url == "https://rpc.testnet.arc.io"
    assert mainnet.usdc_contract_address == testnet.usdc_contract_address
    assert mainnet.native_decimals == 18
    assert mainnet.erc20_decimals == 6


def test_mainnet_requires_both_feature_flag_and_approval_reference():
    config = ArcNetworkConfig.for_network(ArcNetwork.MAINNET)
    with pytest.raises(MainnetSafetyError):
        require_mainnet_authorization(config, allow_mainnet=False, approval_reference="approval-1")
    with pytest.raises(MainnetSafetyError):
        require_mainnet_authorization(config, allow_mainnet=True, approval_reference=None)
    require_mainnet_authorization(config, allow_mainnet=True, approval_reference="approval-1")


def test_testnet_does_not_require_mainnet_authorization():
    config = ArcNetworkConfig.for_network(ArcNetwork.TESTNET)
    require_mainnet_authorization(config, allow_mainnet=False, approval_reference=None)

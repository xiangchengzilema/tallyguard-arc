"""Canonical Arc network configuration and mainnet safety gates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os


USDC_ERC20_ADDRESS = "0x3600000000000000000000000000000000000000"


class ArcNetwork(StrEnum):
    TESTNET = "ARC-TESTNET"
    MAINNET = "ARC-MAINNET"


@dataclass(frozen=True, slots=True)
class ArcNetworkConfig:
    name: ArcNetwork
    chain_id: int
    rpc_url: str
    explorer_url: str
    usdc_contract_address: str = USDC_ERC20_ADDRESS
    native_symbol: str = "USDC"
    native_decimals: int = 18
    erc20_decimals: int = 6

    @property
    def is_mainnet(self) -> bool:
        return self.name == ArcNetwork.MAINNET

    @property
    def circle_blockchain(self) -> str:
        """Return Circle's API identifier for the selected Arc network.

        TallyGuard keeps the explicit ``ARC-MAINNET`` label internally so an
        operator cannot confuse a production intent with a testnet intent.
        Circle's Wallets API calls the same network simply ``ARC``.
        """

        return "ARC" if self.is_mainnet else ArcNetwork.TESTNET.value

    @classmethod
    def for_network(cls, network: ArcNetwork | str) -> "ArcNetworkConfig":
        selected = network if isinstance(network, ArcNetwork) else ArcNetwork(network.strip().upper())
        if selected == ArcNetwork.MAINNET:
            return cls(
                name=selected,
                chain_id=5042,
                rpc_url=os.getenv("ARC_MAINNET_RPC_URL", "https://rpc.mainnet.arc.io"),
                explorer_url="https://explorer.arc.io",
            )
        return cls(
            name=selected,
            chain_id=5042002,
            rpc_url=os.getenv("ARC_TESTNET_RPC_URL", "https://rpc.testnet.arc.io"),
            explorer_url="https://explorer.testnet.arc.io",
        )

    @classmethod
    def from_env(cls) -> "ArcNetworkConfig":
        return cls.for_network(os.getenv("TALLYGUARD_ARC_NETWORK", ArcNetwork.TESTNET.value))


class MainnetSafetyError(RuntimeError):
    """Raised when a real-money operation lacks explicit runtime authorization."""


def require_mainnet_authorization(
    config: ArcNetworkConfig,
    *,
    allow_mainnet: bool,
    approval_reference: str | None,
) -> None:
    if not config.is_mainnet:
        return
    if not allow_mainnet:
        raise MainnetSafetyError("Arc mainnet settlement is disabled by runtime policy.")
    if not approval_reference or not approval_reference.strip():
        raise MainnetSafetyError("Arc mainnet settlement requires an approval reference.")

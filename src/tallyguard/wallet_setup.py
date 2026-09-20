"""Explicit Circle developer-wallet provisioning for Arc Testnet.

This command creates account resources but never funds a wallet or submits a
transfer. It is intentionally locked to ARC-TESTNET and requires a typed
confirmation phrase before any Circle mutation.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from typing import Any
from uuid import uuid4

from .circle_arc import CircleConfigurationError, _enum_value
from .network import ArcNetwork
from .environment import load_local_environment


CONFIRMATION_PHRASE = "CREATE-ARC-TESTNET-WALLET"


@dataclass(frozen=True, slots=True)
class ProvisionedWallet:
    wallet_set_id: str
    wallet_id: str
    address: str
    blockchain: str
    state: str
    account_type: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AcceptanceWalletPair:
    wallet_set_id: str
    treasury: ProvisionedWallet
    recipient: ProvisionedWallet

    def to_dict(self) -> dict[str, object]:
        return {
            "wallet_set_id": self.wallet_set_id,
            "treasury": self.treasury.to_dict(),
            "recipient": self.recipient.to_dict(),
        }


class CircleWalletProvisioner:
    """Small official-SDK bridge dedicated to one Arc Testnet EOA wallet."""

    def __init__(self, *, api_key: str, entity_secret: str) -> None:
        if not api_key.strip() or not entity_secret.strip():
            raise CircleConfigurationError("Circle API key and entity secret are required.")
        try:
            from circle.web3 import developer_controlled_wallets, utils
        except ImportError as exc:  # pragma: no cover - optional dependency path
            raise CircleConfigurationError(
                "Install the project with the 'circle' extra before provisioning a wallet."
            ) from exc

        client = utils.init_developer_controlled_wallets_client(
            api_key=api_key,
            entity_secret=entity_secret,
            user_agent="TallyGuard/0.1",
        )
        self._sdk = developer_controlled_wallets
        self._wallet_sets = developer_controlled_wallets.WalletSetsApi(client)
        self._wallets = developer_controlled_wallets.WalletsApi(client)

    @classmethod
    def from_env(cls) -> "CircleWalletProvisioner":
        api_key = os.getenv("CIRCLE_WEB3_API_KEY", "")
        entity_secret = os.getenv("CIRCLE_ENTITY_SECRET", "")
        missing = [
            name
            for name, value in (
                ("CIRCLE_WEB3_API_KEY", api_key),
                ("CIRCLE_ENTITY_SECRET", entity_secret),
            )
            if not value.strip()
        ]
        if missing:
            raise CircleConfigurationError(f"Missing Circle configuration: {', '.join(missing)}.")
        return cls(api_key=api_key, entity_secret=entity_secret)

    def provision(
        self,
        *,
        wallet_set_id: str | None = None,
        wallet_set_name: str = "TallyGuard Arc Testnet",
    ) -> ProvisionedWallet:
        return self.provision_wallets(
            count=1,
            wallet_set_id=wallet_set_id,
            wallet_set_name=wallet_set_name,
        )[0]

    def provision_acceptance_pair(
        self,
        *,
        wallet_set_id: str | None = None,
        wallet_set_name: str = "TallyGuard Arc Testnet",
    ) -> AcceptanceWalletPair:
        wallets = self.provision_wallets(
            count=2,
            wallet_set_id=wallet_set_id,
            wallet_set_name=wallet_set_name,
        )
        return AcceptanceWalletPair(
            wallet_set_id=wallets[0].wallet_set_id,
            treasury=wallets[0],
            recipient=wallets[1],
        )

    def provision_wallets(
        self,
        *,
        count: int,
        wallet_set_id: str | None = None,
        wallet_set_name: str = "TallyGuard Arc Testnet",
    ) -> tuple[ProvisionedWallet, ...]:
        if count not in {1, 2}:
            raise ValueError("TallyGuard provisioning is limited to one wallet or one acceptance pair.")
        selected_set_id = (wallet_set_id or "").strip()
        if not selected_set_id:
            request = self._sdk.CreateWalletSetRequest.from_dict(
                {
                    "idempotencyKey": str(uuid4()),
                    "name": wallet_set_name,
                }
            )
            response = self._wallet_sets.create_wallet_set(request)
            selected_set_id = str(response.data.wallet_set.id)
        if not selected_set_id:
            raise CircleConfigurationError("Circle did not return a wallet set ID.")

        request = self._sdk.CreateWalletRequest.from_dict(
            {
                "idempotencyKey": str(uuid4()),
                "accountType": "EOA",
                "blockchains": [ArcNetwork.TESTNET.value],
                "count": count,
                "walletSetId": selected_set_id,
            }
        )
        response = self._wallets.create_wallet(request)
        wallets = response.data.wallets
        if len(wallets) != count:
            raise CircleConfigurationError(f"Circle did not return exactly {count} wallet(s).")
        provisioned: list[ProvisionedWallet] = []
        for item in wallets:
            wallet = _unwrap_wallet(item)
            blockchain = _enum_value(getattr(wallet, "blockchain", ""))
            if blockchain != ArcNetwork.TESTNET.value:
                raise CircleConfigurationError(
                    f"Circle created a wallet on {blockchain or '<unknown>'}, not ARC-TESTNET."
                )
            address = str(getattr(wallet, "address", ""))
            if not _is_evm_address(address):
                raise CircleConfigurationError("Circle returned an invalid wallet address.")
            wallet_id = str(getattr(wallet, "id", ""))
            if not wallet_id.strip():
                raise CircleConfigurationError("Circle returned an empty wallet ID.")
            provisioned.append(
                ProvisionedWallet(
                    wallet_set_id=selected_set_id,
                    wallet_id=wallet_id,
                    address=address,
                    blockchain=blockchain,
                    state=_enum_value(getattr(wallet, "state", "")),
                    account_type=str(getattr(wallet, "account_type", "")),
                )
            )
        if len({wallet.wallet_id for wallet in provisioned}) != count:
            raise CircleConfigurationError("Circle returned duplicate wallet IDs.")
        if len({wallet.address.lower() for wallet in provisioned}) != count:
            raise CircleConfigurationError("Circle returned duplicate wallet addresses.")
        return tuple(provisioned)


def _unwrap_wallet(value: Any) -> Any:
    return getattr(value, "actual_instance", None) or value


def _is_evm_address(value: str) -> bool:
    if len(value) != 42 or not value.startswith("0x"):
        return False
    try:
        int(value[2:], 16)
    except ValueError:
        return False
    return True


def main() -> None:
    load_local_environment()
    parser = argparse.ArgumentParser(
        description="Create one dedicated Circle developer-controlled EOA wallet on Arc Testnet"
    )
    parser.add_argument("--confirm", required=True, help=f"must equal {CONFIRMATION_PHRASE}")
    parser.add_argument(
        "--wallet-set-id",
        default=os.getenv("CIRCLE_WALLET_SET_ID", ""),
        help="reuse an existing Circle wallet set; otherwise one new set is created",
    )
    parser.add_argument("--wallet-set-name", default="TallyGuard Arc Testnet")
    parser.add_argument(
        "--acceptance-pair",
        action="store_true",
        help="create separate treasury and controlled-recipient wallets for Testnet acceptance",
    )
    args = parser.parse_args()

    if args.confirm != CONFIRMATION_PHRASE:
        raise SystemExit(f"Refusing to create account resources: --confirm must equal {CONFIRMATION_PHRASE}.")

    provisioner = CircleWalletProvisioner.from_env()
    if args.acceptance_pair:
        pair = provisioner.provision_acceptance_pair(
            wallet_set_id=args.wallet_set_id,
            wallet_set_name=args.wallet_set_name,
        )
        output = pair.to_dict()
        output["next_steps"] = [
            "Store treasury.wallet_id locally as CIRCLE_WALLET_ID.",
            "Store recipient.address locally as TALLYGUARD_ACCEPTANCE_RECIPIENT.",
            "Fund only treasury.address with Arc Testnet USDC from the Circle Faucet.",
            "Run tallyguard-preflight before the explicit acceptance transfer.",
        ]
    else:
        wallet = provisioner.provision(
            wallet_set_id=args.wallet_set_id,
            wallet_set_name=args.wallet_set_name,
        )
        output = wallet.to_dict()
        output["next_step"] = (
            "Store wallet_id locally as CIRCLE_WALLET_ID, request Arc Testnet USDC from the Circle "
            "Faucet for the returned address, then run tallyguard-preflight."
        )
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()

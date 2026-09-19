"""Read-only Circle and Arc settlement readiness checks.

This module never signs, submits, or estimates a transaction. It is safe to
run before supplying a low-balance wallet for testnet or controlled mainnet
acceptance testing.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import json
import os
from typing import Protocol

from .circle_arc import (
    ArcRpcClient,
    CircleConfigurationError,
    CircleSdkGateway,
    CircleWalletSnapshot,
)
from .network import ArcNetworkConfig


MAX_MAINNET_TRANSFER_CAP_USDC = Decimal("5")


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    network: str
    rpc_url: str
    safe_to_enable_live_adapter: bool
    checks: tuple[PreflightCheck, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "network": self.network,
            "rpc_url": self.rpc_url,
            "safe_to_enable_live_adapter": self.safe_to_enable_live_adapter,
            "checks": [asdict(check) for check in self.checks],
        }


class WalletInspector(Protocol):
    def inspect_wallet(self) -> CircleWalletSnapshot: ...


def run_preflight(
    *,
    config: ArcNetworkConfig,
    arc_rpc: ArcRpcClient,
    circle: WalletInspector | None,
    allow_mainnet: bool,
    max_transfer_usdc: Decimal,
    network_only: bool = False,
) -> PreflightReport:
    checks: list[PreflightCheck] = []

    if max_transfer_usdc <= 0:
        checks.append(PreflightCheck("transfer_cap", CheckStatus.FAIL, "Cap must be positive."))
    elif config.is_mainnet and max_transfer_usdc > MAX_MAINNET_TRANSFER_CAP_USDC:
        checks.append(
            PreflightCheck(
                "transfer_cap",
                CheckStatus.FAIL,
                f"Mainnet cap exceeds the {MAX_MAINNET_TRANSFER_CAP_USDC} USDC safety ceiling.",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                "transfer_cap",
                CheckStatus.PASS,
                f"Independent live-adapter cap is {max_transfer_usdc} USDC.",
            )
        )

    if config.is_mainnet and not allow_mainnet:
        checks.append(
            PreflightCheck(
                "mainnet_gate",
                CheckStatus.FAIL,
                "Mainnet is selected but TALLYGUARD_ALLOW_MAINNET is not true.",
            )
        )
    else:
        detail = "Explicit mainnet gate is enabled." if config.is_mainnet else "Testnet selected."
        checks.append(PreflightCheck("mainnet_gate", CheckStatus.PASS, detail))

    try:
        status = arc_rpc.inspect_network()
    except Exception as exc:
        checks.append(PreflightCheck("arc_rpc", CheckStatus.FAIL, str(exc)))
    else:
        checks.append(
            PreflightCheck(
                "arc_rpc",
                CheckStatus.PASS,
                f"Chain {status.chain_id}; latest block {status.latest_block}; canonical USDC code present.",
            )
        )

    if network_only:
        checks.append(
            PreflightCheck(
                "circle_wallet",
                CheckStatus.SKIP,
                "Skipped by --network-only; no credential was read.",
            )
        )
    elif circle is None:
        checks.append(
            PreflightCheck(
                "circle_wallet",
                CheckStatus.FAIL,
                "Circle credentials and wallet ID are required for full preflight.",
            )
        )
    else:
        try:
            wallet = circle.inspect_wallet()
            _validate_wallet(config, wallet)
        except Exception as exc:
            checks.append(PreflightCheck("circle_wallet", CheckStatus.FAIL, str(exc)))
        else:
            balance = "not returned" if wallet.usdc_balance is None else f"{wallet.usdc_balance} USDC"
            checks.append(
                PreflightCheck(
                    "circle_wallet",
                    CheckStatus.PASS,
                    f"Wallet {_redact_address(wallet.address)} is LIVE on {wallet.blockchain}; balance {balance}.",
                )
            )

    considered = [check for check in checks if check.status != CheckStatus.SKIP]
    safe = bool(considered) and all(check.status == CheckStatus.PASS for check in considered)
    if network_only:
        safe = False
    return PreflightReport(
        network=config.name.value,
        rpc_url=config.rpc_url,
        safe_to_enable_live_adapter=safe,
        checks=tuple(checks),
    )


def _validate_wallet(config: ArcNetworkConfig, wallet: CircleWalletSnapshot) -> None:
    if wallet.wallet_id.strip() == "":
        raise CircleConfigurationError("Circle wallet ID is empty.")
    if wallet.blockchain != config.circle_blockchain:
        raise CircleConfigurationError(
            f"Circle wallet network mismatch: expected {config.circle_blockchain}, received {wallet.blockchain}."
        )
    if wallet.state != "LIVE":
        raise CircleConfigurationError(f"Circle wallet is not LIVE; current state is {wallet.state}.")
    if not _is_evm_address(wallet.address):
        raise CircleConfigurationError("Circle wallet returned an invalid EVM address.")
    if wallet.usdc_balance is None:
        raise CircleConfigurationError("Canonical Arc USDC balance was not returned for this wallet.")
    if wallet.usdc_balance < 0:
        raise CircleConfigurationError("Circle returned a negative USDC balance.")


def _is_evm_address(value: str) -> bool:
    if len(value) != 42 or not value.startswith("0x"):
        return False
    try:
        int(value[2:], 16)
    except ValueError:
        return False
    return True


def _redact_address(value: str) -> str:
    return f"{value[:8]}...{value[-6:]}" if len(value) >= 16 else "<invalid>"


def _decimal_env(name: str, default: str) -> Decimal:
    raw = os.getenv(name, default)
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise CircleConfigurationError(f"{name} must be a decimal amount.") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Circle/Arc settlement preflight")
    parser.add_argument(
        "--network-only",
        action="store_true",
        help="check Arc RPC and safety gates without reading Circle credentials",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    config = ArcNetworkConfig.from_env()
    allow_mainnet = os.getenv("TALLYGUARD_ALLOW_MAINNET", "false").strip().lower() == "true"
    max_transfer = _decimal_env("TALLYGUARD_MAX_TRANSFER_USDC", "5")
    circle: WalletInspector | None = None
    setup_error: str | None = None
    if not args.network_only:
        try:
            circle = CircleSdkGateway.from_env(config)
        except CircleConfigurationError as exc:
            setup_error = str(exc)

    report = run_preflight(
        config=config,
        arc_rpc=ArcRpcClient(config=config),
        circle=circle,
        allow_mainnet=allow_mainnet,
        max_transfer_usdc=max_transfer,
        network_only=args.network_only,
    )
    if setup_error:
        checks = tuple(
            PreflightCheck(check.name, check.status, setup_error)
            if check.name == "circle_wallet"
            else check
            for check in report.checks
        )
        report = PreflightReport(
            network=report.network,
            rpc_url=report.rpc_url,
            safe_to_enable_live_adapter=False,
            checks=checks,
        )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"TallyGuard settlement preflight: {report.network}")
        for check in report.checks:
            print(f"[{check.status.value.upper():4}] {check.name}: {check.detail}")
        verdict = "READY" if report.safe_to_enable_live_adapter else "NOT READY"
        print(f"Live adapter verdict: {verdict}")
    raise SystemExit(0 if report.safe_to_enable_live_adapter or args.network_only else 1)


if __name__ == "__main__":
    main()

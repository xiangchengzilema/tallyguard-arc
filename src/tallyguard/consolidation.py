"""Guarded Arc Testnet wallet consolidation for controlled test inventory.

This is an operator maintenance command, not an application settlement path.
It is locked to Arc Testnet, requires an exact confirmation phrase, leaves a
small fee reserve in every source wallet, and independently verifies every
Circle transfer through Arc RPC before recording it as complete.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import json
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from .circle_arc import ArcRpcClient, CircleArcAdapter, CircleSdkGateway
from .environment import load_local_environment
from .network import ArcNetwork, ArcNetworkConfig
from .settlement import PaymentIntent, SettlementDenied


CONFIRMATION_PHRASE = "CONSOLIDATE-ARC-TESTNET-WALLETS"
USDC_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class InventoryWallet:
    kind: str
    label: str
    wallet_id: str
    address: str


def transfer_amount(
    balance: Decimal,
    *,
    reserve: Decimal,
    maximum: Decimal,
) -> Decimal | None:
    if reserve < 0:
        raise ValueError("Fee reserve cannot be negative.")
    if maximum <= 0:
        raise ValueError("Maximum source transfer must be positive.")
    amount = (balance - reserve).quantize(USDC_QUANTUM, rounding=ROUND_DOWN)
    if amount <= 0:
        return None
    if amount > maximum:
        raise ValueError(
            f"Planned transfer {amount} USDC exceeds the consolidation ceiling {maximum} USDC."
        )
    return amount


def _wallet(value: dict[str, Any], *, kind: str, label: str) -> InventoryWallet:
    wallet_id = str(value.get("wallet_id", "")).strip()
    address = str(value.get("address", "")).strip().lower()
    if not wallet_id:
        raise ValueError(f"{label} has no Circle wallet ID.")
    if len(address) != 42 or not address.startswith("0x"):
        raise ValueError(f"{label} has an invalid Arc address.")
    try:
        int(address[2:], 16)
    except ValueError as exc:
        raise ValueError(f"{label} has an invalid Arc address.") from exc
    return InventoryWallet(kind=kind, label=label, wallet_id=wallet_id, address=address)


def load_inventory(*, buyers_path: Path, creators_path: Path) -> tuple[InventoryWallet, ...]:
    buyers = json.loads(buyers_path.read_text(encoding="utf-8"))
    creators = json.loads(creators_path.read_text(encoding="utf-8"))
    if not isinstance(buyers, list) or not isinstance(creators, dict):
        raise ValueError("Wallet inventory JSON has an unexpected shape.")
    wallets = [
        _wallet(item, kind="buyer", label=str(item.get("buyer_id", "buyer")))
        for item in buyers
    ]
    wallets.extend(
        _wallet(item, kind="creator", label=str(item.get("creator_name", key)))
        for key, item in creators.items()
    )
    if len({item.wallet_id for item in wallets}) != len(wallets):
        raise ValueError("Wallet inventory contains duplicate Circle wallet IDs.")
    if len({item.address for item in wallets}) != len(wallets):
        raise ValueError("Wallet inventory contains duplicate Arc addresses.")
    return tuple(wallets)


def _read_balance_with_retry(
    rpc: ArcRpcClient,
    address: str,
    *,
    attempts: int = 6,
) -> Decimal:
    for attempt in range(1, attempts + 1):
        try:
            return rpc.read_usdc_balance(address)
        except SettlementDenied:
            if attempt == attempts:
                raise
            time.sleep(min(1.25 * attempt, 5))
    raise AssertionError("unreachable")


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def consolidate(
    *,
    buyers_path: Path,
    creators_path: Path,
    target_wallet_id: str,
    reserve_usdc: Decimal,
    maximum_source_transfer_usdc: Decimal,
    report_path: Path,
    execute: bool,
    rpc_delay_seconds: float,
) -> dict[str, Any]:
    config = ArcNetworkConfig.from_env()
    if config.name != ArcNetwork.TESTNET:
        raise ValueError("Wallet consolidation is locked to ARC-TESTNET.")
    rpc = ArcRpcClient(config=config)
    network = rpc.inspect_network()
    wallets = load_inventory(buyers_path=buyers_path, creators_path=creators_path)
    target = next((item for item in wallets if item.wallet_id == target_wallet_id), None)
    if target is None:
        raise ValueError("Target Circle wallet ID is not present in the supplied inventory.")

    target_gateway = CircleSdkGateway.from_env(config)
    if target_gateway.wallet_id != target.wallet_id:
        raise ValueError("CIRCLE_WALLET_ID must identify the selected treasury target.")
    target_snapshot = target_gateway.inspect_wallet()
    if target_snapshot.address.lower() != target.address:
        raise ValueError("Circle target wallet address does not match the inventory.")
    if target_snapshot.blockchain != config.circle_blockchain or target_snapshot.state != "LIVE":
        raise ValueError("Circle target wallet is not LIVE on Arc Testnet.")

    report: dict[str, Any] = {
        "schema_version": "1.0",
        "classification": "controlled Arc Testnet wallet consolidation",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "network": config.name.value,
        "chain_id": network.chain_id,
        "target": {
            "kind": target.kind,
            "label": target.label,
            "wallet_id": target.wallet_id,
            "address": target.address,
            "balance_before_usdc": format(target_snapshot.usdc_balance or Decimal("0"), "f"),
        },
        "fee_reserve_per_source_usdc": format(reserve_usdc, "f"),
        "maximum_source_transfer_usdc": format(maximum_source_transfer_usdc, "f"),
        "execute": execute,
        "items": [],
    }

    for wallet in wallets:
        if wallet.wallet_id == target.wallet_id:
            continue
        balance = _read_balance_with_retry(rpc, wallet.address)
        if rpc_delay_seconds > 0:
            time.sleep(rpc_delay_seconds)
        amount = transfer_amount(
            balance,
            reserve=reserve_usdc,
            maximum=maximum_source_transfer_usdc,
        )
        if amount is None:
            continue
        report["items"].append(
            {
                **asdict(wallet),
                "balance_before_usdc": format(balance, "f"),
                "amount_usdc": format(amount, "f"),
                "idempotency_key": str(uuid4()),
                "status": "PLANNED",
            }
        )
    report["summary"] = {
        "planned_wallets": len(report["items"]),
        "planned_amount_usdc": format(
            sum((Decimal(item["amount_usdc"]) for item in report["items"]), Decimal("0")),
            "f",
        ),
        "confirmed_wallets": 0,
        "confirmed_amount_usdc": "0",
        "failed_wallets": 0,
    }
    _write_report(report_path, report)
    if not execute:
        return report

    for index, item in enumerate(report["items"], start=1):
        try:
            gateway = CircleSdkGateway(
                api_key=os.environ["CIRCLE_WEB3_API_KEY"],
                entity_secret=os.environ["CIRCLE_ENTITY_SECRET"],
                wallet_id=item["wallet_id"],
                config=config,
            )
            snapshot = gateway.inspect_wallet()
            if snapshot.address.lower() != item["address"]:
                raise ValueError("Circle source wallet address does not match inventory.")
            if snapshot.blockchain != config.circle_blockchain or snapshot.state != "LIVE":
                raise ValueError("Circle source wallet is not LIVE on Arc Testnet.")
            actual_amount = transfer_amount(
                snapshot.usdc_balance or Decimal("0"),
                reserve=reserve_usdc,
                maximum=maximum_source_transfer_usdc,
            )
            if actual_amount is None:
                item["status"] = "SKIPPED"
                item["detail"] = "Balance no longer exceeds the fee reserve."
                _write_report(report_path, report)
                continue
            item["amount_usdc"] = format(actual_amount, "f")
            item["circle_balance_before_usdc"] = format(
                snapshot.usdc_balance or Decimal("0"), "f"
            )
            _write_report(report_path, report)
            adapter = CircleArcAdapter(
                config=config,
                circle=gateway,
                arc_rpc=rpc,
                max_transfer_usdc=maximum_source_transfer_usdc,
                max_poll_attempts=40,
                poll_interval_seconds=2,
            )
            intent = PaymentIntent(
                id=f"consolidation-{item['wallet_id']}",
                organization_id="tallyguard-testnet-maintenance",
                invoice_id=f"consolidation-{item['wallet_id']}",
                decision_id="operator-authorized-testnet-consolidation",
                recipient=target.address,
                amount_usdc=actual_amount,
                network=ArcNetwork.TESTNET,
                idempotency_key=item["idempotency_key"],
                approval_reference=CONFIRMATION_PHRASE,
            )
            submission = adapter.submit(intent)
            item.update(
                {
                    "status": "CONFIRMED",
                    "provider_reference": submission.provider_reference,
                    "transaction_hash": submission.transaction_hash,
                    "block_number": submission.block_number,
                    "explorer_url": f"{config.explorer_url}/tx/{submission.transaction_hash}",
                }
            )
            print(
                f"[{index}/{len(report['items'])}] confirmed {actual_amount} USDC "
                f"from {item['label']} at block {submission.block_number}",
                flush=True,
            )
        except Exception as exc:  # operator report must retain per-wallet failures
            item["status"] = "FAILED"
            item["error_type"] = type(exc).__name__
            item["error"] = str(exc)[:500]
            print(
                f"[{index}/{len(report['items'])}] failed {item['label']}: {type(exc).__name__}",
                flush=True,
            )
        finally:
            confirmed = [entry for entry in report["items"] if entry["status"] == "CONFIRMED"]
            failed = [entry for entry in report["items"] if entry["status"] == "FAILED"]
            report["summary"].update(
                {
                    "confirmed_wallets": len(confirmed),
                    "confirmed_amount_usdc": format(
                        sum(
                            (Decimal(entry["amount_usdc"]) for entry in confirmed),
                            Decimal("0"),
                        ),
                        "f",
                    ),
                    "failed_wallets": len(failed),
                }
            )
            _write_report(report_path, report)

    target_after = _read_balance_with_retry(rpc, target.address)
    report["target"]["balance_after_usdc"] = format(target_after, "f")
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    report["status"] = "complete" if report["summary"]["failed_wallets"] == 0 else "partial"
    _write_report(report_path, report)
    return report


def main() -> None:
    load_local_environment()
    parser = argparse.ArgumentParser(
        description="Consolidate controlled Circle Arc Testnet wallets into one treasury wallet"
    )
    parser.add_argument("--buyers", required=True, type=Path)
    parser.add_argument("--creators", required=True, type=Path)
    parser.add_argument("--target-wallet-id", default=os.getenv("CIRCLE_WALLET_ID", ""))
    parser.add_argument("--reserve-usdc", default="0.005")
    parser.add_argument("--maximum-source-transfer-usdc", default="50")
    parser.add_argument("--rpc-delay-seconds", type=float, default=0.25)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.target_wallet_id.strip():
        raise SystemExit("Set --target-wallet-id or CIRCLE_WALLET_ID.")
    if args.execute and args.confirm != CONFIRMATION_PHRASE:
        raise SystemExit(f"Refusing to move funds: --confirm must equal {CONFIRMATION_PHRASE}.")
    report = consolidate(
        buyers_path=args.buyers,
        creators_path=args.creators,
        target_wallet_id=args.target_wallet_id,
        reserve_usdc=Decimal(args.reserve_usdc),
        maximum_source_transfer_usdc=Decimal(args.maximum_source_transfer_usdc),
        report_path=args.report,
        execute=args.execute,
        rpc_delay_seconds=args.rpc_delay_seconds,
    )
    print(json.dumps(report["summary"], indent=2), flush=True)
    print(f"Consolidation report: {args.report.resolve()}", flush=True)
    if report.get("status") == "partial":
        raise SystemExit("One or more wallet transfers failed; inspect and resume with saved keys.")


if __name__ == "__main__":
    main()

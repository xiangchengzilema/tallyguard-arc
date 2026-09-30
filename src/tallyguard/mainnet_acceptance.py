"""Explicit, role-separated, low-value Arc Mainnet acceptance workflow.

Importing this module never reads credentials or submits a transaction. The CLI
requires an exact confirmation phrase, an enabled mainnet gate, a full read-only
preflight, and a hard-capped amount before constructing the Circle adapter.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .acceptance import _headers, _json_bytes, _post_json, _session, _decimal
from .api import create_app
from .circle_arc import ArcRpcClient, CircleArcAdapter, CircleSdkGateway
from .environment import load_local_environment
from .network import ArcNetwork, ArcNetworkConfig
from .preflight import run_preflight
from .settlement import EVM_ADDRESS, SettlementAdapter, SimulatedArcAdapter


MAX_MAINNET_ACCEPTANCE_AMOUNT_USDC = Decimal("0.01")
MAX_MAINNET_ADAPTER_CAP_USDC = Decimal("0.10")
CONFIRMATION_PHRASE = "MOVE-MAINNET-USDC"


def validate_mainnet_acceptance_authorization(
    *,
    config: ArcNetworkConfig,
    allow_mainnet: bool,
    confirmation: str,
    recipient: str,
    amount_usdc: Decimal,
    adapter_cap_usdc: Decimal,
) -> None:
    """Fail before credential loading unless every operator safety gate agrees."""

    if confirmation != CONFIRMATION_PHRASE:
        raise ValueError(
            f"--confirm must exactly equal {CONFIRMATION_PHRASE}."
        )
    if config.name != ArcNetwork.MAINNET:
        raise ValueError("This command is locked to ARC-MAINNET.")
    if not allow_mainnet:
        raise ValueError("TALLYGUARD_ALLOW_MAINNET must be true.")
    if not EVM_ADDRESS.fullmatch(recipient):
        raise ValueError("Mainnet acceptance recipient must be a 20-byte EVM address.")
    if amount_usdc <= 0 or amount_usdc > MAX_MAINNET_ACCEPTANCE_AMOUNT_USDC:
        raise ValueError(
            "Mainnet acceptance amount must be above 0 and at most "
            f"{MAX_MAINNET_ACCEPTANCE_AMOUNT_USDC} USDC."
        )
    if amount_usdc * Decimal(1_000_000) != (
        amount_usdc * Decimal(1_000_000)
    ).to_integral_value():
        raise ValueError("Mainnet acceptance amount supports at most six decimal places.")
    if adapter_cap_usdc <= 0 or adapter_cap_usdc > MAX_MAINNET_ADAPTER_CAP_USDC:
        raise ValueError(
            "TALLYGUARD_MAX_TRANSFER_USDC must be above 0 and at most 0.10 "
            "for mainnet acceptance."
        )
    if amount_usdc > adapter_cap_usdc:
        raise ValueError("Acceptance amount exceeds TALLYGUARD_MAX_TRANSFER_USDC.")


def run_mainnet_acceptance(
    *,
    database_path: str | Path,
    config: ArcNetworkConfig,
    settlement_adapter: SettlementAdapter,
    recipient: str,
    amount_usdc: Decimal,
    treasury_available_usdc: Decimal,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Exercise the exact mainnet approval and settlement path once.

    The operator and approver are separate durable principals. This helper is
    safe to use with a fake adapter in tests; only the guarded CLI constructs a
    live Circle adapter.
    """

    if config.name != ArcNetwork.MAINNET:
        raise ValueError("Mainnet acceptance runner is locked to ARC-MAINNET.")
    if amount_usdc <= 0 or amount_usdc > MAX_MAINNET_ACCEPTANCE_AMOUNT_USDC:
        raise ValueError(
            "Mainnet acceptance amount must be above 0 and at most "
            f"{MAX_MAINNET_ACCEPTANCE_AMOUNT_USDC} USDC."
        )
    if amount_usdc * Decimal(1_000_000) != (
        amount_usdc * Decimal(1_000_000)
    ).to_integral_value():
        raise ValueError("Mainnet acceptance amount supports at most six decimal places.")
    if treasury_available_usdc < amount_usdc:
        raise ValueError("Observed Circle wallet balance is below the acceptance amount.")

    identity = run_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid4().hex[:8]
    )
    invoice_id = f"mainnet-acceptance-{identity}"
    vendor_id = f"mainnet-vendor-{identity}"
    invoice_number = f"TG-MAINNET-{identity}"
    amount = format(amount_usdc, "f")
    due_date = (date.today() + timedelta(days=14)).isoformat()
    invoice_document = _json_bytes(
        {
            "invoice_id": invoice_id,
            "vendor_id": vendor_id,
            "invoice_number": invoice_number,
            "currency": "USDC",
            "amount": amount,
            "due_date": due_date,
            "payment_wallet_address": recipient,
        }
    )
    purchase_order = _json_bytes(
        {
            "purchase_order_id": f"po-{identity}",
            "vendor_id": vendor_id,
            "po_number": f"PO-MAINNET-{identity}",
            "currency": "USDC",
            "authorized_amount": amount,
        }
    )
    delivery = _json_bytes(
        {
            "delivery_id": f"delivery-{identity}",
            "purchase_order_id": f"po-{identity}",
            "delivered_value": amount,
        }
    )

    app = create_app(
        database_path=database_path,
        testing=True,
        settlement_adapter=settlement_adapter,
        settlement_config=config,
        allow_mainnet=True,
    )
    client = app.test_client()
    try:
        admin = _session(client, "admin")
        operator = _session(client, "operator")
        approver = _session(client, "approver")
        auditor = _session(client, "auditor")

        _post_json(
            client,
            "/api/policies",
            {
                "version": f"mainnet-acceptance-policy-{identity}",
                "daily_payment_limit_usdc": amount,
                "daily_autonomous_payment_limit_usdc": amount,
                "autonomous_payments_enabled": True,
                "minimum_cash_reserve_usdc": "0",
                "maximum_autonomous_payment_usdc": amount,
                "po_amount_tolerance_usdc": "0",
                "allowed_asset": "USDC",
                "allowed_network": ArcNetwork.MAINNET.value,
                "kill_switch_enabled": False,
            },
            admin,
        )
        _post_json(
            client,
            "/api/treasury/snapshots" if isinstance(settlement_adapter, SimulatedArcAdapter) else "/api/treasury/snapshots/refresh",
            {
                "available_usdc": format(treasury_available_usdc, "f"),
                "spent_today_usdc": "0",
                "source_reference": f"circle-mainnet-preflight-{identity}",
            },
            operator,
        )
        _post_json(
            client,
            "/api/vendors",
            {
                "id": vendor_id,
                "legal_name": "TallyGuard Controlled Mainnet Recipient",
                "approved_wallet_address": recipient,
                "autopay_limit": amount,
                "verification_method": "MANUAL_REVIEW",
                "verification_reference": f"mainnet-acceptance-cli-{identity}",
            },
            operator,
        )
        _post_json(
            client,
            "/api/invoices",
            {
                "id": invoice_id,
                "vendor_id": vendor_id,
                "invoice_number": invoice_number,
                "currency": "USDC",
                "amount": amount,
                "due_date": due_date,
                "payment_wallet_address": recipient,
                "source_document_hash": hashlib.sha256(invoice_document).hexdigest(),
            },
            operator,
        )
        for evidence_type, filename, content in (
            ("INVOICE", "invoice.json", invoice_document),
            ("PURCHASE_ORDER", "purchase-order.json", purchase_order),
            ("DELIVERY", "delivery.json", delivery),
        ):
            response = client.post(
                f"/api/invoices/{invoice_id}/evidence",
                data={
                    "evidence_type": evidence_type,
                    "file": (BytesIO(content), filename, "application/json"),
                },
                content_type="multipart/form-data",
                headers=_headers(operator, f"{identity}-{evidence_type.lower()}"),
            )
            if response.status_code != 201:
                raise RuntimeError(
                    f"Mainnet evidence upload returned HTTP {response.status_code}: "
                    f"{response.get_json(silent=True)}"
                )

        evaluated = _post_json(
            client,
            f"/api/invoices/{invoice_id}/evaluate",
            {},
            operator,
        )
        decision = evaluated["decision"]
        if decision["final_action"] != "PAY":
            raise RuntimeError(
                "Mainnet acceptance evidence did not authorize PAY: "
                f"{decision['reason_codes']}"
            )

        requested = _post_json(
            client,
            f"/api/decisions/{decision['id']}/request-mainnet-approval",
            {},
            operator,
        )["approval"]
        resolved = _post_json(
            client,
            f"/api/approvals/{requested['id']}/resolve",
            {
                "approve": True,
                "note": "Controlled low-value mainnet proof reviewed before transfer.",
                "expected_version": requested["version"],
            },
            approver,
        )["approval"]
        if resolved["status"] != "APPROVED":
            raise RuntimeError("Mainnet approval did not reach APPROVED state.")
        if resolved["requested_by_user_id"] == resolved["resolved_by_user_id"]:
            raise RuntimeError("Mainnet approval violated role separation.")

        settlement_payload = {
            "decision_id": decision["id"],
            "approval_reference": resolved["id"],
        }
        first = _post_json(
            client,
            f"/api/invoices/{invoice_id}/settle",
            settlement_payload,
            approver,
        )["payment"]
        second = _post_json(
            client,
            f"/api/invoices/{invoice_id}/settle",
            settlement_payload,
            approver,
        )["payment"]
        if first["receipt"] != second["receipt"] or second["reused_receipt"] is not True:
            raise RuntimeError(
                "Mainnet idempotent replay did not return the original settlement receipt."
            )

        audit_response = client.get(
            "/api/audit/events",
            headers=_headers(auditor, f"{identity}-audit"),
        )
        if audit_response.status_code != 200:
            raise RuntimeError(
                f"Mainnet audit read returned HTTP {audit_response.status_code}."
            )
        audit = audit_response.get_json()
        if audit["chain_valid"] is not True:
            raise RuntimeError("Tenant audit hash chain did not verify after mainnet settlement.")

        provider = str(first["receipt"]["provider"])
        return {
            "run_id": identity,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "classification": (
                "controlled-live-mainnet-proof"
                if provider == "circle-developer-wallets+arc-rpc"
                else "synthetic-mainnet-path-test"
            ),
            "network": config.name.value,
            "amount_usdc": amount,
            "treasury_available_usdc": format(treasury_available_usdc, "f"),
            "invoice_id": invoice_id,
            "decision_id": decision["id"],
            "decision_action": decision["final_action"],
            "evidence_manifest_hash": decision["evidence_manifest_hash"],
            "approval": {
                "id": resolved["id"],
                "status": resolved["status"],
                "requested_by_user_id": resolved["requested_by_user_id"],
                "resolved_by_user_id": resolved["resolved_by_user_id"],
            },
            "intent": first["intent"],
            "receipt": first["receipt"],
            "idempotent_replay": {
                "same_receipt": True,
                "second_request_reused_receipt": True,
            },
            "audit_chain_valid": True,
            "audit_event_count": len(audit["items"]),
            "database_path": str(Path(database_path).resolve()),
        }
    finally:
        app.extensions["tallyguard_repository"].close()


def write_mainnet_acceptance_artifacts(
    *,
    output_dir: Path,
    acceptance: dict[str, Any],
) -> tuple[Path, Path, str]:
    """Write content-addressed machine and reviewer mainnet evidence."""

    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(acceptance["run_id"])
    report_path = output_dir / f"{run_id}.json"
    report_bytes = (
        json.dumps(acceptance, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    report_path.write_bytes(report_bytes)
    report_hash = hashlib.sha256(report_bytes).hexdigest()
    receipt = dict(acceptance["receipt"])
    approval = dict(acceptance["approval"])
    replay = dict(acceptance["idempotent_replay"])
    summary_path = output_dir / f"{run_id}.md"
    summary = f"""# TallyGuard controlled Arc Mainnet acceptance

- Classification: `{acceptance['classification']}`
- Run: `{run_id}`
- Completed: `{acceptance['completed_at']}`
- Network: `{acceptance['network']}`
- Amount: `{acceptance['amount_usdc']} USDC`
- Decision: `{acceptance['decision_action']}` (`{acceptance['decision_id']}`)
- Approval: `{approval['status']}` (`{approval['id']}`), requester `{approval['requested_by_user_id']}`, resolver `{approval['resolved_by_user_id']}`
- Evidence manifest: `{acceptance['evidence_manifest_hash']}`
- Transaction: `{receipt['transaction_hash']}`
- Block: `{receipt['block_number']}`
- Provider: `{receipt['provider']}`
- Explorer: {receipt['explorer_url']}
- Idempotent replay: `same receipt = {str(replay['same_receipt']).lower()}`; `second request reused = {str(replay['second_request_reused_receipt']).lower()}`
- Tenant audit chain: `valid = {str(acceptance['audit_chain_valid']).lower()}` across `{acceptance['audit_event_count']}` events
- JSON report SHA-256: `{report_hash}`

This is a controlled, self-operated low-value acceptance proof. It is not customer traction,
revenue, or evidence of organic product usage. Only a report whose provider is
`circle-developer-wallets+arc-rpc` and whose Arc Explorer transaction independently verifies
may be presented as a live mainnet proof.
"""
    summary_path.write_text(summary, encoding="utf-8")
    return report_path, summary_path, report_hash


def main() -> None:
    load_local_environment()
    parser = argparse.ArgumentParser(
        description="Run one explicitly approved, low-value Arc Mainnet USDC acceptance transfer"
    )
    parser.add_argument("--confirm", required=True, help=f"must equal {CONFIRMATION_PHRASE}")
    parser.add_argument(
        "--recipient",
        default=os.getenv("TALLYGUARD_MAINNET_ACCEPTANCE_RECIPIENT", ""),
        help="controlled Arc Mainnet recipient address distinct from the treasury wallet",
    )
    parser.add_argument("--amount", default="0.01", help="mainnet USDC amount, maximum 0.01")
    parser.add_argument("--output-dir", default="artifacts/mainnet-acceptance")
    args = parser.parse_args()

    config = ArcNetworkConfig.from_env()
    allow_mainnet = os.getenv("TALLYGUARD_ALLOW_MAINNET", "false").strip().lower() == "true"
    recipient = args.recipient.strip()
    amount = _decimal(args.amount, field="--amount")
    cap = _decimal(os.getenv("TALLYGUARD_MAX_TRANSFER_USDC", "0.10"), field="transfer cap")
    try:
        validate_mainnet_acceptance_authorization(
            config=config,
            allow_mainnet=allow_mainnet,
            confirmation=args.confirm,
            recipient=recipient,
            amount_usdc=amount,
            adapter_cap_usdc=cap,
        )
    except ValueError as exc:
        raise SystemExit(f"Refusing to move funds: {exc}") from exc

    circle = CircleSdkGateway.from_env(config)
    arc_rpc = ArcRpcClient(config=config)
    preflight = run_preflight(
        config=config,
        arc_rpc=arc_rpc,
        circle=circle,
        allow_mainnet=True,
        max_transfer_usdc=cap,
    )
    if not preflight.safe_to_enable_live_adapter:
        raise SystemExit(json.dumps(preflight.to_dict(), indent=2))
    wallet = circle.inspect_wallet()
    if wallet.usdc_balance is None or wallet.usdc_balance < amount:
        raise SystemExit("Configured Circle wallet does not have enough canonical Arc Mainnet USDC.")
    if wallet.address.lower() == recipient.lower():
        raise SystemExit("Mainnet acceptance recipient must differ from the treasury wallet.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    database_path = output_dir / f"{run_id}.sqlite3"
    adapter = CircleArcAdapter(
        config=config,
        circle=circle,
        arc_rpc=arc_rpc,
        max_transfer_usdc=cap,
    )
    acceptance = run_mainnet_acceptance(
        database_path=database_path,
        config=config,
        settlement_adapter=adapter,
        recipient=recipient,
        amount_usdc=amount,
        treasury_available_usdc=wallet.usdc_balance,
        run_id=run_id,
    )
    if acceptance["classification"] != "controlled-live-mainnet-proof":
        raise SystemExit("Live mainnet acceptance did not use the Circle adapter.")
    report_path, summary_path, report_hash = write_mainnet_acceptance_artifacts(
        output_dir=output_dir,
        acceptance=acceptance,
    )
    print(json.dumps(acceptance, indent=2))
    print(f"Acceptance report: {report_path.resolve()}")
    print(f"Reviewer summary: {summary_path.resolve()}")
    print(f"Report SHA-256: {report_hash}")


if __name__ == "__main__":
    main()

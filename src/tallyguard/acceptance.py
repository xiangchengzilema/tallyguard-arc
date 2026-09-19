"""Explicit, low-value Arc Testnet settlement acceptance workflow."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask.testing import FlaskClient

from .api import create_app
from .circle_arc import ArcRpcClient, CircleArcAdapter, CircleSdkGateway
from .network import ArcNetwork, ArcNetworkConfig
from .preflight import run_preflight
from .settlement import SettlementAdapter


MAX_ACCEPTANCE_AMOUNT_USDC = Decimal("0.10")
CONFIRMATION_PHRASE = "MOVE-TESTNET-USDC"


def run_testnet_acceptance(
    *,
    database_path: str | Path,
    config: ArcNetworkConfig,
    settlement_adapter: SettlementAdapter,
    recipient: str,
    amount_usdc: Decimal,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Exercise evidence -> decision -> settlement -> replay against an adapter."""

    if config.name != ArcNetwork.TESTNET:
        raise ValueError("Acceptance runner is locked to ARC-TESTNET.")
    if amount_usdc <= 0 or amount_usdc > MAX_ACCEPTANCE_AMOUNT_USDC:
        raise ValueError(f"Acceptance amount must be above 0 and at most {MAX_ACCEPTANCE_AMOUNT_USDC} USDC.")
    if amount_usdc * Decimal(1_000_000) != (amount_usdc * Decimal(1_000_000)).to_integral_value():
        raise ValueError("Acceptance amount supports at most six decimal places.")

    identity = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    invoice_id = f"acceptance-{identity}"
    vendor_id = f"vendor-{identity}"
    invoice_number = f"TG-{identity}"
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
            "po_number": f"PO-{identity}",
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
                "version": f"acceptance-policy-{identity}",
                "daily_payment_limit_usdc": "1",
                "minimum_cash_reserve_usdc": "0",
                "maximum_autonomous_payment_usdc": format(MAX_ACCEPTANCE_AMOUNT_USDC, "f"),
                "po_amount_tolerance_usdc": "0",
                "allowed_asset": "USDC",
                "allowed_network": ArcNetwork.TESTNET.value,
                "kill_switch_enabled": False,
            },
            admin,
        )
        _post_json(
            client,
            "/api/treasury/snapshots",
            {
                "available_usdc": "1",
                "spent_today_usdc": "0",
                "source_reference": f"circle-preflight-{identity}",
            },
            operator,
        )
        _post_json(
            client,
            "/api/vendors",
            {
                "id": vendor_id,
                "legal_name": "TallyGuard Controlled Test Recipient",
                "approved_wallet_address": recipient,
                "autopay_limit": format(MAX_ACCEPTANCE_AMOUNT_USDC, "f"),
                "verification_method": "MANUAL_REVIEW",
                "verification_reference": f"acceptance-cli-{identity}",
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
            _expect(response, 201)

        evaluated = _post_json(
            client,
            f"/api/invoices/{invoice_id}/evaluate",
            {},
            operator,
        )
        decision = evaluated["decision"]
        if decision["final_action"] != "PAY":
            raise RuntimeError(f"Acceptance evidence did not authorize PAY: {decision['reason_codes']}")

        first = _post_json(
            client,
            f"/api/invoices/{invoice_id}/settle",
            {"decision_id": decision["id"]},
            approver,
        )["payment"]
        second = _post_json(
            client,
            f"/api/invoices/{invoice_id}/settle",
            {"decision_id": decision["id"]},
            approver,
        )["payment"]
        if first["receipt"] != second["receipt"] or second["reused_receipt"] is not True:
            raise RuntimeError("Idempotent replay did not return the original settlement receipt.")

        audit_response = client.get("/api/audit/events", headers=_headers(auditor, f"{identity}-audit"))
        _expect(audit_response, 200)
        audit = audit_response.get_json()
        if audit["chain_valid"] is not True:
            raise RuntimeError("Tenant audit hash chain did not verify after settlement.")

        return {
            "run_id": identity,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "network": config.name.value,
            "amount_usdc": amount,
            "invoice_id": invoice_id,
            "decision_id": decision["id"],
            "decision_action": decision["final_action"],
            "evidence_manifest_hash": decision["evidence_manifest_hash"],
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


def _json_bytes(value: dict[str, str]) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _headers(token: str, correlation_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Correlation-ID": correlation_id}


def _session(client: FlaskClient, role: str) -> str:
    response = client.post("/api/demo/session", json={"role": role})
    _expect(response, 200)
    return str(response.get_json()["access_token"])


def _post_json(client: FlaskClient, path: str, payload: dict[str, Any], token: str) -> dict[str, Any]:
    response = client.post(path, json=payload, headers=_headers(token, f"acceptance-{uuid4().hex}"))
    _expect(response, 200, 201)
    return dict(response.get_json())


def _expect(response: Any, *statuses: int) -> None:
    if response.status_code not in statuses:
        payload = response.get_json(silent=True)
        raise RuntimeError(f"Acceptance API returned HTTP {response.status_code}: {payload}")


def _decimal(value: str, *, field: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a decimal amount.") from exc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an explicit low-value end-to-end Arc Testnet USDC acceptance transfer"
    )
    parser.add_argument("--confirm", required=True, help=f"must equal {CONFIRMATION_PHRASE}")
    parser.add_argument(
        "--recipient",
        default=os.getenv("TALLYGUARD_ACCEPTANCE_RECIPIENT", ""),
        help="controlled Arc Testnet recipient address",
    )
    parser.add_argument("--amount", default="0.01", help="test USDC amount, maximum 0.10")
    parser.add_argument("--output-dir", default="artifacts/acceptance")
    args = parser.parse_args()

    if args.confirm != CONFIRMATION_PHRASE:
        raise SystemExit(f"Refusing to move funds: --confirm must equal {CONFIRMATION_PHRASE}.")
    config = ArcNetworkConfig.from_env()
    if config.name != ArcNetwork.TESTNET:
        raise SystemExit("Refusing to run: this command is locked to ARC-TESTNET.")
    recipient = args.recipient.strip()
    if not recipient:
        raise SystemExit("Set --recipient or TALLYGUARD_ACCEPTANCE_RECIPIENT.")
    amount = _decimal(args.amount, field="--amount")
    cap = _decimal(os.getenv("TALLYGUARD_MAX_TRANSFER_USDC", "5"), field="transfer cap")
    if amount > cap:
        raise SystemExit("Acceptance amount exceeds TALLYGUARD_MAX_TRANSFER_USDC.")

    circle = CircleSdkGateway.from_env(config)
    arc_rpc = ArcRpcClient(config=config)
    report = run_preflight(
        config=config,
        arc_rpc=arc_rpc,
        circle=circle,
        allow_mainnet=False,
        max_transfer_usdc=cap,
    )
    if not report.safe_to_enable_live_adapter:
        raise SystemExit(json.dumps(report.to_dict(), indent=2))
    wallet = circle.inspect_wallet()
    if wallet.usdc_balance is None or wallet.usdc_balance < amount:
        raise SystemExit("Configured Circle wallet does not have enough canonical Arc Testnet USDC.")

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
    acceptance = run_testnet_acceptance(
        database_path=database_path,
        config=config,
        settlement_adapter=adapter,
        recipient=recipient,
        amount_usdc=amount,
        run_id=run_id,
    )
    report_path = output_dir / f"{run_id}.json"
    report_path.write_text(json.dumps(acceptance, indent=2), encoding="utf-8")
    print(json.dumps(acceptance, indent=2))
    print(f"Acceptance report: {report_path.resolve()}")


if __name__ == "__main__":
    main()

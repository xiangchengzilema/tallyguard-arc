"""Real-HTTP deployment smoke test for the public-safe TallyGuard stack."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask
from werkzeug.serving import WSGIRequestHandler, make_server

from .api import create_app


class DeploymentSmokeError(RuntimeError):
    """Raised when a deployment contract does not survive real HTTP."""


class _QuietRequestHandler(WSGIRequestHandler):
    def log(self, request_type: str, message: str, *args: Any) -> None:
        del request_type, message, args


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    correlation_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    headers = {"Accept": "application/json"}
    data = None
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if correlation_id is not None:
        headers["X-Correlation-ID"] = correlation_id
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(
        f"{base_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise DeploymentSmokeError(
            f"{method} {path} returned HTTP {exc.code}: {body[:500]}"
        ) from exc
    except URLError as exc:
        raise DeploymentSmokeError(f"{method} {path} failed: {exc.reason}") from exc


def _json_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    correlation_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    status, headers, body = _request(
        base_url,
        path,
        method=method,
        token=token,
        correlation_id=correlation_id,
        payload=payload,
    )
    try:
        decoded = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentSmokeError(f"{method} {path} did not return valid JSON.") from exc
    if not isinstance(decoded, dict):
        raise DeploymentSmokeError(f"{method} {path} returned a non-object JSON body.")
    return status, headers, decoded


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DeploymentSmokeError(message)


def run_deployment_smoke(app: Flask) -> dict[str, Any]:
    """Exercise the production-shaped app over a loopback TCP socket."""

    started_at = monotonic()
    server = make_server(
        "127.0.0.1",
        0,
        app,
        threaded=True,
        request_handler=_QuietRequestHandler,
    )
    thread = Thread(target=server.serve_forever, name="tallyguard-smoke", daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    checks: list[dict[str, str]] = []

    def passed(name: str, detail: str) -> None:
        checks.append({"name": name, "status": "passed", "detail": detail})

    try:
        status, _, body = _request(base_url, "/")
        _require(status == 200, "The judge console did not return HTTP 200.")
        _require(b'<div id="root"></div>' in body, "The judge console root marker is missing.")
        passed("judge_console", "Built frontend served over HTTP with the React root marker.")

        status, _, health = _json_request(base_url, "/api/health")
        _require(status == 200 and health.get("status") == "ok", "Health probe failed.")
        passed("health_probe", "API health probe returned ok.")

        status, _, readiness = _json_request(base_url, "/api/readiness")
        _require(status == 200 and readiness.get("status") == "ready", "Readiness failed.")
        _require(readiness.get("database") == "ok", "Database readiness failed.")
        _require(
            readiness.get("settlement_mode") == "simulation",
            "Smoke test refuses to run against a funds-moving settlement adapter.",
        )
        _require(readiness.get("funds_movement") == "disabled", "Funds movement is not disabled.")
        _require(readiness.get("mainnet_enabled") is False, "Mainnet must remain disabled.")
        passed("safe_readiness", "Database ready; simulation active; funds and mainnet disabled.")

        tokens: dict[str, str] = {}
        for role in ("operator", "approver", "auditor"):
            status, _, session = _json_request(
                base_url,
                "/api/demo/session",
                method="POST",
                payload={"role": role},
            )
            _require(status == 200, f"Could not create the {role} demo session.")
            token = session.get("access_token")
            _require(isinstance(token, str) and bool(token), f"The {role} token is missing.")
            tokens[role] = token
        passed("role_separation", "Operator, approver, and auditor sessions were issued separately.")

        status, _, scenario = _json_request(
            base_url,
            "/api/demo/scenarios/clean-payment/run",
            method="POST",
            token=tokens["operator"],
            correlation_id="deployment-smoke-evaluate",
        )
        _require(status == 200, "The clean-payment scenario did not run.")
        decision = scenario.get("decision")
        invoice = scenario.get("invoice")
        _require(isinstance(decision, dict), "The scenario decision is missing.")
        _require(isinstance(invoice, dict), "The scenario invoice is missing.")
        _require(decision.get("final_action") == "PAY", "Expected a PAY decision.")
        invoice_id = str(invoice.get("id", ""))
        decision_id = str(decision.get("id", ""))
        _require(bool(invoice_id and decision_id), "Scenario identifiers are missing.")
        passed("deterministic_decision", "Three-way evidence produced the expected PAY action.")

        status, _, settlement = _json_request(
            base_url,
            f"/api/invoices/{invoice_id}/settle",
            method="POST",
            token=tokens["approver"],
            correlation_id="deployment-smoke-settle",
            payload={"decision_id": decision_id},
        )
        _require(status == 200, "Simulation settlement did not complete.")
        payment = settlement.get("payment")
        _require(isinstance(payment, dict), "Settlement payment envelope is missing.")
        receipt = payment.get("receipt")
        settled_invoice = payment.get("invoice")
        _require(isinstance(receipt, dict), "Settlement receipt is missing.")
        _require(isinstance(settled_invoice, dict), "Settled invoice is missing.")
        _require(receipt.get("status") == "CONFIRMED", "Receipt is not confirmed.")
        _require(settled_invoice.get("status") == "RECONCILED", "Invoice is not reconciled.")
        _require(payment.get("reused_receipt") is False, "First settlement unexpectedly reused a receipt.")
        transaction_hash = str(receipt.get("transaction_hash", ""))
        _require(transaction_hash.startswith("0x"), "Receipt transaction hash is malformed.")
        passed("simulation_settlement", "Approver settled once; receipt confirmed and invoice reconciled.")

        status, ledger_headers, ledger_body = _request(
            base_url,
            "/api/accounting/ledger.csv",
            token=tokens["auditor"],
            correlation_id="deployment-smoke-ledger",
        )
        _require(status == 200, "Accounting ledger export failed.")
        header_hash = ledger_headers.get("X-TallyGuard-Ledger-SHA256", "")
        body_hash = sha256(ledger_body).hexdigest()
        _require(header_hash == body_hash, "Ledger content hash does not match its response header.")
        _require(ledger_headers.get("X-TallyGuard-Ledger-Rows") == "1", "Ledger row count is not one.")
        rows = list(csv.DictReader(StringIO(ledger_body.decode("utf-8-sig"))))
        _require(len(rows) == 1, "Ledger body does not contain exactly one row.")
        row = rows[0]
        _require(row.get("invoice_id") == invoice_id, "Ledger invoice binding failed.")
        _require(row.get("decision_id") == decision_id, "Ledger decision binding failed.")
        _require(row.get("transaction_hash") == transaction_hash, "Ledger receipt binding failed.")
        _require(row.get("settlement_status") == "CONFIRMED", "Ledger status is not confirmed.")
        passed("accounting_export", "One reconciled row matched invoice, decision, receipt, and SHA-256.")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    duration_ms = round((monotonic() - started_at) * 1000, 2)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification": "synthetic deployment acceptance; not customer traction",
        "target": "loopback ephemeral HTTP server",
        "safety": {
            "settlement_mode": "simulation",
            "funds_moved": False,
            "mainnet_enabled": False,
            "credentials_required": False,
        },
        "summary": {
            "status": "passed",
            "checks_passed": len(checks),
            "checks_failed": 0,
            "duration_ms": duration_ms,
        },
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the TallyGuard public-safe deployment acceptance over real HTTP."
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    args = parser.parse_args()

    with TemporaryDirectory(prefix="tallyguard-deployment-smoke-") as directory:
        app = create_app(database_path=Path(directory) / "smoke.sqlite3")
        try:
            report = run_deployment_smoke(app)
        finally:
            app.extensions["tallyguard_repository"].close()

    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()

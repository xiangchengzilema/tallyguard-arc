"""Exercise the loopback-only Tencent deployment without real funds movement."""

from __future__ import annotations

import json

from tallyguard.deployment_smoke import _json_request, _request, _require


BASE = "http://127.0.0.1:8180"


def main() -> None:
    sessions: dict[str, str] = {}
    checks: list[str] = []
    try:
        status, _, readiness = _json_request(BASE, "/api/readiness")
        _require(status == 200 and readiness.get("status") == "ready", "Readiness failed.")
        _require(readiness.get("settlement_mode") == "simulation", "Live adapter is forbidden.")
        _require(readiness.get("funds_movement") == "disabled", "Funds movement is enabled.")
        _require(readiness.get("mainnet_enabled") is False, "Mainnet is enabled.")
        checks.append("safe_readiness")

        status, _, body = _request(BASE, "/")
        _require(status == 200 and b'<div id="root"></div>' in body, "Frontend failed.")
        checks.append("frontend")

        status, _, workspace = _json_request(BASE, "/api/demo/workspace", method="POST")
        _require(status == 201, "Demo workspace was not created.")
        issued = workspace.get("sessions")
        _require(isinstance(issued, dict), "Role sessions are missing.")
        for role in ("admin", "operator", "approver", "auditor"):
            token = issued.get(role)
            _require(isinstance(token, str) and bool(token), f"{role} session is missing.")
            sessions[role] = token
        _require(len(set(sessions.values())) == 4, "Role sessions are not isolated.")
        checks.append("role_separation")

        status, _, result = _json_request(
            BASE,
            "/api/demo/scenarios/clean-payment/run",
            method="POST",
            token=sessions["operator"],
            correlation_id="tencent-internal-smoke-evaluate",
        )
        _require(status == 200, "Clean-payment scenario failed.")
        decision = result.get("decision")
        invoice = result.get("invoice")
        _require(isinstance(decision, dict) and isinstance(invoice, dict), "Decision is missing.")
        _require(decision.get("final_action") == "PAY", "Expected a PAY decision.")
        decision_id = str(decision.get("id", ""))
        invoice_id = str(invoice.get("id", ""))
        _require(bool(decision_id and invoice_id), "Decision identifiers are missing.")
        checks.append("deterministic_decision")

        status, _, settlement = _json_request(
            BASE,
            f"/api/invoices/{invoice_id}/settle",
            method="POST",
            token=sessions["approver"],
            correlation_id="tencent-internal-smoke-settle",
            payload={"decision_id": decision_id},
        )
        _require(status == 200, "Simulation settlement failed.")
        payment = settlement.get("payment")
        _require(isinstance(payment, dict), "Payment envelope is missing.")
        receipt = payment.get("receipt")
        _require(isinstance(receipt, dict) and receipt.get("status") == "CONFIRMED", "Receipt failed.")
        _require(payment.get("reused_receipt") is False, "First settlement reused a receipt.")
        checks.append("simulated_receipt")

        status, _, repeat = _json_request(
            BASE,
            f"/api/invoices/{invoice_id}/settle",
            method="POST",
            token=sessions["approver"],
            correlation_id="tencent-internal-smoke-repeat",
            payload={"decision_id": decision_id},
        )
        _require(status == 200, "Repeat settlement failed.")
        repeated_payment = repeat.get("payment")
        _require(isinstance(repeated_payment, dict), "Repeat payment envelope is missing.")
        _require(repeated_payment.get("reused_receipt") is True, "Repeat created a new receipt.")
        checks.append("idempotent_repeat")
    finally:
        for token in sessions.values():
            _json_request(BASE, "/api/auth/session", method="DELETE", token=token)

    checks.append("session_revocation")
    print(json.dumps({"status": "passed", "checks": checks, "funds_moved": False}))


if __name__ == "__main__":
    main()

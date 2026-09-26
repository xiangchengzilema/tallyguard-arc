"""Guarded 50-agent Arc Testnet campaign planning and execution.

The campaign is synthetic engineering traffic. Only an explicitly armed local
runner may move test USDC; no Mainnet path or public demo session is used.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import random
import time
from typing import Any, Iterator

from flask.testing import FlaskClient

from .api import create_app
from .network import ArcNetwork, ArcNetworkConfig
from .operator_setup import CONFIRMATION_PHRASE, provision_operator_access


AGENT_COUNT = 50
PAYMENT_AMOUNT = Decimal("0.01")
MAX_CAMPAIGN_SPEND = Decimal("0.40")
NETWORK = "ARC-TESTNET"
SCENARIO_COUNTS = {
    "AUTO_PAY": 20,
    "APPROVE_PAY": 20,
    "DECLINE": 5,
    "EVIDENCE_HOLD": 5,
}
DEFAULT_INVENTORY = Path("artifacts/consolidation/live-20260922.json")
EXECUTION_PHRASE = "RUN-50-AGENTS-ON-ARC-TESTNET"


class CampaignError(RuntimeError):
    """The campaign cannot proceed without violating its safety contract."""


def _is_address(value: str) -> bool:
    if len(value) != 42 or not value.startswith("0x"):
        return False
    try:
        int(value[2:], 16)
    except ValueError:
        return False
    return True


def controlled_recipients(inventory_path: Path) -> tuple[str, tuple[str, ...]]:
    """Read only confirmed, locally controlled recipients from consolidation."""

    report = json.loads(inventory_path.read_text(encoding="utf-8"))
    if report.get("network") != NETWORK or report.get("status") != "complete":
        raise CampaignError("A completed Arc Testnet consolidation inventory is required.")
    treasury = str(report.get("target", {}).get("address", "")).lower()
    if not _is_address(treasury):
        raise CampaignError("The recorded treasury address is invalid.")
    recipients: list[str] = []
    for entry in report.get("items", []):
        address = str(entry.get("address", "")).lower()
        if entry.get("status") != "CONFIRMED" or not _is_address(address):
            raise CampaignError("Every recipient must be a confirmed controlled Testnet wallet.")
        if address == treasury or address in recipients:
            raise CampaignError("Recipient inventory contains the treasury or a duplicate wallet.")
        recipients.append(address)
    if not recipients:
        raise CampaignError("No controlled recipient wallets were found.")
    return treasury, tuple(recipients)


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def make_plan(
    *,
    now: datetime,
    treasury: str,
    recipients: tuple[str, ...],
    rng: random.Random,
    campaign_id: str,
    unique_wallets: bool = False,
) -> dict[str, Any]:
    """Create 50 staggered jobs; only 40 are eligible for real payment."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise CampaignError("The schedule start must be timezone-aware.")
    if not 3 <= len(campaign_id) <= 30 or not campaign_id.isascii() or not campaign_id.replace("-", "").isalnum():
        raise CampaignError("Campaign ID must contain only ASCII letters, numbers, and hyphens.")
    normalized_treasury = treasury.lower()
    wallets = tuple(address.lower() for address in recipients)
    if not _is_address(normalized_treasury) or any(not _is_address(address) for address in wallets):
        raise CampaignError("Treasury and recipient addresses must be valid EVM addresses.")
    if len(set(wallets)) != len(wallets) or normalized_treasury in wallets:
        raise CampaignError("The recipient wallet set must be unique and exclude the treasury.")
    if unique_wallets and len(wallets) < AGENT_COUNT:
        raise CampaignError(f"{AGENT_COUNT} distinct recipients are required for unique-wallet mode.")

    # Make the first transfer due soon enough to inspect under supervision.
    # It is still randomly placed inside a short setup window; the remaining
    # jobs retain randomized times across the full day.
    scenarios = [name for name, count in SCENARIO_COUNTS.items() for _ in range(count)]
    scenarios.remove("AUTO_PAY")
    rng.shuffle(scenarios)
    scenarios.insert(0, "AUTO_PAY")
    wallet_order = list(wallets)
    rng.shuffle(wallet_order)
    start = now.astimezone(timezone.utc)
    jobs: list[dict[str, Any]] = []
    for index, scenario in enumerate(scenarios, start=1):
        # A five-minute setup interval prevents a newly generated schedule from
        # accidentally beginning while the operator is still checking it.
        submit_at = start + timedelta(
            seconds=rng.randrange(300, 540) if index == 1 else rng.randrange(600, 24 * 60 * 60)
        )
        review_at = (
            submit_at + timedelta(minutes=rng.randrange(2, 16))
            if scenario in {"APPROVE_PAY", "DECLINE"}
            else None
        )
        suffix = f"{index:03d}"
        jobs.append(
            {
                "agent_id": f"agent-{suffix}",
                "scenario": scenario,
                "recipient": wallet_order[(index - 1) % len(wallet_order)],
                "amount_usdc": format(PAYMENT_AMOUNT, "f"),
                "submit_at": submit_at.isoformat(),
                "review_at": review_at.isoformat() if review_at else None,
                "invoice_id": f"{campaign_id}-invoice-{suffix}",
                "invoice_number": f"TG-{campaign_id.upper()}-{suffix}",
                "vendor_id": f"{campaign_id}-vendor-{suffix}",
            }
        )
    jobs.sort(key=lambda job: (job["submit_at"], job["agent_id"]))
    body: dict[str, Any] = {
        "schema_version": 1,
        "classification": "synthetic Testnet engineering traffic; not customer traction",
        "campaign_id": campaign_id,
        "network": NETWORK,
        "treasury": normalized_treasury,
        "wallet_inventory_count": len(wallets),
        "agent_count": AGENT_COUNT,
        "maximum_payment_usdc": format(PAYMENT_AMOUNT, "f"),
        "maximum_campaign_spend_usdc": format(MAX_CAMPAIGN_SPEND, "f"),
        "created_at": start.isoformat(),
        "jobs": jobs,
    }
    body["plan_sha256"] = sha256(_canonical_bytes(body)).hexdigest()
    validate_plan(body, treasury=normalized_treasury, recipients=wallets)
    return body


def validate_plan(plan: dict[str, Any], *, treasury: str, recipients: tuple[str, ...]) -> None:
    """Fail closed if schedule, recipients, scenarios, or hard caps were edited."""

    body = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if sha256(_canonical_bytes(body)).hexdigest() != plan.get("plan_sha256"):
        raise CampaignError("Campaign plan hash mismatch; create a new plan instead of editing it.")
    if plan.get("network") != NETWORK or plan.get("treasury") != treasury.lower():
        raise CampaignError("The plan must use the recorded Arc Testnet treasury.")
    campaign_id = str(plan.get("campaign_id", ""))
    if not 3 <= len(campaign_id) <= 30 or not campaign_id.isascii() or not campaign_id.replace("-", "").isalnum():
        raise CampaignError("The campaign identity is invalid.")
    if plan.get("agent_count") != AGENT_COUNT or plan.get("wallet_inventory_count") != len(recipients):
        raise CampaignError("The plan has an unexpected agent or wallet count.")
    if plan.get("maximum_payment_usdc") != format(PAYMENT_AMOUNT, "f") or plan.get(
        "maximum_campaign_spend_usdc"
    ) != format(MAX_CAMPAIGN_SPEND, "f"):
        raise CampaignError("The hard payment caps were changed.")
    jobs = plan.get("jobs")
    if not isinstance(jobs, list) or len(jobs) != AGENT_COUNT:
        raise CampaignError("The campaign requires exactly 50 jobs.")
    counts = {scenario: 0 for scenario in SCENARIO_COUNTS}
    invoice_ids: set[str] = set()
    agent_ids: set[str] = set()
    allowed = set(recipients)
    created = datetime.fromisoformat(str(plan.get("created_at")))
    if created.tzinfo is None:
        raise CampaignError("Campaign creation time must include a timezone.")
    for job in jobs:
        if not isinstance(job, dict) or job.get("scenario") not in counts:
            raise CampaignError("Unexpected campaign job or scenario.")
        counts[job["scenario"]] += 1
        if job.get("recipient") not in allowed or job.get("recipient") == treasury.lower():
            raise CampaignError("A job uses an unapproved recipient wallet.")
        if job.get("amount_usdc") != format(PAYMENT_AMOUNT, "f"):
            raise CampaignError("A job exceeds the hard per-payment amount.")
        invoice_id = str(job.get("invoice_id", ""))
        agent_id = str(job.get("agent_id", ""))
        suffix = agent_id.removeprefix("agent-")
        if (
            not suffix.isdigit()
            or not 1 <= int(suffix) <= AGENT_COUNT
            or invoice_id != f"{campaign_id}-invoice-{suffix}"
            or invoice_id in invoice_ids
            or not agent_id.startswith("agent-")
            or agent_id in agent_ids
            or job.get("vendor_id") != f"{campaign_id}-vendor-{suffix}"
            or job.get("invoice_number") != f"TG-{campaign_id.upper()}-{suffix}"
        ):
            raise CampaignError("Invoice IDs must be present and unique for replay safety.")
        invoice_ids.add(invoice_id)
        agent_ids.add(agent_id)
        submission = datetime.fromisoformat(str(job.get("submit_at")))
        if submission.tzinfo is None or not created + timedelta(minutes=5) <= submission < created + timedelta(hours=24):
            raise CampaignError("Every submission must be within the bounded 24-hour schedule.")
        review = job.get("review_at")
        if job["scenario"] in {"APPROVE_PAY", "DECLINE"}:
            review_time = datetime.fromisoformat(review) if review else None
            if (
                review_time is None
                or review_time.tzinfo is None
                or not submission + timedelta(minutes=2) <= review_time <= submission + timedelta(minutes=15)
            ):
                raise CampaignError("Manual decisions need a later review time.")
        elif review is not None:
            raise CampaignError("Only manual-decision jobs may have a review time.")
    if counts != SCENARIO_COUNTS:
        raise CampaignError("The campaign scenario mix was changed.")
    payable = counts["AUTO_PAY"] + counts["APPROVE_PAY"]
    if PAYMENT_AMOUNT * payable != MAX_CAMPAIGN_SPEND:
        raise CampaignError("The scheduled payment total exceeds the campaign cap.")


def _api_json(
    client: FlaskClient,
    method: str,
    path: str,
    token: str,
    *,
    body: dict[str, Any] | None = None,
    expected: tuple[int, ...] = (200,),
) -> dict[str, Any]:
    response = client.open(
        path,
        method=method,
        json=body if method != "GET" else None,
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code not in expected:
        payload = response.get_json(silent=True)
        raise CampaignError(f"{method} {path} failed with HTTP {response.status_code}: {payload}")
    return dict(response.get_json())


def _source_documents(job: dict[str, Any], *, due_date: str) -> tuple[tuple[str, str, bytes], ...]:
    amount = job["amount_usdc"]
    invoice = {
        "invoice_id": job["invoice_id"],
        "vendor_id": job["vendor_id"],
        "invoice_number": job["invoice_number"],
        "currency": "USDC",
        "amount": amount,
        "due_date": due_date,
        "payment_wallet_address": job["recipient"],
    }
    po_id = f"po-{job['invoice_id']}"
    purchase_order = {
        "purchase_order_id": po_id,
        "vendor_id": job["vendor_id"],
        "po_number": f"PO-{job['invoice_number']}",
        "currency": "USDC",
        "authorized_amount": amount,
    }
    delivery = {
        "delivery_id": f"delivery-{job['invoice_id']}",
        "purchase_order_id": po_id,
        "delivered_value": "0" if job["scenario"] == "EVIDENCE_HOLD" else amount,
    }
    return (
        ("INVOICE", "invoice.json", _canonical_bytes(invoice)),
        ("PURCHASE_ORDER", "purchase-order.json", _canonical_bytes(purchase_order)),
        ("DELIVERY", "delivery.json", _canonical_bytes(delivery)),
    )


def _ensure_policy_and_treasury(
    client: FlaskClient,
    *,
    plan: dict[str, Any],
    sessions: dict[str, str],
    live: bool,
) -> None:
    policy_version = f"{plan['campaign_id']}-policy-v1"
    active = client.get(
        "/api/policies/active",
        headers={"Authorization": f"Bearer {sessions['operator']}"},
    )
    if active.status_code == 404:
        _api_json(
            client,
            "POST",
            "/api/policies",
            sessions["admin"],
            body={
                "version": policy_version,
                "daily_payment_limit_usdc": "0.40",
                "daily_autonomous_payment_limit_usdc": "0.40",
                "autonomous_payments_enabled": True,
                "minimum_cash_reserve_usdc": "1",
                "maximum_autonomous_payment_usdc": "0.01",
                "po_amount_tolerance_usdc": "0",
                "allowed_asset": "USDC",
                "allowed_network": NETWORK,
                "kill_switch_enabled": False,
            },
            expected=(201,),
        )
    elif active.status_code == 200:
        policy = active.get_json()["policy"]
        if policy["version"] != policy_version:
            raise CampaignError("The campaign policy changed; stop before sending more payments.")
        for field, expected in (
            ("daily_payment_limit_usdc", "0.40"),
            ("daily_autonomous_payment_limit_usdc", "0.40"),
            ("maximum_autonomous_payment_usdc", "0.01"),
        ):
            if Decimal(policy[field]) != Decimal(expected):
                raise CampaignError(f"The campaign policy {field} changed.")
        if not policy["autonomous_payments_enabled"] or policy["kill_switch_enabled"]:
            raise CampaignError("The campaign policy was disabled or its emergency stop is active.")
    else:
        raise CampaignError(f"Could not read the campaign policy: HTTP {active.status_code}.")
    if live:
        _api_json(
            client,
            "POST",
            "/api/treasury/snapshots/refresh",
            sessions["operator"],
            body={},
            expected=(201,),
        )
    else:
        existing = client.get(
            "/api/treasury/summary?optional=true",
            headers={"Authorization": f"Bearer {sessions['operator']}"},
        )
        if existing.status_code != 200:
            raise CampaignError("Could not inspect the simulated treasury.")
        if existing.get_json()["treasury"] is None:
            _api_json(
                client,
                "POST",
                "/api/treasury/snapshots",
                sessions["operator"],
                body={
                    "available_usdc": "10",
                    "spent_today_usdc": "0",
                    "source_reference": f"{plan['campaign_id']}-simulated-test",
                },
                expected=(201,),
            )


def _ensure_submitted(
    client: FlaskClient,
    *,
    job: dict[str, Any],
    operator: str,
    campaign_id: str,
) -> dict[str, Any]:
    vendors = _api_json(client, "GET", "/api/vendors", operator)["items"]
    existing_vendor = next((item for item in vendors if item["id"] == job["vendor_id"]), None)
    if existing_vendor is None:
        _api_json(
            client,
            "POST",
            "/api/vendors",
            operator,
            body={
                "id": job["vendor_id"],
                "legal_name": f"Controlled Test {job['agent_id']}",
                "approved_wallet_address": job["recipient"],
                "autopay_limit": "0" if job["scenario"] in {"APPROVE_PAY", "DECLINE"} else "0.01",
                "verification_method": "MANUAL_REVIEW",
                "verification_reference": f"{campaign_id}:controlled-test-wallet-inventory",
            },
            expected=(201,),
        )
    elif existing_vendor["approved_wallet_address"].lower() != job["recipient"]:
        raise CampaignError(f"Recipient wallet for {job['agent_id']} changed.")

    invoice_path = f"/api/invoices/{job['invoice_id']}"
    existing = client.get(invoice_path, headers={"Authorization": f"Bearer {operator}"})
    due_date = (date.today() + timedelta(days=14)).isoformat()
    documents = _source_documents(job, due_date=due_date)
    if existing.status_code == 404:
        _api_json(
            client,
            "POST",
            "/api/invoices",
            operator,
            body={
                "id": job["invoice_id"],
                "vendor_id": job["vendor_id"],
                "invoice_number": job["invoice_number"],
                "currency": "USDC",
                "amount": job["amount_usdc"],
                "due_date": due_date,
                "payment_wallet_address": job["recipient"],
                "source_document_hash": sha256(documents[0][2]).hexdigest(),
            },
            expected=(201,),
        )
    elif existing.status_code == 200:
        invoice = existing.get_json()["invoice"]
        if (
            invoice["payment_wallet_address"].lower() != job["recipient"]
            or Decimal(invoice["amount"]) != PAYMENT_AMOUNT
            or invoice["vendor_id"] != job["vendor_id"]
        ):
            raise CampaignError(f"Existing invoice for {job['agent_id']} has changed payment terms.")
        due_date = invoice["due_date"]
        documents = _source_documents(job, due_date=due_date)
    else:
        raise CampaignError(f"Could not inspect invoice {job['invoice_id']}.")

    evidence = _api_json(client, "GET", f"{invoice_path}/evidence", operator)["items"]
    existing_types = {item["evidence_type"] for item in evidence}
    for evidence_type, filename, content in documents:
        if evidence_type in existing_types:
            continue
        response = client.post(
            f"{invoice_path}/evidence",
            data={
                "evidence_type": evidence_type,
                "file": (BytesIO(content), filename, "application/json"),
            },
            content_type="multipart/form-data",
            headers={"Authorization": f"Bearer {operator}"},
        )
        if response.status_code != 201:
            raise CampaignError(f"Evidence upload for {job['agent_id']} failed: HTTP {response.status_code}.")
    return _api_json(
        client,
        "POST",
        f"{invoice_path}/evaluate?auto_settle={'true' if job['scenario'] == 'AUTO_PAY' else 'false'}",
        operator,
        body={},
    )


def _campaign_spend(repository: Any, *, organization_id: str, campaign_id: str) -> Decimal:
    return sum(
        (
            row.amount_usdc
            for row in repository.accounting_ledger(organization_id=organization_id)
            if row.invoice_id.startswith(f"{campaign_id}-invoice-")
        ),
        Decimal("0"),
    )


def _payment_result(payment: dict[str, Any], *, job: dict[str, Any]) -> dict[str, Any]:
    receipt = payment["receipt"]
    if (
        receipt["network"] != NETWORK
        or receipt["confirmed_recipient"].lower() != job["recipient"]
        or Decimal(receipt["confirmed_amount_usdc"]) != PAYMENT_AMOUNT
        or not str(receipt["transaction_hash"]).startswith("0x")
    ):
        raise CampaignError("Arc receipt did not reconcile to the planned Testnet recipient and amount.")
    return {
        "agent_id": job["agent_id"],
        "scenario": job["scenario"],
        "status": "PAID",
        "invoice_id": job["invoice_id"],
        "transaction_hash": receipt["transaction_hash"],
        "block_number": receipt["block_number"],
        "amount_usdc": receipt["confirmed_amount_usdc"],
    }


def run_due(
    *,
    plan: dict[str, Any],
    now: datetime,
    client: FlaskClient,
    repository: Any,
    sessions: dict[str, str],
    organization_id: str,
    live: bool,
) -> list[dict[str, Any]]:
    """Resume all due jobs from durable invoice/approval/receipt state."""

    if now.tzinfo is None:
        raise CampaignError("The current time must be timezone-aware.")
    results: list[dict[str, Any]] = []
    prepared = False
    for job in plan["jobs"]:
        if datetime.fromisoformat(job["submit_at"]) > now:
            continue
        existing_receipts = {
            row.invoice_id: row
            for row in repository.accounting_ledger(organization_id=organization_id)
            if row.invoice_id.startswith(f"{plan['campaign_id']}-invoice-")
        }
        if job["invoice_id"] in existing_receipts:
            row = existing_receipts[job["invoice_id"]]
            if row.recipient.lower() != job["recipient"] or row.amount_usdc != PAYMENT_AMOUNT or row.network != NETWORK:
                raise CampaignError(f"Stored payment for {job['agent_id']} does not match the locked Testnet plan.")
            results.append({
                "agent_id": job["agent_id"],
                "scenario": job["scenario"],
                "status": "PAID",
                "invoice_id": job["invoice_id"],
                "transaction_hash": row.transaction_hash,
                "block_number": row.block_number,
                "amount_usdc": format(row.amount_usdc, "f"),
            })
            continue
        existing_invoice = client.get(
            f"/api/invoices/{job['invoice_id']}",
            headers={"Authorization": f"Bearer {sessions['operator']}"},
        )
        if existing_invoice.status_code == 200:
            existing_status = existing_invoice.get_json()["invoice"]["status"]
            if job["scenario"] == "EVIDENCE_HOLD" and existing_status == "HOLD":
                results.append({"agent_id": job["agent_id"], "scenario": job["scenario"], "status": "HELD", "invoice_id": job["invoice_id"]})
                continue
            if job["scenario"] == "DECLINE" and existing_status == "REJECTED":
                results.append({"agent_id": job["agent_id"], "scenario": job["scenario"], "status": "DECLINED", "invoice_id": job["invoice_id"]})
                continue
            if existing_status == "RECONCILED":
                raise CampaignError("Invoice is reconciled without a matching durable campaign ledger entry.")
        elif existing_invoice.status_code != 404:
            raise CampaignError(f"Could not inspect {job['agent_id']} before processing.")
        if not prepared:
            _ensure_policy_and_treasury(client, plan=plan, sessions=sessions, live=live)
            prepared = True
        spent = _campaign_spend(repository, organization_id=organization_id, campaign_id=plan["campaign_id"])
        if job["scenario"] in {"AUTO_PAY", "APPROVE_PAY"} and spent + PAYMENT_AMOUNT > MAX_CAMPAIGN_SPEND:
            raise CampaignError("The hard total Testnet campaign spend cap would be exceeded.")
        evaluated = _ensure_submitted(
            client, job=job, operator=sessions["operator"], campaign_id=plan["campaign_id"]
        )
        decision = evaluated["decision"]
        expected_action = {
            "AUTO_PAY": "PAY",
            "APPROVE_PAY": "ESCALATE",
            "DECLINE": "ESCALATE",
            "EVIDENCE_HOLD": "HOLD",
        }[job["scenario"]]
        if decision["final_action"] != expected_action:
            raise CampaignError(
                f"{job['agent_id']} policy returned {decision['final_action']}, expected {expected_action}; no further payment attempted."
            )
        if job["scenario"] == "EVIDENCE_HOLD":
            results.append({"agent_id": job["agent_id"], "scenario": job["scenario"], "status": "HELD", "invoice_id": job["invoice_id"]})
            continue
        if job["scenario"] == "AUTO_PAY":
            payment = evaluated.get("autopay", {}).get("payment")
            if payment is None:
                payment = _api_json(
                    client, "POST", f"/api/invoices/{job['invoice_id']}/settle", sessions["approver"],
                    body={"decision_id": decision["id"]},
                )["payment"]
            results.append(_payment_result(payment, job=job))
            continue

        approval_response = _api_json(
            client, "GET", f"/api/decisions/{decision['id']}/approval", sessions["operator"]
        )
        approval = approval_response["approval"]
        if approval is None:
            approval = _api_json(
                client, "POST", f"/api/decisions/{decision['id']}/request-approval",
                sessions["operator"], body={}, expected=(201,)
            )["approval"]
        if datetime.fromisoformat(job["review_at"]) > now:
            results.append({"agent_id": job["agent_id"], "scenario": job["scenario"], "status": "AWAITING_REVIEW", "invoice_id": job["invoice_id"]})
            continue
        approve = job["scenario"] == "APPROVE_PAY"
        if approval["status"] == "PENDING":
            approval = _api_json(
                client, "POST", f"/api/approvals/{approval['id']}/resolve", sessions["approver"],
                body={
                    "approve": approve,
                    "note": "Synthetic Testnet campaign: evidence reviewed; controlled test wallet."
                    if approve else "Synthetic Testnet campaign: intentionally declined to test feedback.",
                    "expected_version": approval["version"],
                },
            )["approval"]
        if not approve:
            if approval["status"] != "REJECTED":
                raise CampaignError("Decline scenario was not rejected by the approver role.")
            results.append({"agent_id": job["agent_id"], "scenario": job["scenario"], "status": "DECLINED", "invoice_id": job["invoice_id"]})
            continue
        if approval["status"] != "APPROVED":
            raise CampaignError("The approval scenario lacks an approved decision.")
        payment = _api_json(
            client, "POST", f"/api/invoices/{job['invoice_id']}/settle", sessions["approver"],
            body={"decision_id": decision["id"], "approval_reference": approval["id"]},
        )["payment"]
        results.append(_payment_result(payment, job=job))
    return results


@contextmanager
def _exclusive_runner(lock_path: Path) -> Iterator[None]:
    """Keep two schedulers from submitting the same Testnet campaign at once."""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CampaignError("Another campaign runner already holds the execution lock.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_report(path: Path, *, plan: dict[str, Any], results: list[dict[str, Any]]) -> None:
    counts = {status: sum(item["status"] == status for item in results) for status in (
        "PAID", "DECLINED", "HELD", "AWAITING_REVIEW"
    )}
    report = {
        "classification": plan["classification"],
        "campaign_id": plan["campaign_id"],
        "network": NETWORK,
        "plan_sha256": plan["plan_sha256"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "planned_agents": AGENT_COUNT,
        "processed_agents": len(results),
        "counts": counts,
        "confirmed_transfer_principal_usdc_excluding_fees": format(PAYMENT_AMOUNT * counts["PAID"], "f"),
        "items": results,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _run_live(plan: dict[str, Any], *, database_path: Path, report_path: Path, forever: bool) -> None:
    for name in ("CIRCLE_WEB3_API_KEY", "CIRCLE_ENTITY_SECRET", "CIRCLE_WALLET_ID"):
        if not os.getenv(name):
            raise CampaignError(f"Missing {name}; no Testnet payment attempted.")
    os.environ.update({
        "TALLYGUARD_MODE": "circle",
        "TALLYGUARD_ARC_NETWORK": NETWORK,
        "TALLYGUARD_ALLOW_MAINNET": "false",
        "TALLYGUARD_MAX_TRANSFER_USDC": format(PAYMENT_AMOUNT, "f"),
        "TALLYGUARD_ENABLE_DEMO_SESSIONS": "false",
    })
    organization_id = f"tg-{plan['campaign_id']}"
    bundle = provision_operator_access(
        database_path=database_path,
        organization_id=organization_id,
        organization_name="TallyGuard Controlled Agent Testnet Campaign",
        confirmation=CONFIRMATION_PHRASE,
        session_hours=24,
    )
    session_issued_at = datetime.now(timezone.utc)
    sessions = {entry.role.lower().replace("finance_operator", "operator"): entry.bearer_token for entry in bundle.sessions}
    app = create_app(database_path=database_path, testing=False)
    repository = app.extensions["tallyguard_repository"]
    adapter = app.extensions["tallyguard_settlement_adapter"]
    try:
        if adapter.name == "arc-simulator" or app.extensions["tallyguard_demo_sessions_enabled"]:
            raise CampaignError("The campaign refuses simulation adapters and public demo sessions.")
        network = adapter.inspect_arc_network()
        wallet = adapter.inspect_treasury_wallet()
        if (
            network.chain_id != ArcNetworkConfig.for_network(ArcNetwork.TESTNET).chain_id
            or not network.usdc_contract_has_code
            or wallet.address.lower() != plan["treasury"]
            or wallet.state.upper() != "LIVE"
            or wallet.usdc_balance is None
            or wallet.usdc_balance < Decimal("1") + MAX_CAMPAIGN_SPEND
        ):
            raise CampaignError("Arc RPC or Circle treasury preflight did not match the locked Testnet plan.")
        with _exclusive_runner(database_path.with_suffix(".campaign.lock")):
            while True:
                now = datetime.now(timezone.utc)
                if now - session_issued_at >= timedelta(hours=12):
                    bundle = provision_operator_access(
                        database_path=database_path,
                        organization_id=organization_id,
                        organization_name="TallyGuard Controlled Agent Testnet Campaign",
                        confirmation=CONFIRMATION_PHRASE,
                        session_hours=24,
                    )
                    sessions = {
                        entry.role.lower().replace("finance_operator", "operator"): entry.bearer_token
                        for entry in bundle.sessions
                    }
                    session_issued_at = now
                with app.test_client() as client:
                    results = run_due(
                        plan=plan,
                        now=now,
                        client=client,
                        repository=repository,
                        sessions=sessions,
                        organization_id=organization_id,
                        live=True,
                    )
                _write_report(report_path, plan=plan, results=results)
                print(
                    f"{now.isoformat()} processed={len(results)}/{AGENT_COUNT} "
                    f"paid={sum(item['status'] == 'PAID' for item in results)} "
                    f"report={report_path}",
                    flush=True,
                )
                if not forever or len(results) == AGENT_COUNT and all(
                    item["status"] in {"PAID", "DECLINED", "HELD"} for item in results
                ):
                    break
                future_times = [
                    datetime.fromisoformat(value)
                    for job in plan["jobs"]
                    for value in (job["submit_at"], job["review_at"])
                    if value is not None and datetime.fromisoformat(value) > now
                ]
                seconds = min(60.0, max(1.0, (min(future_times) - now).total_seconds())) if future_times else 60.0
                time.sleep(seconds)
    finally:
        repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Guarded 50-agent Arc Testnet campaign")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("plan", help="Write an immutable random schedule; no funds move")
    prepare.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--campaign-id", required=True)
    prepare.add_argument("--unique-wallets", action="store_true")
    for command in ("tick", "run"):
        runner = commands.add_parser(command, help="Execute due jobs on real Arc Testnet")
        runner.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
        runner.add_argument("--plan", type=Path, required=True)
        runner.add_argument("--database", type=Path, required=True)
        runner.add_argument("--report", type=Path, required=True)
        runner.add_argument("--confirm", required=True)
    args = parser.parse_args()
    treasury, recipients = controlled_recipients(args.inventory)
    if args.command == "plan":
        plan = make_plan(
            now=datetime.now(timezone.utc),
            treasury=treasury,
            recipients=recipients,
            rng=random.SystemRandom(),
            campaign_id=args.campaign_id,
            unique_wallets=args.unique_wallets,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists():
            raise CampaignError("A campaign plan already exists at this path; it will not be overwritten.")
        args.output.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({
            "plan": str(args.output.resolve()),
            "agents": AGENT_COUNT,
            "confirmed_controlled_recipients": len(recipients),
            "maximum_real_payments": 40,
            "maximum_transfer_principal_usdc_excluding_fees": format(MAX_CAMPAIGN_SPEND, "f"),
            "first_submission_at": plan["jobs"][0]["submit_at"],
            "last_submission_at": plan["jobs"][-1]["submit_at"],
            "funds_moved": False,
        }, indent=2))
        return
    if args.confirm != EXECUTION_PHRASE:
        raise CampaignError(f"Explicit Testnet execution requires --confirm {EXECUTION_PHRASE}.")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    validate_plan(plan, treasury=treasury, recipients=recipients)
    _run_live(plan, database_path=args.database, report_path=args.report, forever=args.command == "run")


if __name__ == "__main__":
    main()

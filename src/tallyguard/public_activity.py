"""Read-only, redacted Arc activity feed for the public product website.

The live campaign process writes a plan and an atomic progress report. This
module never opens its private database, loads Circle credentials, or submits a
transaction. Only the fields needed to explain real workflow progress leave the
server.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
_STATUSES = {"PAID", "DECLINED", "HELD", "AWAITING_REVIEW"}
_SCENARIOS = {"AUTO_PAY", "APPROVE_PAY", "DECLINE", "EVIDENCE_HOLD"}
_EXPECTED_COUNTS = {"AUTO_PAY": 20, "APPROVE_PAY": 20, "DECLINE": 5, "EVIDENCE_HOLD": 5}
_PAID_AMOUNT = Decimal("0.01")


class ActivityFeedError(ValueError):
    """A source report cannot be published safely or accurately."""


def _json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ActivityFeedError("Activity source must be a JSON object.")
    return payload


def _iso(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ActivityFeedError("Activity timestamp is missing.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ActivityFeedError("Activity timestamp is invalid.") from exc
    if parsed.tzinfo is None:
        raise ActivityFeedError("Activity timestamp needs a timezone.")
    return parsed


def public_activity_snapshot(
    *, plan_path: Path, report_path: Path, now: datetime | None = None
) -> dict[str, Any]:
    """Validate the locked inputs and return only public workflow fields."""

    plan = _json_object(plan_path)
    report = _json_object(report_path)
    plan_body = {key: value for key, value in plan.items() if key != "plan_sha256"}
    digest = sha256(
        json.dumps(plan_body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if (
        plan.get("plan_sha256") != digest
        or report.get("plan_sha256") != digest
        or report.get("campaign_id") != plan.get("campaign_id")
        or plan.get("network") != "ARC-TESTNET"
        or report.get("network") != "ARC-TESTNET"
        or plan.get("agent_count") != 50
        or report.get("planned_agents") != 50
        or plan.get("maximum_payment_usdc") != "0.01"
        or plan.get("maximum_campaign_spend_usdc") != "0.40"
    ):
        raise ActivityFeedError("Activity sources do not match the locked Arc Testnet campaign.")
    jobs = plan.get("jobs")
    items = report.get("items")
    if not isinstance(jobs, list) or len(jobs) != 50 or not isinstance(items, list):
        raise ActivityFeedError("Activity source has an unexpected number of workflows.")
    planned: dict[str, dict[str, Any]] = {}
    scenario_counts = {scenario: 0 for scenario in _SCENARIOS}
    for job in jobs:
        if not isinstance(job, dict) or job.get("scenario") not in _SCENARIOS:
            raise ActivityFeedError("Activity plan contains an unknown workflow.")
        invoice_id = job.get("invoice_id")
        if not isinstance(invoice_id, str) or invoice_id in planned:
            raise ActivityFeedError("Activity plan contains a duplicate invoice.")
        if job.get("amount_usdc") != "0.01":
            raise ActivityFeedError("Activity plan changed its payment amount.")
        _iso(job.get("submit_at"))
        if job.get("review_at") is not None:
            _iso(job["review_at"])
        planned[invoice_id] = job
        scenario_counts[job["scenario"]] += 1
    if scenario_counts != _EXPECTED_COUNTS:
        raise ActivityFeedError("Activity plan scenario counts changed.")

    actual: dict[str, dict[str, Any]] = {}
    seen_transactions: set[str] = set()
    counts = {status: 0 for status in _STATUSES}
    for item in items:
        if not isinstance(item, dict) or item.get("status") not in _STATUSES:
            raise ActivityFeedError("Activity report contains an unknown outcome.")
        invoice_id = item.get("invoice_id")
        if invoice_id not in planned or invoice_id in actual:
            raise ActivityFeedError("Activity report contains an unknown or duplicate invoice.")
        job = planned[invoice_id]
        if item.get("agent_id") != job["agent_id"] or item.get("scenario") != job["scenario"]:
            raise ActivityFeedError("Activity outcome does not match its planned workflow.")
        status = item["status"]
        expected_scenario = {
            "DECLINED": "DECLINE",
            "HELD": "EVIDENCE_HOLD",
            "AWAITING_REVIEW": None,
        }.get(status)
        if status == "PAID" and job["scenario"] not in {"AUTO_PAY", "APPROVE_PAY"}:
            raise ActivityFeedError("A non-payment scenario has a paid receipt.")
        if expected_scenario and job["scenario"] != expected_scenario:
            raise ActivityFeedError("Activity outcome contradicts the locked workflow.")
        if status == "AWAITING_REVIEW" and job["scenario"] not in {"APPROVE_PAY", "DECLINE"}:
            raise ActivityFeedError("An unexpected workflow is awaiting review.")
        if status == "PAID":
            transaction_hash = item.get("transaction_hash")
            if not isinstance(transaction_hash, str) or not _HASH.fullmatch(transaction_hash):
                raise ActivityFeedError("Paid workflow lacks a transaction hash.")
            try:
                amount = Decimal(str(item.get("amount_usdc")))
            except (InvalidOperation, TypeError) as exc:
                raise ActivityFeedError("Paid workflow has an invalid amount.") from exc
            if amount != _PAID_AMOUNT or not isinstance(item.get("block_number"), int) or item["block_number"] < 1:
                raise ActivityFeedError("Paid workflow lacks an exact Arc receipt.")
            if transaction_hash.lower() in seen_transactions:
                raise ActivityFeedError("Two workflows claim the same transaction.")
            seen_transactions.add(transaction_hash.lower())
        actual[invoice_id] = item
        counts[status] += 1
    report_counts = report.get("counts")
    if not isinstance(report_counts, dict) or report.get("processed_agents") != len(actual) or any(
        report_counts.get(status) != count for status, count in counts.items()
    ):
        raise ActivityFeedError("Activity report counts do not match its receipts.")
    if counts["PAID"] > 40 or _PAID_AMOUNT * counts["PAID"] > Decimal("0.40"):
        raise ActivityFeedError("Activity report exceeds its payment cap.")
    try:
        principal = Decimal(str(report.get("confirmed_transfer_principal_usdc_excluding_fees")))
    except (InvalidOperation, TypeError) as exc:
        raise ActivityFeedError("Activity principal is invalid.") from exc
    if principal != _PAID_AMOUNT * counts["PAID"]:
        raise ActivityFeedError("Activity principal does not match confirmed receipts.")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ActivityFeedError("Current time needs a timezone.")
    updated = _iso(report.get("updated_at"))
    entries: list[dict[str, Any]] = []
    for index, job in enumerate(jobs, start=1):
        item = actual.get(job["invoice_id"])
        outcome = item["status"] if item else "SCHEDULED"
        route = "AUTOMATIC" if job["scenario"] == "AUTO_PAY" else "APPROVAL" if job["scenario"] in {"APPROVE_PAY", "DECLINE"} else "EVIDENCE"
        transaction_hash = item.get("transaction_hash") if item else None
        entries.append({
            "id": f"FLOW-{index:03d}",
            "invoice_number": f"TG-20260925-{index:03d}",
            "scheduled_at": job["submit_at"],
            "review_at": job["review_at"],
            "amount_usdc": job["amount_usdc"],
            "route": route,
            "status": outcome,
            "transaction_hash": transaction_hash,
            "explorer_url": f"https://explorer.testnet.arc.io/tx/{transaction_hash}" if transaction_hash else None,
            "block_number": item.get("block_number") if item else None,
        })
    entries.sort(key=lambda entry: (entry["status"] != "SCHEDULED", entry["scheduled_at"]), reverse=True)
    upcoming = sorted(
        _iso(job["submit_at"])
        for job in jobs
        if job["invoice_id"] not in actual and _iso(job["submit_at"]) > current
    )
    return {
        "available": True,
        "network": "ARC-TESTNET",
        "updated_at": updated.isoformat(),
        "planned": 50,
        "processed": len(actual),
        "queued": 50 - len(actual),
        "confirmed_payments": counts["PAID"],
        "awaiting_review": counts["AWAITING_REVIEW"],
        "declined": counts["DECLINED"],
        "held": counts["HELD"],
        "confirmed_principal_usdc": format(_PAID_AMOUNT * counts["PAID"], "f"),
        "next_scheduled_at": upcoming[0].isoformat() if upcoming else None,
        "entries": entries,
    }

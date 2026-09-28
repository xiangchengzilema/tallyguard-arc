"""Repeatable multi-tenant HTTP load test for the TallyGuard control plane.

This is synthetic engineering validation, not customer traction. It creates
isolated organizations and role-separated sessions in an ephemeral database,
then exercises the real Flask API over loopback HTTP.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
from threading import Thread
from time import perf_counter
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from werkzeug.serving import WSGIRequestHandler, make_server

from .api import create_app
from .auth import Principal, Role


@dataclass(frozen=True, slots=True)
class LoadConfiguration:
    organizations: int = 10
    invoices: int = 100
    concurrency: int = 16
    duplicate_storm: int = 50
    treasury_contention: int = 20
    slow_provider_delay_ms: int = 250
    timeout_seconds: float = 15

    def __post_init__(self) -> None:
        if self.organizations < 2:
            raise ValueError("Load tests require at least two organizations.")
        if self.invoices < self.organizations:
            raise ValueError("Invoice count must be at least the organization count.")
        if self.concurrency < 1:
            raise ValueError("Concurrency must be positive.")
        if self.duplicate_storm < 2:
            raise ValueError("Duplicate storm size must be at least two.")
        if self.treasury_contention < 2:
            raise ValueError("Treasury contention size must be at least two.")
        if self.slow_provider_delay_ms < 0:
            raise ValueError("Slow-provider delay cannot be negative.")
        if self.timeout_seconds <= 0:
            raise ValueError("HTTP timeout must be positive.")
        if self.slow_provider_delay_ms >= self.timeout_seconds * 1_000:
            raise ValueError("Slow-provider delay must remain below the HTTP timeout.")


@dataclass(frozen=True, slots=True)
class RequestSample:
    name: str
    status: int
    latency_ms: float
    ok: bool


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    organization_id: str
    scenario: str
    invoice_id: str | None
    transaction_hash: str | None
    protected: bool
    samples: tuple[RequestSample, ...]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    payload: dict[str, Any]
    sample: RequestSample


class SilentRequestHandler(WSGIRequestHandler):
    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        return


class LocalHttpServer:
    def __init__(self, app: Any) -> None:
        self._server = make_server(
            "127.0.0.1",
            0,
            app,
            threaded=True,
            request_handler=SilentRequestHandler,
        )
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "LocalHttpServer":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()


def _scenario_for(index: int) -> str:
    bucket = index % 20
    if bucket < 12:
        return "clean-payment"
    if bucket < 15:
        return "wallet-change"
    if bucket < 17:
        return "duplicate-invoice"
    if bucket == 17:
        return "scheduled-payment"
    return "large-invoice"


def _http_json(
    *,
    base_url: str,
    path: str,
    name: str,
    token: str,
    timeout_seconds: float,
    body: dict[str, Any] | None = None,
) -> HttpResult:
    encoded = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
    request = Request(
        base_url + path,
        data=encoded,
        method="POST" if body is not None else "GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Correlation-ID": f"load_{uuid4().hex}",
        },
    )
    started = perf_counter()
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - loopback only
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        status = exc.code
        payload = json.loads(exc.read().decode("utf-8"))
    latency_ms = (perf_counter() - started) * 1_000
    return HttpResult(
        status=status,
        payload=payload,
        sample=RequestSample(name=name, status=status, latency_ms=latency_ms, ok=200 <= status < 300),
    )


def _run_workflow(
    *,
    base_url: str,
    organization_id: str,
    scenario: str,
    operator_token: str,
    approver_token: str,
    timeout_seconds: float,
) -> WorkflowResult:
    samples: list[RequestSample] = []
    invoice_id: str | None = None
    transaction_hash: str | None = None
    try:
        run = _http_json(
            base_url=base_url,
            path=f"/api/demo/scenarios/{scenario}/run",
            name="scenario.evaluate",
            token=operator_token,
            timeout_seconds=timeout_seconds,
            body={},
        )
        samples.append(run.sample)
        if run.status != 200:
            raise RuntimeError(f"scenario evaluation returned {run.status}")
        invoice_id = str(run.payload["invoice"]["id"])
        decision_id = str(run.payload["decision"]["id"])

        approval_reference: str | None = None
        if scenario == "large-invoice":
            requested = _http_json(
                base_url=base_url,
                path=f"/api/decisions/{decision_id}/request-approval",
                name="approval.request",
                token=operator_token,
                timeout_seconds=timeout_seconds,
                body={},
            )
            samples.append(requested.sample)
            if requested.status != 201:
                raise RuntimeError(f"approval request returned {requested.status}")
            approval = requested.payload["approval"]
            approval_reference = str(approval["id"])
            resolved = _http_json(
                base_url=base_url,
                path=f"/api/approvals/{approval_reference}/resolve",
                name="approval.resolve",
                token=approver_token,
                timeout_seconds=timeout_seconds,
                body={
                    "approve": True,
                    "note": "Synthetic load test approval after deterministic evidence review.",
                    "expected_version": int(approval["version"]),
                },
            )
            samples.append(resolved.sample)
            if resolved.status != 200:
                raise RuntimeError(f"approval resolution returned {resolved.status}")

        if scenario in {"clean-payment", "large-invoice"}:
            settled = _http_json(
                base_url=base_url,
                path=f"/api/invoices/{invoice_id}/settle",
                name="payment.settle",
                token=approver_token,
                timeout_seconds=timeout_seconds,
                body={
                    "decision_id": decision_id,
                    **(
                        {"approval_reference": approval_reference}
                        if approval_reference is not None
                        else {}
                    ),
                },
            )
            samples.append(settled.sample)
            if settled.status != 200:
                raise RuntimeError(f"settlement returned {settled.status}")
            transaction_hash = str(settled.payload["payment"]["receipt"]["transaction_hash"])

        return WorkflowResult(
            organization_id=organization_id,
            scenario=scenario,
            invoice_id=invoice_id,
            transaction_hash=transaction_hash,
            protected=scenario != "clean-payment",
            samples=tuple(samples),
        )
    except Exception as exc:
        return WorkflowResult(
            organization_id=organization_id,
            scenario=scenario,
            invoice_id=invoice_id,
            transaction_hash=transaction_hash,
            protected=scenario != "clean-payment",
            samples=tuple(samples),
            error=str(exc),
        )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


def _latency_summary(samples: list[RequestSample]) -> dict[str, float]:
    values = [sample.latency_ms for sample in samples]
    return {
        "mean": round(mean(values), 3) if values else 0.0,
        "p50": round(_percentile(values, 0.50), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "p99": round(_percentile(values, 0.99), 3),
        "max": round(max(values), 3) if values else 0.0,
    }


def execute_load_test(configuration: LoadConfiguration) -> dict[str, Any]:
    """Run isolated synthetic tenants over real HTTP and return a JSON-safe report."""

    with TemporaryDirectory(prefix="tallyguard-load-") as temporary_directory:
        database_path = Path(temporary_directory) / "load.sqlite3"
        app = create_app(database_path=database_path, testing=True)
        repository = app.extensions["tallyguard_repository"]
        authenticator = app.extensions["tallyguard_authenticator"]
        adapter = app.extensions["tallyguard_settlement_adapter"]
        identities: dict[str, tuple[str, str, str]] = {}

        for index in range(configuration.organizations):
            organization_id = f"load-org-{index:04d}"
            operator_id = f"load-operator-{index:04d}"
            approver_id = f"load-approver-{index:04d}"
            admin_id = f"load-admin-{index:04d}"
            repository.create_organization(
                organization_id=organization_id,
                name=f"Synthetic Organization {index:04d}",
            )
            repository.create_user(
                organization_id=organization_id,
                user_id=operator_id,
                display_name=f"Operator {index:04d}",
                roles=(Role.FINANCE_OPERATOR.value,),
            )
            repository.create_user(
                organization_id=organization_id,
                user_id=approver_id,
                display_name=f"Approver {index:04d}",
                roles=(Role.APPROVER.value,),
            )
            repository.create_user(
                organization_id=organization_id,
                user_id=admin_id,
                display_name=f"Administrator {index:04d}",
                roles=(Role.ADMIN.value,),
            )
            operator_token, _ = authenticator.issue_session(
                Principal(
                    user_id=operator_id,
                    organization_id=organization_id,
                    roles=(Role.FINANCE_OPERATOR,),
                )
            )
            approver_token, _ = authenticator.issue_session(
                Principal(
                    user_id=approver_id,
                    organization_id=organization_id,
                    roles=(Role.APPROVER,),
                )
            )
            admin_token, _ = authenticator.issue_session(
                Principal(
                    user_id=admin_id,
                    organization_id=organization_id,
                    roles=(Role.ADMIN,),
                )
            )
            identities[organization_id] = (operator_token, approver_token, admin_token)

        started = perf_counter()
        with LocalHttpServer(app) as server:
            futures = []
            with ThreadPoolExecutor(max_workers=configuration.concurrency) as pool:
                for index in range(configuration.invoices):
                    organization_id = f"load-org-{index % configuration.organizations:04d}"
                    operator_token, approver_token, _admin_token = identities[organization_id]
                    futures.append(
                        pool.submit(
                            _run_workflow,
                            base_url=server.base_url,
                            organization_id=organization_id,
                            scenario=_scenario_for(index),
                            operator_token=operator_token,
                            approver_token=approver_token,
                            timeout_seconds=configuration.timeout_seconds,
                        )
                    )
                workflows = [future.result() for future in as_completed(futures)]

            successful_by_org: dict[str, WorkflowResult] = {}
            for result in workflows:
                if result.error is None and result.invoice_id is not None:
                    successful_by_org.setdefault(result.organization_id, result)

            cross_tenant_samples: list[RequestSample] = []
            cross_tenant_denied = 0
            organizations = sorted(identities)
            for index, organization_id in enumerate(organizations):
                target_org = organizations[(index + 1) % len(organizations)]
                target = successful_by_org.get(target_org)
                if target is None or target.invoice_id is None:
                    continue
                attack = _http_json(
                    base_url=server.base_url,
                    path=f"/api/invoices/{target.invoice_id}",
                    name="security.cross_tenant_read",
                    token=identities[organization_id][0],
                    timeout_seconds=configuration.timeout_seconds,
                )
                cross_tenant_samples.append(attack.sample)
                if attack.status == 404:
                    cross_tenant_denied += 1

            storm_org = organizations[0]
            storm_operator, storm_approver, storm_admin = identities[storm_org]
            storm_run = _http_json(
                base_url=server.base_url,
                path="/api/demo/scenarios/clean-payment/run",
                name="storm.prepare",
                token=storm_operator,
                timeout_seconds=configuration.timeout_seconds,
                body={},
            )
            storm_invoice_id = str(storm_run.payload["invoice"]["id"])
            storm_decision_id = str(storm_run.payload["decision"]["id"])
            adapter.arm_delay(
                organization_id=storm_org,
                invoice_id=storm_invoice_id,
                delay_seconds=configuration.slow_provider_delay_ms / 1_000,
            )
            submissions_before_storm = adapter.submission_count
            delayed_attempts_before_storm = adapter.delayed_attempt_count
            storm_results: list[HttpResult] = []
            with ThreadPoolExecutor(max_workers=configuration.concurrency) as pool:
                storm_futures = [
                    pool.submit(
                        _http_json,
                        base_url=server.base_url,
                        path=f"/api/invoices/{storm_invoice_id}/settle",
                        name="storm.duplicate_settlement",
                        token=storm_approver,
                        timeout_seconds=configuration.timeout_seconds,
                        body={"decision_id": storm_decision_id},
                    )
                    for _ in range(configuration.duplicate_storm)
                ]
                storm_results = [future.result() for future in as_completed(storm_futures)]
            storm_hashes = {
                str(result.payload["payment"]["receipt"]["transaction_hash"])
                for result in storm_results
                if result.status == 200
            }
            storm_provider_submissions = adapter.submission_count - submissions_before_storm
            storm_delayed_attempts = (
                adapter.delayed_attempt_count - delayed_attempts_before_storm
            )

            # Prepare independent PAY decisions, then make every worker contend for
            # the same immutable treasury snapshot and policy headroom. The final
            # policy admits only four 1,200 USDC payments. Correctness therefore
            # requires exactly four provider submissions regardless of thread order.
            contention_samples: list[RequestSample] = []
            contention_errors: list[str] = []
            contention_items: list[tuple[str, str]] = []
            for _ in range(configuration.treasury_contention):
                prepared = _http_json(
                    base_url=server.base_url,
                    path="/api/demo/scenarios/clean-payment/run",
                    name="contention.prepare",
                    token=storm_operator,
                    timeout_seconds=configuration.timeout_seconds,
                    body={},
                )
                contention_samples.append(prepared.sample)
                if prepared.status != 200:
                    contention_errors.append(
                        f"contention preparation returned {prepared.status}"
                    )
                    continue
                contention_items.append(
                    (
                        str(prepared.payload["invoice"]["id"]),
                        str(prepared.payload["decision"]["id"]),
                    )
                )

            contention_policy = _http_json(
                base_url=server.base_url,
                path="/api/policies",
                name="contention.activate_policy",
                token=storm_admin,
                timeout_seconds=configuration.timeout_seconds,
                body={
                    "version": f"contention-{uuid4().hex}",
                    "daily_payment_limit_usdc": "5000",
                    # Earlier synthetic payments in this tenant still count toward
                    # the no-touch UTC-day budget even after a new treasury snapshot.
                    # Keep this contention drill focused on the hard daily limit.
                    "daily_autonomous_payment_limit_usdc": "50000",
                    "autonomous_payments_enabled": True,
                    "minimum_cash_reserve_usdc": "3000",
                    "maximum_autonomous_payment_usdc": "2000",
                    "po_amount_tolerance_usdc": "0",
                    "allowed_asset": "USDC",
                    "allowed_network": "ARC-TESTNET",
                    "kill_switch_enabled": False,
                },
            )
            contention_samples.append(contention_policy.sample)
            if contention_policy.status != 201:
                contention_errors.append(
                    f"contention policy activation returned {contention_policy.status}"
                )

            contention_snapshot = _http_json(
                base_url=server.base_url,
                path="/api/treasury/snapshots",
                name="contention.record_treasury",
                token=storm_operator,
                timeout_seconds=configuration.timeout_seconds,
                body={
                    "available_usdc": "10000",
                    "spent_today_usdc": "0",
                    "source_reference": f"synthetic-contention-{uuid4().hex}",
                },
            )
            contention_samples.append(contention_snapshot.sample)
            if contention_snapshot.status != 201:
                contention_errors.append(
                    f"contention treasury snapshot returned {contention_snapshot.status}"
                )

            submissions_before_contention = adapter.submission_count
            contention_results: list[HttpResult] = []
            with ThreadPoolExecutor(max_workers=configuration.concurrency) as pool:
                contention_futures = [
                    pool.submit(
                        _http_json,
                        base_url=server.base_url,
                        path=f"/api/invoices/{invoice_id}/settle",
                        name="contention.atomic_reservation",
                        token=storm_approver,
                        timeout_seconds=configuration.timeout_seconds,
                        body={"decision_id": decision_id},
                    )
                    for invoice_id, decision_id in contention_items
                ]
                contention_results = [
                    future.result() for future in as_completed(contention_futures)
                ]
            contention_samples.extend(result.sample for result in contention_results)
            contention_provider_submissions = (
                adapter.submission_count - submissions_before_contention
            )
            contention_hashes = {
                str(result.payload["payment"]["receipt"]["transaction_hash"])
                for result in contention_results
                if result.status == 200
            }
            contention_denied = sum(result.status == 409 for result in contention_results)
            contention_expected_successes = 4
            for result in contention_results:
                if result.status not in {200, 409}:
                    contention_errors.append(
                        f"contention settlement returned {result.status}"
                    )

        duration_seconds = perf_counter() - started
        workflow_samples = [sample for result in workflows for sample in result.samples]
        all_samples = (
            workflow_samples
            + cross_tenant_samples
            + [storm_run.sample]
            + [result.sample for result in storm_results]
            + contention_samples
        )
        successful = [result for result in workflows if result.error is None]
        transaction_hashes = {
            result.transaction_hash for result in successful if result.transaction_hash is not None
        }
        by_request: dict[str, dict[str, float]] = {}
        for name in sorted({sample.name for sample in all_samples}):
            by_request[name] = _latency_summary([sample for sample in all_samples if sample.name == name])

        report = {
            "schema_version": "1.1",
            "run_id": f"load_{uuid4().hex}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "methodology": {
                "classification": "synthetic multi-tenant engineering load test",
                "transport": "real loopback HTTP against the Flask API",
                "settlement": "deterministic Arc simulator; no funds moved",
                "identity": "isolated organizations with distinct operator, approver, and administrator sessions",
                "slow_provider_injection": "the duplicate-settlement storm holds the accepted provider call open for the configured delay while concurrent retries arrive",
                "treasury_contention": "concurrent payments share one immutable treasury snapshot; policy permits exactly four 1,200 USDC reservations",
                "note": "This report measures reliability and is not customer traction.",
            },
            "configuration": asdict(configuration),
            "summary": {
                "duration_seconds": round(duration_seconds, 3),
                "workflow_throughput_per_second": round(configuration.invoices / duration_seconds, 3),
                "http_requests": len(all_samples),
                "successful_workflows": len(successful),
                "failed_workflows": len(workflows) - len(successful),
                "workflow_error_rate": round((len(workflows) - len(successful)) / len(workflows), 6),
                "settled_workflows": len(transaction_hashes),
                "protected_workflows": sum(result.protected for result in successful),
                "provider_submissions": adapter.submission_count,
                "duplicate_payment_count": max(
                    0,
                    adapter.submission_count
                    - len(transaction_hashes)
                    - len(storm_hashes)
                    - len(contention_hashes),
                ),
                "cross_tenant_attempts": len(cross_tenant_samples),
                "cross_tenant_attempts_denied": cross_tenant_denied,
                "duplicate_storm_requests": len(storm_results),
                "duplicate_storm_successes": sum(result.status == 200 for result in storm_results),
                "duplicate_storm_unique_transaction_hashes": len(storm_hashes),
                "duplicate_storm_provider_submissions": storm_provider_submissions,
                "duplicate_storm_delayed_provider_attempts": storm_delayed_attempts,
                "slow_provider_idempotency_preserved": (
                    sum(result.status == 200 for result in storm_results)
                    == configuration.duplicate_storm
                    and len(storm_hashes) == 1
                    and storm_provider_submissions == 1
                    and (
                        storm_delayed_attempts == 1
                        if configuration.slow_provider_delay_ms > 0
                        else storm_delayed_attempts == 0
                    )
                ),
                "treasury_contention_requests": len(contention_results),
                "treasury_contention_successes": sum(
                    result.status == 200 for result in contention_results
                ),
                "treasury_contention_denied": contention_denied,
                "treasury_contention_expected_successes": contention_expected_successes,
                "treasury_contention_provider_submissions": contention_provider_submissions,
                "treasury_contention_unique_transaction_hashes": len(
                    contention_hashes
                ),
                "treasury_atomic_limit_preserved": (
                    len(contention_items) == configuration.treasury_contention
                    and sum(result.status == 200 for result in contention_results)
                    == contention_expected_successes
                    and contention_denied
                    == configuration.treasury_contention
                    - contention_expected_successes
                    and contention_provider_submissions
                    == contention_expected_successes
                    and len(contention_hashes) == contention_expected_successes
                    and not contention_errors
                ),
            },
            "latency_ms": {
                "overall": _latency_summary(all_samples),
                "by_request": by_request,
            },
            "errors": (
                [result.error for result in workflows if result.error is not None]
                + contention_errors
            )[:25],
        }
        repository.close()
        return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organizations", type=int, default=10)
    parser.add_argument("--invoices", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--duplicate-storm", type=int, default=50)
    parser.add_argument("--treasury-contention", type=int, default=20)
    parser.add_argument("--slow-provider-delay-ms", type=int, default=250)
    parser.add_argument("--timeout-seconds", type=float, default=15)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    report = execute_load_test(
        LoadConfiguration(
            organizations=arguments.organizations,
            invoices=arguments.invoices,
            concurrency=arguments.concurrency,
            duplicate_storm=arguments.duplicate_storm,
            treasury_contention=arguments.treasury_contention,
            slow_provider_delay_ms=arguments.slow_provider_delay_ms,
            timeout_seconds=arguments.timeout_seconds,
        )
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()

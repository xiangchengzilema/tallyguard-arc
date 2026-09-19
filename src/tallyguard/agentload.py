"""Concurrent multi-tenant reliability test for bounded TallyGuard agent runs.

This suite drives the real HTTP API over loopback, not Flask's in-process test
client. It proves that mixed AP queues remain tenant-isolated, that only
policy-authorized work executes, that proof packets verify, and that a storm of
duplicate execution requests receives only one orchestration lease.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any
from uuid import uuid4

from .api import create_app
from .audit import canonical_json
from .auth import Principal, Role
from .loadtest import HttpResult, LocalHttpServer, RequestSample, _http_json, _latency_summary


@dataclass(frozen=True, slots=True)
class AgentLoadConfiguration:
    organizations: int = 10
    concurrency: int = 10
    duplicate_execution_storm: int = 50
    timeout_seconds: float = 15

    def __post_init__(self) -> None:
        if self.organizations < 2:
            raise ValueError("Agent load tests require at least two organizations.")
        if self.concurrency < 1:
            raise ValueError("Concurrency must be positive.")
        if self.duplicate_execution_storm < 2:
            raise ValueError("Duplicate execution storm must contain at least two requests.")
        if self.timeout_seconds <= 0:
            raise ValueError("HTTP timeout must be positive.")


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    organization_id: str
    operator_token: str
    approver_token: str
    auditor_token: str


@dataclass(frozen=True, slots=True)
class AgentWorkflowResult:
    organization_id: str
    run_id: str | None
    statuses: tuple[str, ...]
    proof_hash: str | None
    samples: tuple[RequestSample, ...]
    error: str | None = None


def _create_identity(*, repository: Any, authenticator: Any, index: str) -> AgentIdentity:
    organization_id = f"agent-load-org-{index}"
    users = (
        ("operator", Role.FINANCE_OPERATOR),
        ("approver", Role.APPROVER),
        ("auditor", Role.AUDITOR),
    )
    repository.create_organization(
        organization_id=organization_id,
        name=f"Agent Load Organization {index}",
    )
    tokens: dict[str, str] = {}
    for label, role in users:
        user_id = f"agent-load-{label}-{index}"
        repository.create_user(
            organization_id=organization_id,
            user_id=user_id,
            display_name=f"{label.title()} {index}",
            roles=(role.value,),
        )
        token, _ = authenticator.issue_session(
            Principal(
                user_id=user_id,
                organization_id=organization_id,
                roles=(role,),
            )
        )
        tokens[label] = token
    return AgentIdentity(
        organization_id=organization_id,
        operator_token=tokens["operator"],
        approver_token=tokens["approver"],
        auditor_token=tokens["auditor"],
    )


def _run_agent_workflow(
    *,
    base_url: str,
    identity: AgentIdentity,
    timeout_seconds: float,
) -> AgentWorkflowResult:
    samples: list[RequestSample] = []
    run_id: str | None = None
    try:
        seeded = _http_json(
            base_url=base_url,
            path="/api/demo/autonomy-showcase",
            name="agent.showcase.seed",
            token=identity.operator_token,
            timeout_seconds=timeout_seconds,
            body={},
        )
        samples.append(seeded.sample)
        if seeded.status != 201:
            raise RuntimeError(f"showcase seed returned {seeded.status}")

        planned = _http_json(
            base_url=base_url,
            path="/api/agent-runs",
            name="agent.plan",
            token=identity.operator_token,
            timeout_seconds=timeout_seconds,
            body={"max_items": 25},
        )
        samples.append(planned.sample)
        if planned.status != 201:
            raise RuntimeError(f"agent plan returned {planned.status}")
        run = planned.payload["agent_run"]
        run_id = str(run["id"])
        if run["summary"] != {
            "scanned": 4,
            "executable": 2,
            "requires_attention": 2,
            "actions": {
                "REMEDIATE": 1,
                "REQUIRE_APPROVAL": 1,
                "SETTLE": 1,
                "WAIT_SCHEDULE": 1,
            },
        }:
            raise RuntimeError("agent plan did not preserve the expected mixed queue")

        executed = _http_json(
            base_url=base_url,
            path=f"/api/agent-runs/{run_id}/execute",
            name="agent.execute",
            token=identity.approver_token,
            timeout_seconds=timeout_seconds,
            body={},
        )
        samples.append(executed.sample)
        if executed.status != 200:
            raise RuntimeError(f"agent execution returned {executed.status}")
        completed = executed.payload["agent_run"]
        statuses = tuple(sorted(str(item["status"]) for item in completed["results"]))
        if statuses != ("ROUTED", "SETTLED", "SKIPPED", "SKIPPED"):
            raise RuntimeError(f"unexpected agent result set: {statuses}")

        proof = _http_json(
            base_url=base_url,
            path=f"/api/agent-runs/{run_id}/proof-packet",
            name="agent.proof",
            token=identity.auditor_token,
            timeout_seconds=timeout_seconds,
        )
        samples.append(proof.sample)
        if proof.status != 200:
            raise RuntimeError(f"proof packet returned {proof.status}")
        packet = proof.payload["packet"]
        computed_hash = sha256(canonical_json(packet).encode("utf-8")).hexdigest()
        if proof.payload["packet_sha256"] != computed_hash:
            raise RuntimeError("proof packet content hash did not verify")
        if not packet["integrity"]["plan_hash_verified"]:
            raise RuntimeError("agent plan hash did not verify")
        if not packet["audit"]["tenant_chain_valid"]:
            raise RuntimeError("tenant audit chain did not verify")

        return AgentWorkflowResult(
            organization_id=identity.organization_id,
            run_id=run_id,
            statuses=statuses,
            proof_hash=computed_hash,
            samples=tuple(samples),
        )
    except Exception as exc:
        return AgentWorkflowResult(
            organization_id=identity.organization_id,
            run_id=run_id,
            statuses=(),
            proof_hash=None,
            samples=tuple(samples),
            error=str(exc),
        )


def execute_agent_load_test(configuration: AgentLoadConfiguration) -> dict[str, Any]:
    """Run concurrent autonomous queues and return a JSON-safe reliability report."""

    with TemporaryDirectory(prefix="tallyguard-agent-load-") as temporary_directory:
        database_path = Path(temporary_directory) / "agent-load.sqlite3"
        app = create_app(database_path=database_path, testing=True)
        repository = app.extensions["tallyguard_repository"]
        authenticator = app.extensions["tallyguard_authenticator"]
        adapter = app.extensions["tallyguard_settlement_adapter"]
        identities = [
            _create_identity(
                repository=repository,
                authenticator=authenticator,
                index=f"{index:04d}",
            )
            for index in range(configuration.organizations)
        ]
        storm_identity = _create_identity(
            repository=repository,
            authenticator=authenticator,
            index="storm",
        )

        started = perf_counter()
        with LocalHttpServer(app) as server:
            with ThreadPoolExecutor(max_workers=configuration.concurrency) as pool:
                futures = [
                    pool.submit(
                        _run_agent_workflow,
                        base_url=server.base_url,
                        identity=identity,
                        timeout_seconds=configuration.timeout_seconds,
                    )
                    for identity in identities
                ]
                workflows = [future.result() for future in as_completed(futures)]

            cross_tenant_results: list[HttpResult] = []
            successful_by_org = {
                result.organization_id: result
                for result in workflows
                if result.error is None and result.run_id is not None
            }
            for index, identity in enumerate(identities):
                target = identities[(index + 1) % len(identities)]
                target_result = successful_by_org.get(target.organization_id)
                if target_result is None or target_result.run_id is None:
                    continue
                cross_tenant_results.append(
                    _http_json(
                        base_url=server.base_url,
                        path=f"/api/agent-runs/{target_result.run_id}/proof-packet",
                        name="security.cross_tenant_agent_proof",
                        token=identity.auditor_token,
                        timeout_seconds=configuration.timeout_seconds,
                    )
                )

            storm_seed = _http_json(
                base_url=server.base_url,
                path="/api/demo/autonomy-showcase",
                name="storm.showcase.seed",
                token=storm_identity.operator_token,
                timeout_seconds=configuration.timeout_seconds,
                body={},
            )
            storm_plan = _http_json(
                base_url=server.base_url,
                path="/api/agent-runs",
                name="storm.agent.plan",
                token=storm_identity.operator_token,
                timeout_seconds=configuration.timeout_seconds,
                body={"max_items": 25},
            )
            storm_run_id = str(storm_plan.payload.get("agent_run", {}).get("id", ""))
            submissions_before_storm = adapter.submission_count
            with ThreadPoolExecutor(max_workers=configuration.concurrency) as pool:
                storm_futures = [
                    pool.submit(
                        _http_json,
                        base_url=server.base_url,
                        path=f"/api/agent-runs/{storm_run_id}/execute",
                        name="storm.duplicate_agent_execution",
                        token=storm_identity.approver_token,
                        timeout_seconds=configuration.timeout_seconds,
                        body={},
                    )
                    for _ in range(configuration.duplicate_execution_storm)
                ]
                storm_results = [future.result() for future in as_completed(storm_futures)]

            final_storm_run = _http_json(
                base_url=server.base_url,
                path=f"/api/agent-runs/{storm_run_id}",
                name="storm.agent.final",
                token=storm_identity.auditor_token,
                timeout_seconds=configuration.timeout_seconds,
            )
            storm_proof = _http_json(
                base_url=server.base_url,
                path=f"/api/agent-runs/{storm_run_id}/proof-packet",
                name="storm.agent.proof",
                token=storm_identity.auditor_token,
                timeout_seconds=configuration.timeout_seconds,
            )

        duration_seconds = perf_counter() - started
        workflow_samples = [sample for result in workflows for sample in result.samples]
        all_samples = (
            workflow_samples
            + [result.sample for result in cross_tenant_results]
            + [storm_seed.sample, storm_plan.sample]
            + [result.sample for result in storm_results]
            + [final_storm_run.sample, storm_proof.sample]
        )
        successful = [result for result in workflows if result.error is None]
        storm_statuses = [result.status for result in storm_results]
        storm_run = final_storm_run.payload.get("agent_run", {})
        storm_result_statuses = sorted(
            str(item["status"]) for item in storm_run.get("results", [])
        )
        storm_events = [
            event
            for event in repository.audit_events(
                organization_id=storm_identity.organization_id
            )
            if event.aggregate_id == storm_run_id
        ]
        provider_submissions_in_storm = adapter.submission_count - submissions_before_storm
        by_request = {
            name: _latency_summary([sample for sample in all_samples if sample.name == name])
            for name in sorted({sample.name for sample in all_samples})
        }
        errors = [result.error for result in workflows if result.error is not None]
        if storm_seed.status != 201:
            errors.append(f"storm showcase seed returned {storm_seed.status}")
        if storm_plan.status != 201:
            errors.append(f"storm agent plan returned {storm_plan.status}")
        if any(status not in {200, 202} for status in storm_statuses):
            errors.append(f"duplicate execution storm returned statuses {sorted(set(storm_statuses))}")
        if final_storm_run.status != 200:
            errors.append(f"final storm run lookup returned {final_storm_run.status}")
        if storm_proof.status != 200:
            errors.append(f"storm proof returned {storm_proof.status}")

        summary = {
            "duration_seconds": round(duration_seconds, 3),
            "http_requests": len(all_samples),
            "organizations": configuration.organizations,
            "successful_agent_workflows": len(successful),
            "failed_agent_workflows": len(workflows) - len(successful),
            "mixed_queue_items": sum(len(result.statuses) for result in successful),
            "policy_authorized_settlements": sum(
                result.statuses.count("SETTLED") for result in successful
            ),
            "approval_routes": sum(result.statuses.count("ROUTED") for result in successful),
            "protected_non_settlements": sum(
                result.statuses.count("SKIPPED") for result in successful
            ),
            "verified_proof_packets": sum(result.proof_hash is not None for result in successful),
            "cross_tenant_attempts": len(cross_tenant_results),
            "cross_tenant_attempts_denied": sum(
                result.status == 404 for result in cross_tenant_results
            ),
            "duplicate_execution_requests": len(storm_results),
            "duplicate_execution_claim_winners": sum(
                result.status == 200 and not result.payload.get("reused_result", False)
                for result in storm_results
            ),
            "duplicate_execution_in_progress": sum(
                result.status == 202 for result in storm_results
            ),
            "duplicate_execution_reused": sum(
                result.status == 200 and result.payload.get("reused_result", False)
                for result in storm_results
            ),
            "duplicate_execution_provider_submissions": provider_submissions_in_storm,
            "duplicate_execution_terminal_status": storm_run.get("status"),
            "duplicate_execution_result_statuses": storm_result_statuses,
            "duplicate_execution_audit_events": [event.event_type for event in storm_events],
            "orchestration_single_execution_preserved": (
                len(storm_results) == configuration.duplicate_execution_storm
                and all(status in {200, 202} for status in storm_statuses)
                and sum(
                    result.status == 200 and not result.payload.get("reused_result", False)
                    for result in storm_results
                )
                == 1
                and provider_submissions_in_storm == 1
                and storm_run.get("status") == "EXECUTED"
                and storm_result_statuses == ["ROUTED", "SETTLED", "SKIPPED", "SKIPPED"]
                and [event.event_type for event in storm_events]
                == ["AGENT_RUN_PLANNED", "AGENT_RUN_EXECUTED"]
            ),
        }
        report = {
            "schema_version": "1.0",
            "run_id": f"agent_load_{uuid4().hex}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "methodology": {
                "classification": "synthetic multi-tenant autonomous-agent reliability test",
                "transport": "real loopback HTTP against the Flask API",
                "settlement": "deterministic Arc simulator; no funds moved",
                "queue": "one PAY, one approval escalation, one policy hold, and one future schedule per tenant",
                "identity": "separate operator, approver, and auditor sessions per tenant",
                "duplicate_execution": "concurrent callers contend for one durable database execution claim",
                "note": "This report measures engineering reliability and is not customer traction.",
            },
            "configuration": asdict(configuration),
            "summary": summary,
            "latency_ms": {
                "overall": _latency_summary(all_samples),
                "by_request": by_request,
            },
            "errors": errors[:25],
        }
        repository.close()
        return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organizations", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--duplicate-execution-storm", type=int, default=50)
    parser.add_argument("--timeout-seconds", type=float, default=15)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    report = execute_agent_load_test(
        AgentLoadConfiguration(
            organizations=arguments.organizations,
            concurrency=arguments.concurrency,
            duplicate_execution_storm=arguments.duplicate_execution_storm,
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

"""Flask API for TallyGuard's tenant-scoped finance control plane."""

from __future__ import annotations

import csv
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from hashlib import sha256
from io import BytesIO, StringIO
import json
import logging
import os
from pathlib import Path
import re
from time import monotonic
from typing import Any, Callable
from uuid import uuid4

from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from .agent import (
    DeterministicEvidenceAnalyst,
    EvidenceAnalyst,
    OpenAICompatibleEvidenceAnalyst,
)
from .audit import canonical_json
from .approvals import (
    ApprovalError,
    ApprovalInbox,
    ApprovalRequest,
    ApprovalStatus,
    apply_approved_mainnet_payment,
    apply_approved_escalation,
)
from .autonomy import (
    AgentCandidate,
    AgentRun,
    AgentRunStatus,
    canonical_payload,
    plan_candidate,
    plan_payload,
    state_payload,
)
from .auth import (
    AuthenticationDenied,
    Authenticator,
    AuthorizationDenied,
    Permission,
    Principal,
    Role,
    authorize,
)
from .models import Invoice, TreasurySnapshot, Vendor
from .network import ArcNetworkConfig
from .openapi import build_openapi_contract
from .operations import RateLimitExceeded, RequestMetrics, TenantRateLimiter
from .evidence import (
    EvidencePackage,
    EvidenceRecord,
    EvidenceStore,
    EvidenceType,
    ExtractedField,
    ExtractionMethod,
    SourceLocation,
)
from .extraction import extract_pdf_text_fields
from .environment import load_local_environment
from .normalization import EvidenceNormalizer
from .payments import PaymentOrchestrator, PaymentOutcome
from .decisions import DecisionRecord, DecisionService
from .demo import build_demo_scenario, scenario_catalog
from .persistence import (
    PersistenceError,
    SettlementExecutionBlocked,
    SqliteRepository,
    StoredInvoice,
    StoredTreasurySnapshot,
)
from .policies import PolicyRepositoryError, StoredPolicy
from .policy import Policy
from .settlement import (
    SettlementAdapter,
    SettlementAttempt,
    SettlementDenied,
    SettlementService,
    SettlementUnavailable,
    SimulatedArcAdapter,
)
from .vendors import VendorDirectoryError, WalletVerificationMethod
from .workflow import InvoiceStatus, WorkflowError, status_for_decision


DEMO_ORGANIZATION_ID = "demo-org"


def _evidence_json(record: EvidenceRecord) -> dict[str, Any]:
    document = record.document
    return {
        "id": document.id,
        "organization_id": document.organization_id,
        "evidence_type": document.evidence_type.value,
        "filename": document.filename,
        "mime_type": document.mime_type,
        "content_sha256": document.content_sha256,
        "byte_size": document.byte_size,
        "ingested_at": document.ingested_at.isoformat(),
        "fields": [
            {
                "name": field.name,
                "raw_value": field.raw_value,
                "normalized_value": field.normalized_value,
                "confidence": format(field.confidence, "f"),
                "method": field.method.value,
                "source": {
                    "document_id": field.source.document_id,
                    "page_number": field.source.page_number,
                    "bounding_box": field.source.bounding_box,
                    "json_pointer": field.source.json_pointer,
                },
            }
            for field in record.fields
        ],
    }


def _json_pointer_segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _evidence_fields(
    *,
    document_id: str,
    evidence_type: EvidenceType,
    mime_type: str,
    content: bytes,
    submitted_fields: str | None,
) -> tuple[ExtractedField, ...]:
    if submitted_fields:
        data = json.loads(submitted_fields)
        if not isinstance(data, list) or not data or len(data) > 64:
            raise ValueError("Evidence fields must be a JSON array containing 1 to 64 items.")
        fields: list[ExtractedField] = []
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("Each evidence field must be a JSON object.")
            source = item.get("source") or {}
            if not isinstance(source, dict):
                raise ValueError("Evidence field source must be a JSON object.")
            bounding_box = source.get("bounding_box")
            fields.append(
                ExtractedField(
                    name=str(item.get("name", "")),
                    raw_value=str(item.get("raw_value", item.get("normalized_value", ""))),
                    normalized_value=str(item.get("normalized_value", "")),
                    confidence=Decimal(str(item.get("confidence", "1"))),
                    method=ExtractionMethod(
                        str(
                            item.get(
                                "method",
                                "JSON" if mime_type == "application/json" else "MANUAL",
                            )
                        ).upper()
                    ),
                    source=SourceLocation(
                        document_id=document_id,
                        page_number=(
                            int(source["page_number"])
                            if source.get("page_number") is not None
                            else None
                        ),
                        bounding_box=(
                            tuple(float(value) for value in bounding_box)
                            if bounding_box is not None
                            else None
                        ),
                        json_pointer=source.get("json_pointer"),
                    ),
                )
            )
        return tuple(fields)

    if mime_type == "application/pdf":
        return extract_pdf_text_fields(
            document_id=document_id,
            evidence_type=evidence_type,
            content=content,
        )
    if mime_type != "application/json":
        raise ValueError(
            "Image evidence requires provenance-bound extracted fields; autonomous OCR is not enabled."
        )
    parsed = json.loads(content.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("JSON evidence must contain a top-level object.")
    fields = tuple(
        ExtractedField(
            name=str(name),
            raw_value=str(value),
            normalized_value=str(value),
            confidence=Decimal("1"),
            method=ExtractionMethod.JSON,
            source=SourceLocation(
                document_id=document_id,
                json_pointer=f"/{_json_pointer_segment(str(name))}",
            ),
        )
        for name, value in parsed.items()
        if value is not None and not isinstance(value, (dict, list))
    )
    if not fields:
        raise ValueError("JSON evidence contains no scalar fields to extract.")
    if len(fields) > 64:
        raise ValueError("JSON evidence may expose at most 64 top-level scalar fields.")
    return fields


def _invoice_json(stored: StoredInvoice) -> dict[str, Any]:
    invoice = stored.invoice
    return {
        "id": invoice.id,
        "organization_id": invoice.organization_id,
        "vendor_id": invoice.vendor_id,
        "invoice_number": invoice.invoice_number,
        "currency": invoice.currency,
        "amount": format(invoice.amount, "f"),
        "due_date": invoice.due_date.isoformat(),
        "payment_wallet_address": invoice.payment_wallet_address,
        "source_document_hash": invoice.source_document_hash,
        "status": stored.status.value,
        "version": stored.version,
        "created_at": stored.created_at.isoformat(),
        "updated_at": stored.updated_at.isoformat(),
    }


def _vendor_json(vendor: Vendor) -> dict[str, Any]:
    return {
        "id": vendor.id,
        "organization_id": vendor.organization_id,
        "legal_name": vendor.legal_name,
        "approved_wallet_address": vendor.approved_wallet_address,
        "autopay_limit": format(vendor.autopay_limit, "f"),
        "risk_tier": vendor.risk_tier,
        "active": vendor.active,
    }


def _policy_json(stored: StoredPolicy) -> dict[str, Any]:
    policy = stored.policy
    return {
        "version": policy.version,
        "organization_id": policy.organization_id,
        "daily_payment_limit_usdc": format(policy.daily_payment_limit_usdc, "f"),
        "minimum_cash_reserve_usdc": format(policy.minimum_cash_reserve_usdc, "f"),
        "maximum_autonomous_payment_usdc": format(
            policy.maximum_autonomous_payment_usdc, "f"
        ),
        "po_amount_tolerance_usdc": format(policy.po_amount_tolerance_usdc, "f"),
        "allowed_asset": policy.allowed_asset,
        "allowed_network": policy.allowed_network,
        "kill_switch_enabled": policy.kill_switch_enabled,
        "schedule_payments_before_due_days": policy.schedule_payments_before_due_days,
        "content_hash": stored.content_hash,
        "activated_by_user_id": stored.activated_by_user_id,
        "activated_at": stored.activated_at.isoformat(),
    }


def _treasury_json(stored: StoredTreasurySnapshot) -> dict[str, Any]:
    return {
        "sequence": stored.sequence,
        "organization_id": stored.snapshot.organization_id,
        "available_usdc": format(stored.snapshot.available_usdc, "f"),
        "spent_today_usdc": format(stored.snapshot.spent_today_usdc, "f"),
        "source_reference": stored.source_reference,
        "recorded_by_user_id": stored.recorded_by_user_id,
        "recorded_at": stored.recorded_at.isoformat(),
    }


def _json_scalar(value: Any) -> Any:
    return format(value, "f") if isinstance(value, Decimal) else value


def _csv_cell(value: Any) -> str:
    """Render user-controlled text without spreadsheet formula execution."""

    text = "" if value is None else str(value)
    if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _scheduled_for(record: DecisionRecord) -> date | None:
    inputs = record.replay_inputs
    if record.final_action.value != "SCHEDULE" or inputs is None:
        return None
    lead_days = inputs.policy.schedule_payments_before_due_days
    if lead_days is None:
        return None
    return inputs.evidence.invoice.due_date - timedelta(days=lead_days)


def _decision_json(record: DecisionRecord) -> dict[str, Any]:
    recommendation = record.agent_recommendation
    scheduled_for = _scheduled_for(record)
    return {
        "id": record.id,
        "organization_id": record.organization_id,
        "invoice_id": record.invoice_id,
        "evidence_manifest_hash": record.evidence_manifest_hash,
        "policy_version": record.policy_version,
        "policy_content_hash": record.policy_content_hash,
        "replayable": record.replay_inputs is not None and record.replay_input_hash is not None,
        "replay_input_hash": record.replay_input_hash,
        "agent_recommendation": (
            {
                "action": recommendation.action.value,
                "summary": recommendation.summary,
                "reason_codes": list(recommendation.reason_codes),
                "confidence": format(recommendation.confidence, "f"),
                "evidence_refs": list(recommendation.evidence_refs),
            }
            if recommendation is not None
            else None
        ),
        "agent_disagreed": record.agent_disagreed,
        "final_action": record.final_action.value,
        "scheduled_for": scheduled_for.isoformat() if scheduled_for is not None else None,
        "reason_codes": list(record.policy_decision.reason_codes),
        "remediation": list(record.policy_decision.remediation),
        "rules": [
            {
                "code": result.code,
                "disposition": result.disposition.value,
                "message": result.message,
                "remediation": result.remediation,
            }
            for result in record.policy_decision.rule_results
        ],
        "created_at": record.created_at.isoformat(),
    }


def _payment_json(outcome: PaymentOutcome, config: ArcNetworkConfig) -> dict[str, Any]:
    intent = outcome.intent
    receipt = outcome.receipt
    return {
        "intent": {
            "id": intent.id,
            "organization_id": intent.organization_id,
            "invoice_id": intent.invoice_id,
            "decision_id": intent.decision_id,
            "recipient": intent.recipient,
            "amount_usdc": format(intent.amount_usdc, "f"),
            "network": intent.network.value,
            "approval_reference": intent.approval_reference,
        },
        "receipt": {
            "provider": receipt.provider,
            "provider_reference": receipt.provider_reference,
            "transaction_hash": receipt.transaction_hash,
            "block_number": receipt.block_number,
            "confirmed_recipient": receipt.confirmed_recipient,
            "confirmed_amount_usdc": format(receipt.confirmed_amount_usdc, "f"),
            "network": receipt.network.value,
            "status": receipt.status.value,
            "confirmed_at": receipt.confirmed_at.isoformat(),
            "explorer_url": f"{config.explorer_url.rstrip('/')}/tx/{receipt.transaction_hash}",
        },
        "invoice": _invoice_json(outcome.invoice),
        "reused_receipt": outcome.reused_receipt,
    }


def _settlement_attempt_json(attempt: SettlementAttempt) -> dict[str, Any]:
    return {
        "sequence": attempt.sequence,
        "organization_id": attempt.organization_id,
        "payment_intent_id": attempt.payment_intent_id,
        "invoice_id": attempt.invoice_id,
        "provider": attempt.provider,
        "outcome": attempt.outcome.value,
        "retryable": attempt.retryable,
        "error_code": attempt.error_code,
        "error_message": attempt.error_message,
        "correlation_id": attempt.correlation_id,
        "created_at": attempt.created_at.isoformat(),
    }


def _agent_run_json(run: AgentRun) -> dict[str, Any]:
    executable = sum(item.executable for item in run.items)
    counts: dict[str, int] = {}
    for item in run.items:
        counts[item.action.value] = counts.get(item.action.value, 0) + 1
    return {
        "id": run.id,
        "organization_id": run.organization_id,
        "status": run.status.value,
        "as_of": run.as_of.isoformat(),
        "state_hash": run.state_hash,
        "plan_hash": run.plan_hash,
        "summary": {
            "scanned": len(run.items),
            "executable": executable,
            "requires_attention": len(run.items) - executable,
            "actions": counts,
        },
        "items": [item.to_payload() for item in run.items],
        "created_by_user_id": run.created_by_user_id,
        "created_at": run.created_at.isoformat(),
        "executed_by_user_id": run.executed_by_user_id,
        "executed_at": run.executed_at.isoformat() if run.executed_at else None,
        "results": list(run.results),
    }


def _approval_json(approval: ApprovalRequest) -> dict[str, Any]:
    return {
        "id": approval.id,
        "organization_id": approval.organization_id,
        "invoice_id": approval.invoice_id,
        "decision_id": approval.decision_id,
        "requested_by_user_id": approval.requested_by_user_id,
        "requested_at": approval.requested_at.isoformat(),
        "status": approval.status.value,
        "version": approval.version,
        "resolved_by_user_id": approval.resolved_by_user_id,
        "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
        "resolution_note": approval.resolution_note,
    }


def _audit_event_json(event: Any) -> dict[str, Any]:
    return {
        "sequence": event.sequence,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "event_type": event.event_type,
        "payload": event.payload,
        "previous_hash": event.previous_hash,
        "event_hash": event.event_hash,
        "created_at": event.created_at.isoformat(),
    }


def _error(code: str, message: str, status: int) -> tuple[Response, int]:
    return jsonify({"error": {"code": code, "message": message}, "correlation_id": _correlation_id()}), status


def _correlation_id() -> str:
    if not hasattr(g, "correlation_id"):
        candidate = (request.headers.get("X-Correlation-ID") or "").strip()
        g.correlation_id = (
            candidate
            if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", candidate)
            else f"req_{uuid4().hex}"
        )
    return g.correlation_id


def _evidence_analyst_from_env() -> EvidenceAnalyst:
    mode = os.getenv("TALLYGUARD_AGENT_MODE", "deterministic").strip().lower()
    fallback = DeterministicEvidenceAnalyst()
    if mode == "deterministic":
        return fallback
    if mode != "openai-compatible":
        raise ValueError(
            "TALLYGUARD_AGENT_MODE must be deterministic or openai-compatible."
        )
    return OpenAICompatibleEvidenceAnalyst(
        endpoint=os.getenv("TALLYGUARD_AGENT_URL", ""),
        api_key=os.getenv("TALLYGUARD_AGENT_API_KEY", ""),
        model=os.getenv("TALLYGUARD_AGENT_MODEL", ""),
        timeout_seconds=float(os.getenv("TALLYGUARD_AGENT_TIMEOUT_SECONDS", "10")),
        fallback=fallback,
    )


def create_app(
    *,
    database_path: str | Path | None = None,
    testing: bool = False,
    settlement_adapter: SettlementAdapter | None = None,
    settlement_config: ArcNetworkConfig | None = None,
    allow_mainnet: bool | None = None,
    rate_limit_per_minute: int | None = None,
    demo_session_rate_limit_per_minute: int | None = None,
    maximum_active_sessions_per_principal: int | None = None,
    evidence_analyst: EvidenceAnalyst | None = None,
    date_provider: Callable[[], date] | None = None,
    request_logging_enabled: bool | None = None,
) -> Flask:
    default_frontend_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    frontend_dist = Path(
        os.getenv("TALLYGUARD_FRONTEND_DIST", str(default_frontend_dist))
    ).resolve()
    default_reliability_report = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "reports"
        / "load-test-10000.json"
    )
    reliability_report_path = Path(
        os.getenv("TALLYGUARD_RELIABILITY_REPORT", str(default_reliability_report))
    ).resolve()
    default_agent_reliability_report = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "reports"
        / "agent-run-load-50.json"
    )
    agent_reliability_report_path = Path(
        os.getenv(
            "TALLYGUARD_AGENT_RELIABILITY_REPORT",
            str(default_agent_reliability_report),
        )
    ).resolve()
    app = Flask(__name__, static_folder=None)
    max_evidence_bytes = int(
        os.getenv("TALLYGUARD_MAX_EVIDENCE_BYTES", str(10 * 1024 * 1024))
    )
    if max_evidence_bytes < 1:
        raise ValueError("TALLYGUARD_MAX_EVIDENCE_BYTES must be positive.")
    app.config.update(
        TESTING=testing,
        MAX_CONTENT_LENGTH=max_evidence_bytes + (1024 * 1024),
    )
    resolved_path = database_path or os.getenv("TALLYGUARD_DATABASE_PATH", "data/tallyguard.sqlite3")
    repository = SqliteRepository(resolved_path)
    configured_maximum_active_sessions = (
        maximum_active_sessions_per_principal
        if maximum_active_sessions_per_principal is not None
        else int(os.getenv("TALLYGUARD_MAX_ACTIVE_SESSIONS_PER_PRINCIPAL", "32"))
    )
    authenticator = Authenticator(
        store=repository,
        maximum_active_sessions_per_principal=configured_maximum_active_sessions,
    )
    decision_service = DecisionService(repository=repository, audit_chain=repository)
    approval_inbox = ApprovalInbox(store=repository)
    network_config = settlement_config or ArcNetworkConfig.from_env()
    if settlement_adapter is None:
        mode = "simulation" if testing else os.getenv("TALLYGUARD_MODE", "simulation").strip().lower()
        if mode == "simulation":
            settlement_adapter = SimulatedArcAdapter()
        elif mode == "circle":
            from .circle_arc import CircleArcAdapter

            settlement_adapter = CircleArcAdapter.from_env(network_config)
        else:
            raise ValueError("TALLYGUARD_MODE must be simulation or circle.")
    if allow_mainnet is None:
        allow_mainnet = (
            os.getenv("TALLYGUARD_ALLOW_MAINNET", "false").strip().lower()
            == "true"
        )
    settlement_service = SettlementService(
        config=network_config,
        adapter=settlement_adapter,
        allow_mainnet=allow_mainnet,
    )
    treasury_max_age = timedelta(
        seconds=int(os.getenv("TALLYGUARD_TREASURY_MAX_AGE_SECONDS", "900"))
    )
    payment_orchestrator = PaymentOrchestrator(
        repository=repository,
        settlement_service=settlement_service,
        network=network_config,
        enforce_execution_controls=True,
        maximum_snapshot_age=treasury_max_age,
    )
    configured_rate_limit = rate_limit_per_minute or int(
        os.getenv("TALLYGUARD_RATE_LIMIT_PER_MINUTE", "6000")
    )
    rate_limiter = TenantRateLimiter(limit=configured_rate_limit)
    configured_demo_session_rate_limit = (
        demo_session_rate_limit_per_minute
        if demo_session_rate_limit_per_minute is not None
        else int(os.getenv("TALLYGUARD_DEMO_SESSION_RATE_LIMIT_PER_MINUTE", "120"))
    )
    demo_session_rate_limiter = TenantRateLimiter(
        limit=configured_demo_session_rate_limit
    )
    request_metrics = RequestMetrics()
    if request_logging_enabled is None:
        request_logging_enabled = os.getenv(
            "TALLYGUARD_REQUEST_LOGS",
            "false" if testing else "true",
        ).strip().lower() == "true"
    if request_logging_enabled:
        app.logger.setLevel(logging.INFO)
    evidence_analyst = evidence_analyst or _evidence_analyst_from_env()
    current_date = date_provider or date.today
    app.extensions["tallyguard_repository"] = repository
    app.extensions["tallyguard_authenticator"] = authenticator
    app.extensions["tallyguard_decision_service"] = decision_service
    app.extensions["tallyguard_approval_inbox"] = approval_inbox
    app.extensions["tallyguard_network"] = network_config
    app.extensions["tallyguard_settlement_adapter"] = settlement_adapter
    app.extensions["tallyguard_payment_orchestrator"] = payment_orchestrator
    app.extensions["tallyguard_rate_limiter"] = rate_limiter
    app.extensions["tallyguard_demo_session_rate_limiter"] = demo_session_rate_limiter
    app.extensions["tallyguard_request_metrics"] = request_metrics
    app.extensions["tallyguard_request_logging_enabled"] = request_logging_enabled
    app.extensions["tallyguard_evidence_analyst"] = evidence_analyst
    app.extensions["tallyguard_frontend_dist"] = frontend_dist
    demo_sessions_enabled = (
        testing
        or settlement_adapter.name == "arc-simulator"
        or os.getenv("TALLYGUARD_ENABLE_DEMO_SESSIONS", "false").strip().lower() == "true"
    )
    app.extensions["tallyguard_demo_sessions_enabled"] = demo_sessions_enabled

    _seed_demo_identity(repository)

    @app.before_request
    def assign_correlation_id() -> None:
        _correlation_id()
        g.request_started_monotonic = monotonic()

    @app.after_request
    def attach_correlation_id(response: Response) -> Response:
        elapsed_seconds = monotonic() - g.request_started_monotonic
        request_metrics.observe(
            endpoint=request.endpoint or "unmatched",
            status_code=response.status_code,
            elapsed_seconds=elapsed_seconds,
        )
        if request_logging_enabled:
            app.logger.info(
                canonical_json(
                    {
                        "correlation_id": _correlation_id(),
                        "duration_ms": round(elapsed_seconds * 1000, 3),
                        "endpoint": request.endpoint or "unmatched",
                        "event": "http_request",
                        "method": request.method,
                        "status_code": response.status_code,
                    }
                )
            )
        response.headers["X-Correlation-ID"] = _correlation_id()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "base-uri 'none'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "object-src 'none'"
        )
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        if request.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large_error(exc: RequestEntityTooLarge):
        return _error("REQUEST_TOO_LARGE", "Evidence request exceeds the configured limit.", 413)

    @app.errorhandler(AuthenticationDenied)
    def authentication_error(exc: AuthenticationDenied):
        return _error("AUTHENTICATION_DENIED", str(exc), 401)

    @app.errorhandler(AuthorizationDenied)
    def authorization_error(exc: AuthorizationDenied):
        return _error("AUTHORIZATION_DENIED", str(exc), 403)

    @app.errorhandler(PersistenceError)
    def persistence_error(exc: PersistenceError):
        status = 404 if "not found" in str(exc).lower() else 409
        return _error("PERSISTENCE_ERROR", str(exc), status)

    @app.errorhandler(RateLimitExceeded)
    def rate_limit_error(exc: RateLimitExceeded):
        response, status = _error("RATE_LIMIT_EXCEEDED", str(exc), 429)
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
        return response, status

    @app.errorhandler(ApprovalError)
    def approval_error(exc: ApprovalError):
        status = 404 if "not found" in str(exc).lower() else 409
        return _error("APPROVAL_ERROR", str(exc), status)

    @app.errorhandler(VendorDirectoryError)
    def vendor_directory_error(exc: VendorDirectoryError):
        status = 404 if "not found" in str(exc).lower() else 409
        return _error("VENDOR_ERROR", str(exc), status)

    @app.errorhandler(PolicyRepositoryError)
    def policy_repository_error(exc: PolicyRepositoryError):
        message = str(exc).lower()
        status = 404 if "not found" in message or "no active" in message else 409
        return _error("POLICY_ERROR", str(exc), status)

    @app.errorhandler(WorkflowError)
    def workflow_error(exc: WorkflowError):
        return _error("WORKFLOW_ERROR", str(exc), 409)

    @app.errorhandler(SettlementDenied)
    def settlement_error(exc: SettlementDenied):
        return _error("SETTLEMENT_DENIED", str(exc), 409)

    @app.errorhandler(SettlementUnavailable)
    def settlement_unavailable(exc: SettlementUnavailable):
        response, status = _error("SETTLEMENT_UNAVAILABLE", str(exc), 503)
        response.headers["Retry-After"] = "2"
        return response, status

    @app.errorhandler(KeyError)
    def missing_domain_record(exc: KeyError):
        return _error("NOT_FOUND", str(exc).strip("'"), 404)

    @app.errorhandler(ValueError)
    def validation_error(exc: ValueError):
        return _error("VALIDATION_ERROR", str(exc), 400)

    def require(permission: Permission):
        def decorator(handler: Callable[..., Any]):
            @wraps(handler)
            def wrapped(*args: Any, **kwargs: Any):
                header = request.headers.get("Authorization", "")
                if not header.startswith("Bearer "):
                    raise AuthenticationDenied("Bearer token is required.")
                principal = authenticator.authenticate(header.removeprefix("Bearer ").strip())
                authorize(
                    principal,
                    permission=permission,
                    resource_organization_id=principal.organization_id,
                )
                rate_limiter.check(principal.organization_id)
                g.principal = principal
                return handler(*args, **kwargs)

            return wrapped

        return decorator

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "tallyguard-api"})

    @app.get("/api/openapi.json")
    def openapi_contract():
        return jsonify(
            build_openapi_contract(
                app,
                server_url=request.url_root.rstrip("/"),
            )
        )

    @app.get("/api/readiness")
    def readiness():
        repository.list_invoices(organization_id=DEMO_ORGANIZATION_ID, limit=1)
        simulated = settlement_adapter.name == "arc-simulator"
        return jsonify(
            {
                "status": "ready",
                "database": "ok",
                "network": network_config.name.value,
                "settlement_adapter": settlement_adapter.name,
                "settlement_mode": "simulation" if simulated else "circle-live",
                "funds_movement": "disabled" if simulated else "enabled",
                "arc_rpc_verification": "simulated" if simulated else "independent-live",
                "mainnet_enabled": network_config.is_mainnet and allow_mainnet,
                "demo_sessions_enabled": demo_sessions_enabled,
                "evidence_analyst": getattr(
                    evidence_analyst, "name", evidence_analyst.__class__.__name__
                ),
            }
        )

    @app.get("/api/metrics")
    def metrics():
        snapshot = request_metrics.snapshot()
        return jsonify(
            {
                "requests_total": snapshot.requests_total,
                "responses_by_class": snapshot.responses_by_class,
                "responses_by_endpoint": snapshot.responses_by_endpoint,
                "latency_ms": snapshot.latency_ms,
                "uptime_seconds": snapshot.uptime_seconds,
                "labels": "No tenant, user, invoice, vendor, or wallet labels are recorded.",
            }
        )

    @app.get("/api/auth/session")
    @require(Permission.AUDIT_READ)
    def current_session():
        principal: Principal = g.principal
        return jsonify(
            {
                "principal": {
                    "user_id": principal.user_id,
                    "organization_id": principal.organization_id,
                    "roles": [role.value for role in principal.roles],
                }
            }
        )

    @app.get("/api/reliability/report")
    @require(Permission.AUDIT_READ)
    def reliability_report():
        def read_checked_report(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise PersistenceError(f"{label} reliability report is not available.") from exc
            if len(raw) > 64 * 1024:
                raise PersistenceError(f"{label} reliability report exceeds the 64 KiB safety limit.")
            try:
                parsed = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PersistenceError(f"{label} reliability report is not valid JSON.") from exc
            if not isinstance(parsed, dict) or not all(
                isinstance(parsed.get(key), dict)
                for key in ("configuration", "latency_ms", "methodology", "summary")
            ):
                raise PersistenceError(f"{label} reliability report schema is incomplete.")
            return parsed, raw

        report, raw_report = read_checked_report(reliability_report_path, "Workflow")
        agent_report, raw_agent_report = read_checked_report(
            agent_reliability_report_path,
            "Agent-run",
        )
        return jsonify(
            {
                "report": report,
                "artifact": {
                    "filename": reliability_report_path.name,
                    "sha256": sha256(raw_report).hexdigest(),
                    "immutable": True,
                },
                "agent_report": agent_report,
                "agent_artifact": {
                    "filename": agent_reliability_report_path.name,
                    "sha256": sha256(raw_agent_report).hexdigest(),
                    "immutable": True,
                },
            }
        )

    @app.post("/api/demo/session")
    def demo_session():
        if not demo_sessions_enabled:
            return _error(
                "DEMO_SESSIONS_DISABLED",
                "Demo identities are disabled while a live settlement adapter is configured.",
                404,
            )
        remote_address = request.remote_addr or "unknown"
        anonymous_scope = sha256(remote_address.encode("utf-8")).hexdigest()
        demo_session_rate_limiter.check(
            f"demo-session:{anonymous_scope}",
            scope_label="Demo session",
        )
        payload = request.get_json(silent=True) or {}
        role_name = str(payload.get("role", "operator")).lower()
        demo_principals = {
            "admin": Principal(
                user_id="demo-admin",
                organization_id=DEMO_ORGANIZATION_ID,
                roles=(Role.ADMIN,),
            ),
            "operator": Principal(
                user_id="demo-operator",
                organization_id=DEMO_ORGANIZATION_ID,
                roles=(Role.FINANCE_OPERATOR,),
            ),
            "approver": Principal(
                user_id="demo-approver",
                organization_id=DEMO_ORGANIZATION_ID,
                roles=(Role.APPROVER,),
            ),
            "auditor": Principal(
                user_id="demo-auditor",
                organization_id=DEMO_ORGANIZATION_ID,
                roles=(Role.AUDITOR,),
            ),
        }
        principal = demo_principals.get(role_name)
        if principal is None:
            raise ValueError("Demo role must be admin, operator, approver, or auditor.")
        token, session = authenticator.issue_session(principal)
        return jsonify(
            {
                "access_token": token,
                "token_type": "Bearer",
                "expires_at": session.expires_at.isoformat(),
                "principal": {
                    "user_id": principal.user_id,
                    "organization_id": principal.organization_id,
                    "roles": [role.value for role in principal.roles],
                },
            }
        )

    @app.get("/api/demo/scenarios")
    def demo_scenarios():
        available = scenario_catalog()
        if not isinstance(settlement_adapter, SimulatedArcAdapter):
            available = tuple(item for item in available if item.key != "provider-recovery")
        return jsonify(
            {
                "items": [
                    {
                        "key": item.key,
                        "title": item.title,
                        "description": item.description,
                        "expected_action": item.expected_action.value,
                    }
                    for item in available
                ]
            }
        )

    @app.post("/api/policies")
    @require(Permission.POLICY_WRITE)
    def activate_policy():
        payload = request.get_json(silent=False) or {}
        kill_switch_enabled = payload.get("kill_switch_enabled", False)
        if not isinstance(kill_switch_enabled, bool):
            raise ValueError("kill_switch_enabled must be a JSON boolean.")
        schedule_days = payload.get("schedule_payments_before_due_days")
        if schedule_days is not None and (
            isinstance(schedule_days, bool) or not isinstance(schedule_days, int)
        ):
            raise ValueError("schedule_payments_before_due_days must be an integer or null.")
        policy = Policy(
            version=str(payload["version"]),
            organization_id=g.principal.organization_id,
            daily_payment_limit_usdc=Decimal(
                str(payload["daily_payment_limit_usdc"])
            ),
            minimum_cash_reserve_usdc=Decimal(
                str(payload["minimum_cash_reserve_usdc"])
            ),
            maximum_autonomous_payment_usdc=Decimal(
                str(payload["maximum_autonomous_payment_usdc"])
            ),
            po_amount_tolerance_usdc=Decimal(
                str(payload.get("po_amount_tolerance_usdc", "0"))
            ),
            allowed_asset=str(payload.get("allowed_asset", "USDC")),
            allowed_network=str(payload.get("allowed_network", network_config.name.value)),
            kill_switch_enabled=kill_switch_enabled,
            schedule_payments_before_due_days=schedule_days,
        )
        stored = repository.activate_policy(
            policy,
            activated_by_user_id=g.principal.user_id,
        )
        repository.append(
            aggregate_type="policy",
            aggregate_id=stored.policy.version,
            event_type="POLICY_ACTIVATED",
            payload={
                "organization_id": g.principal.organization_id,
                "policy_version": stored.policy.version,
                "content_hash": stored.content_hash,
                "activated_by_user_id": g.principal.user_id,
            },
        )
        return jsonify({"policy": _policy_json(stored)}), 201

    @app.get("/api/policies")
    @require(Permission.INVOICE_READ)
    def list_policies():
        history = repository.policy_history(
            organization_id=g.principal.organization_id
        )
        active = (
            repository.active_policy(organization_id=g.principal.organization_id)
            if history
            else None
        )
        return jsonify(
            {
                "active_version": active.policy.version if active is not None else None,
                "items": [_policy_json(item) for item in history],
            }
        )

    @app.get("/api/policies/active")
    @require(Permission.INVOICE_READ)
    def get_active_policy():
        stored = repository.active_policy(
            organization_id=g.principal.organization_id
        )
        return jsonify({"policy": _policy_json(stored)})

    @app.get("/api/policies/diff")
    @require(Permission.INVOICE_READ)
    def get_policy_diff():
        from_version = request.args.get("from", "").strip()
        to_version = request.args.get("to", "").strip()
        if not from_version or not to_version:
            raise ValueError("Policy diff requires from and to version query parameters.")
        changes = repository.policy_diff(
            organization_id=g.principal.organization_id,
            from_version=from_version,
            to_version=to_version,
        )
        return jsonify(
            {
                "from_version": from_version,
                "to_version": to_version,
                "changes": [
                    {
                        "field": item.field,
                        "before": _json_scalar(item.before),
                        "after": _json_scalar(item.after),
                    }
                    for item in changes
                ],
            }
        )

    @app.post("/api/treasury/snapshots")
    @require(Permission.TREASURY_WRITE)
    def record_treasury_snapshot():
        payload = request.get_json(silent=False) or {}
        snapshot = TreasurySnapshot(
            organization_id=g.principal.organization_id,
            available_usdc=Decimal(str(payload["available_usdc"])),
            spent_today_usdc=Decimal(str(payload["spent_today_usdc"])),
        )
        stored = repository.record_treasury_snapshot(
            snapshot,
            source_reference=str(payload["source_reference"]),
            recorded_by_user_id=g.principal.user_id,
        )
        repository.append(
            aggregate_type="treasury_snapshot",
            aggregate_id=str(stored.sequence),
            event_type="TREASURY_SNAPSHOT_RECORDED",
            payload={
                "organization_id": g.principal.organization_id,
                "sequence": stored.sequence,
                "available_usdc": format(snapshot.available_usdc, "f"),
                "spent_today_usdc": format(snapshot.spent_today_usdc, "f"),
                "source_reference": stored.source_reference,
                "recorded_by_user_id": g.principal.user_id,
            },
        )
        return jsonify({"treasury": _treasury_json(stored)}), 201

    @app.get("/api/treasury/summary")
    @require(Permission.INVOICE_READ)
    def get_treasury_summary():
        stored = repository.latest_treasury_snapshot(
            organization_id=g.principal.organization_id
        )
        return jsonify({"treasury": _treasury_json(stored)})

    @app.post("/api/vendors")
    @require(Permission.VENDOR_WRITE)
    def onboard_vendor():
        payload = request.get_json(silent=False) or {}
        method = WalletVerificationMethod(
            str(payload["verification_method"]).strip().upper()
        )
        vendor = Vendor(
            id=str(payload["id"]),
            organization_id=g.principal.organization_id,
            legal_name=str(payload["legal_name"]),
            approved_wallet_address=str(payload["approved_wallet_address"]),
            autopay_limit=Decimal(str(payload["autopay_limit"])),
            risk_tier=str(payload.get("risk_tier", "standard")),
            active=bool(payload.get("active", True)),
        )
        stored = repository.onboard_vendor(
            vendor,
            verification_method=method,
            verification_reference=str(payload["verification_reference"]),
            verified_by_user_id=g.principal.user_id,
        )
        repository.append(
            aggregate_type="vendor",
            aggregate_id=stored.id,
            event_type="VENDOR_ONBOARDED",
            payload={
                "organization_id": g.principal.organization_id,
                "vendor_id": stored.id,
                "wallet_address": stored.approved_wallet_address,
                "verification_method": method.value,
                "verification_reference": str(payload["verification_reference"]),
                "verified_by_user_id": g.principal.user_id,
            },
        )
        return jsonify({"vendor": _vendor_json(stored)}), 201

    @app.get("/api/vendors")
    @require(Permission.INVOICE_READ)
    def list_vendors():
        items = repository.list_vendors(
            organization_id=g.principal.organization_id
        )
        return jsonify({"items": [_vendor_json(item) for item in items]})

    @app.patch("/api/vendors/<vendor_id>/wallet")
    @require(Permission.VENDOR_WRITE)
    def replace_vendor_wallet(vendor_id: str):
        payload = request.get_json(silent=False) or {}
        method = WalletVerificationMethod(
            str(payload["verification_method"]).strip().upper()
        )
        stored = repository.replace_vendor_wallet(
            organization_id=g.principal.organization_id,
            vendor_id=vendor_id,
            expected_current_wallet=str(payload["expected_current_wallet"]),
            new_wallet=str(payload["new_wallet"]),
            verification_method=method,
            verification_reference=str(payload["verification_reference"]),
            verified_by_user_id=g.principal.user_id,
        )
        repository.append(
            aggregate_type="vendor_wallet_change",
            aggregate_id=f"{vendor_id}:{uuid4().hex}",
            event_type="VENDOR_WALLET_REPLACED",
            payload={
                "organization_id": g.principal.organization_id,
                "vendor_id": stored.id,
                "previous_wallet_address": str(payload["expected_current_wallet"]).strip().lower(),
                "wallet_address": stored.approved_wallet_address,
                "verification_method": method.value,
                "verification_reference": str(payload["verification_reference"]),
                "verified_by_user_id": g.principal.user_id,
            },
        )
        return jsonify({"vendor": _vendor_json(stored)})

    @app.get("/api/vendors/<vendor_id>/wallet-history")
    @require(Permission.INVOICE_READ)
    def vendor_wallet_history(vendor_id: str):
        history = repository.vendor_wallet_history(
            organization_id=g.principal.organization_id,
            vendor_id=vendor_id,
        )
        return jsonify(
            {
                "items": [
                    {
                        "organization_id": item.organization_id,
                        "vendor_id": item.vendor_id,
                        "event_type": item.event_type.value,
                        "wallet_address": item.wallet_address,
                        "previous_wallet_address": item.previous_wallet_address,
                        "verification_method": item.verification_method.value,
                        "verification_reference": item.verification_reference,
                        "verified_by_user_id": item.verified_by_user_id,
                        "verified_at": item.verified_at.isoformat(),
                    }
                    for item in history
                ]
            }
        )

    def execute_demo_scenario(
        scenario_key: str,
        *,
        actor_user_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        if scenario_key == "provider-recovery" and not isinstance(
            settlement_adapter, SimulatedArcAdapter
        ):
            raise ValueError("The provider recovery drill is available only in safe simulation mode.")
        invoice_id = f"invoice_{scenario_key.replace('-', '_')}_{uuid4().hex[:12]}"
        scenario = build_demo_scenario(
            scenario_key,
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
        )
        try:
            repository.get_vendor(
                organization_id=g.principal.organization_id,
                vendor_id=scenario.vendor.id,
            )
        except VendorDirectoryError:
            try:
                repository.onboard_vendor(
                    scenario.vendor,
                    verification_method=WalletVerificationMethod.SIGNED_CHALLENGE,
                    verification_reference="demo-fixture-wallet-proof",
                    verified_by_user_id=actor_user_id,
                )
            except PersistenceError:
                # Concurrent demo requests can race on the shared fixture vendor.
                # The losing request must verify that the winner persisted the same record.
                if repository.get_vendor(
                    organization_id=g.principal.organization_id,
                    vendor_id=scenario.vendor.id,
                ) != scenario.vendor:
                    raise
        repository.activate_policy(
            scenario.policy,
            activated_by_user_id=actor_user_id,
        )
        repository.record_treasury_snapshot(
            scenario.treasury,
            source_reference="demo-fixture-treasury",
            recorded_by_user_id=actor_user_id,
        )
        stored = repository.create_invoice(scenario.evidence.invoice)
        stored = repository.transition_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
            target_status=InvoiceStatus.EVALUATING,
            expected_version=stored.version,
            actor_user_id=actor_user_id,
            correlation_id=correlation_id,
        )
        wallet_event = repository.vendor_wallet_history(
            organization_id=g.principal.organization_id,
            vendor_id=scenario.vendor.id,
        )[-1]
        decision = decision_service.evaluate(
            evidence=scenario.evidence,
            vendor=scenario.vendor,
            treasury=scenario.treasury,
            policy=scenario.policy,
            agent_recommendation=scenario.recommendation,
            known_invoice_fingerprints=scenario.known_invoice_fingerprints,
            evaluation_date=scenario.evaluation_date,
            vendor_wallet_event_type=wallet_event.event_type.value,
            vendor_wallet_verified_date=wallet_event.verified_at.date(),
        )
        if decision.final_action != scenario.definition.expected_action:
            raise RuntimeError("Demo scenario produced an unexpected control result.")
        stored = repository.transition_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
            target_status=status_for_decision(decision.final_action),
            expected_version=stored.version,
            actor_user_id="tallyguard-policy-engine",
            correlation_id=correlation_id,
        )
        if scenario_key == "provider-recovery":
            settlement_adapter.arm_transient_failure(
                organization_id=g.principal.organization_id,
                invoice_id=invoice_id,
            )
        return {
            "scenario": {
                "key": scenario.definition.key,
                "title": scenario.definition.title,
            },
            "invoice": _invoice_json(stored),
            "decision": _decision_json(decision),
            "correlation_id": correlation_id,
        }

    @app.post("/api/demo/scenarios/<scenario_key>/run")
    @require(Permission.DECISION_RUN)
    def run_demo_scenario(scenario_key: str):
        return jsonify(
            execute_demo_scenario(
                scenario_key,
                actor_user_id=g.principal.user_id,
                correlation_id=_correlation_id(),
            )
        )

    @app.post("/api/demo/autonomy-showcase")
    @require(Permission.DECISION_RUN)
    def seed_autonomy_showcase():
        showcase_id = f"showcase_{uuid4().hex}"
        scenario_keys = (
            "clean-payment",
            "large-invoice",
            "wallet-change",
            "scheduled-payment",
        )
        items = [
            execute_demo_scenario(
                scenario_key,
                actor_user_id=g.principal.user_id,
                correlation_id=f"{_correlation_id()}:{index + 1}",
            )
            for index, scenario_key in enumerate(scenario_keys)
        ]
        repository.append(
            aggregate_type="demo_showcase",
            aggregate_id=showcase_id,
            event_type="AUTONOMY_SHOWCASE_SEEDED",
            payload={
                "organization_id": g.principal.organization_id,
                "showcase_id": showcase_id,
                "scenario_keys": list(scenario_keys),
                "invoice_ids": [item["invoice"]["id"] for item in items],
                "actor_user_id": g.principal.user_id,
            },
        )
        return jsonify(
            {
                "showcase": {
                    "id": showcase_id,
                    "items": items,
                    "expected_actions": [
                        "SETTLE",
                        "REQUIRE_APPROVAL",
                        "REMEDIATE",
                        "WAIT_SCHEDULE",
                    ],
                },
                "correlation_id": _correlation_id(),
            }
        ), 201

    @app.post("/api/decisions/<decision_id>/request-approval")
    @require(Permission.DECISION_RUN)
    def request_approval(decision_id: str):
        decision = decision_service.repository.get_decision(
            organization_id=g.principal.organization_id,
            decision_id=decision_id,
        )
        approval = approval_inbox.request(decision, requested_by=g.principal)
        repository.append(
            aggregate_type="approval",
            aggregate_id=approval.id,
            event_type="APPROVAL_REQUESTED",
            payload={
                "organization_id": approval.organization_id,
                "invoice_id": approval.invoice_id,
                "decision_id": approval.decision_id,
                "requested_by_user_id": approval.requested_by_user_id,
            },
            created_at=approval.requested_at,
        )
        return jsonify(
            {
                "approval": {
                    "id": approval.id,
                    "decision_id": approval.decision_id,
                    "invoice_id": approval.invoice_id,
                    "status": approval.status.value,
                    "version": approval.version,
                    "requested_by_user_id": approval.requested_by_user_id,
                    "requested_at": approval.requested_at.isoformat(),
                },
                "correlation_id": _correlation_id(),
            }
        ), 201

    @app.post("/api/decisions/<decision_id>/request-mainnet-approval")
    @require(Permission.DECISION_RUN)
    def request_mainnet_approval(decision_id: str):
        if not network_config.is_mainnet or not allow_mainnet:
            raise ApprovalError(
                "Mainnet approval requests require an explicitly enabled ARC-MAINNET runtime."
            )
        decision = decision_service.repository.get_decision(
            organization_id=g.principal.organization_id,
            decision_id=decision_id,
        )
        approval = approval_inbox.request_mainnet_payment(
            decision,
            requested_by=g.principal,
        )
        repository.append(
            aggregate_type="approval",
            aggregate_id=approval.id,
            event_type="MAINNET_APPROVAL_REQUESTED",
            payload={
                "organization_id": approval.organization_id,
                "invoice_id": approval.invoice_id,
                "decision_id": approval.decision_id,
                "requested_by_user_id": approval.requested_by_user_id,
                "network": network_config.name.value,
            },
            created_at=approval.requested_at,
        )
        return jsonify(
            {
                "approval": {
                    **_approval_json(approval),
                    "purpose": "MAINNET_PAYMENT",
                },
                "correlation_id": _correlation_id(),
            }
        ), 201

    @app.get("/api/approvals/pending")
    @require(Permission.PAYMENT_APPROVE)
    def list_pending_approvals():
        items = []
        for approval in approval_inbox.pending(
            organization_id=g.principal.organization_id
        ):
            invoice = repository.get_invoice(
                organization_id=g.principal.organization_id,
                invoice_id=approval.invoice_id,
            )
            decision = decision_service.repository.get_decision(
                organization_id=g.principal.organization_id,
                decision_id=approval.decision_id,
            )
            items.append(
                {
                    "approval": _approval_json(approval),
                    "invoice": _invoice_json(invoice),
                    "decision": _decision_json(decision),
                }
            )
        return jsonify({"items": items, "correlation_id": _correlation_id()})

    @app.get("/api/governance/overview")
    @require(Permission.PAYMENT_APPROVE)
    def get_governance_overview():
        history = repository.policy_history(
            organization_id=g.principal.organization_id
        )
        active_policy = (
            repository.active_policy(organization_id=g.principal.organization_id)
            if history
            else None
        )
        try:
            capacity = repository.settlement_capacity(
                organization_id=g.principal.organization_id,
                maximum_snapshot_age=treasury_max_age,
            )
        except PersistenceError:
            capacity_json = None
        else:
            capacity_json = {
                "organization_id": capacity.organization_id,
                "active_policy_version": capacity.active_policy_version,
                "active_policy_hash": capacity.active_policy_hash,
                "kill_switch_enabled": capacity.kill_switch_enabled,
                "treasury_snapshot_sequence": capacity.treasury_snapshot_sequence,
                "treasury_snapshot_recorded_at": capacity.treasury_snapshot_recorded_at.isoformat(),
                "snapshot_age_seconds": capacity.snapshot_age_seconds,
                "snapshot_fresh": capacity.snapshot_fresh,
                "snapshot_available_usdc": format(capacity.snapshot_available_usdc, "f"),
                "snapshot_spent_today_usdc": format(capacity.snapshot_spent_today_usdc, "f"),
                "committed_since_snapshot_usdc": format(capacity.committed_since_snapshot_usdc, "f"),
                "effective_available_usdc": format(capacity.effective_available_usdc, "f"),
                "daily_payment_limit_usdc": format(capacity.daily_payment_limit_usdc, "f"),
                "daily_remaining_usdc": format(capacity.daily_remaining_usdc, "f"),
                "minimum_cash_reserve_usdc": format(capacity.minimum_cash_reserve_usdc, "f"),
                "maximum_new_payment_usdc": format(capacity.maximum_new_payment_usdc, "f"),
            }
        pending_approvals = []
        for approval in approval_inbox.pending(
            organization_id=g.principal.organization_id
        ):
            invoice = repository.get_invoice(
                organization_id=g.principal.organization_id,
                invoice_id=approval.invoice_id,
            )
            decision = decision_service.repository.get_decision(
                organization_id=g.principal.organization_id,
                decision_id=approval.decision_id,
            )
            pending_approvals.append(
                {
                    "approval": _approval_json(approval),
                    "invoice": _invoice_json(invoice),
                    "decision": _decision_json(decision),
                }
            )
        return jsonify(
            {
                "active_policy": (
                    _policy_json(active_policy) if active_policy is not None else None
                ),
                "settlement_capacity": capacity_json,
                "pending_approvals": pending_approvals,
                "correlation_id": _correlation_id(),
            }
        )

    @app.get("/api/decisions/<decision_id>")
    @require(Permission.INVOICE_READ)
    def get_decision(decision_id: str):
        record = decision_service.repository.get_decision(
            organization_id=g.principal.organization_id,
            decision_id=decision_id,
        )
        return jsonify(
            {
                "decision": _decision_json(record),
                "correlation_id": _correlation_id(),
            }
        )

    @app.get("/api/decisions/<decision_id>/replay")
    @require(Permission.AUDIT_READ)
    def replay_decision(decision_id: str):
        record = decision_service.repository.get_decision(
            organization_id=g.principal.organization_id,
            decision_id=decision_id,
        )
        verification = decision_service.verify_replay(record)
        return jsonify(
            {
                "verification": {
                    "verified": verification.verified,
                    "original_decision_id": verification.original_decision_id,
                    "replayed_decision_id": verification.replayed_decision_id,
                    "input_snapshot_hash": verification.input_snapshot_hash,
                    "action": verification.replayed_decision.action.value,
                    "reason_codes": list(verification.replayed_decision.reason_codes),
                    "checks": [
                        {
                            "code": check.code,
                            "passed": check.passed,
                            "expected": check.expected,
                            "actual": check.actual,
                        }
                        for check in verification.checks
                    ],
                },
                "correlation_id": _correlation_id(),
            }
        )

    @app.post("/api/decisions/<decision_id>/policy-simulation")
    @require(Permission.POLICY_WRITE)
    def simulate_decision_policy(decision_id: str):
        record = decision_service.repository.get_decision(
            organization_id=g.principal.organization_id,
            decision_id=decision_id,
        )
        inputs = record.replay_inputs
        if inputs is None or record.replay_input_hash is None:
            raise ValueError("Decision predates replay snapshots and cannot be simulated safely.")
        payload = request.get_json(silent=False)
        if not isinstance(payload, dict):
            raise ValueError("Policy simulation body must be a JSON object.")
        allowed = {
            "daily_payment_limit_usdc",
            "minimum_cash_reserve_usdc",
            "maximum_autonomous_payment_usdc",
            "po_amount_tolerance_usdc",
            "kill_switch_enabled",
            "schedule_payments_before_due_days",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(f"Unsupported policy simulation fields: {', '.join(unknown)}.")
        if not payload:
            raise ValueError("Policy simulation requires at least one changed field.")

        changes: dict[str, Any] = {"version": f"{inputs.policy.version}:simulation"}
        monetary_fields = allowed - {"kill_switch_enabled", "schedule_payments_before_due_days"}
        try:
            for field_name in monetary_fields:
                if field_name in payload:
                    changes[field_name] = Decimal(str(payload[field_name]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("Policy simulation monetary fields must be valid decimals.") from exc
        if "kill_switch_enabled" in payload:
            if not isinstance(payload["kill_switch_enabled"], bool):
                raise ValueError("kill_switch_enabled must be a boolean.")
            changes["kill_switch_enabled"] = payload["kill_switch_enabled"]
        if "schedule_payments_before_due_days" in payload:
            value = payload["schedule_payments_before_due_days"]
            if isinstance(value, bool):
                raise ValueError(
                    "schedule_payments_before_due_days must be an integer or null."
                )
            try:
                changes["schedule_payments_before_due_days"] = (
                    int(value) if value is not None else None
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "schedule_payments_before_due_days must be an integer or null."
                ) from exc
        simulated_policy = replace(inputs.policy, **changes)
        simulated = decision_service.policy_engine.evaluate(
            invoice=inputs.evidence.invoice,
            vendor=inputs.vendor,
            purchase_order=inputs.evidence.purchase_order,
            delivery=inputs.evidence.delivery,
            treasury=inputs.treasury,
            policy=simulated_policy,
            known_invoice_fingerprints=inputs.known_invoice_fingerprints,
            asset=inputs.asset,
            network=inputs.network,
            evaluation_date=inputs.evaluation_date,
            vendor_wallet_event_type=inputs.vendor_wallet_event_type,
            vendor_wallet_verified_date=inputs.vendor_wallet_verified_date,
        )

        changed_fields = []
        for field_name in sorted(set(changes) - {"version"}):
            before = getattr(inputs.policy, field_name)
            after = getattr(simulated_policy, field_name)
            changed_fields.append(
                {
                    "field": field_name,
                    "before": _json_scalar(before),
                    "after": _json_scalar(after),
                }
            )
        return jsonify(
            {
                "simulation": {
                    "persisted": False,
                    "source_decision_id": record.id,
                    "source_replay_input_hash": record.replay_input_hash,
                    "original_action": record.final_action.value,
                    "simulated_action": simulated.action.value,
                    "changed_fields": changed_fields,
                    "reason_codes": list(simulated.reason_codes),
                    "remediation": list(simulated.remediation),
                    "rules": [
                        {
                            "code": item.code,
                            "disposition": item.disposition.value,
                            "message": item.message,
                            "remediation": item.remediation,
                        }
                        for item in simulated.rule_results
                    ],
                },
                "correlation_id": _correlation_id(),
            }
        )

    @app.post("/api/approvals/<approval_id>/resolve")
    @require(Permission.PAYMENT_APPROVE)
    def resolve_approval(approval_id: str):
        payload = request.get_json(silent=True) or {}
        if "approve" not in payload or not isinstance(payload["approve"], bool):
            raise ValueError("Approval resolution requires a boolean approve field.")
        pending = approval_inbox.get(
            organization_id=g.principal.organization_id,
            approval_id=approval_id,
        )
        decision = decision_service.repository.get_decision(
            organization_id=g.principal.organization_id,
            decision_id=pending.decision_id,
        )
        if decision.final_action.value == "PAY" and (
            not network_config.is_mainnet or not allow_mainnet
        ):
            raise ApprovalError(
                "A PAY approval can only be resolved on an explicitly enabled ARC-MAINNET runtime."
            )
        if decision.final_action.value not in {"ESCALATE", "PAY"}:
            raise ApprovalError("This decision action cannot enter payment approval.")
        resolved = approval_inbox.resolve(
            organization_id=g.principal.organization_id,
            approval_id=approval_id,
            approver=g.principal,
            approve=payload["approve"],
            resolution_note=str(payload.get("note", "")),
            expected_version=int(payload.get("expected_version", 1)),
        )
        final_action = None
        invoice = repository.get_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=resolved.invoice_id,
        )
        if resolved.status.value == "APPROVED":
            if decision.final_action.value == "ESCALATE":
                authorized = apply_approved_escalation(decision, resolved)
                repository.transition_invoice(
                    organization_id=g.principal.organization_id,
                    invoice_id=resolved.invoice_id,
                    target_status=InvoiceStatus.READY,
                    expected_version=invoice.version,
                    actor_user_id=g.principal.user_id,
                    correlation_id=_correlation_id(),
                )
            elif decision.final_action.value == "PAY":
                authorized = apply_approved_mainnet_payment(decision, resolved)
            final_action = authorized.action.value
        else:
            if decision.final_action.value == "ESCALATE":
                repository.transition_invoice(
                    organization_id=g.principal.organization_id,
                    invoice_id=resolved.invoice_id,
                    target_status=InvoiceStatus.REJECTED,
                    expected_version=invoice.version,
                    actor_user_id=g.principal.user_id,
                    correlation_id=_correlation_id(),
                )
        repository.append(
            aggregate_type="approval",
            aggregate_id=resolved.id,
            event_type="APPROVAL_RESOLVED",
            payload={
                "organization_id": resolved.organization_id,
                "invoice_id": resolved.invoice_id,
                "decision_id": resolved.decision_id,
                "status": resolved.status.value,
                "resolved_by_user_id": resolved.resolved_by_user_id,
            },
            created_at=resolved.resolved_at,
        )
        return jsonify(
            {
                "approval": {
                    **_approval_json(resolved),
                    "authorized_action": final_action,
                },
                "correlation_id": _correlation_id(),
            }
        )

    @app.post("/api/invoices")
    @require(Permission.EVIDENCE_WRITE)
    def create_invoice():
        payload = request.get_json(silent=False)
        if not isinstance(payload, dict):
            raise ValueError("Invoice body must be a JSON object.")
        try:
            amount = Decimal(str(payload["amount"]))
            due_date = date.fromisoformat(str(payload["due_date"]))
            invoice = Invoice(
                id=str(payload.get("id") or f"invoice_{uuid4().hex}"),
                organization_id=g.principal.organization_id,
                vendor_id=str(payload["vendor_id"]),
                invoice_number=str(payload["invoice_number"]),
                currency=str(payload.get("currency", "USDC")),
                amount=amount,
                due_date=due_date,
                payment_wallet_address=str(payload["payment_wallet_address"]),
                source_document_hash=str(payload["source_document_hash"]),
            )
        except KeyError as exc:
            raise ValueError(f"Missing invoice field: {exc.args[0]}") from exc
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Invalid invoice payload: {exc}") from exc
        stored = repository.create_invoice(invoice)
        return jsonify({"invoice": _invoice_json(stored), "correlation_id": _correlation_id()}), 201

    @app.post("/api/evidence/extract")
    @require(Permission.EVIDENCE_WRITE)
    def extract_evidence_preview():
        upload = request.files.get("file")
        if upload is None:
            raise ValueError("Evidence extraction requires a multipart file field named 'file'.")
        filename = secure_filename(upload.filename or "")
        if not filename:
            raise ValueError("Evidence filename is required.")
        mime_type = (upload.mimetype or "").strip().lower()
        try:
            evidence_type = EvidenceType(str(request.form.get("evidence_type", "")).upper())
        except ValueError as exc:
            raise ValueError(
                "evidence_type must be INVOICE, PURCHASE_ORDER, or DELIVERY."
            ) from exc
        content = upload.read(max_evidence_bytes + 1)
        if len(content) > max_evidence_bytes:
            raise RequestEntityTooLarge()
        document_id = f"preview_{sha256(content).hexdigest()[:24]}"
        fields = _evidence_fields(
            document_id=document_id,
            evidence_type=evidence_type,
            mime_type=mime_type,
            content=content,
            submitted_fields=request.form.get("fields"),
        )
        record = EvidenceStore().ingest(
            document_id=document_id,
            organization_id=g.principal.organization_id,
            evidence_type=evidence_type,
            filename=filename,
            mime_type=mime_type,
            content=content,
            fields=fields,
        )
        return jsonify(
            {
                "preview": _evidence_json(record),
                "persisted": False,
                "correlation_id": _correlation_id(),
            }
        )

    @app.post("/api/invoices/<invoice_id>/evidence")
    @require(Permission.EVIDENCE_WRITE)
    def upload_invoice_evidence(invoice_id: str):
        stored = repository.get_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
        )
        if stored.status != InvoiceStatus.DRAFT:
            raise WorkflowError("Evidence cannot be changed after evaluation begins.")
        upload = request.files.get("file")
        if upload is None:
            raise ValueError("Evidence upload requires a multipart file field named 'file'.")
        filename = secure_filename(upload.filename or "")
        if not filename:
            raise ValueError("Evidence filename is required.")
        mime_type = (upload.mimetype or "").strip().lower()
        try:
            evidence_type = EvidenceType(str(request.form.get("evidence_type", "")).upper())
        except ValueError as exc:
            raise ValueError("evidence_type must be INVOICE, PURCHASE_ORDER, or DELIVERY.") from exc
        content = upload.read(max_evidence_bytes + 1)
        if len(content) > max_evidence_bytes:
            raise RequestEntityTooLarge()
        document_id = f"evidence_{uuid4().hex}"
        fields = _evidence_fields(
            document_id=document_id,
            evidence_type=evidence_type,
            mime_type=mime_type,
            content=content,
            submitted_fields=request.form.get("fields"),
        )
        record = EvidenceStore().ingest(
            document_id=document_id,
            organization_id=g.principal.organization_id,
            evidence_type=evidence_type,
            filename=filename,
            mime_type=mime_type,
            content=content,
            fields=fields,
        )
        if evidence_type == EvidenceType.INVOICE:
            if record.document.content_sha256 != stored.invoice.source_document_hash:
                raise ValueError(
                    "Invoice evidence hash must match the invoice source_document_hash."
                )
            if record.field("invoice_id").normalized_value.strip() != invoice_id:
                raise ValueError("Invoice evidence invoice_id must match the URL invoice ID.")
        repository.save_and_link_invoice_evidence(
            record,
            content=content,
            invoice_id=invoice_id,
        )
        repository.append(
            aggregate_type="evidence",
            aggregate_id=record.document.id,
            event_type="EVIDENCE_INGESTED",
            payload={
                "organization_id": g.principal.organization_id,
                "invoice_id": invoice_id,
                "evidence_type": evidence_type.value,
                "content_sha256": record.document.content_sha256,
                "field_names": [field.name for field in record.fields],
            },
            created_at=record.document.ingested_at,
        )
        return jsonify(
            {
                "evidence": _evidence_json(record),
                "correlation_id": _correlation_id(),
            }
        ), 201

    @app.post("/api/invoices/<invoice_id>/evaluate")
    @require(Permission.DECISION_RUN)
    def evaluate_invoice(invoice_id: str):
        stored = repository.get_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
        )
        if stored.status not in {InvoiceStatus.DRAFT, InvoiceStatus.EVALUATING}:
            existing = repository.latest_decision_for_invoice(
                organization_id=g.principal.organization_id,
                invoice_id=invoice_id,
            )
            return jsonify(
                {
                    "invoice": _invoice_json(stored),
                    "decision": _decision_json(existing),
                    "correlation_id": _correlation_id(),
                }
            )
        records = repository.list_invoice_evidence(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
        )
        normalized = EvidenceNormalizer().normalize(
            EvidencePackage(
                id=f"package:{invoice_id}",
                organization_id=g.principal.organization_id,
                records=records,
            )
        )
        if normalized.invoice != stored.invoice:
            raise WorkflowError(
                "Normalized invoice evidence does not exactly match the immutable invoice record."
            )
        vendor = repository.get_vendor(
            organization_id=g.principal.organization_id,
            vendor_id=stored.invoice.vendor_id,
        )
        policy = repository.active_policy(
            organization_id=g.principal.organization_id
        ).policy
        treasury = repository.latest_treasury_snapshot(
            organization_id=g.principal.organization_id
        ).snapshot

        if stored.status == InvoiceStatus.DRAFT:
            stored = repository.transition_invoice(
                organization_id=g.principal.organization_id,
                invoice_id=invoice_id,
                target_status=InvoiceStatus.EVALUATING,
                expected_version=stored.version,
                actor_user_id=g.principal.user_id,
                correlation_id=_correlation_id(),
            )
        wallet_event = repository.vendor_wallet_history(
            organization_id=g.principal.organization_id,
            vendor_id=vendor.id,
        )[-1]
        decision = decision_service.evaluate(
            evidence=normalized,
            vendor=vendor,
            treasury=treasury,
            policy=policy,
            agent_recommendation=evidence_analyst.recommend(
                evidence=normalized,
                vendor=vendor,
                treasury=treasury,
                policy=policy,
            ),
            known_invoice_fingerprints=repository.known_invoice_fingerprints(
                organization_id=g.principal.organization_id,
                exclude_invoice_id=invoice_id,
            ),
            asset=stored.invoice.currency,
            network=network_config.name.value,
            evaluation_date=current_date(),
            vendor_wallet_event_type=wallet_event.event_type.value,
            vendor_wallet_verified_date=wallet_event.verified_at.date(),
        )
        target_status = status_for_decision(decision.final_action)
        stored = repository.transition_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
            target_status=target_status,
            expected_version=stored.version,
            actor_user_id="tallyguard-policy-engine",
            correlation_id=_correlation_id(),
        )
        return jsonify(
            {
                "invoice": _invoice_json(stored),
                "decision": _decision_json(decision),
                "correlation_id": _correlation_id(),
            }
        )

    @app.get("/api/invoices/<invoice_id>/evidence")
    @require(Permission.INVOICE_READ)
    def list_invoice_evidence(invoice_id: str):
        repository.get_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
        )
        records = repository.list_invoice_evidence(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
        )
        return jsonify(
            {
                "items": [_evidence_json(record) for record in records],
                "correlation_id": _correlation_id(),
            }
        )

    @app.get("/api/evidence/<document_id>/content")
    @require(Permission.INVOICE_READ)
    def download_evidence(document_id: str):
        record = repository.get_evidence(
            organization_id=g.principal.organization_id,
            document_id=document_id,
        )
        content = repository.get_evidence_content(
            organization_id=g.principal.organization_id,
            document_id=document_id,
        )
        return send_file(
            BytesIO(content),
            mimetype=record.document.mime_type,
            as_attachment=True,
            download_name=record.document.filename,
            max_age=0,
        )

    @app.get("/api/invoices")
    @require(Permission.INVOICE_READ)
    def list_invoices():
        raw_status = request.args.get("status")
        status = InvoiceStatus(raw_status) if raw_status else None
        limit = int(request.args.get("limit", "50"))
        page = repository.list_invoices(
            organization_id=g.principal.organization_id,
            status=status,
            limit=limit,
            cursor=request.args.get("cursor"),
        )
        return jsonify(
            {
                "items": [_invoice_json(item) for item in page.items],
                "next_cursor": page.next_cursor,
                "correlation_id": _correlation_id(),
            }
        )

    @app.get("/api/operations/overview")
    @require(Permission.INVOICE_READ)
    def get_operations_overview():
        raw_as_of = request.args.get("as_of")
        as_of = date.fromisoformat(raw_as_of) if raw_as_of else current_date()
        overview = repository.operations_overview(
            organization_id=g.principal.organization_id,
            as_of=as_of,
            queue_limit=int(request.args.get("queue_limit", "12")),
        )

        def optional_money(value: Decimal | None) -> str | None:
            return format(value, "f") if value is not None else None

        work_queue: list[dict[str, Any]] = []
        for item in overview.work_queue:
            item_json = _invoice_json(item)
            try:
                decision = repository.latest_decision_for_invoice(
                    organization_id=g.principal.organization_id,
                    invoice_id=item.invoice.id,
                )
                scheduled_for = _scheduled_for(decision)
                item_json.update(
                    {
                        "decision_id": decision.id,
                        "decision_action": decision.final_action.value,
                        "scheduled_for": (
                            scheduled_for.isoformat() if scheduled_for is not None else None
                        ),
                    }
                )
                try:
                    intent = repository.get_payment_intent_for_decision(
                        organization_id=g.principal.organization_id,
                        decision_id=decision.id,
                    )
                    latest_attempt = repository.latest_settlement_attempt(
                        organization_id=g.principal.organization_id,
                        payment_intent_id=intent.id,
                    )
                    item_json["settlement_retryable"] = (
                        latest_attempt.retryable if latest_attempt is not None else False
                    )
                except PersistenceError:
                    item_json["settlement_retryable"] = False
            except PersistenceError:
                item_json.update(
                    {
                        "decision_id": None,
                        "decision_action": None,
                        "scheduled_for": None,
                        "settlement_retryable": False,
                    }
                )
            work_queue.append(item_json)

        return jsonify(
            {
                "overview": {
                    "organization_id": overview.organization_id,
                    "as_of": overview.as_of.isoformat(),
                    "invoice_count": overview.invoice_count,
                    "status_counts": overview.status_counts,
                    "open_exposure_usdc": format(overview.open_exposure_usdc, "f"),
                    "blocked_exposure_usdc": format(overview.blocked_exposure_usdc, "f"),
                    "due_next_7_days_usdc": format(overview.due_next_7_days_usdc, "f"),
                    "due_next_7_days_count": overview.due_next_7_days_count,
                    "overdue_usdc": format(overview.overdue_usdc, "f"),
                    "overdue_count": overview.overdue_count,
                    "reconciled_usdc": format(overview.reconciled_usdc, "f"),
                    "treasury_available_usdc": optional_money(
                        overview.treasury_available_usdc
                    ),
                    "minimum_reserve_usdc": optional_money(
                        overview.minimum_reserve_usdc
                    ),
                    "projected_after_open_usdc": optional_money(
                        overview.projected_after_open_usdc
                    ),
                    "work_queue": work_queue,
                },
                "correlation_id": _correlation_id(),
            }
        )

    @app.get("/api/accounting/ledger.csv")
    @require(Permission.AUDIT_READ)
    def export_accounting_ledger():
        rows = repository.accounting_ledger(
            organization_id=g.principal.organization_id,
        )
        output = StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerow(
            (
                "invoice_id",
                "invoice_number",
                "vendor_id",
                "vendor_legal_name",
                "currency",
                "amount_usdc",
                "due_date",
                "invoice_status",
                "decision_id",
                "evidence_manifest_hash",
                "policy_version",
                "policy_content_hash",
                "final_action",
                "approval_id",
                "approval_status",
                "approval_resolved_by",
                "approval_resolved_at",
                "payment_intent_id",
                "recipient",
                "network",
                "provider",
                "provider_reference",
                "transaction_hash",
                "block_number",
                "settlement_status",
                "confirmed_at",
            )
        )
        for row in rows:
            writer.writerow(
                tuple(
                    _csv_cell(value)
                    for value in (
                        row.invoice_id,
                        row.invoice_number,
                        row.vendor_id,
                        row.vendor_legal_name,
                        row.currency,
                        format(row.amount_usdc, "f"),
                        row.due_date.isoformat(),
                        row.invoice_status,
                        row.decision_id,
                        row.evidence_manifest_hash,
                        row.policy_version,
                        row.policy_content_hash,
                        row.final_action,
                        row.approval_id,
                        row.approval_status,
                        row.approval_resolved_by,
                        (
                            row.approval_resolved_at.isoformat()
                            if row.approval_resolved_at is not None
                            else None
                        ),
                        row.payment_intent_id,
                        row.recipient,
                        row.network,
                        row.provider,
                        row.provider_reference,
                        row.transaction_hash,
                        row.block_number,
                        row.settlement_status,
                        row.confirmed_at.isoformat(),
                    )
                )
            )
        body = ("\ufeff" + output.getvalue()).encode("utf-8")
        content_hash = sha256(body).hexdigest()
        response = Response(body, mimetype="text/csv")
        response.headers["Content-Disposition"] = 'attachment; filename="tallyguard-ledger.csv"'
        response.headers["X-TallyGuard-Ledger-SHA256"] = content_hash
        response.headers["X-TallyGuard-Ledger-Rows"] = str(len(rows))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/operations/settlement-incidents")
    @require(Permission.AUDIT_READ)
    def get_settlement_incidents():
        organization_id = g.principal.organization_id
        attempts = repository.settlement_attempts(
            organization_id=organization_id,
            limit=int(request.args.get("limit", "100")),
        )
        grouped: dict[str, list[SettlementAttempt]] = {}
        for attempt in attempts:
            grouped.setdefault(attempt.payment_intent_id, []).append(attempt)

        incidents: list[dict[str, Any]] = []
        for payment_intent_id, history in grouped.items():
            failed = next(
                (attempt for attempt in history if attempt.error_code is not None),
                None,
            )
            if failed is None:
                continue
            latest = history[0]
            invoice = repository.get_invoice(
                organization_id=organization_id,
                invoice_id=failed.invoice_id,
            )
            intent = repository.get_payment_intent(
                organization_id=organization_id,
                payment_intent_id=payment_intent_id,
            )
            resolved = latest.outcome.value == "CONFIRMED"
            incidents.append(
                {
                    "payment_intent_id": payment_intent_id,
                    "idempotency_fingerprint": sha256(
                        intent.idempotency_key.encode("utf-8")
                    ).hexdigest(),
                    "invoice": _invoice_json(invoice),
                    "provider": failed.provider,
                    "state": (
                        "RESOLVED"
                        if resolved
                        else "OPEN_RETRYABLE"
                        if latest.retryable
                        else "LOCKED"
                    ),
                    "retryable": latest.retryable and not resolved,
                    "latest_attempt": _settlement_attempt_json(latest),
                    "failed_attempt": _settlement_attempt_json(failed),
                    "attempt_count": len(history),
                }
            )
        incidents.sort(
            key=lambda item: item["failed_attempt"]["created_at"], reverse=True
        )
        return jsonify(
            {
                "summary": {
                    "total": len(incidents),
                    "open_retryable": sum(
                        item["state"] == "OPEN_RETRYABLE" for item in incidents
                    ),
                    "locked": sum(item["state"] == "LOCKED" for item in incidents),
                    "resolved": sum(item["state"] == "RESOLVED" for item in incidents),
                },
                "items": incidents,
                "correlation_id": _correlation_id(),
            }
        )

    @app.get("/api/invoices/<invoice_id>")
    @require(Permission.INVOICE_READ)
    def get_invoice(invoice_id: str):
        stored = repository.get_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
        )
        return jsonify({"invoice": _invoice_json(stored), "correlation_id": _correlation_id()})

    @app.get("/api/invoices/<invoice_id>/evidence-packet")
    @require(Permission.AUDIT_READ)
    def get_invoice_evidence_packet(invoice_id: str):
        organization_id = g.principal.organization_id
        stored = repository.get_invoice(
            organization_id=organization_id,
            invoice_id=invoice_id,
        )
        decision = repository.latest_decision_for_invoice(
            organization_id=organization_id,
            invoice_id=invoice_id,
        )
        replay = (
            decision_service.verify_replay(decision)
            if decision.replay_inputs is not None and decision.replay_input_hash is not None
            else None
        )
        evidence = repository.list_invoice_evidence(
            organization_id=organization_id,
            invoice_id=invoice_id,
        )
        approval = repository.find_approval_for_decision(
            organization_id=organization_id,
            decision_id=decision.id,
        )
        payment = None
        try:
            intent = repository.get_payment_intent_for_decision(
                organization_id=organization_id,
                decision_id=decision.id,
            )
            receipt = repository.find_settlement_receipt(
                organization_id=organization_id,
                payment_intent_id=intent.id,
            )
            if receipt is not None:
                payment = _payment_json(
                    PaymentOutcome(
                        intent=intent,
                        receipt=receipt,
                        invoice=stored,
                        reused_receipt=True,
                    ),
                    network_config,
                )
        except PersistenceError:
            pass

        invoice_events = tuple(
            event
            for event in repository.audit_events(organization_id=organization_id)
            if event.aggregate_id == invoice_id
            or event.payload.get("invoice_id") == invoice_id
        )
        packet = {
            "schema_version": "1.0",
            "organization_id": organization_id,
            "invoice": _invoice_json(stored),
            "evidence": [_evidence_json(record) for record in evidence],
            "decision": {
                **_decision_json(decision),
                "sealed_replay_inputs": (
                    decision.replay_inputs.to_payload()
                    if decision.replay_inputs is not None
                    else None
                ),
            },
            "replay_verification": {
                "verified": replay.verified if replay is not None else False,
                "original_decision_id": decision.id,
                "replayed_decision_id": (
                    replay.replayed_decision_id if replay is not None else None
                ),
                "input_snapshot_hash": decision.replay_input_hash,
                "checks": [
                    {
                        "code": check.code,
                        "passed": check.passed,
                        "expected": check.expected,
                        "actual": check.actual,
                    }
                    for check in (replay.checks if replay is not None else ())
                ],
            },
            "approval": _approval_json(approval) if approval is not None else None,
            "payment": payment,
            "audit": {
                "tenant_chain_valid": repository.verify_audit_chain(
                    organization_id=organization_id
                ),
                "invoice_events": [_audit_event_json(event) for event in invoice_events],
                "last_invoice_event_hash": (
                    invoice_events[-1].event_hash if invoice_events else None
                ),
            },
        }
        canonical_packet = canonical_json(packet).encode("utf-8")
        packet_hash = sha256(canonical_packet).hexdigest()
        envelope = {
            "packet_id": f"packet_{packet_hash[:24]}",
            "packet_sha256": packet_hash,
            "hash_scope": "UTF-8 canonical JSON of the packet field",
            "packet": packet,
        }
        body = json.dumps(envelope, indent=2, ensure_ascii=False).encode("utf-8")
        response = Response(body, mimetype="application/json")
        response.headers["Content-Disposition"] = (
            f'attachment; filename="tallyguard-{secure_filename(invoice_id)}-evidence-packet.json"'
        )
        response.headers["X-TallyGuard-Packet-SHA256"] = packet_hash
        return response

    def execute_settlement(
        *,
        invoice_id: str,
        payload: dict[str, Any],
        actor_user_id: str,
        correlation_id: str,
    ) -> PaymentOutcome:
        decision_id = str(payload.get("decision_id", "")).strip()
        if not decision_id:
            raise ValueError("Settlement requires a decision_id.")
        record = decision_service.repository.get_decision(
            organization_id=g.principal.organization_id,
            decision_id=decision_id,
        )
        if record.invoice_id != invoice_id:
            raise WorkflowError("Decision is not bound to this invoice.")

        decision = record.policy_decision
        if decision.action.value != "PAY":
            approval_reference = str(payload.get("approval_reference", "")).strip()
            if not approval_reference:
                raise ApprovalError("An approved escalation reference is required for settlement.")
            approval = approval_inbox.get(
                organization_id=g.principal.organization_id,
                approval_id=approval_reference,
            )
            if approval.decision_id != record.id or approval.invoice_id != invoice_id:
                raise ApprovalError("Approval is not bound to this decision and invoice.")
            decision = apply_approved_escalation(record, approval)
        elif network_config.is_mainnet:
            if not allow_mainnet:
                raise ApprovalError(
                    "Arc mainnet settlement is disabled by runtime policy."
                )
            approval_reference = str(payload.get("approval_reference", "")).strip()
            if not approval_reference:
                raise ApprovalError(
                    "An approved mainnet payment reference is required for settlement."
                )
            approval = approval_inbox.get(
                organization_id=g.principal.organization_id,
                approval_id=approval_reference,
            )
            if approval.decision_id != record.id or approval.invoice_id != invoice_id:
                raise ApprovalError("Approval is not bound to this decision and invoice.")
            decision = apply_approved_mainnet_payment(record, approval)

        stored = repository.get_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
        )
        try:
            outcome = payment_orchestrator.settle(
                invoice=stored,
                decision_id=decision_id,
                decision=decision,
                actor_user_id=actor_user_id,
                correlation_id=correlation_id,
            )
        except SettlementUnavailable as exc:
            repository.append(
                aggregate_type="payment",
                aggregate_id=f"{decision_id}:{correlation_id}:{uuid4().hex}",
                event_type="SETTLEMENT_PROVIDER_UNAVAILABLE",
                payload={
                    "organization_id": g.principal.organization_id,
                    "invoice_id": invoice_id,
                    "decision_id": decision_id,
                    "actor_user_id": actor_user_id,
                    "correlation_id": correlation_id,
                    "retryable": True,
                    "message": str(exc),
                },
            )
            raise
        except SettlementExecutionBlocked as exc:
            event_type = (
                "SETTLEMENT_BLOCKED_BY_ACTIVE_KILL_SWITCH"
                if exc.control_code == "KILL_SWITCH_ENABLED"
                else "SETTLEMENT_BLOCKED_BY_EXECUTION_CONTROL"
            )
            repository.append(
                aggregate_type="settlement_gate",
                aggregate_id=f"{decision_id}:{correlation_id}:{uuid4().hex}",
                event_type=event_type,
                payload={
                    "organization_id": g.principal.organization_id,
                    "invoice_id": invoice_id,
                    "decision_id": decision_id,
                    "control_code": exc.control_code,
                    "actor_user_id": actor_user_id,
                    "correlation_id": correlation_id,
                    **exc.details,
                },
            )
            raise SettlementDenied(str(exc)) from exc
        repository.append(
            aggregate_type="payment",
            aggregate_id=outcome.intent.id,
            event_type="SETTLEMENT_RECONCILED",
            payload={
                "organization_id": outcome.intent.organization_id,
                "invoice_id": outcome.intent.invoice_id,
                "decision_id": outcome.intent.decision_id,
                "approval_reference": outcome.intent.approval_reference,
                "network": outcome.receipt.network.value,
                "provider": outcome.receipt.provider,
                "transaction_hash": outcome.receipt.transaction_hash,
                "status": outcome.receipt.status.value,
            },
            created_at=outcome.receipt.confirmed_at,
        )
        return outcome

    def build_agent_candidates(*, organization_id: str, max_items: int) -> tuple[AgentCandidate, ...]:
        terminal = {
            InvoiceStatus.RECONCILED,
            InvoiceStatus.REJECTED,
            InvoiceStatus.CANCELLED,
        }
        invoices = repository.list_invoices(
            organization_id=organization_id,
            limit=100,
        ).items
        candidates: list[AgentCandidate] = []
        for stored in sorted(invoices, key=lambda item: (item.invoice.due_date, item.invoice.id)):
            if stored.status in terminal:
                continue
            decision_id: str | None = None
            decision_action: str | None = None
            scheduled_for: date | None = None
            retryable = False
            approval_reference: str | None = None
            approval_status: str | None = None
            try:
                decision = repository.latest_decision_for_invoice(
                    organization_id=organization_id,
                    invoice_id=stored.invoice.id,
                )
                decision_id = decision.id
                decision_action = decision.final_action.value
                scheduled_for = _scheduled_for(decision)
                approval = repository.find_approval_for_decision(
                    organization_id=organization_id,
                    decision_id=decision.id,
                )
                if approval is not None:
                    approval_reference = approval.id
                    approval_status = approval.status.value
                try:
                    intent = repository.get_payment_intent_for_decision(
                        organization_id=organization_id,
                        decision_id=decision.id,
                    )
                    attempt = repository.latest_settlement_attempt(
                        organization_id=organization_id,
                        payment_intent_id=intent.id,
                    )
                    retryable = attempt.retryable if attempt is not None else False
                except PersistenceError:
                    pass
            except PersistenceError:
                pass
            candidates.append(
                AgentCandidate(
                    invoice_id=stored.invoice.id,
                    invoice_number=stored.invoice.invoice_number,
                    amount_usdc=format(stored.invoice.amount, "f"),
                    due_date=stored.invoice.due_date,
                    status=stored.status,
                    version=stored.version,
                    decision_id=decision_id,
                    decision_action=decision_action,
                    scheduled_for=scheduled_for,
                    settlement_retryable=retryable,
                    approval_reference=approval_reference,
                    approval_status=approval_status,
                )
            )
            if len(candidates) == max_items:
                break
        return tuple(candidates)

    @app.post("/api/agent-runs")
    @require(Permission.DECISION_RUN)
    def create_agent_run():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            raise ValueError("Agent run body must be a JSON object.")
        max_items = int(payload.get("max_items", 25))
        if not 1 <= max_items <= 25:
            raise ValueError("Agent runs may scan between 1 and 25 invoices.")
        organization_id = g.principal.organization_id
        as_of = current_date()
        candidates = build_agent_candidates(
            organization_id=organization_id,
            max_items=max_items,
        )
        items = tuple(plan_candidate(candidate, as_of=as_of) for candidate in candidates)
        state_hash = sha256(
            canonical_payload(state_payload(candidates, as_of=as_of)).encode("utf-8")
        ).hexdigest()
        plan_hash = sha256(
            canonical_payload(plan_payload(items, as_of=as_of)).encode("utf-8")
        ).hexdigest()
        timestamp = datetime.now(timezone.utc)
        run = repository.create_agent_run(
            AgentRun(
                id=f"agent_run_{uuid4().hex}",
                organization_id=organization_id,
                status=AgentRunStatus.PLANNED,
                as_of=as_of,
                state_hash=state_hash,
                plan_hash=plan_hash,
                items=items,
                created_by_user_id=g.principal.user_id,
                created_at=timestamp,
            )
        )
        repository.append(
            aggregate_type="agent_run",
            aggregate_id=run.id,
            event_type="AGENT_RUN_PLANNED",
            payload={
                "organization_id": organization_id,
                "run_id": run.id,
                "state_hash": run.state_hash,
                "plan_hash": run.plan_hash,
                "scanned": len(run.items),
                "executable": sum(item.executable for item in run.items),
                "actor_user_id": g.principal.user_id,
            },
            created_at=timestamp,
        )
        return jsonify({"agent_run": _agent_run_json(run), "correlation_id": _correlation_id()}), 201

    @app.get("/api/agent-runs/latest")
    @require(Permission.AUDIT_READ)
    def get_latest_agent_run():
        run = repository.latest_agent_run(organization_id=g.principal.organization_id)
        return jsonify(
            {
                "agent_run": _agent_run_json(run) if run is not None else None,
                "correlation_id": _correlation_id(),
            }
        )

    @app.get("/api/agent-runs/<run_id>")
    @require(Permission.AUDIT_READ)
    def get_agent_run(run_id: str):
        run = repository.get_agent_run(
            organization_id=g.principal.organization_id,
            run_id=run_id,
        )
        return jsonify({"agent_run": _agent_run_json(run), "correlation_id": _correlation_id()})

    @app.get("/api/agent-runs/<run_id>/proof-packet")
    @require(Permission.AUDIT_READ)
    def get_agent_run_proof_packet(run_id: str):
        organization_id = g.principal.organization_id
        run = repository.get_agent_run(
            organization_id=organization_id,
            run_id=run_id,
        )
        invoice_ids = {item.invoice_id for item in run.items}
        approval_ids = {
            item.approval_reference for item in run.items if item.approval_reference
        }
        approval_ids.update(
            str(result["approval"]["id"])
            for result in run.results
            if isinstance(result.get("approval"), dict)
            and result["approval"].get("id")
        )
        approvals = []
        for approval_id in sorted(approval_ids):
            approval = repository.get_approval(
                organization_id=organization_id,
                approval_id=approval_id,
            )
            if approval is not None:
                approvals.append(_approval_json(approval))

        tenant_events = repository.audit_events(organization_id=organization_id)
        related_events = tuple(
            event
            for event in tenant_events
            if event.aggregate_id == run.id
            or event.aggregate_id in invoice_ids
            or event.payload.get("agent_run_id") == run.id
            or event.payload.get("invoice_id") in invoice_ids
        )
        computed_plan_hash = sha256(
            canonical_payload(plan_payload(run.items, as_of=run.as_of)).encode("utf-8")
        ).hexdigest()
        packet = {
            "schema_version": "1.0",
            "organization_id": organization_id,
            "agent_run": _agent_run_json(run),
            "integrity": {
                "plan_hash_verified": computed_plan_hash == run.plan_hash,
                "recorded_plan_hash": run.plan_hash,
                "computed_plan_hash": computed_plan_hash,
                "recorded_state_hash": run.state_hash,
                "state_hash_scope": (
                    "The tenant queue state observed at planning time; preserved in the "
                    "durable run and committed into the plan audit event."
                ),
            },
            "approvals": approvals,
            "audit": {
                "tenant_chain_valid": repository.verify_audit_chain(
                    organization_id=organization_id
                ),
                "related_events": [_audit_event_json(event) for event in related_events],
                "last_tenant_event_hash": (
                    tenant_events[-1].event_hash if tenant_events else None
                ),
            },
        }
        canonical_packet = canonical_json(packet).encode("utf-8")
        packet_hash = sha256(canonical_packet).hexdigest()
        envelope = {
            "packet_id": f"agent_proof_{packet_hash[:24]}",
            "packet_sha256": packet_hash,
            "hash_scope": "UTF-8 canonical JSON of the packet field",
            "packet": packet,
        }
        body = json.dumps(envelope, indent=2, ensure_ascii=False).encode("utf-8")
        response = Response(body, mimetype="application/json")
        response.headers["Content-Disposition"] = (
            f'attachment; filename="tallyguard-{secure_filename(run_id)}-proof-packet.json"'
        )
        response.headers["X-TallyGuard-Packet-SHA256"] = packet_hash
        return response

    @app.post("/api/agent-runs/<run_id>/execute")
    @require(Permission.SETTLEMENT_EXECUTE)
    def execute_agent_run(run_id: str):
        organization_id = g.principal.organization_id
        run, claimed = repository.claim_agent_run_execution(
            organization_id=organization_id,
            run_id=run_id,
            executed_by_user_id=g.principal.user_id,
        )
        if not claimed and run.status == AgentRunStatus.EXECUTING:
            response = jsonify(
                {
                    "agent_run": _agent_run_json(run),
                    "execution_in_progress": True,
                    "reused_result": False,
                    "correlation_id": _correlation_id(),
                }
            )
            response.headers["Retry-After"] = "1"
            return response, 202
        if not claimed:
            return jsonify(
                {
                    "agent_run": _agent_run_json(run),
                    "reused_result": True,
                    "correlation_id": _correlation_id(),
                }
            )

        results: list[dict[str, Any]] = []
        failures = 0
        for index, item in enumerate(run.items):
            if not item.executable:
                results.append(
                    {
                        "invoice_id": item.invoice_id,
                        "action": item.action.value,
                        "status": "SKIPPED",
                        "reason_code": item.reason_code,
                    }
                )
                continue
            try:
                current = repository.get_invoice(
                    organization_id=organization_id,
                    invoice_id=item.invoice_id,
                )
                decision = repository.latest_decision_for_invoice(
                    organization_id=organization_id,
                    invoice_id=item.invoice_id,
                )
                stale_reasons: list[str] = []
                if current.version != item.invoice_version:
                    stale_reasons.append("INVOICE_VERSION_CHANGED")
                if current.status != item.invoice_status:
                    stale_reasons.append("WORKFLOW_STATUS_CHANGED")
                if decision.id != item.decision_id:
                    stale_reasons.append("POLICY_DECISION_CHANGED")
                if item.action.value in {"SETTLE", "RETRY_SETTLEMENT"}:
                    if decision.final_action.value != "PAY":
                        stale_reasons.append("POLICY_DECISION_CHANGED")
                elif item.action.value == "SETTLE_APPROVED":
                    approval = repository.find_approval_for_decision(
                        organization_id=organization_id,
                        decision_id=decision.id,
                    )
                    if (
                        decision.final_action.value != "ESCALATE"
                        or approval is None
                        or approval.id != item.approval_reference
                        or approval.status != ApprovalStatus.APPROVED
                    ):
                        stale_reasons.append("APPROVAL_AUTHORITY_CHANGED")
                elif item.action.value == "RELEASE_SCHEDULE":
                    if (
                        decision.final_action.value != "SCHEDULE"
                        or _scheduled_for(decision) is None
                        or _scheduled_for(decision) > current_date()
                    ):
                        stale_reasons.append("SCHEDULE_RELEASE_CHANGED")
                elif item.action.value == "REQUIRE_APPROVAL":
                    if decision.final_action.value != "ESCALATE":
                        stale_reasons.append("POLICY_DECISION_CHANGED")
                if item.action.value == "RETRY_SETTLEMENT":
                    try:
                        intent = repository.get_payment_intent_for_decision(
                            organization_id=organization_id,
                            decision_id=decision.id,
                        )
                        attempt = repository.latest_settlement_attempt(
                            organization_id=organization_id,
                            payment_intent_id=intent.id,
                        )
                        if attempt is None or not attempt.retryable:
                            stale_reasons.append("RETRY_AUTHORIZATION_CHANGED")
                    except PersistenceError:
                        stale_reasons.append("PAYMENT_INTENT_MISSING")
                if stale_reasons:
                    failures += 1
                    results.append(
                        {
                            "invoice_id": item.invoice_id,
                            "action": item.action.value,
                            "status": "STALE",
                            "reason_codes": stale_reasons,
                        }
                    )
                    continue

                if item.action.value == "REQUIRE_APPROVAL":
                    timestamp = datetime.now(timezone.utc)
                    identity = (
                        f"{organization_id}:{decision.id}:"
                        f"{run.created_by_user_id}:{run.id}"
                    )
                    approval = repository.create_or_get_approval(
                        ApprovalRequest(
                            id="approval_" + sha256(identity.encode("utf-8")).hexdigest()[:24],
                            organization_id=organization_id,
                            invoice_id=item.invoice_id,
                            decision_id=decision.id,
                            requested_by_user_id=run.created_by_user_id,
                            requested_at=timestamp,
                        )
                    )
                    repository.append(
                        aggregate_type="approval",
                        aggregate_id=approval.id,
                        event_type="APPROVAL_REQUESTED",
                        payload={
                            "organization_id": organization_id,
                            "invoice_id": approval.invoice_id,
                            "decision_id": approval.decision_id,
                            "requested_by_user_id": approval.requested_by_user_id,
                            "agent_run_id": run.id,
                        },
                        created_at=approval.requested_at,
                    )
                    results.append(
                        {
                            "invoice_id": item.invoice_id,
                            "action": item.action.value,
                            "status": "ROUTED",
                            "approval": _approval_json(approval),
                        }
                    )
                    continue

                if item.action.value == "RELEASE_SCHEDULE":
                    schedule_result = release_scheduled_invoice(
                        stored=current,
                        evaluated_on=current_date(),
                        actor_user_id=g.principal.user_id,
                        correlation_id=f"{_correlation_id()}:{index + 1}",
                    )
                    results.append(
                        {
                            "invoice_id": item.invoice_id,
                            "action": item.action.value,
                            "status": (
                                "SETTLED"
                                if schedule_result["status"] == "SETTLED"
                                else "REVALIDATED"
                            ),
                            "schedule_result": schedule_result,
                        }
                    )
                    continue

                outcome = execute_settlement(
                    invoice_id=item.invoice_id,
                    payload={
                        "decision_id": item.decision_id,
                        **(
                            {"approval_reference": item.approval_reference}
                            if item.action.value == "SETTLE_APPROVED"
                            else {}
                        ),
                    },
                    actor_user_id=g.principal.user_id,
                    correlation_id=f"{_correlation_id()}:{index + 1}",
                )
                results.append(
                    {
                        "invoice_id": item.invoice_id,
                        "action": item.action.value,
                        "status": "SETTLED",
                        "payment": _payment_json(outcome, network_config),
                    }
                )
            except (
                ApprovalError,
                KeyError,
                PersistenceError,
                PolicyRepositoryError,
                SettlementDenied,
                SettlementUnavailable,
                ValueError,
                VendorDirectoryError,
                WorkflowError,
            ) as exc:
                failures += 1
                results.append(
                    {
                        "invoice_id": item.invoice_id,
                        "action": item.action.value,
                        "status": "FAILED",
                        "error": {
                            "code": type(exc).__name__.upper(),
                            "message": str(exc).strip("'"),
                        },
                    }
                )

        completed = repository.complete_agent_run(
            organization_id=organization_id,
            run_id=run.id,
            status=AgentRunStatus.PARTIAL if failures else AgentRunStatus.EXECUTED,
            executed_by_user_id=g.principal.user_id,
            results=tuple(results),
        )
        repository.append(
            aggregate_type="agent_run",
            aggregate_id=run.id,
            event_type="AGENT_RUN_EXECUTED",
            payload={
                "organization_id": organization_id,
                "run_id": run.id,
                "plan_hash": run.plan_hash,
                "status": completed.status.value,
                "settled": sum(result["status"] == "SETTLED" for result in results),
                "routed": sum(result["status"] == "ROUTED" for result in results),
                "revalidated": sum(result["status"] == "REVALIDATED" for result in results),
                "stale": sum(result["status"] == "STALE" for result in results),
                "failed": sum(result["status"] == "FAILED" for result in results),
                "actor_user_id": g.principal.user_id,
            },
        )
        return jsonify(
            {
                "agent_run": _agent_run_json(completed),
                "reused_result": False,
                "correlation_id": _correlation_id(),
            }
        ), (207 if failures else 200)

    def release_scheduled_invoice(
        *,
        stored: StoredInvoice,
        evaluated_on: date,
        actor_user_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        organization_id = g.principal.organization_id
        source = repository.latest_decision_for_invoice(
            organization_id=organization_id,
            invoice_id=stored.invoice.id,
        )
        scheduled_for = _scheduled_for(source)
        if source.final_action.value != "SCHEDULE" or source.replay_inputs is None:
            raise WorkflowError("Latest invoice decision is not a replayable schedule.")
        if scheduled_for is None:
            raise WorkflowError("Scheduled decision is missing a deterministic release date.")
        if evaluated_on < scheduled_for:
            return {
                "invoice_id": stored.invoice.id,
                "status": "WAITING",
                "scheduled_for": scheduled_for.isoformat(),
                "evaluated_on": evaluated_on.isoformat(),
                "source_decision_id": source.id,
            }

        inputs = source.replay_inputs
        vendor = repository.get_vendor(
            organization_id=organization_id,
            vendor_id=stored.invoice.vendor_id,
        )
        policy = repository.active_policy(organization_id=organization_id).policy
        treasury = repository.latest_treasury_snapshot(
            organization_id=organization_id
        ).snapshot
        wallet_event = repository.vendor_wallet_history(
            organization_id=organization_id,
            vendor_id=vendor.id,
        )[-1]
        release = decision_service.evaluate(
            evidence=inputs.evidence,
            vendor=vendor,
            treasury=treasury,
            policy=policy,
            agent_recommendation=source.agent_recommendation,
            known_invoice_fingerprints=repository.known_invoice_fingerprints(
                organization_id=organization_id,
                exclude_invoice_id=stored.invoice.id,
            ),
            asset=stored.invoice.currency,
            network=network_config.name.value,
            evaluation_date=evaluated_on,
            vendor_wallet_event_type=wallet_event.event_type.value,
            vendor_wallet_verified_date=wallet_event.verified_at.date(),
        )
        target_status = status_for_decision(release.final_action)
        current = repository.get_invoice(
            organization_id=organization_id,
            invoice_id=stored.invoice.id,
        )
        if current.status == InvoiceStatus.SCHEDULED and target_status != InvoiceStatus.SCHEDULED:
            try:
                current = repository.transition_invoice(
                    organization_id=organization_id,
                    invoice_id=stored.invoice.id,
                    target_status=target_status,
                    expected_version=current.version,
                    actor_user_id="tallyguard-schedule-runner",
                    correlation_id=correlation_id,
                )
            except WorkflowError as exc:
                if "another operation" not in str(exc):
                    raise
                current = repository.get_invoice(
                    organization_id=organization_id,
                    invoice_id=stored.invoice.id,
                )
        elif current.status != InvoiceStatus.SCHEDULED:
            if not (
                release.final_action.value == "PAY"
                and current.status
                in {
                    InvoiceStatus.READY,
                    InvoiceStatus.SUBMITTING,
                    InvoiceStatus.SUBMITTED,
                    InvoiceStatus.CONFIRMED,
                    InvoiceStatus.RECONCILED,
                    InvoiceStatus.SUBMISSION_FAILED,
                }
            ):
                raise WorkflowError(
                    f"Scheduled invoice advanced unexpectedly to {current.status.value}."
                )

        repository.append(
            aggregate_type="schedule",
            aggregate_id=f"{stored.invoice.id}:{release.id}",
            event_type="SCHEDULE_RELEASE_EVALUATED",
            payload={
                "organization_id": organization_id,
                "invoice_id": stored.invoice.id,
                "source_decision_id": source.id,
                "release_decision_id": release.id,
                "scheduled_for": scheduled_for.isoformat(),
                "evaluated_on": evaluated_on.isoformat(),
                "release_action": release.final_action.value,
            },
        )
        if release.final_action.value != "PAY":
            return {
                "invoice_id": stored.invoice.id,
                "status": "REVALIDATED",
                "scheduled_for": scheduled_for.isoformat(),
                "evaluated_on": evaluated_on.isoformat(),
                "source_decision_id": source.id,
                "release_decision": _decision_json(release),
                "invoice": _invoice_json(current),
            }

        payment = execute_settlement(
            invoice_id=stored.invoice.id,
            payload={"decision_id": release.id},
            actor_user_id=actor_user_id,
            correlation_id=correlation_id,
        )
        return {
            "invoice_id": stored.invoice.id,
            "status": "SETTLED",
            "scheduled_for": scheduled_for.isoformat(),
            "evaluated_on": evaluated_on.isoformat(),
            "source_decision_id": source.id,
            "release_decision": _decision_json(release),
            "payment": _payment_json(payment, network_config),
        }

    @app.post("/api/schedules/run")
    @require(Permission.SETTLEMENT_EXECUTE)
    def run_due_schedules():
        evaluated_on = current_date()
        scheduled = repository.list_invoices(
            organization_id=g.principal.organization_id,
            status=InvoiceStatus.SCHEDULED,
            limit=100,
        ).items
        results: list[dict[str, Any]] = []
        waiting = 0
        settled = 0
        revalidated = 0
        failed = 0
        for index, stored in enumerate(scheduled):
            try:
                result = release_scheduled_invoice(
                    stored=stored,
                    evaluated_on=evaluated_on,
                    actor_user_id=g.principal.user_id,
                    correlation_id=f"{_correlation_id()}:{index + 1}",
                )
                results.append(result)
                if result["status"] == "WAITING":
                    waiting += 1
                elif result["status"] == "SETTLED":
                    settled += 1
                else:
                    revalidated += 1
            except (
                ApprovalError,
                KeyError,
                PersistenceError,
                PolicyRepositoryError,
                SettlementDenied,
                ValueError,
                VendorDirectoryError,
                WorkflowError,
            ) as exc:
                failed += 1
                results.append(
                    {
                        "invoice_id": stored.invoice.id,
                        "status": "FAILED",
                        "error": {
                            "code": type(exc).__name__.upper(),
                            "message": str(exc).strip("'"),
                        },
                    }
                )
        payload = {
            "schedule_run": {
                "evaluated_on": evaluated_on.isoformat(),
                "scanned": len(scheduled),
                "waiting": waiting,
                "settled": settled,
                "revalidated": revalidated,
                "failed": failed,
                "results": results,
            },
            "correlation_id": _correlation_id(),
        }
        return jsonify(payload), (207 if failed else 200)

    @app.post("/api/invoices/<invoice_id>/settle")
    @require(Permission.SETTLEMENT_EXECUTE)
    def settle_invoice(invoice_id: str):
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            raise ValueError("Settlement body must be a JSON object.")
        outcome = execute_settlement(
            invoice_id=invoice_id,
            payload=payload,
            actor_user_id=g.principal.user_id,
            correlation_id=_correlation_id(),
        )
        return jsonify(
            {
                "payment": _payment_json(outcome, network_config),
                "correlation_id": _correlation_id(),
            }
        )

    @app.post("/api/payment-batches/settle")
    @require(Permission.SETTLEMENT_EXECUTE)
    def settle_payment_batch():
        payload = request.get_json(silent=False)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("Batch settlement requires an items array.")
        items = payload["items"]
        if not 1 <= len(items) <= 25:
            raise ValueError("Batch settlement requires between 1 and 25 items.")
        if any(not isinstance(item, dict) for item in items):
            raise ValueError("Every batch item must be a JSON object.")
        invoice_ids = [str(item.get("invoice_id", "")).strip() for item in items]
        if any(not invoice_id for invoice_id in invoice_ids):
            raise ValueError("Every batch item requires an invoice_id.")
        if len(invoice_ids) != len(set(invoice_ids)):
            raise ValueError("A batch cannot contain the same invoice more than once.")

        results: list[dict[str, Any]] = []
        succeeded = 0
        for index, item in enumerate(items):
            invoice_id = invoice_ids[index]
            try:
                outcome = execute_settlement(
                    invoice_id=invoice_id,
                    payload=item,
                    actor_user_id=g.principal.user_id,
                    correlation_id=f"{_correlation_id()}:{index + 1}",
                )
                succeeded += 1
                results.append(
                    {
                        "invoice_id": invoice_id,
                        "status": "SETTLED",
                        "payment": _payment_json(outcome, network_config),
                    }
                )
            except (
                ApprovalError,
                KeyError,
                PersistenceError,
                SettlementDenied,
                ValueError,
                WorkflowError,
            ) as exc:
                results.append(
                    {
                        "invoice_id": invoice_id,
                        "status": "FAILED",
                        "error": {
                            "code": type(exc).__name__.upper(),
                            "message": str(exc).strip("'"),
                        },
                    }
                )
        response_status = 200 if succeeded == len(items) else 207
        return jsonify(
            {
                "batch": {
                    "requested": len(items),
                    "succeeded": succeeded,
                    "failed": len(items) - succeeded,
                    "results": results,
                },
                "correlation_id": _correlation_id(),
            }
        ), response_status

    @app.get("/api/payments/<payment_intent_id>")
    @require(Permission.INVOICE_READ)
    def get_payment(payment_intent_id: str):
        intent = repository.get_payment_intent(
            organization_id=g.principal.organization_id,
            payment_intent_id=payment_intent_id,
        )
        receipt = repository.get_settlement_receipt(
            organization_id=g.principal.organization_id,
            payment_intent_id=payment_intent_id,
        )
        invoice = repository.get_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=intent.invoice_id,
        )
        outcome = PaymentOutcome(
            intent=intent,
            receipt=receipt,
            invoice=invoice,
            reused_receipt=True,
        )
        return jsonify(
            {
                "payment": _payment_json(outcome, network_config),
                "correlation_id": _correlation_id(),
            }
        )

    @app.get("/api/audit/events")
    @require(Permission.AUDIT_READ)
    def get_audit_events():
        organization_id = g.principal.organization_id
        if not request.args:
            events = repository.audit_events(organization_id=organization_id)
            return jsonify(
                {
                    "chain_valid": repository.verify_audit_chain(
                        organization_id=organization_id
                    ),
                    "items": [_audit_event_json(event) for event in events],
                    "page": {
                        "limit": len(events),
                        "has_more": False,
                        "next_before_sequence": None,
                    },
                    "filters": {
                        "event_type": None,
                        "aggregate_type": None,
                        "aggregate_id": None,
                        "query": None,
                        "created_after": None,
                        "created_before": None,
                        "before_sequence": None,
                    },
                    "correlation_id": _correlation_id(),
                }
            )

        def bounded_argument(name: str, *, maximum: int) -> str | None:
            value = request.args.get(name)
            if value is None:
                return None
            value = value.strip()
            if len(value) > maximum:
                raise ValueError(f"Audit filter {name} exceeds {maximum} characters.")
            return value or None

        try:
            limit = int(request.args.get("limit", "50"))
            before_sequence_raw = request.args.get("before_sequence")
            before_sequence = (
                int(before_sequence_raw) if before_sequence_raw is not None else None
            )
        except ValueError as exc:
            raise ValueError("Audit limit and cursor must be integers.") from exc
        if not 1 <= limit <= 200:
            raise ValueError("Audit limit must be between 1 and 200.")
        if before_sequence is not None and before_sequence < 1:
            raise ValueError("Audit cursor must be positive.")

        def datetime_argument(name: str) -> datetime | None:
            raw = bounded_argument(name, maximum=64)
            if raw is None:
                return None
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"Audit filter {name} must be an ISO-8601 timestamp.") from exc
            if parsed.tzinfo is None:
                raise ValueError(f"Audit filter {name} must include a timezone.")
            return parsed.astimezone(timezone.utc)

        created_after = datetime_argument("created_after")
        created_before = datetime_argument("created_before")
        if (
            created_after is not None
            and created_before is not None
            and created_after > created_before
        ):
            raise ValueError("Audit start time cannot be later than the end time.")

        filters = {
            "event_type": bounded_argument("event_type", maximum=80),
            "aggregate_type": bounded_argument("aggregate_type", maximum=80),
            "aggregate_id": bounded_argument("aggregate_id", maximum=200),
            "query": bounded_argument("q", maximum=200),
            "created_after": created_after,
            "created_before": created_before,
        }
        candidates = repository.search_audit_events(
            organization_id=organization_id,
            **filters,
            before_sequence=before_sequence,
            limit=limit + 1,
        )
        has_more = len(candidates) > limit
        events = candidates[:limit]
        return jsonify(
            {
                "chain_valid": repository.verify_audit_chain(
                    organization_id=organization_id
                ),
                "items": [_audit_event_json(event) for event in events],
                "page": {
                    "limit": limit,
                    "has_more": has_more,
                    "next_before_sequence": (
                        events[-1].sequence if has_more and events else None
                    ),
                },
                "filters": {
                    **{
                        key: value.isoformat() if isinstance(value, datetime) else value
                        for key, value in filters.items()
                    },
                    "before_sequence": before_sequence,
                },
                "correlation_id": _correlation_id(),
            }
        )

    @app.get("/")
    def frontend_index():
        if not (frontend_dist / "index.html").is_file():
            return _error(
                "FRONTEND_NOT_BUILT",
                "Build the judge console with 'npm run build' in web/.",
                503,
            )
        return send_from_directory(frontend_dist, "index.html")

    @app.get("/assets/<path:filename>")
    def frontend_assets(filename: str):
        return send_from_directory(frontend_dist / "assets", filename)

    @app.get("/favicon.svg")
    def frontend_favicon():
        return send_from_directory(frontend_dist, "favicon.svg")

    @app.get("/samples/<path:filename>")
    def frontend_samples(filename: str):
        return send_from_directory(frontend_dist / "samples", filename)

    return app


def _seed_demo_identity(repository: SqliteRepository) -> None:
    try:
        repository.create_organization(organization_id=DEMO_ORGANIZATION_ID, name="TallyGuard Demo")
    except PersistenceError:
        pass
    for user_id, display_name, roles in (
        ("demo-admin", "Demo Admin", (Role.ADMIN.value,)),
        ("demo-operator", "Demo Operator", (Role.FINANCE_OPERATOR.value,)),
        ("demo-approver", "Demo Approver", (Role.APPROVER.value,)),
        ("demo-auditor", "Demo Auditor", (Role.AUDITOR.value,)),
    ):
        try:
            repository.create_user(
                organization_id=DEMO_ORGANIZATION_ID,
                user_id=user_id,
                display_name=display_name,
                roles=roles,
            )
        except PersistenceError:
            pass


def main() -> None:
    load_local_environment()
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()

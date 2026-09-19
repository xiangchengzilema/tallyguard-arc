"""Flask API for TallyGuard's tenant-scoped finance control plane."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps
from io import BytesIO
import json
import os
from pathlib import Path
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
from .approvals import ApprovalError, ApprovalInbox, apply_approved_escalation
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
from .normalization import EvidenceNormalizer
from .payments import PaymentOrchestrator, PaymentOutcome
from .decisions import DecisionRecord, DecisionService
from .demo import build_demo_scenario, scenario_catalog
from .persistence import (
    PersistenceError,
    SqliteRepository,
    StoredInvoice,
    StoredTreasurySnapshot,
)
from .policies import PolicyRepositoryError, StoredPolicy
from .policy import Policy
from .settlement import (
    SettlementAdapter,
    SettlementDenied,
    SettlementService,
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

    if mime_type != "application/json":
        raise ValueError("PDF and image evidence require provenance-bound extracted fields.")
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


def _decision_json(record: DecisionRecord) -> dict[str, Any]:
    recommendation = record.agent_recommendation
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


def _error(code: str, message: str, status: int) -> tuple[Response, int]:
    return jsonify({"error": {"code": code, "message": message}, "correlation_id": _correlation_id()}), status


def _correlation_id() -> str:
    if not hasattr(g, "correlation_id"):
        g.correlation_id = request.headers.get("X-Correlation-ID") or f"req_{uuid4().hex}"
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
    rate_limit_per_minute: int | None = None,
    evidence_analyst: EvidenceAnalyst | None = None,
) -> Flask:
    default_frontend_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    frontend_dist = Path(
        os.getenv("TALLYGUARD_FRONTEND_DIST", str(default_frontend_dist))
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
    authenticator = Authenticator(store=repository)
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
    allow_mainnet = os.getenv("TALLYGUARD_ALLOW_MAINNET", "false").strip().lower() == "true"
    settlement_service = SettlementService(
        config=network_config,
        adapter=settlement_adapter,
        allow_mainnet=allow_mainnet,
    )
    payment_orchestrator = PaymentOrchestrator(
        repository=repository,
        settlement_service=settlement_service,
        network=network_config,
    )
    configured_rate_limit = rate_limit_per_minute or int(
        os.getenv("TALLYGUARD_RATE_LIMIT_PER_MINUTE", "6000")
    )
    rate_limiter = TenantRateLimiter(limit=configured_rate_limit)
    request_metrics = RequestMetrics()
    evidence_analyst = evidence_analyst or _evidence_analyst_from_env()
    app.extensions["tallyguard_repository"] = repository
    app.extensions["tallyguard_authenticator"] = authenticator
    app.extensions["tallyguard_decision_service"] = decision_service
    app.extensions["tallyguard_approval_inbox"] = approval_inbox
    app.extensions["tallyguard_network"] = network_config
    app.extensions["tallyguard_settlement_adapter"] = settlement_adapter
    app.extensions["tallyguard_payment_orchestrator"] = payment_orchestrator
    app.extensions["tallyguard_rate_limiter"] = rate_limiter
    app.extensions["tallyguard_request_metrics"] = request_metrics
    app.extensions["tallyguard_evidence_analyst"] = evidence_analyst
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
        request_metrics.observe(
            endpoint=request.endpoint or "unmatched",
            status_code=response.status_code,
            elapsed_seconds=monotonic() - g.request_started_monotonic,
        )
        response.headers["X-Correlation-ID"] = _correlation_id()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
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

    @app.post("/api/demo/session")
    def demo_session():
        if not demo_sessions_enabled:
            return _error(
                "DEMO_SESSIONS_DISABLED",
                "Demo identities are disabled while a live settlement adapter is configured.",
                404,
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
        return jsonify(
            {
                "items": [
                    {
                        "key": item.key,
                        "title": item.title,
                        "description": item.description,
                        "expected_action": item.expected_action.value,
                    }
                    for item in scenario_catalog()
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

    @app.post("/api/demo/scenarios/<scenario_key>/run")
    @require(Permission.DECISION_RUN)
    def run_demo_scenario(scenario_key: str):
        invoice_id = f"invoice_{scenario_key.replace('-', '_')}_{uuid4().hex[:12]}"
        scenario = build_demo_scenario(
            scenario_key,
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
        )
        stored = repository.create_invoice(scenario.evidence.invoice)
        stored = repository.transition_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
            target_status=InvoiceStatus.EVALUATING,
            expected_version=stored.version,
            actor_user_id=g.principal.user_id,
            correlation_id=_correlation_id(),
        )
        decision = decision_service.evaluate(
            evidence=scenario.evidence,
            vendor=scenario.vendor,
            treasury=scenario.treasury,
            policy=scenario.policy,
            agent_recommendation=scenario.recommendation,
            known_invoice_fingerprints=scenario.known_invoice_fingerprints,
            evaluation_date=scenario.evaluation_date,
        )
        if decision.final_action != scenario.definition.expected_action:
            raise RuntimeError("Demo scenario produced an unexpected control result.")
        stored = repository.transition_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
            target_status=status_for_decision(decision.final_action),
            expected_version=stored.version,
            actor_user_id="tallyguard-policy-engine",
            correlation_id=_correlation_id(),
        )
        return jsonify(
            {
                "scenario": {
                    "key": scenario.definition.key,
                    "title": scenario.definition.title,
                },
                "invoice": _invoice_json(stored),
                "decision": _decision_json(decision),
                "correlation_id": _correlation_id(),
            }
        )

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

    @app.post("/api/approvals/<approval_id>/resolve")
    @require(Permission.PAYMENT_APPROVE)
    def resolve_approval(approval_id: str):
        payload = request.get_json(silent=True) or {}
        if "approve" not in payload or not isinstance(payload["approve"], bool):
            raise ValueError("Approval resolution requires a boolean approve field.")
        resolved = approval_inbox.resolve(
            organization_id=g.principal.organization_id,
            approval_id=approval_id,
            approver=g.principal,
            approve=payload["approve"],
            resolution_note=str(payload.get("note", "")),
            expected_version=int(payload.get("expected_version", 1)),
        )
        final_action = None
        if resolved.status.value == "APPROVED":
            decision = decision_service.repository.get_decision(
                organization_id=g.principal.organization_id,
                decision_id=resolved.decision_id,
            )
            authorized = apply_approved_escalation(decision, resolved)
            final_action = authorized.action.value
            invoice = repository.get_invoice(
                organization_id=g.principal.organization_id,
                invoice_id=resolved.invoice_id,
            )
            repository.transition_invoice(
                organization_id=g.principal.organization_id,
                invoice_id=resolved.invoice_id,
                target_status=InvoiceStatus.READY,
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
                    "id": resolved.id,
                    "status": resolved.status.value,
                    "version": resolved.version,
                    "resolved_by_user_id": resolved.resolved_by_user_id,
                    "resolution_note": resolved.resolution_note,
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

    @app.get("/api/invoices/<invoice_id>")
    @require(Permission.INVOICE_READ)
    def get_invoice(invoice_id: str):
        stored = repository.get_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
        )
        return jsonify({"invoice": _invoice_json(stored), "correlation_id": _correlation_id()})

    @app.post("/api/invoices/<invoice_id>/settle")
    @require(Permission.SETTLEMENT_EXECUTE)
    def settle_invoice(invoice_id: str):
        payload = request.get_json(silent=True) or {}
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

        stored = repository.get_invoice(
            organization_id=g.principal.organization_id,
            invoice_id=invoice_id,
        )
        outcome = payment_orchestrator.settle(
            invoice=stored,
            decision_id=decision_id,
            decision=decision,
            actor_user_id=g.principal.user_id,
            correlation_id=_correlation_id(),
        )
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
        return jsonify(
            {
                "payment": _payment_json(outcome, network_config),
                "correlation_id": _correlation_id(),
            }
        )

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
        events = repository.audit_events(organization_id=g.principal.organization_id)
        return jsonify(
            {
                "chain_valid": repository.verify_audit_chain(
                    organization_id=g.principal.organization_id
                ),
                "items": [
                    {
                        "sequence": event.sequence,
                        "aggregate_type": event.aggregate_type,
                        "aggregate_id": event.aggregate_id,
                        "event_type": event.event_type,
                        "payload": event.payload,
                        "previous_hash": event.previous_hash,
                        "event_hash": event.event_hash,
                        "created_at": event.created_at.isoformat(),
                    }
                    for event in events
                ],
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
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()

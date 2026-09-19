"""Flask API for TallyGuard's tenant-scoped finance control plane."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps
import os
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from flask import Flask, Response, g, jsonify, request

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
from .models import Invoice
from .decisions import DecisionRecord, DecisionService
from .demo import build_demo_scenario, scenario_catalog
from .persistence import PersistenceError, SqliteRepository, StoredInvoice
from .workflow import InvoiceStatus, WorkflowError, status_for_decision


DEMO_ORGANIZATION_ID = "demo-org"


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


def _decision_json(record: DecisionRecord) -> dict[str, Any]:
    recommendation = record.agent_recommendation
    return {
        "id": record.id,
        "organization_id": record.organization_id,
        "invoice_id": record.invoice_id,
        "evidence_manifest_hash": record.evidence_manifest_hash,
        "policy_version": record.policy_version,
        "policy_content_hash": record.policy_content_hash,
        "agent_recommendation": (
            {
                "action": recommendation.action.value,
                "summary": recommendation.summary,
                "reason_codes": list(recommendation.reason_codes),
                "confidence": format(recommendation.confidence, "f"),
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


def _error(code: str, message: str, status: int) -> tuple[Response, int]:
    return jsonify({"error": {"code": code, "message": message}, "correlation_id": _correlation_id()}), status


def _correlation_id() -> str:
    if not hasattr(g, "correlation_id"):
        g.correlation_id = request.headers.get("X-Correlation-ID") or f"req_{uuid4().hex}"
    return g.correlation_id


def create_app(
    *,
    database_path: str | Path | None = None,
    testing: bool = False,
) -> Flask:
    app = Flask(__name__)
    app.config.update(TESTING=testing)
    resolved_path = database_path or os.getenv("TALLYGUARD_DATABASE_PATH", "data/tallyguard.sqlite3")
    repository = SqliteRepository(resolved_path)
    authenticator = Authenticator()
    decision_service = DecisionService()
    approval_inbox = ApprovalInbox()
    authorized_decisions: dict[str, Any] = {}
    app.extensions["tallyguard_repository"] = repository
    app.extensions["tallyguard_authenticator"] = authenticator
    app.extensions["tallyguard_decision_service"] = decision_service
    app.extensions["tallyguard_approval_inbox"] = approval_inbox
    app.extensions["tallyguard_authorized_decisions"] = authorized_decisions

    _seed_demo_identity(repository)

    @app.before_request
    def assign_correlation_id() -> None:
        _correlation_id()

    @app.after_request
    def attach_correlation_id(response: Response) -> Response:
        response.headers["X-Correlation-ID"] = _correlation_id()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

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

    @app.errorhandler(ApprovalError)
    def approval_error(exc: ApprovalError):
        status = 404 if "not found" in str(exc).lower() else 409
        return _error("APPROVAL_ERROR", str(exc), status)

    @app.errorhandler(WorkflowError)
    def workflow_error(exc: WorkflowError):
        return _error("WORKFLOW_ERROR", str(exc), 409)

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
        return jsonify({"status": "ready", "database": "ok"})

    @app.post("/api/demo/session")
    def demo_session():
        payload = request.get_json(silent=True) or {}
        role_name = str(payload.get("role", "operator")).lower()
        demo_principals = {
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
            raise ValueError("Demo role must be operator, approver, or auditor.")
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
        decision = decision_service.repository.get(
            organization_id=g.principal.organization_id,
            decision_id=decision_id,
        )
        approval = approval_inbox.request(decision, requested_by=g.principal)
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
            decision = decision_service.repository.get(
                organization_id=g.principal.organization_id,
                decision_id=resolved.decision_id,
            )
            authorized = apply_approved_escalation(decision, resolved)
            authorized_decisions[resolved.id] = authorized
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

    return app


def _seed_demo_identity(repository: SqliteRepository) -> None:
    try:
        repository.create_organization(organization_id=DEMO_ORGANIZATION_ID, name="TallyGuard Demo")
    except PersistenceError:
        pass
    for user_id, display_name, roles in (
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

"""Deterministic OpenAPI contract generated from the registered Flask routes."""

from __future__ import annotations

import re
from typing import Any

from flask import Flask
from werkzeug.routing import Rule


PUBLIC_ENDPOINTS = frozenset(
    {
        "health",
        "readiness",
        "metrics",
        "openapi_contract",
        "demo_session",
        "demo_scenarios",
    }
)

TAG_DESCRIPTIONS = {
    "accounting": "Reconciled finance-system exports.",
    "agent-runs": "Bounded autonomous planning, execution, and proof packets.",
    "approvals": "Segregated human approval workflows.",
    "auth": "Opaque role-scoped session inspection.",
    "audit": "Tenant-scoped tamper-evident audit evidence.",
    "decisions": "Evidence-bound deterministic decisions and replay.",
    "demo": "Credential-free judge identities and deterministic scenarios.",
    "evidence": "Immutable source intake, extraction, and retrieval.",
    "governance": "Current payment authority and exception state.",
    "health": "Service health and readiness probes.",
    "invoices": "Accounts-payable records and their workflow.",
    "metrics": "Bounded operational telemetry without financial labels.",
    "operations": "Finance queue, capacity, and settlement incidents.",
    "payment-batches": "Isolated, idempotent batch settlement.",
    "payments": "Settlement receipt lookup and Arc proof.",
    "policies": "Immutable policy versions and field-level diffs.",
    "reliability": "Content-addressed synthetic engineering evidence.",
    "schedules": "Due-payment release with fresh policy evaluation.",
    "treasury": "Source-referenced balance observations and capacity.",
    "vendors": "Verified payout identities and wallet history.",
}

_PATH_PARAMETER = re.compile(r"<(?:(?:[^:>]+):)?([^>]+)>")


def _openapi_path(rule: str) -> str:
    return _PATH_PARAMETER.sub(r"{\1}", rule)


def _tag(rule: str) -> str:
    segments = [segment for segment in rule.split("/") if segment]
    return segments[1] if len(segments) > 1 else "health"


def _summary(endpoint: str) -> str:
    return endpoint.replace("_", " ").strip().capitalize()


def _path_parameters(rule: Rule) -> list[dict[str, Any]]:
    return [
        {
            "name": argument,
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
        for argument in sorted(rule.arguments)
    ]


def _success_content(rule: str) -> dict[str, Any]:
    media_type = "text/csv" if rule.endswith("ledger.csv") else "application/json"
    schema: dict[str, Any]
    if media_type == "text/csv":
        schema = {"type": "string"}
    else:
        schema = {"type": "object", "additionalProperties": True}
    return {media_type: {"schema": schema}}


def _request_body(endpoint: str) -> dict[str, Any]:
    if endpoint in {"extract_evidence_preview", "upload_invoice_evidence"}:
        return {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {"type": "object", "additionalProperties": True}
                }
            },
        }
    return {
        "required": False,
        "content": {
            "application/json": {
                "schema": {"type": "object", "additionalProperties": True}
            }
        },
    }


def build_openapi_contract(app: Flask, *, server_url: str) -> dict[str, Any]:
    """Build a stable public contract from the routes actually registered by Flask."""

    paths: dict[str, dict[str, Any]] = {}
    operation_ids: set[str] = set()
    for rule in sorted(app.url_map.iter_rules(), key=lambda item: (item.rule, item.endpoint)):
        if not rule.rule.startswith("/api/"):
            continue
        path = _openapi_path(rule.rule)
        path_item = paths.setdefault(path, {})
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            operation_id = f"{method.lower()}_{rule.endpoint}"
            if operation_id in operation_ids:
                raise RuntimeError(f"Duplicate OpenAPI operation ID: {operation_id}")
            operation_ids.add(operation_id)
            operation: dict[str, Any] = {
                "operationId": operation_id,
                "summary": _summary(rule.endpoint),
                "tags": [_tag(rule.rule)],
                "parameters": _path_parameters(rule),
                "responses": {
                    "200": {
                        "description": "Request completed.",
                        "content": _success_content(rule.rule),
                    },
                    "400": {"$ref": "#/components/responses/BadRequest"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "403": {"$ref": "#/components/responses/Forbidden"},
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "429": {"$ref": "#/components/responses/RateLimited"},
                },
            }
            if rule.endpoint not in PUBLIC_ENDPOINTS:
                operation["security"] = [{"bearerAuth": []}]
            if method in {"POST", "PUT", "PATCH"}:
                operation["requestBody"] = _request_body(rule.endpoint)
            path_item[method.lower()] = operation

    used_tags = sorted(
        {
            operation["tags"][0]
            for item in paths.values()
            for operation in item.values()
        }
    )
    error_schema = {
        "type": "object",
        "required": ["error"],
        "properties": {
            "error": {
                "type": "object",
                "required": ["code", "message"],
                "properties": {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                    "correlation_id": {"type": "string"},
                },
            }
        },
    }

    def error_response(description: str) -> dict[str, Any]:
        return {
            "description": description,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                }
            },
        }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "TallyGuard Finance Control API",
            "version": "0.1.0",
            "description": (
                "Evidence-bound AP automation on Arc. AI recommendations are advisory; "
                "deterministic controls and role-separated settlement remain authoritative."
            ),
        },
        "servers": [{"url": server_url}],
        "tags": [
            {"name": tag, "description": TAG_DESCRIPTIONS.get(tag, "TallyGuard API.")}
            for tag in used_tags
        ],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "opaque",
                    "description": (
                        "Opaque role-scoped session token; only its SHA-256 digest is stored."
                    ),
                }
            },
            "schemas": {"ErrorResponse": error_schema},
            "responses": {
                "BadRequest": error_response("Malformed or unsafe request."),
                "Unauthorized": error_response(
                    "Missing, invalid, expired, or revoked session."
                ),
                "Forbidden": error_response(
                    "The principal lacks the required role or tenant boundary."
                ),
                "Conflict": error_response(
                    "Durable workflow, idempotency, or optimistic-version conflict."
                ),
                "RateLimited": {
                    **error_response("Request budget exceeded; inspect Retry-After."),
                    "headers": {
                        "Retry-After": {
                            "schema": {"type": "integer", "minimum": 1}
                        }
                    },
                },
            },
        },
    }

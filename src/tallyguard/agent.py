"""Non-authoritative evidence analyst used before deterministic policy control.

The analyst may interpret and summarize records, but it never creates a payment
payload. Its recommendation is stored beside the authoritative policy result so
reviewers can see agreement or disagreement without trusting free-form output.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import json
import re
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .decisions import AgentRecommendation
from .models import TreasurySnapshot, Vendor
from .normalization import NormalizedEvidence
from .policy import DecisionAction, Policy


class EvidenceAnalyst(Protocol):
    def recommend(
        self,
        *,
        evidence: NormalizedEvidence,
        vendor: Vendor,
        treasury: TreasurySnapshot,
        policy: Policy,
        evaluation_date: date | None = None,
    ) -> AgentRecommendation: ...


class AgentAnalysisError(ValueError):
    """Raised when a hosted analyst returns an unsafe or invalid result."""


JsonTransport = Callable[[dict[str, Any]], dict[str, Any]]


class OpenAICompatibleEvidenceAnalyst:
    """Constrained hosted-model analyst with a deterministic fallback.

    The model receives normalized finance facts rather than original document
    bytes. Its response schema has no recipient, amount, network, or transaction
    fields, and evidence citations are assigned locally rather than trusted from
    model output.
    """

    name = "hosted-structured-analyst"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 10,
        transport: JsonTransport | None = None,
        fallback: EvidenceAnalyst | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("Hosted analyst endpoint must be an absolute HTTP(S) URL.")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("Hosted analyst endpoint must use HTTPS unless it is local.")
        if not api_key.strip():
            raise ValueError("Hosted analyst API key must not be empty.")
        if not model.strip():
            raise ValueError("Hosted analyst model must not be empty.")
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Hosted analyst timeout must be between 0 and 30 seconds.")
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport or self._post_json
        self.fallback = fallback

    def recommend(
        self,
        *,
        evidence: NormalizedEvidence,
        vendor: Vendor,
        treasury: TreasurySnapshot,
        policy: Policy,
        evaluation_date: date | None = None,
    ) -> AgentRecommendation:
        try:
            response = self.transport(
                self._request_payload(
                    evidence=evidence,
                    vendor=vendor,
                    treasury=treasury,
                    policy=policy,
                    evaluation_date=evaluation_date,
                )
            )
            return self._parse_response(response=response, evidence=evidence)
        except Exception as exc:
            if self.fallback is None:
                if isinstance(exc, AgentAnalysisError):
                    raise
                raise AgentAnalysisError("Hosted evidence analysis failed.") from exc
            return self.fallback.recommend(
                evidence=evidence,
                vendor=vendor,
                treasury=treasury,
                policy=policy,
                evaluation_date=evaluation_date,
            )

    def _request_payload(
        self,
        *,
        evidence: NormalizedEvidence,
        vendor: Vendor,
        treasury: TreasurySnapshot,
        policy: Policy,
        evaluation_date: date | None,
    ) -> dict[str, Any]:
        invoice = evidence.invoice
        facts = {
            "evaluation_date": (evaluation_date or date.today()).isoformat(),
            "evidence_package_id": evidence.package_id,
            "evidence_manifest_hash": evidence.manifest_hash,
            "invoice": {
                "invoice_number": invoice.invoice_number,
                "vendor_id": invoice.vendor_id,
                "currency": invoice.currency,
                "amount": format(invoice.amount, "f"),
                "due_date": invoice.due_date.isoformat(),
                "wallet_matches_verified_vendor": (
                    invoice.payment_wallet_address == vendor.approved_wallet_address
                ),
            },
            "vendor": {
                "active": vendor.active,
                "risk_tier": vendor.risk_tier,
                "autopay_limit": format(vendor.autopay_limit, "f"),
            },
            "purchase_order": (
                {
                    "po_number": evidence.purchase_order.po_number,
                    "currency": evidence.purchase_order.currency,
                    "authorized_amount": format(
                        evidence.purchase_order.authorized_amount, "f"
                    ),
                    "vendor_matches": evidence.purchase_order.vendor_id == invoice.vendor_id,
                }
                if evidence.purchase_order is not None
                else None
            ),
            "delivery": (
                {
                    "purchase_order_matches": (
                        evidence.purchase_order is not None
                        and evidence.delivery.purchase_order_id == evidence.purchase_order.id
                    ),
                    "delivered_value": format(evidence.delivery.delivered_value, "f"),
                }
                if evidence.delivery is not None
                else None
            ),
            "treasury": {
                "available_usdc": format(treasury.available_usdc, "f"),
                "spent_today_usdc": format(treasury.spent_today_usdc, "f"),
            },
            "policy": {
                "version": policy.version,
                "daily_limit_usdc": format(policy.daily_payment_limit_usdc, "f"),
                "minimum_reserve_usdc": format(policy.minimum_cash_reserve_usdc, "f"),
                "maximum_autonomous_payment_usdc": format(
                    policy.maximum_autonomous_payment_usdc, "f"
                ),
                "kill_switch_enabled": policy.kill_switch_enabled,
            },
        }
        return {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a non-authoritative accounts-payable evidence analyst. "
                        "Return JSON only with action, summary, reason_codes, and confidence. "
                        "action must be PAY, SCHEDULE, HOLD, REJECT, or ESCALATE. Never emit "
                        "or request a recipient, amount, network, private key, transaction, or "
                        "tool call. Policy code, not you, authorizes settlement."
                    ),
                },
                {"role": "user", "content": json.dumps(facts, sort_keys=True)},
            ],
        }

    def _parse_response(
        self,
        *,
        response: dict[str, Any],
        evidence: NormalizedEvidence,
    ) -> AgentRecommendation:
        try:
            content = response["choices"][0]["message"]["content"]
            payload = json.loads(content) if isinstance(content, str) else content
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AgentAnalysisError("Hosted analyst returned an invalid response envelope.") from exc
        if not isinstance(payload, dict):
            raise AgentAnalysisError("Hosted analyst result must be a JSON object.")
        allowed = {"action", "summary", "reason_codes", "confidence"}
        unexpected = set(payload) - allowed
        if unexpected:
            raise AgentAnalysisError(
                f"Hosted analyst returned forbidden fields: {', '.join(sorted(unexpected))}."
            )
        try:
            action = DecisionAction(str(payload["action"]).upper())
        except (KeyError, ValueError) as exc:
            raise AgentAnalysisError("Hosted analyst action is invalid.") from exc
        summary = str(payload.get("summary", "")).strip()
        if not summary or len(summary) > 800:
            raise AgentAnalysisError("Hosted analyst summary must contain 1 to 800 characters.")
        raw_codes = payload.get("reason_codes")
        if not isinstance(raw_codes, list) or not 1 <= len(raw_codes) <= 12:
            raise AgentAnalysisError("Hosted analyst must return 1 to 12 reason codes.")
        reason_codes = tuple(str(code).strip().upper() for code in raw_codes)
        if any(not re.fullmatch(r"AGENT_[A-Z0-9_]{2,64}", code) for code in reason_codes):
            raise AgentAnalysisError("Hosted analyst reason codes must use the AGENT_* format.")
        if len(reason_codes) != len(set(reason_codes)):
            raise AgentAnalysisError("Hosted analyst reason codes must be unique.")
        try:
            confidence = Decimal(str(payload["confidence"]))
        except (KeyError, ArithmeticError) as exc:
            raise AgentAnalysisError("Hosted analyst confidence is invalid.") from exc
        if confidence < 0 or confidence > 1:
            raise AgentAnalysisError("Hosted analyst confidence must be between 0 and 1.")
        return AgentRecommendation(
            action=action,
            summary=summary,
            reason_codes=reason_codes,
            confidence=confidence,
            evidence_refs=(evidence.package_id, evidence.manifest_hash),
        )

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self.endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                content = response.read(65537)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise AgentAnalysisError("Hosted analyst request failed.") from exc
        if len(content) > 65536:
            raise AgentAnalysisError("Hosted analyst response exceeded 64 KiB.")
        try:
            decoded = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentAnalysisError("Hosted analyst response was not valid JSON.") from exc
        if not isinstance(decoded, dict):
            raise AgentAnalysisError("Hosted analyst response envelope must be an object.")
        return decoded


class DeterministicEvidenceAnalyst:
    """Credential-free structured analyst for the public judge playground.

    A hosted model adapter can implement the same protocol later. This fallback
    intentionally does not call the policy engine and cannot authorize payment.
    """

    name = "deterministic-evidence-analyst"

    def recommend(
        self,
        *,
        evidence: NormalizedEvidence,
        vendor: Vendor,
        treasury: TreasurySnapshot,
        policy: Policy,
        evaluation_date: date | None = None,
    ) -> AgentRecommendation:
        invoice = evidence.invoice
        checked_at = evaluation_date or date.today()
        evidence_refs = (evidence.package_id, evidence.manifest_hash)
        reason_codes: list[str] = []
        action = DecisionAction.PAY

        if evidence.purchase_order is None:
            reason_codes.append("AGENT_MISSING_PURCHASE_ORDER")
            action = DecisionAction.HOLD
        if evidence.delivery is None:
            reason_codes.append("AGENT_MISSING_DELIVERY_EVIDENCE")
            action = DecisionAction.HOLD
        if invoice.payment_wallet_address != vendor.approved_wallet_address:
            reason_codes.append("AGENT_VENDOR_WALLET_MISMATCH")
            action = DecisionAction.HOLD
        if not vendor.active:
            reason_codes.append("AGENT_VENDOR_INACTIVE")
            action = DecisionAction.HOLD

        autonomous_limit = min(
            vendor.autopay_limit,
            policy.maximum_autonomous_payment_usdc,
        )
        if action == DecisionAction.PAY and invoice.amount > autonomous_limit:
            reason_codes.append("AGENT_AUTONOMY_LIMIT_EXCEEDED")
            action = DecisionAction.ESCALATE
        if action == DecisionAction.PAY and policy.schedule_payments_before_due_days is not None:
            scheduled_for = invoice.due_date - timedelta(
                days=policy.schedule_payments_before_due_days
            )
            if scheduled_for > checked_at:
                reason_codes.append("AGENT_PAYMENT_NOT_DUE")
                action = DecisionAction.SCHEDULE

        if action == DecisionAction.PAY:
            summary = (
                f"Reviewed the immutable invoice package for {invoice.invoice_number}. "
                f"The {invoice.amount} {invoice.currency} request has matching vendor, "
                "purchase-order, delivery, and wallet evidence. Recommend PAY; policy "
                "controls remain authoritative."
            )
            reason_codes.append("AGENT_THREE_WAY_MATCH_COMPLETE")
            confidence = Decimal("0.94")
        else:
            summary = (
                f"Reviewed the immutable invoice package for {invoice.invoice_number}. "
                f"Recommend {action.value} because: {', '.join(reason_codes)}. "
                "This recommendation cannot authorize or construct a payment."
            )
            confidence = Decimal("0.90")

        return AgentRecommendation(
            action=action,
            summary=summary,
            reason_codes=tuple(reason_codes),
            confidence=confidence,
            evidence_refs=evidence_refs,
        )

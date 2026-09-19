"""Idempotent settlement orchestration for simulated and live Arc adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import hashlib
import re
from threading import Lock
from typing import Protocol

from .network import ArcNetwork, ArcNetworkConfig, require_mainnet_authorization
from .policy import Decision, DecisionAction


EVM_ADDRESS = re.compile(r"^0x[a-fA-F0-9]{40}$")


class SettlementStatus(StrEnum):
    CONFIRMED = "CONFIRMED"


class SettlementDenied(RuntimeError):
    """Raised when a decision or settlement result violates a hard control."""


class SettlementUnavailable(SettlementDenied):
    """Raised when a transient provider failure can be retried idempotently."""


class SettlementReconciliationMismatch(SettlementDenied):
    """Raised when provider evidence conflicts with the sealed payment intent."""


class SettlementAttemptOutcome(StrEnum):
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_LOCKED = "FAILED_LOCKED"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    CONFIRMED = "CONFIRMED"


@dataclass(frozen=True, slots=True)
class SettlementAttempt:
    sequence: int | None
    organization_id: str
    payment_intent_id: str
    invoice_id: str
    provider: str
    outcome: SettlementAttemptOutcome
    retryable: bool
    error_code: str | None
    error_message: str | None
    correlation_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    id: str
    organization_id: str
    invoice_id: str
    decision_id: str
    recipient: str
    amount_usdc: Decimal
    network: ArcNetwork
    idempotency_key: str
    approval_reference: str | None = None

    def __post_init__(self) -> None:
        amount = self.amount_usdc if isinstance(self.amount_usdc, Decimal) else Decimal(str(self.amount_usdc))
        if amount <= 0:
            raise ValueError("Payment amount must be positive.")
        if amount * Decimal(1_000_000) != (amount * Decimal(1_000_000)).to_integral_value():
            raise ValueError("USDC payment amounts support at most six decimal places.")
        if not EVM_ADDRESS.fullmatch(self.recipient):
            raise ValueError("Recipient must be a 20-byte EVM address.")
        if not self.idempotency_key.strip():
            raise ValueError("Idempotency key is required.")
        object.__setattr__(self, "amount_usdc", amount)
        object.__setattr__(self, "recipient", self.recipient.lower())


@dataclass(frozen=True, slots=True)
class ProviderSubmission:
    provider_reference: str
    transaction_hash: str
    recipient: str
    amount_usdc: Decimal
    network: ArcNetwork
    block_number: int


@dataclass(frozen=True, slots=True)
class SettlementReceipt:
    payment_intent_id: str
    organization_id: str
    provider: str
    provider_reference: str
    transaction_hash: str
    block_number: int
    confirmed_recipient: str
    confirmed_amount_usdc: Decimal
    network: ArcNetwork
    status: SettlementStatus
    confirmed_at: datetime


class SettlementAdapter(Protocol):
    name: str

    def submit(self, intent: PaymentIntent) -> ProviderSubmission: ...


class IdempotencyRegistry:
    """Process-local exactly-once registry with one lock per tenant/key pair."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[tuple[str, str], Lock] = {}
        self._receipts: dict[tuple[str, str], SettlementReceipt] = {}

    def lock_for(self, scope: tuple[str, str]) -> Lock:
        with self._guard:
            return self._locks.setdefault(scope, Lock())

    def get(self, scope: tuple[str, str]) -> SettlementReceipt | None:
        with self._guard:
            return self._receipts.get(scope)

    def store(self, scope: tuple[str, str], receipt: SettlementReceipt) -> None:
        with self._guard:
            existing = self._receipts.get(scope)
            if existing is not None and existing != receipt:
                raise SettlementDenied("Idempotency key is already bound to another settlement.")
            self._receipts[scope] = receipt


class SettlementService:
    def __init__(
        self,
        *,
        config: ArcNetworkConfig,
        adapter: SettlementAdapter,
        registry: IdempotencyRegistry | None = None,
        allow_mainnet: bool = False,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self.registry = registry or IdempotencyRegistry()
        self.allow_mainnet = allow_mainnet

    def execute(self, *, intent: PaymentIntent, decision: Decision) -> SettlementReceipt:
        self._authorize(intent=intent, decision=decision)
        scope = (intent.organization_id, intent.idempotency_key)
        with self.registry.lock_for(scope):
            existing = self.registry.get(scope)
            if existing is not None:
                self._ensure_same_intent(existing, intent)
                return existing

            submission = self.adapter.submit(intent)
            self._reconcile(intent, submission)
            receipt = SettlementReceipt(
                payment_intent_id=intent.id,
                organization_id=intent.organization_id,
                provider=self.adapter.name,
                provider_reference=submission.provider_reference,
                transaction_hash=submission.transaction_hash,
                block_number=submission.block_number,
                confirmed_recipient=submission.recipient.lower(),
                confirmed_amount_usdc=Decimal(str(submission.amount_usdc)),
                network=submission.network,
                status=SettlementStatus.CONFIRMED,
                confirmed_at=datetime.now(timezone.utc),
            )
            self.registry.store(scope, receipt)
            return receipt

    def _authorize(self, *, intent: PaymentIntent, decision: Decision) -> None:
        if decision.action != DecisionAction.PAY:
            raise SettlementDenied(f"Decision {decision.action} cannot create a payment.")
        if intent.organization_id.strip() == "":
            raise SettlementDenied("Organization scope is required.")
        if decision.approval_reference is not None and intent.approval_reference != decision.approval_reference:
            raise SettlementDenied("Payment intent is not bound to the decision approval reference.")
        if intent.network != self.config.name:
            raise SettlementDenied("Payment intent network does not match the configured Arc network.")
        require_mainnet_authorization(
            self.config,
            allow_mainnet=self.allow_mainnet,
            approval_reference=intent.approval_reference,
        )

    @staticmethod
    def _reconcile(intent: PaymentIntent, submission: ProviderSubmission) -> None:
        if submission.network != intent.network:
            raise SettlementReconciliationMismatch("Provider confirmed settlement on the wrong network.")
        if submission.recipient.lower() != intent.recipient:
            raise SettlementReconciliationMismatch("Provider confirmed settlement to the wrong recipient.")
        if Decimal(str(submission.amount_usdc)) != intent.amount_usdc:
            raise SettlementReconciliationMismatch("Provider confirmed the wrong settlement amount.")
        if not re.fullmatch(r"0x[a-fA-F0-9]{64}", submission.transaction_hash):
            raise SettlementReconciliationMismatch("Provider returned an invalid transaction hash.")
        if submission.block_number < 1:
            raise SettlementReconciliationMismatch("Provider returned an invalid block number.")

    @staticmethod
    def _ensure_same_intent(receipt: SettlementReceipt, intent: PaymentIntent) -> None:
        same = (
            receipt.payment_intent_id == intent.id
            and receipt.organization_id == intent.organization_id
            and receipt.confirmed_recipient == intent.recipient
            and receipt.confirmed_amount_usdc == intent.amount_usdc
            and receipt.network == intent.network
        )
        if not same:
            raise SettlementDenied("Idempotency key was reused for a different payment intent.")


class SimulatedArcAdapter:
    """Deterministic adapter used by tests and the credential-free public demo."""

    name = "arc-simulator"

    def __init__(self) -> None:
        self._guard = Lock()
        self.submission_count = 0
        self.failed_attempt_count = 0
        self._transient_failures: set[tuple[str, str]] = set()

    def arm_transient_failure(self, *, organization_id: str, invoice_id: str) -> None:
        """Fail the next matching attempt before a provider submission is accepted."""

        with self._guard:
            self._transient_failures.add((organization_id, invoice_id))

    def submit(self, intent: PaymentIntent) -> ProviderSubmission:
        with self._guard:
            scope = (intent.organization_id, intent.invoice_id)
            if scope in self._transient_failures:
                self._transient_failures.remove(scope)
                self.failed_attempt_count += 1
                raise SettlementUnavailable(
                    "Simulated provider timeout; the durable intent is safe to retry with the same idempotency key."
                )
            self.submission_count += 1
            block_number = 1_000_000 + self.submission_count
        digest = hashlib.sha256(
            f"{intent.organization_id}:{intent.idempotency_key}:{intent.recipient}:{intent.amount_usdc}:{intent.network}".encode()
        ).hexdigest()
        return ProviderSubmission(
            provider_reference=f"sim-{digest[:16]}",
            transaction_hash=f"0x{digest}",
            recipient=intent.recipient,
            amount_usdc=intent.amount_usdc,
            network=intent.network,
            block_number=block_number,
        )

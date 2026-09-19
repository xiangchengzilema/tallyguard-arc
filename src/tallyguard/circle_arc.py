"""Circle developer-wallet execution with independent Arc receipt verification.

The public demo never imports this module's optional Circle SDK path. Live
execution is constructed explicitly with :meth:`CircleArcAdapter.from_env`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
import json
import os
import time
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from .network import ArcNetwork, ArcNetworkConfig
from .settlement import PaymentIntent, ProviderSubmission, SettlementDenied


TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class CircleTransactionState(StrEnum):
    INITIATED = "INITIATED"
    CLEARED = "CLEARED"
    QUEUED = "QUEUED"
    SENT = "SENT"
    CONFIRMED = "CONFIRMED"
    COMPLETE = "COMPLETE"
    STUCK = "STUCK"
    FAILED = "FAILED"
    DENIED = "DENIED"
    CANCELLED = "CANCELLED"


FAILED_CIRCLE_STATES = {
    CircleTransactionState.STUCK,
    CircleTransactionState.FAILED,
    CircleTransactionState.DENIED,
    CircleTransactionState.CANCELLED,
}


class CircleConfigurationError(RuntimeError):
    """Raised when the live Circle adapter is not safely configured."""


@dataclass(frozen=True, slots=True)
class CircleTransaction:
    id: str
    state: CircleTransactionState
    blockchain: str | None = None
    destination_address: str | None = None
    amounts: tuple[Decimal, ...] = ()
    transaction_hash: str | None = None
    block_height: int | None = None
    error_reason: str | None = None


class CircleGateway(Protocol):
    def create_usdc_transfer(self, intent: PaymentIntent) -> CircleTransaction: ...

    def get_transaction(self, transaction_id: str) -> CircleTransaction: ...


class JsonRpcTransport(Protocol):
    def __call__(self, method: str, params: list[Any]) -> Any: ...


@dataclass(frozen=True, slots=True)
class ArcTransferProof:
    transaction_hash: str
    block_number: int


class ArcRpcClient:
    """Minimal Arc JSON-RPC reader that verifies the exact USDC transfer log."""

    def __init__(
        self,
        *,
        config: ArcNetworkConfig,
        timeout_seconds: float = 10,
        transport: JsonRpcTransport | None = None,
    ) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds
        self._transport = transport or self._http_call

    def confirm_usdc_transfer(
        self,
        *,
        transaction_hash: str,
        recipient: str,
        amount_usdc: Decimal,
    ) -> ArcTransferProof:
        chain_id = _hex_int(self._transport("eth_chainId", []), field="chain ID")
        if chain_id != self.config.chain_id:
            raise SettlementDenied(
                f"Arc RPC chain mismatch: expected {self.config.chain_id}, received {chain_id}."
            )

        receipt = self._transport("eth_getTransactionReceipt", [transaction_hash])
        if not isinstance(receipt, dict):
            raise SettlementDenied("Arc transaction receipt is not available.")
        if receipt.get("transactionHash", "").lower() != transaction_hash.lower():
            raise SettlementDenied("Arc receipt transaction hash does not match Circle.")
        if _hex_int(receipt.get("status"), field="receipt status") != 1:
            raise SettlementDenied("Arc receipt reports a reverted transaction.")
        block_number = _hex_int(receipt.get("blockNumber"), field="block number")
        if block_number < 1:
            raise SettlementDenied("Arc receipt returned an invalid block number.")

        transaction = self._transport("eth_getTransactionByHash", [transaction_hash])
        if not isinstance(transaction, dict):
            raise SettlementDenied("Arc transaction details are not available.")
        if transaction.get("to", "").lower() != self.config.usdc_contract_address.lower():
            raise SettlementDenied("Arc transaction did not call the canonical USDC contract.")

        expected_atomic = _to_usdc_atomic_units(amount_usdc)
        expected_recipient_topic = "0x" + recipient[2:].lower().rjust(64, "0")
        matching_log = False
        for log in receipt.get("logs", []):
            if not isinstance(log, dict):
                continue
            topics = log.get("topics", [])
            if (
                log.get("address", "").lower() == self.config.usdc_contract_address.lower()
                and len(topics) >= 3
                and str(topics[0]).lower() == TRANSFER_TOPIC
                and str(topics[2]).lower() == expected_recipient_topic
                and _hex_int(log.get("data"), field="USDC transfer amount") == expected_atomic
            ):
                matching_log = True
                break
        if not matching_log:
            raise SettlementDenied("Arc receipt does not contain the expected USDC transfer event.")

        return ArcTransferProof(transaction_hash=transaction_hash.lower(), block_number=block_number)

    def _http_call(self, method: str, params: list[Any]) -> Any:
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            self.config.rpc_url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "TallyGuard/0.1"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - configured RPC URL
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise SettlementDenied("Arc RPC request failed.") from exc
        if not isinstance(body, dict) or body.get("error") is not None:
            raise SettlementDenied("Arc RPC returned an error response.")
        return body.get("result")


class CircleSdkGateway:
    """Thin lazy bridge to Circle's official developer-controlled wallet SDK."""

    def __init__(
        self,
        *,
        api_key: str,
        entity_secret: str,
        wallet_id: str,
        config: ArcNetworkConfig,
    ) -> None:
        if not all(value.strip() for value in (api_key, entity_secret, wallet_id)):
            raise CircleConfigurationError("Circle API key, entity secret, and wallet ID are required.")
        try:
            from circle.web3 import developer_controlled_wallets, utils
        except ImportError as exc:  # pragma: no cover - exercised only with optional dependency missing
            raise CircleConfigurationError(
                "Install the project with the 'circle' extra before enabling live settlement."
            ) from exc

        client = utils.init_developer_controlled_wallets_client(
            api_key=api_key,
            entity_secret=entity_secret,
            user_agent="TallyGuard/0.1",
        )
        self._sdk = developer_controlled_wallets
        self._transactions = developer_controlled_wallets.TransactionsApi(client)
        self.wallet_id = wallet_id
        self.config = config

    @classmethod
    def from_env(cls, config: ArcNetworkConfig) -> "CircleSdkGateway":
        required = {
            "CIRCLE_WEB3_API_KEY": os.getenv("CIRCLE_WEB3_API_KEY", ""),
            "CIRCLE_ENTITY_SECRET": os.getenv("CIRCLE_ENTITY_SECRET", ""),
            "CIRCLE_WALLET_ID": os.getenv("CIRCLE_WALLET_ID", ""),
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise CircleConfigurationError(f"Missing Circle configuration: {', '.join(missing)}.")
        return cls(
            api_key=required["CIRCLE_WEB3_API_KEY"],
            entity_secret=required["CIRCLE_ENTITY_SECRET"],
            wallet_id=required["CIRCLE_WALLET_ID"],
            config=config,
        )

    def create_usdc_transfer(self, intent: PaymentIntent) -> CircleTransaction:
        request = self._sdk.CreateTransferTransactionForDeveloperRequest.from_dict(
            {
                "idempotencyKey": intent.idempotency_key,
                "amounts": [_format_usdc(intent.amount_usdc)],
                "destinationAddress": intent.recipient,
                "feeLevel": "MEDIUM",
                "tokenAddress": self.config.usdc_contract_address,
                "blockchain": intent.network.value,
                "walletId": self.wallet_id,
                "refId": intent.id,
            }
        )
        response = self._transactions.create_developer_transaction_transfer(
            create_transfer_transaction_for_developer_request=request
        )
        return CircleTransaction(
            id=str(response.data.id),
            state=CircleTransactionState(_enum_value(response.data.state)),
        )

    def get_transaction(self, transaction_id: str) -> CircleTransaction:
        response = self._transactions.get_transaction(id=transaction_id)
        transaction = response.data.transaction
        if transaction is None:
            raise SettlementDenied("Circle returned an empty transaction record.")
        return CircleTransaction(
            id=str(transaction.id),
            state=CircleTransactionState(_enum_value(transaction.state)),
            blockchain=_enum_value(transaction.blockchain),
            destination_address=transaction.destination_address,
            amounts=tuple(Decimal(str(value)) for value in (transaction.amounts or ())),
            transaction_hash=transaction.tx_hash,
            block_height=transaction.block_height,
            error_reason=transaction.error_reason,
        )


class CircleArcAdapter:
    """Submit through Circle, wait for completion, then prove it independently on Arc."""

    name = "circle-developer-wallets+arc-rpc"

    def __init__(
        self,
        *,
        config: ArcNetworkConfig,
        circle: CircleGateway,
        arc_rpc: ArcRpcClient,
        max_transfer_usdc: Decimal = Decimal("5"),
        max_poll_attempts: int = 20,
        poll_interval_seconds: float = 2,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_transfer_usdc <= 0:
            raise ValueError("Circle hard transfer cap must be positive.")
        if max_poll_attempts < 1:
            raise ValueError("Circle polling requires at least one attempt.")
        self.config = config
        self.circle = circle
        self.arc_rpc = arc_rpc
        self.max_transfer_usdc = Decimal(str(max_transfer_usdc))
        self.max_poll_attempts = max_poll_attempts
        self.poll_interval_seconds = poll_interval_seconds
        self.sleeper = sleeper

    @classmethod
    def from_env(cls, config: ArcNetworkConfig | None = None) -> "CircleArcAdapter":
        selected = config or ArcNetworkConfig.from_env()
        raw_cap = os.getenv("TALLYGUARD_MAX_TRANSFER_USDC", "5")
        try:
            hard_cap = Decimal(raw_cap)
        except Exception as exc:
            raise CircleConfigurationError("TALLYGUARD_MAX_TRANSFER_USDC must be a decimal amount.") from exc
        return cls(
            config=selected,
            circle=CircleSdkGateway.from_env(selected),
            arc_rpc=ArcRpcClient(config=selected),
            max_transfer_usdc=hard_cap,
        )

    def submit(self, intent: PaymentIntent) -> ProviderSubmission:
        if intent.network != self.config.name:
            raise SettlementDenied("Circle adapter network does not match the payment intent.")
        if not _is_uuid4(intent.idempotency_key):
            raise SettlementDenied("Circle settlement requires a UUID v4 idempotency key.")
        if intent.amount_usdc > self.max_transfer_usdc:
            raise SettlementDenied("Payment exceeds the live adapter hard transfer cap.")

        created = self.circle.create_usdc_transfer(intent)
        if not created.id.strip():
            raise SettlementDenied("Circle did not return a transaction ID.")
        provider_reference = created.id

        final: CircleTransaction | None = None
        for attempt in range(self.max_poll_attempts):
            current = self.circle.get_transaction(provider_reference)
            if current.id != provider_reference:
                raise SettlementDenied("Circle returned a different transaction ID while polling.")
            if current.state in FAILED_CIRCLE_STATES:
                reason = current.error_reason or current.state.value
                raise SettlementDenied(f"Circle transaction did not complete: {reason}.")
            if current.state == CircleTransactionState.COMPLETE:
                final = current
                break
            if attempt + 1 < self.max_poll_attempts:
                self.sleeper(self.poll_interval_seconds)
        if final is None:
            raise SettlementDenied("Circle transaction did not reach COMPLETE before the polling limit.")

        self._validate_circle_result(intent, final)
        proof = self.arc_rpc.confirm_usdc_transfer(
            transaction_hash=final.transaction_hash or "",
            recipient=intent.recipient,
            amount_usdc=intent.amount_usdc,
        )
        if final.block_height is not None and final.block_height != proof.block_number:
            raise SettlementDenied("Circle and Arc RPC disagree on the settlement block number.")

        return ProviderSubmission(
            provider_reference=provider_reference,
            transaction_hash=proof.transaction_hash,
            recipient=intent.recipient,
            amount_usdc=intent.amount_usdc,
            network=intent.network,
            block_number=proof.block_number,
        )

    def _validate_circle_result(self, intent: PaymentIntent, result: CircleTransaction) -> None:
        if result.blockchain != intent.network.value:
            raise SettlementDenied("Circle completed the transaction on the wrong network.")
        if (result.destination_address or "").lower() != intent.recipient:
            raise SettlementDenied("Circle completed the transaction to the wrong recipient.")
        if result.amounts != (intent.amount_usdc,):
            raise SettlementDenied("Circle completed a different transfer amount.")
        if not result.transaction_hash:
            raise SettlementDenied("Circle completed the transaction without a transaction hash.")


def _is_uuid4(value: str) -> bool:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value.lower()


def _format_usdc(amount: Decimal) -> str:
    return format(amount.normalize(), "f")


def _to_usdc_atomic_units(amount: Decimal) -> int:
    scaled = amount * Decimal(1_000_000)
    if scaled != scaled.to_integral_value():
        raise SettlementDenied("USDC settlement supports at most six decimal places.")
    return int(scaled)


def _hex_int(value: Any, *, field: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise SettlementDenied(f"Arc RPC returned an invalid {field}.")
    try:
        return int(value, 16)
    except ValueError as exc:
        raise SettlementDenied(f"Arc RPC returned an invalid {field}.") from exc


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))

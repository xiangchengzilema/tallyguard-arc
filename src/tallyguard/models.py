"""Core immutable business records used by the policy engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json


def _decimal(value: Decimal | int | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def normalize_wallet(address: str) -> str:
    """Normalize an EVM address for deterministic comparisons.

    Checksum validation belongs in the settlement adapter. The domain layer only
    performs a stable, case-insensitive identity comparison.
    """

    return address.strip().lower()


@dataclass(frozen=True, slots=True)
class Vendor:
    id: str
    legal_name: str
    approved_wallet_address: str
    autopay_limit: Decimal
    risk_tier: str = "standard"
    active: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "approved_wallet_address", normalize_wallet(self.approved_wallet_address))
        object.__setattr__(self, "autopay_limit", _decimal(self.autopay_limit))


@dataclass(frozen=True, slots=True)
class Invoice:
    id: str
    organization_id: str
    vendor_id: str
    invoice_number: str
    currency: str
    amount: Decimal
    due_date: date
    payment_wallet_address: str
    source_document_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", self.currency.strip().upper())
        object.__setattr__(self, "amount", _decimal(self.amount))
        object.__setattr__(self, "payment_wallet_address", normalize_wallet(self.payment_wallet_address))

    @property
    def fingerprint(self) -> str:
        payload = {
            "organization_id": self.organization_id,
            "vendor_id": self.vendor_id,
            "invoice_number": self.invoice_number.strip().casefold(),
            "currency": self.currency,
            "amount": format(self.amount, "f"),
            "source_document_hash": self.source_document_hash.strip().lower(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PurchaseOrder:
    id: str
    vendor_id: str
    po_number: str
    currency: str
    authorized_amount: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", self.currency.strip().upper())
        object.__setattr__(self, "authorized_amount", _decimal(self.authorized_amount))


@dataclass(frozen=True, slots=True)
class DeliveryEvidence:
    id: str
    purchase_order_id: str
    delivered_value: Decimal
    source_document_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "delivered_value", _decimal(self.delivered_value))


@dataclass(frozen=True, slots=True)
class TreasurySnapshot:
    available_usdc: Decimal
    spent_today_usdc: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "available_usdc", _decimal(self.available_usdc))
        object.__setattr__(self, "spent_today_usdc", _decimal(self.spent_today_usdc))


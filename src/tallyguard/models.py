"""Core immutable business records used by the policy engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json
import re


EVM_ADDRESS = re.compile(r"^0x[a-f0-9]{40}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


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
    organization_id: str
    legal_name: str
    approved_wallet_address: str
    autopay_limit: Decimal
    risk_tier: str = "standard"
    active: bool = True

    def __post_init__(self) -> None:
        wallet = normalize_wallet(self.approved_wallet_address)
        limit = _decimal(self.autopay_limit)
        if not self.id.strip() or not self.organization_id.strip() or not self.legal_name.strip():
            raise ValueError("Vendor ID, organization ID, and legal name are required.")
        if not EVM_ADDRESS.fullmatch(wallet):
            raise ValueError("Vendor wallet must be a 20-byte EVM address.")
        if limit < 0:
            raise ValueError("Vendor autopay limit must not be negative.")
        object.__setattr__(self, "approved_wallet_address", wallet)
        object.__setattr__(self, "autopay_limit", limit)


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
        currency = self.currency.strip().upper()
        amount = _decimal(self.amount)
        wallet = normalize_wallet(self.payment_wallet_address)
        digest = self.source_document_hash.strip().lower()
        if not self.id.strip() or not self.organization_id.strip() or not self.vendor_id.strip():
            raise ValueError("Invoice ID, organization ID, and vendor ID are required.")
        if not self.invoice_number.strip() or not currency:
            raise ValueError("Invoice number and currency are required.")
        if amount <= 0:
            raise ValueError("Invoice amount must be positive.")
        if not EVM_ADDRESS.fullmatch(wallet):
            raise ValueError("Invoice payment wallet must be a 20-byte EVM address.")
        if not SHA256.fullmatch(digest):
            raise ValueError("Invoice source document hash must be a SHA-256 digest.")
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "payment_wallet_address", wallet)
        object.__setattr__(self, "source_document_hash", digest)

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
    organization_id: str
    vendor_id: str
    po_number: str
    currency: str
    authorized_amount: Decimal

    def __post_init__(self) -> None:
        currency = self.currency.strip().upper()
        amount = _decimal(self.authorized_amount)
        if not self.id.strip() or not self.organization_id.strip() or not self.vendor_id.strip():
            raise ValueError("Purchase-order ID, organization ID, and vendor ID are required.")
        if not self.po_number.strip() or not currency:
            raise ValueError("Purchase-order number and currency are required.")
        if amount < 0:
            raise ValueError("Purchase-order amount must not be negative.")
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "authorized_amount", amount)


@dataclass(frozen=True, slots=True)
class DeliveryEvidence:
    id: str
    organization_id: str
    purchase_order_id: str
    delivered_value: Decimal
    source_document_hash: str

    def __post_init__(self) -> None:
        value = _decimal(self.delivered_value)
        digest = self.source_document_hash.strip().lower()
        if not self.id.strip() or not self.organization_id.strip() or not self.purchase_order_id.strip():
            raise ValueError("Delivery ID, organization ID, and purchase-order ID are required.")
        if value < 0:
            raise ValueError("Delivered value must not be negative.")
        if not SHA256.fullmatch(digest):
            raise ValueError("Delivery source document hash must be a SHA-256 digest.")
        object.__setattr__(self, "delivered_value", value)
        object.__setattr__(self, "source_document_hash", digest)


@dataclass(frozen=True, slots=True)
class TreasurySnapshot:
    organization_id: str
    available_usdc: Decimal
    spent_today_usdc: Decimal

    def __post_init__(self) -> None:
        available = _decimal(self.available_usdc)
        spent = _decimal(self.spent_today_usdc)
        if not self.organization_id.strip():
            raise ValueError("Treasury organization ID is required.")
        if available < 0 or spent < 0:
            raise ValueError("Treasury balances must not be negative.")
        object.__setattr__(self, "available_usdc", available)
        object.__setattr__(self, "spent_today_usdc", spent)

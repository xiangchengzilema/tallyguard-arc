"""Tenant-scoped vendor onboarding and verified wallet history."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
import re
from threading import Lock

from .models import Vendor, normalize_wallet


EVM_ADDRESS = re.compile(r"^0x[a-fA-F0-9]{40}$")


class VendorDirectoryError(ValueError):
    """Raised when a vendor or wallet-history invariant is violated."""


class WalletVerificationMethod(StrEnum):
    SIGNED_CHALLENGE = "SIGNED_CHALLENGE"
    OUT_OF_BAND_CALL = "OUT_OF_BAND_CALL"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class WalletEventType(StrEnum):
    VERIFIED = "VERIFIED"
    REPLACED = "REPLACED"


@dataclass(frozen=True, slots=True)
class VendorWalletEvent:
    organization_id: str
    vendor_id: str
    event_type: WalletEventType
    wallet_address: str
    previous_wallet_address: str | None
    verification_method: WalletVerificationMethod
    verification_reference: str
    verified_by_user_id: str
    verified_at: datetime

    def __post_init__(self) -> None:
        wallet = normalize_wallet(self.wallet_address)
        previous = (
            normalize_wallet(self.previous_wallet_address)
            if self.previous_wallet_address is not None
            else None
        )
        if not EVM_ADDRESS.fullmatch(wallet):
            raise VendorDirectoryError("Vendor wallet must be a 20-byte EVM address.")
        if not self.verification_reference.strip() or not self.verified_by_user_id.strip():
            raise VendorDirectoryError("Wallet verification reference and verifier are required.")
        if self.verified_at.tzinfo is None:
            raise VendorDirectoryError("Wallet verification timestamp must be timezone-aware.")
        object.__setattr__(self, "wallet_address", wallet)
        object.__setattr__(self, "previous_wallet_address", previous)


class VendorDirectory:
    """In-memory reference implementation with atomic wallet replacement."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._vendors: dict[tuple[str, str], Vendor] = {}
        self._wallet_events: dict[tuple[str, str], list[VendorWalletEvent]] = {}

    def onboard(
        self,
        vendor: Vendor,
        *,
        verification_method: WalletVerificationMethod,
        verification_reference: str,
        verified_by_user_id: str,
        verified_at: datetime | None = None,
    ) -> Vendor:
        scope = (vendor.organization_id, vendor.id)
        event = VendorWalletEvent(
            organization_id=vendor.organization_id,
            vendor_id=vendor.id,
            event_type=WalletEventType.VERIFIED,
            wallet_address=vendor.approved_wallet_address,
            previous_wallet_address=None,
            verification_method=verification_method,
            verification_reference=verification_reference,
            verified_by_user_id=verified_by_user_id,
            verified_at=verified_at or datetime.now(timezone.utc),
        )
        with self._guard:
            if scope in self._vendors:
                raise VendorDirectoryError("Vendor ID is already registered in this organization.")
            self._vendors[scope] = vendor
            self._wallet_events[scope] = [event]
        return vendor

    def replace_wallet(
        self,
        *,
        organization_id: str,
        vendor_id: str,
        expected_current_wallet: str,
        new_wallet: str,
        verification_method: WalletVerificationMethod,
        verification_reference: str,
        verified_by_user_id: str,
        verified_at: datetime | None = None,
    ) -> Vendor:
        scope = (organization_id, vendor_id)
        expected = normalize_wallet(expected_current_wallet)
        replacement = normalize_wallet(new_wallet)
        with self._guard:
            try:
                current = self._vendors[scope]
            except KeyError as exc:
                raise VendorDirectoryError("Vendor was not found in this organization.") from exc
            if current.approved_wallet_address != expected:
                raise VendorDirectoryError("Vendor wallet changed since it was last read.")
            if replacement == current.approved_wallet_address:
                raise VendorDirectoryError("Replacement wallet must differ from the current wallet.")
            event = VendorWalletEvent(
                organization_id=organization_id,
                vendor_id=vendor_id,
                event_type=WalletEventType.REPLACED,
                wallet_address=replacement,
                previous_wallet_address=current.approved_wallet_address,
                verification_method=verification_method,
                verification_reference=verification_reference,
                verified_by_user_id=verified_by_user_id,
                verified_at=verified_at or datetime.now(timezone.utc),
            )
            updated = replace(current, approved_wallet_address=replacement)
            self._vendors[scope] = updated
            self._wallet_events[scope].append(event)
            return updated

    def get(self, *, organization_id: str, vendor_id: str) -> Vendor:
        with self._guard:
            try:
                return self._vendors[(organization_id, vendor_id)]
            except KeyError as exc:
                raise VendorDirectoryError("Vendor was not found in this organization.") from exc

    def wallet_history(self, *, organization_id: str, vendor_id: str) -> tuple[VendorWalletEvent, ...]:
        with self._guard:
            try:
                return tuple(self._wallet_events[(organization_id, vendor_id)])
            except KeyError as exc:
                raise VendorDirectoryError("Vendor was not found in this organization.") from exc

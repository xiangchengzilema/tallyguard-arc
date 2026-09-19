from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tallyguard.models import Vendor
from tallyguard.vendors import (
    VendorDirectory,
    VendorDirectoryError,
    WalletEventType,
    WalletVerificationMethod,
)


WALLET_1 = "0x1111111111111111111111111111111111111111"
WALLET_2 = "0x2222222222222222222222222222222222222222"


def vendor() -> Vendor:
    return Vendor(
        id="vendor-1",
        organization_id="org-1",
        legal_name="Acme Data LLC",
        approved_wallet_address=WALLET_1,
        autopay_limit=Decimal("2500"),
    )


def test_verified_wallet_history_is_append_only():
    directory = VendorDirectory()
    timestamp = datetime(2026, 9, 20, tzinfo=timezone.utc)
    directory.onboard(
        vendor(),
        verification_method=WalletVerificationMethod.SIGNED_CHALLENGE,
        verification_reference="challenge-001",
        verified_by_user_id="user-finance-1",
        verified_at=timestamp,
    )
    updated = directory.replace_wallet(
        organization_id="org-1",
        vendor_id="vendor-1",
        expected_current_wallet=WALLET_1,
        new_wallet=WALLET_2,
        verification_method=WalletVerificationMethod.OUT_OF_BAND_CALL,
        verification_reference="call-ticket-204",
        verified_by_user_id="user-finance-2",
        verified_at=timestamp,
    )

    history = directory.wallet_history(organization_id="org-1", vendor_id="vendor-1")
    assert updated.approved_wallet_address == WALLET_2
    assert [event.event_type for event in history] == [WalletEventType.VERIFIED, WalletEventType.REPLACED]
    assert history[1].previous_wallet_address == WALLET_1
    assert history[1].verification_reference == "call-ticket-204"


def test_stale_wallet_change_is_rejected():
    directory = VendorDirectory()
    directory.onboard(
        vendor(),
        verification_method=WalletVerificationMethod.MANUAL_REVIEW,
        verification_reference="ticket-1",
        verified_by_user_id="user-1",
    )
    with pytest.raises(VendorDirectoryError, match="changed since"):
        directory.replace_wallet(
            organization_id="org-1",
            vendor_id="vendor-1",
            expected_current_wallet=WALLET_2,
            new_wallet="0x3333333333333333333333333333333333333333",
            verification_method=WalletVerificationMethod.MANUAL_REVIEW,
            verification_reference="ticket-2",
            verified_by_user_id="user-1",
        )


def test_vendor_lookup_is_tenant_scoped():
    directory = VendorDirectory()
    directory.onboard(
        vendor(),
        verification_method=WalletVerificationMethod.MANUAL_REVIEW,
        verification_reference="ticket-1",
        verified_by_user_id="user-1",
    )
    with pytest.raises(VendorDirectoryError, match="not found"):
        directory.get(organization_id="org-2", vendor_id="vendor-1")


def test_wallet_verification_reference_is_required():
    directory = VendorDirectory()
    with pytest.raises(VendorDirectoryError, match="reference"):
        directory.onboard(
            vendor(),
            verification_method=WalletVerificationMethod.MANUAL_REVIEW,
            verification_reference="",
            verified_by_user_id="user-1",
        )

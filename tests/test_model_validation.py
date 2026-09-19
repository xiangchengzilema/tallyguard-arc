from datetime import date
from decimal import Decimal

import pytest

from tallyguard.models import DeliveryEvidence, Invoice, TreasurySnapshot, Vendor
from tallyguard.policy import Policy


WALLET = "0x1111111111111111111111111111111111111111"


def invoice(**changes):
    values = {
        "id": "invoice-1",
        "organization_id": "org-1",
        "vendor_id": "vendor-1",
        "invoice_number": "INV-1",
        "currency": "USDC",
        "amount": Decimal("1"),
        "due_date": date(2026, 10, 8),
        "payment_wallet_address": WALLET,
        "source_document_hash": "a" * 64,
    }
    values.update(changes)
    return Invoice(**values)


@pytest.mark.parametrize("amount", ["0", "-1"])
def test_invoice_amount_must_be_positive(amount):
    with pytest.raises(ValueError, match="positive"):
        invoice(amount=Decimal(amount))


def test_invoice_requires_real_hash_and_evm_wallet_shape():
    with pytest.raises(ValueError, match="SHA-256"):
        invoice(source_document_hash="not-a-hash")
    with pytest.raises(ValueError, match="20-byte"):
        invoice(payment_wallet_address="0x1234")


def test_vendor_and_policy_limits_cannot_be_negative():
    with pytest.raises(ValueError, match="autopay"):
        Vendor(
            id="vendor-1",
            organization_id="org-1",
            legal_name="Acme",
            approved_wallet_address=WALLET,
            autopay_limit=Decimal("-1"),
        )
    with pytest.raises(ValueError, match="monetary"):
        Policy(
            version="v1",
            organization_id="org-1",
            daily_payment_limit_usdc=Decimal("-1"),
            minimum_cash_reserve_usdc=Decimal("0"),
            maximum_autonomous_payment_usdc=Decimal("1"),
        )


def test_delivery_and_treasury_cannot_contain_negative_value():
    with pytest.raises(ValueError, match="Delivered"):
        DeliveryEvidence(
            id="delivery-1",
            organization_id="org-1",
            purchase_order_id="po-1",
            delivered_value=Decimal("-1"),
            source_document_hash="a" * 64,
        )
    with pytest.raises(ValueError, match="balances"):
        TreasurySnapshot(
            organization_id="org-1",
            available_usdc=Decimal("-1"),
            spent_today_usdc=Decimal("0"),
        )

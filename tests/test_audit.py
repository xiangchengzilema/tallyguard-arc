from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from tallyguard.audit import AuditChain


def test_audit_chain_verifies():
    chain = AuditChain()
    timestamp = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)
    chain.append(
        aggregate_type="invoice",
        aggregate_id="invoice-1",
        event_type="INVOICE_INGESTED",
        payload={"amount": Decimal("1200.00")},
        created_at=timestamp,
    )
    chain.append(
        aggregate_type="invoice",
        aggregate_id="invoice-1",
        event_type="POLICY_EVALUATED",
        payload={"action": "PAY", "policy_version": "2026-09-19.1"},
        created_at=timestamp,
    )
    assert chain.verify()
    assert chain.events[1].previous_hash == chain.events[0].event_hash


def test_audit_chain_detects_payload_tampering():
    chain = AuditChain()
    timestamp = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)
    chain.append(
        aggregate_type="invoice",
        aggregate_id="invoice-1",
        event_type="PAYMENT_CONFIRMED",
        payload={"amount": "1200.00", "tx_hash": "0xabc"},
        created_at=timestamp,
    )
    chain._events[0] = replace(chain._events[0], payload={"amount": "9999.00", "tx_hash": "0xabc"})
    assert not chain.verify()


from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from time import perf_counter

import pytest

from tallyguard.network import ArcNetwork, ArcNetworkConfig, MainnetSafetyError
from tallyguard.policy import Decision, DecisionAction
from tallyguard.settlement import (
    PaymentIntent,
    ProviderSubmission,
    SettlementDenied,
    SettlementService,
    SimulatedArcAdapter,
)


WALLET = "0x1111111111111111111111111111111111111111"


def decision(action: DecisionAction = DecisionAction.PAY) -> Decision:
    return Decision(
        action=action,
        policy_version="v1",
        invoice_fingerprint="a" * 64,
        rule_results=(),
    )


def intent(network: ArcNetwork = ArcNetwork.TESTNET, **changes) -> PaymentIntent:
    values = {
        "id": "payment-1",
        "organization_id": "org-1",
        "invoice_id": "invoice-1",
        "decision_id": "decision-1",
        "recipient": WALLET,
        "amount_usdc": Decimal("12.34"),
        "network": network,
        "idempotency_key": "org-1:invoice-1:v1",
    }
    values.update(changes)
    return PaymentIntent(**values)


def test_non_pay_decision_cannot_settle():
    service = SettlementService(
        config=ArcNetworkConfig.for_network(ArcNetwork.TESTNET),
        adapter=SimulatedArcAdapter(),
    )
    with pytest.raises(SettlementDenied):
        service.execute(intent=intent(), decision=decision(DecisionAction.HOLD))


def test_duplicate_concurrent_requests_submit_only_once():
    adapter = SimulatedArcAdapter()
    service = SettlementService(
        config=ArcNetworkConfig.for_network(ArcNetwork.TESTNET),
        adapter=adapter,
    )
    payment = intent()
    with ThreadPoolExecutor(max_workers=20) as executor:
        receipts = list(executor.map(lambda _: service.execute(intent=payment, decision=decision()), range(100)))
    assert adapter.submission_count == 1
    assert len({receipt.transaction_hash for receipt in receipts}) == 1


def test_slow_provider_duplicate_storm_still_submits_only_once():
    adapter = SimulatedArcAdapter()
    adapter.arm_delay(
        organization_id="org-1",
        invoice_id="invoice-1",
        delay_seconds=0.05,
    )
    service = SettlementService(
        config=ArcNetworkConfig.for_network(ArcNetwork.TESTNET),
        adapter=adapter,
    )
    payment = intent()
    started = perf_counter()
    with ThreadPoolExecutor(max_workers=20) as executor:
        receipts = list(
            executor.map(
                lambda _: service.execute(intent=payment, decision=decision()),
                range(100),
            )
        )

    assert perf_counter() - started >= 0.04
    assert adapter.delayed_attempt_count == 1
    assert adapter.submission_count == 1
    assert len({receipt.transaction_hash for receipt in receipts}) == 1


def test_simulated_provider_delay_must_be_non_negative():
    adapter = SimulatedArcAdapter()
    with pytest.raises(ValueError, match="cannot be negative"):
        adapter.arm_delay(
            organization_id="org-1",
            invoice_id="invoice-1",
            delay_seconds=-0.001,
        )


def test_idempotency_key_cannot_be_reused_for_different_amount():
    service = SettlementService(
        config=ArcNetworkConfig.for_network(ArcNetwork.TESTNET),
        adapter=SimulatedArcAdapter(),
    )
    first = intent()
    service.execute(intent=first, decision=decision())
    with pytest.raises(SettlementDenied):
        service.execute(intent=replace(first, amount_usdc=Decimal("99")), decision=decision())


def test_mainnet_is_locked_without_explicit_runtime_authorization():
    service = SettlementService(
        config=ArcNetworkConfig.for_network(ArcNetwork.MAINNET),
        adapter=SimulatedArcAdapter(),
    )
    with pytest.raises(MainnetSafetyError):
        service.execute(
            intent=intent(ArcNetwork.MAINNET, approval_reference="human-approval-1"),
            decision=decision(),
        )


def test_mainnet_requires_approval_reference_even_when_enabled():
    service = SettlementService(
        config=ArcNetworkConfig.for_network(ArcNetwork.MAINNET),
        adapter=SimulatedArcAdapter(),
        allow_mainnet=True,
    )
    with pytest.raises(MainnetSafetyError):
        service.execute(intent=intent(ArcNetwork.MAINNET), decision=decision())


def test_approved_decision_must_match_payment_approval_reference():
    service = SettlementService(
        config=ArcNetworkConfig.for_network(ArcNetwork.TESTNET),
        adapter=SimulatedArcAdapter(),
    )
    approved = replace(decision(), approval_reference="approval-1")
    with pytest.raises(SettlementDenied, match="approval reference"):
        service.execute(intent=intent(), decision=approved)

    receipt = service.execute(
        intent=intent(approval_reference="approval-1"),
        decision=approved,
    )
    assert receipt.payment_intent_id == "payment-1"


class WrongRecipientAdapter:
    name = "bad-provider"

    def submit(self, payment: PaymentIntent) -> ProviderSubmission:
        return ProviderSubmission(
            provider_reference="bad-1",
            transaction_hash="0x" + "b" * 64,
            recipient="0x2222222222222222222222222222222222222222",
            amount_usdc=payment.amount_usdc,
            network=payment.network,
            block_number=1,
        )


def test_reconciliation_rejects_wrong_recipient():
    service = SettlementService(
        config=ArcNetworkConfig.for_network(ArcNetwork.TESTNET),
        adapter=WrongRecipientAdapter(),
    )
    with pytest.raises(SettlementDenied):
        service.execute(intent=intent(), decision=decision())

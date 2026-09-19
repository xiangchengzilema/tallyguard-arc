from decimal import Decimal
from types import SimpleNamespace

import pytest

from tallyguard.circle_arc import (
    ArcRpcClient,
    CircleArcAdapter,
    CircleConfigurationError,
    CircleSdkGateway,
    CircleTransaction,
    CircleTransactionState,
    CircleWalletSnapshot,
    TRANSFER_TOPIC,
)
from tallyguard.network import ArcNetwork, ArcNetworkConfig
from tallyguard.settlement import PaymentIntent, SettlementDenied


RECIPIENT = "0x1111111111111111111111111111111111111111"
TX_HASH = "0x" + "a" * 64
UUID4 = "123e4567-e89b-42d3-a456-426614174000"


def payment(**changes) -> PaymentIntent:
    values = {
        "id": "payment-1",
        "organization_id": "org-1",
        "invoice_id": "invoice-1",
        "decision_id": "decision-1",
        "recipient": RECIPIENT,
        "amount_usdc": Decimal("1.25"),
        "network": ArcNetwork.TESTNET,
        "idempotency_key": UUID4,
    }
    values.update(changes)
    return PaymentIntent(**values)


def circle_result(
    state: CircleTransactionState,
    **changes,
) -> CircleTransaction:
    values = {
        "id": "circle-tx-1",
        "state": state,
        "blockchain": ArcNetwork.TESTNET.value,
        "destination_address": RECIPIENT,
        "amounts": (Decimal("1.25"),),
        "transaction_hash": TX_HASH,
        "block_height": 42,
    }
    values.update(changes)
    return CircleTransaction(**values)


class FakeCircle:
    def __init__(self, *results: CircleTransaction) -> None:
        self.results = list(results)
        self.created = 0
        self.polled = 0

    def create_usdc_transfer(self, intent: PaymentIntent) -> CircleTransaction:
        self.created += 1
        return circle_result(CircleTransactionState.INITIATED)

    def get_transaction(self, transaction_id: str) -> CircleTransaction:
        self.polled += 1
        return self.results.pop(0)


def valid_rpc_transport(method: str, params: list[object]):
    if method == "eth_chainId":
        return hex(5_042_002)
    if method == "eth_getTransactionByHash":
        return {"hash": TX_HASH, "to": "0x3600000000000000000000000000000000000000"}
    if method == "eth_getTransactionReceipt":
        return {
            "transactionHash": TX_HASH,
            "status": "0x1",
            "blockNumber": hex(42),
            "logs": [
                {
                    "address": "0x3600000000000000000000000000000000000000",
                    "topics": [
                        TRANSFER_TOPIC,
                        "0x" + "2" * 64,
                        "0x" + RECIPIENT[2:].rjust(64, "0"),
                    ],
                    "data": hex(1_250_000),
                }
            ],
        }
    raise AssertionError(f"Unexpected RPC method {method}")


def adapter(circle: FakeCircle, **changes) -> CircleArcAdapter:
    config = ArcNetworkConfig.for_network(ArcNetwork.TESTNET)
    values = {
        "config": config,
        "circle": circle,
        "arc_rpc": ArcRpcClient(config=config, transport=valid_rpc_transport),
        "max_transfer_usdc": Decimal("10"),
        "max_poll_attempts": 4,
        "poll_interval_seconds": 0,
        "sleeper": lambda _: None,
    }
    values.update(changes)
    return CircleArcAdapter(**values)


def test_circle_transfer_waits_for_complete_then_verifies_arc_receipt():
    circle = FakeCircle(
        circle_result(CircleTransactionState.INITIATED),
        circle_result(CircleTransactionState.CONFIRMED),
        circle_result(CircleTransactionState.COMPLETE),
    )

    submission = adapter(circle).submit(payment())

    assert circle.created == 1
    assert circle.polled == 3
    assert submission.provider_reference == "circle-tx-1"
    assert submission.transaction_hash == TX_HASH
    assert submission.block_number == 42
    assert submission.amount_usdc == Decimal("1.25")


@pytest.mark.parametrize(
    "state",
    [
        CircleTransactionState.STUCK,
        CircleTransactionState.FAILED,
        CircleTransactionState.DENIED,
        CircleTransactionState.CANCELLED,
    ],
)
def test_circle_terminal_failure_states_fail_closed(state):
    circle = FakeCircle(circle_result(state, error_reason="provider refused"))

    with pytest.raises(SettlementDenied, match="provider refused"):
        adapter(circle).submit(payment())


def test_circle_polling_timeout_does_not_claim_payment_complete():
    pending = [circle_result(CircleTransactionState.SENT) for _ in range(2)]
    circle = FakeCircle(*pending)

    with pytest.raises(SettlementDenied, match="polling limit"):
        adapter(circle, max_poll_attempts=2).submit(payment())


def test_live_adapter_rejects_non_uuid4_before_calling_circle():
    circle = FakeCircle(circle_result(CircleTransactionState.COMPLETE))

    with pytest.raises(SettlementDenied, match="UUID v4"):
        adapter(circle).submit(payment(idempotency_key="invoice-1"))

    assert circle.created == 0


def test_live_adapter_has_independent_hard_transfer_cap():
    circle = FakeCircle(circle_result(CircleTransactionState.COMPLETE))

    with pytest.raises(SettlementDenied, match="hard transfer cap"):
        adapter(circle, max_transfer_usdc=Decimal("1")).submit(payment())

    assert circle.created == 0


@pytest.mark.parametrize(
    "change, message",
    [
        ({"blockchain": ArcNetwork.MAINNET.value}, "wrong network"),
        ({"destination_address": "0x2222222222222222222222222222222222222222"}, "wrong recipient"),
        ({"amounts": (Decimal("1.24"),)}, "different transfer amount"),
        ({"transaction_hash": None}, "without a transaction hash"),
    ],
)
def test_circle_final_record_must_match_the_authorized_intent(change, message):
    circle = FakeCircle(circle_result(CircleTransactionState.COMPLETE, **change))

    with pytest.raises(SettlementDenied, match=message):
        adapter(circle).submit(payment())


def test_arc_rpc_rejects_wrong_chain_even_with_valid_receipt():
    config = ArcNetworkConfig.for_network(ArcNetwork.TESTNET)

    def wrong_chain(method: str, params: list[object]):
        if method == "eth_chainId":
            return hex(5_042)
        return valid_rpc_transport(method, params)

    rpc = ArcRpcClient(config=config, transport=wrong_chain)
    with pytest.raises(SettlementDenied, match="chain mismatch"):
        rpc.confirm_usdc_transfer(
            transaction_hash=TX_HASH,
            recipient=RECIPIENT,
            amount_usdc=Decimal("1.25"),
        )


def test_arc_rpc_rejects_receipt_without_exact_usdc_transfer_log():
    config = ArcNetworkConfig.for_network(ArcNetwork.TESTNET)

    def wrong_amount(method: str, params: list[object]):
        result = valid_rpc_transport(method, params)
        if method == "eth_getTransactionReceipt":
            result["logs"][0]["data"] = hex(1_249_999)
        return result

    rpc = ArcRpcClient(config=config, transport=wrong_amount)
    with pytest.raises(SettlementDenied, match="expected USDC transfer event"):
        rpc.confirm_usdc_transfer(
            transaction_hash=TX_HASH,
            recipient=RECIPIENT,
            amount_usdc=Decimal("1.25"),
        )


def test_circle_and_rpc_block_numbers_must_agree():
    circle = FakeCircle(circle_result(CircleTransactionState.COMPLETE, block_height=43))

    with pytest.raises(SettlementDenied, match="block number"):
        adapter(circle).submit(payment())


def test_usdc_intent_rejects_more_than_six_decimals():
    with pytest.raises(ValueError, match="six decimal"):
        payment(amount_usdc=Decimal("0.0000001"))


def test_circle_configuration_lists_missing_environment_names(monkeypatch):
    for name in ("CIRCLE_WEB3_API_KEY", "CIRCLE_ENTITY_SECRET", "CIRCLE_WALLET_ID"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(CircleConfigurationError) as exc_info:
        CircleSdkGateway.from_env(ArcNetworkConfig.for_network(ArcNetwork.TESTNET))

    assert "CIRCLE_WEB3_API_KEY" in str(exc_info.value)
    assert "CIRCLE_ENTITY_SECRET" in str(exc_info.value)
    assert "CIRCLE_WALLET_ID" in str(exc_info.value)


def test_official_sdk_request_uses_arc_usdc_and_explicit_uuid4():
    sdk = pytest.importorskip("circle.web3.developer_controlled_wallets")

    class FakeTransactions:
        request = None

        def create_developer_transaction_transfer(self, *, create_transfer_transaction_for_developer_request):
            self.request = create_transfer_transaction_for_developer_request
            return SimpleNamespace(
                data=SimpleNamespace(id="circle-provider-id", state=CircleTransactionState.INITIATED)
            )

    transactions = FakeTransactions()
    gateway = object.__new__(CircleSdkGateway)
    gateway._sdk = sdk
    gateway._transactions = transactions
    gateway.wallet_id = "301e9038-e4c3-5a77-a9fe-95fd644f4c85"
    gateway.config = ArcNetworkConfig.for_network(ArcNetwork.TESTNET)

    created = gateway.create_usdc_transfer(payment())
    body = transactions.request.to_dict()

    assert created.id == "circle-provider-id"
    assert body["idempotencyKey"] == UUID4
    assert body["amounts"] == ["1.25"]
    assert body["destinationAddress"] == RECIPIENT
    assert str(body["blockchain"].value) == ArcNetwork.TESTNET.value
    assert body["tokenAddress"] == gateway.config.usdc_contract_address
    assert body["walletId"] == gateway.wallet_id


def test_circle_mainnet_uses_official_arc_identifier_not_internal_label():
    sdk = pytest.importorskip("circle.web3.developer_controlled_wallets")

    class FakeTransactions:
        request = None

        def create_developer_transaction_transfer(self, *, create_transfer_transaction_for_developer_request):
            self.request = create_transfer_transaction_for_developer_request
            return SimpleNamespace(
                data=SimpleNamespace(id="circle-provider-id", state=CircleTransactionState.INITIATED)
            )

    transactions = FakeTransactions()
    gateway = object.__new__(CircleSdkGateway)
    gateway._sdk = sdk
    gateway._transactions = transactions
    gateway.wallet_id = "301e9038-e4c3-5a77-a9fe-95fd644f4c85"
    gateway.config = ArcNetworkConfig.for_network(ArcNetwork.MAINNET)

    gateway.create_usdc_transfer(payment(network=ArcNetwork.MAINNET))

    assert transactions.request.to_dict()["blockchain"].value == "ARC"


def test_circle_wallet_inspection_reads_exact_canonical_usdc_balance():
    class FakeWallets:
        def get_wallet(self, *, id):
            assert id == "wallet-id"
            return SimpleNamespace(
                data=SimpleNamespace(
                    wallet=SimpleNamespace(
                        id=id,
                        address=RECIPIENT,
                        blockchain=SimpleNamespace(value="ARC-TESTNET"),
                        state=SimpleNamespace(value="LIVE"),
                    )
                )
            )

        def list_wallet_balance(self, *, id, include_all, token_address):
            assert id == "wallet-id"
            assert include_all is True
            assert token_address == "0x3600000000000000000000000000000000000000"
            return SimpleNamespace(
                data=SimpleNamespace(
                    token_balances=[
                        SimpleNamespace(
                            amount="2.75",
                            token=SimpleNamespace(token_address=token_address),
                        )
                    ]
                )
            )

    gateway = object.__new__(CircleSdkGateway)
    gateway._wallets = FakeWallets()
    gateway.wallet_id = "wallet-id"
    gateway.config = ArcNetworkConfig.for_network(ArcNetwork.TESTNET)

    assert gateway.inspect_wallet() == CircleWalletSnapshot(
        wallet_id="wallet-id",
        address=RECIPIENT,
        blockchain="ARC-TESTNET",
        state="LIVE",
        usdc_balance=Decimal("2.75"),
    )

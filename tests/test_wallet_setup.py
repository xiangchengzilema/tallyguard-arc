from types import SimpleNamespace

import pytest

from tallyguard.circle_arc import CircleConfigurationError
from tallyguard.wallet_setup import CircleWalletProvisioner


WALLET_SET_ID = "301e9038-e4c3-5a77-a9fe-95fd644f4c85"
WALLET_ID = "401e9038-e4c3-5a77-a9fe-95fd644f4c85"
ADDRESS = "0x1111111111111111111111111111111111111111"
SECOND_WALLET_ID = "501e9038-e4c3-5a77-a9fe-95fd644f4c85"
SECOND_ADDRESS = "0x2222222222222222222222222222222222222222"


class FakeSdk:
    class CreateWalletSetRequest:
        @staticmethod
        def from_dict(value):
            return value

    class CreateWalletRequest:
        @staticmethod
        def from_dict(value):
            return value


class FakeWalletSets:
    def __init__(self):
        self.requests = []

    def create_wallet_set(self, request):
        self.requests.append(request)
        return SimpleNamespace(data=SimpleNamespace(wallet_set=SimpleNamespace(id=WALLET_SET_ID)))


class FakeWallets:
    def __init__(self, *, blockchain="ARC-TESTNET", address=ADDRESS):
        self.blockchain = blockchain
        self.address = address
        self.requests = []

    def create_wallet(self, request):
        self.requests.append(request)
        values = [(WALLET_ID, self.address)]
        if request["count"] == 2:
            values.append((SECOND_WALLET_ID, SECOND_ADDRESS))
        wallets = [
            SimpleNamespace(
                actual_instance=SimpleNamespace(
                    id=wallet_id,
                    address=address,
                    blockchain=SimpleNamespace(value=self.blockchain),
                    state=SimpleNamespace(value="LIVE"),
                    account_type="EOA",
                )
            )
            for wallet_id, address in values
        ]
        return SimpleNamespace(
            data=SimpleNamespace(wallets=wallets)
        )


def provisioner(*, wallets=None):
    value = object.__new__(CircleWalletProvisioner)
    value._sdk = FakeSdk
    value._wallet_sets = FakeWalletSets()
    value._wallets = wallets or FakeWallets()
    return value


def test_provision_creates_testnet_wallet_and_new_wallet_set():
    client = provisioner()

    created = client.provision()

    assert created.wallet_set_id == WALLET_SET_ID
    assert created.wallet_id == WALLET_ID
    assert created.address == ADDRESS
    assert created.blockchain == "ARC-TESTNET"
    assert created.state == "LIVE"
    assert created.account_type == "EOA"
    assert len(client._wallet_sets.requests) == 1
    assert client._wallets.requests[0]["blockchains"] == ["ARC-TESTNET"]
    assert client._wallets.requests[0]["accountType"] == "EOA"
    assert client._wallets.requests[0]["count"] == 1


def test_provision_reuses_explicit_wallet_set_without_creating_one():
    client = provisioner()

    created = client.provision(wallet_set_id=WALLET_SET_ID)

    assert created.wallet_set_id == WALLET_SET_ID
    assert client._wallet_sets.requests == []


def test_provision_acceptance_pair_assigns_distinct_treasury_and_recipient_wallets():
    client = provisioner()

    pair = client.provision_acceptance_pair(wallet_set_id=WALLET_SET_ID)

    assert pair.wallet_set_id == WALLET_SET_ID
    assert pair.treasury.wallet_id == WALLET_ID
    assert pair.treasury.address == ADDRESS
    assert pair.recipient.wallet_id == SECOND_WALLET_ID
    assert pair.recipient.address == SECOND_ADDRESS
    assert client._wallets.requests[0]["count"] == 2


def test_provisioning_rejects_counts_outside_single_or_pair():
    client = provisioner()

    with pytest.raises(ValueError, match="limited"):
        client.provision_wallets(count=3, wallet_set_id=WALLET_SET_ID)


def test_provision_fails_closed_if_circle_returns_wrong_network():
    client = provisioner(wallets=FakeWallets(blockchain="ARC"))

    with pytest.raises(CircleConfigurationError, match="not ARC-TESTNET"):
        client.provision(wallet_set_id=WALLET_SET_ID)


def test_provision_fails_closed_if_circle_returns_invalid_address():
    client = provisioner(wallets=FakeWallets(address="not-an-address"))

    with pytest.raises(CircleConfigurationError, match="invalid wallet address"):
        client.provision(wallet_set_id=WALLET_SET_ID)


def test_from_env_reports_only_missing_variable_names(monkeypatch):
    monkeypatch.delenv("CIRCLE_WEB3_API_KEY", raising=False)
    monkeypatch.delenv("CIRCLE_ENTITY_SECRET", raising=False)

    with pytest.raises(CircleConfigurationError) as exc_info:
        CircleWalletProvisioner.from_env()

    assert "CIRCLE_WEB3_API_KEY" in str(exc_info.value)
    assert "CIRCLE_ENTITY_SECRET" in str(exc_info.value)

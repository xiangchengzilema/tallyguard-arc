"""2026-09-30 audit regressions: isolated SQLite and simulator, no network."""
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json

import pytest

from tallyguard.api import create_app
from tallyguard.auth import Principal, Role
from tallyguard.models import TreasurySnapshot
from tallyguard.network import ArcNetwork, ArcNetworkConfig
from tallyguard.persistence import SettlementExecutionBlocked
from tallyguard.settlement import PaymentIntent

WALLET = "0x1111111111111111111111111111111111111111"
NEW_WALLET = "0x4444444444444444444444444444444444444444"


@pytest.fixture
def runtime(tmp_path):
    app = create_app(database_path=tmp_path / "audit.sqlite3", testing=True)
    client = app.test_client()
    roles = {role: {"Authorization": "Bearer " + client.post(
        "/api/demo/session", json={"role": role},
    ).get_json()["access_token"]} for role in ("admin", "operator", "approver")}
    yield app, client, roles
    app.extensions["tallyguard_repository"].close()


def scenario(client, roles):
    r = client.post("/api/demo/scenarios/clean-payment/run", headers=roles["operator"])
    assert r.status_code == 200, r.get_json()
    return r.get_json()


@pytest.mark.parametrize("rotate_back", [False, True])
def test_wallet_rotation_invalidates_unsubmitted_pay_decision(runtime, rotate_back):
    app, client, roles = runtime
    run = scenario(client, roles)
    for old, new in [(WALLET, NEW_WALLET)] + ([(NEW_WALLET, WALLET)] if rotate_back else []):
        r = client.patch("/api/vendors/vendor-acme/wallet", headers=roles["operator"], json={
            "expected_current_wallet": old, "new_wallet": new,
            "verification_method": "MANUAL_REVIEW", "verification_reference": "audit-test",
        })
        assert r.status_code == 200
    r = client.post(f"/api/invoices/{run['invoice']['id']}/settle", headers=roles["approver"],
                    json={"decision_id": run["decision"]["id"]})
    assert r.status_code == 409, r.get_json()
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 0


def test_existing_receipt_remains_readable_after_wallet_rotation(runtime):
    app, client, roles = runtime
    run = scenario(client, roles)
    path = f"/api/invoices/{run['invoice']['id']}/settle"
    body = {"decision_id": run["decision"]["id"]}
    first = client.post(path, headers=roles["approver"], json=body)
    assert first.status_code == 200
    assert client.patch("/api/vendors/vendor-acme/wallet", headers=roles["operator"], json={
        "expected_current_wallet": WALLET, "new_wallet": NEW_WALLET,
        "verification_method": "MANUAL_REVIEW", "verification_reference": "audit-test",
    }).status_code == 200
    second = client.post(path, headers=roles["approver"], json=body)
    assert second.status_code == 200
    assert second.get_json()["payment"]["receipt"] == first.get_json()["payment"]["receipt"]
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 1


def test_demo_preserves_admin_kill_switch_and_treasury(runtime):
    app, client, roles = runtime
    scenario(client, roles)
    repo = app.extensions["tallyguard_repository"]
    policy = replace(repo.active_policy(organization_id="demo-org").policy, version="admin-lock", kill_switch_enabled=True)
    repo.activate_policy(policy, activated_by_user_id="demo-admin")
    before = repo.latest_treasury_snapshot(organization_id="demo-org")
    run = scenario(client, roles)
    assert run["decision"]["final_action"] != "PAY"
    assert repo.active_policy(organization_id="demo-org").policy == policy
    assert repo.latest_treasury_snapshot(organization_id="demo-org") == before


@pytest.mark.parametrize("path", ["/api/demo/scenarios/clean-payment/run", "/api/demo/autonomy-showcase", "/api/treasury/snapshots"])
def test_live_adapter_rejects_demo_mutations_even_in_testing_mode(tmp_path, path):
    class LiveAdapter:
        name = "circle-developer-wallets+arc-rpc"
        def submit(self, intent):
            pytest.fail("This regression must not send funds")
    app = create_app(database_path=tmp_path / "live.sqlite3", testing=True, settlement_adapter=LiveAdapter(),
                     settlement_config=ArcNetworkConfig.for_network(ArcNetwork.TESTNET))
    token, _ = app.extensions["tallyguard_authenticator"].issue_session(
        Principal("demo-operator", "demo-org", (Role.FINANCE_OPERATOR,)))
    r = app.test_client().post(path, headers={"Authorization": "Bearer " + token}, json={
        "available_usdc": "999999", "spent_today_usdc": "0", "source_reference": "unverified",
    })
    assert r.status_code == 409
    assert app.extensions["tallyguard_repository"].list_invoices(organization_id="demo-org").items == ()
    app.extensions["tallyguard_repository"].close()


def test_pending_reservation_survives_new_balance_snapshot(runtime):
    app, client, roles = runtime
    runs = [scenario(client, roles), scenario(client, roles)]
    repo = app.extensions["tallyguard_repository"]
    def intent(index):
        run = runs[index]
        return PaymentIntent(id=f"reserved-{index}", organization_id="demo-org", invoice_id=run["invoice"]["id"],
            decision_id=run["decision"]["id"], recipient=WALLET, amount_usdc=Decimal("1200"),
            network=ArcNetwork.TESTNET, idempotency_key=f"key-{index}", approval_reference=f"approval-{index}")
    repo.create_or_get_payment_intent(intent(0), enforce_active_controls=True)
    repo.record_treasury_snapshot(TreasurySnapshot(organization_id="demo-org", available_usdc=Decimal("5000"),
        spent_today_usdc=Decimal("0")), source_reference="fresh-with-first-transfer-pending", recorded_by_user_id="demo-operator")
    capacity = repo.settlement_capacity(organization_id="demo-org")
    assert capacity.committed_since_snapshot_usdc == Decimal("1200")
    assert capacity.effective_available_usdc == Decimal("3800")
    with pytest.raises(SettlementExecutionBlocked) as error:
        repo.create_or_get_payment_intent(intent(1), enforce_active_controls=True)
    assert error.value.control_code == "MINIMUM_RESERVE_BREACH"
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 0


def test_confirmed_receipt_is_not_reserved_twice_after_balance_refresh(runtime):
    app, client, roles = runtime
    run = scenario(client, roles)
    assert client.post(f"/api/invoices/{run['invoice']['id']}/settle", headers=roles["approver"],
                       json={"decision_id": run["decision"]["id"]}).status_code == 200
    repo = app.extensions["tallyguard_repository"]
    assert repo.settlement_capacity(organization_id="demo-org").committed_since_snapshot_usdc == Decimal("1200")
    repo.record_treasury_snapshot(TreasurySnapshot(organization_id="demo-org", available_usdc=Decimal("8800"),
        spent_today_usdc=Decimal("1200")), source_reference="post-confirmation-balance", recorded_by_user_id="demo-operator")
    capacity = repo.settlement_capacity(organization_id="demo-org")
    assert capacity.committed_since_snapshot_usdc == 0
    assert capacity.effective_available_usdc == Decimal("8800")


def documents():
    return {
        "INVOICE": {"invoice_id": "original", "vendor_id": "vendor-acme", "invoice_number": "AUDIT-001",
            "currency": "USDC", "amount": "70", "due_date": "2026-10-08", "payment_wallet_address": WALLET},
        "PURCHASE_ORDER": {"purchase_order_id": "audit-po", "vendor_id": "vendor-acme", "po_number": "AUDIT-PO", "currency": "USDC", "authorized_amount": "60"},
        "DELIVERY": {"delivery_id": "audit-delivery", "purchase_order_id": "audit-po", "delivered_value": "70"},
    }


def create(client, roles, docs, parent=None, invoice_id="original"):
    source = json.dumps(docs["INVOICE"]).encode()
    inv = docs["INVOICE"]
    body = {"id": invoice_id, "vendor_id": inv["vendor_id"], "invoice_number": inv["invoice_number"],
        "currency": "USDC", "amount": inv["amount"], "due_date": inv["due_date"],
        "payment_wallet_address": inv["payment_wallet_address"], "source_document_hash": sha256(source).hexdigest()}
    if parent:
        body["supersedes_invoice_id"] = parent
    return client.post("/api/invoices", headers=roles["operator"], json=body)


def upload(client, roles, invoice_id, kind, values):
    fields = [{"name": name, "raw_value": value,
        "normalized_value": invoice_id if name == "invoice_id" else value,
        "method": "MANUAL" if name == "invoice_id" and value != invoice_id else "JSON",
        "confidence": "1", "source": {"json_pointer": "/" + name}} for name, value in values.items()]
    return client.post(f"/api/invoices/{invoice_id}/evidence", headers=roles["operator"], data={
        "evidence_type": kind, "fields": json.dumps(fields),
        "file": (BytesIO(json.dumps(values).encode()), kind.lower()+".json", "application/json"),
    })


def held_original(client, roles):
    scenario(client, roles)
    docs = documents()
    assert create(client, roles, docs).status_code == 201
    for kind, values in docs.items():
        r = upload(client, roles, "original", kind, values)
        assert r.status_code == 201, r.get_json()
    result = client.post("/api/invoices/original/evaluate", headers=roles["operator"])
    assert result.get_json()["decision"]["final_action"] == "HOLD", result.get_json()
    return docs


def test_correction_reuses_source_bytes_preserves_parent_and_reaches_receipt(runtime):
    app, client, roles = runtime
    docs = held_original(client, roles)
    parent = client.get("/api/invoices/original", headers=roles["operator"]).get_json()["invoice"]
    docs["PURCHASE_ORDER"]["authorized_amount"] = "70"
    r = create(client, roles, docs, parent="original")
    assert r.status_code == 201, r.get_json()
    child = r.get_json()["invoice"]
    assert child["id"] != "original" and child["supersedes_invoice_id"] == "original"
    assert create(client, roles, docs, parent="original").get_json()["invoice"]["id"] == child["id"]
    for kind, values in docs.items():
        r = upload(client, roles, child["id"], kind, values)
        assert r.status_code == 201, r.get_json()
    r = client.post(f"/api/invoices/{child['id']}/evaluate?auto_settle=true", headers=roles["operator"])
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["invoice"]["status"] == "RECONCILED", r.get_json()
    retained = client.get("/api/invoices/original", headers=roles["operator"]).get_json()["invoice"]
    assert retained == {**parent, "superseded_by_invoice_id": child["id"]}
    overview = app.extensions["tallyguard_repository"].operations_overview(organization_id="demo-org")
    assert "original" not in {item.invoice.id for item in overview.work_queue}
    assert overview.blocked_exposure_usdc == 0
    assert app.extensions["tallyguard_settlement_adapter"].submission_count == 1
    assert create(client, roles, docs, parent=child["id"]).status_code == 409
    # No second branch with altered invoice values off the sealed parent.
    docs["INVOICE"]["amount"] = "69"
    assert create(client, roles, docs, parent="original").status_code == 409


def test_partial_upload_is_idempotent_and_different_attachment_is_rejected(runtime):
    app, client, roles = runtime
    scenario(client, roles)
    docs = documents()
    assert create(client, roles, docs).status_code == 201
    first = upload(client, roles, "original", "INVOICE", docs["INVOICE"])
    again = upload(client, roles, "original", "INVOICE", docs["INVOICE"])
    assert again.status_code == 201
    assert first.get_json()["evidence"]["id"] == again.get_json()["evidence"]["id"]
    for kind in ("PURCHASE_ORDER", "DELIVERY"):
        assert upload(client, roles, "original", kind, docs[kind]).status_code == 201
    different = {**docs["PURCHASE_ORDER"], "authorized_amount": "99"}
    assert upload(client, roles, "original", "PURCHASE_ORDER", different).status_code == 409
    assert len(app.extensions["tallyguard_repository"].list_invoice_evidence(organization_id="demo-org", invoice_id="original")) == 3


def test_revision_cannot_cross_workspace_or_reuse_unrelated_evidence(runtime):
    app, client, roles = runtime
    docs = held_original(client, roles)
    # Use a newly issued principal in an unrelated tenant, not a guessed ID.
    repo = app.extensions["tallyguard_repository"]
    repo.create_organization(organization_id="other-org", name="Other")
    repo.create_user(organization_id="other-org", user_id="other-op", display_name="Other", roles=(Role.FINANCE_OPERATOR.value,))
    token, _ = app.extensions["tallyguard_authenticator"].issue_session(Principal("other-op", "other-org", (Role.FINANCE_OPERATOR,)))
    foreign_roles = {"operator": {"Authorization": "Bearer " + token}}
    assert create(client, foreign_roles, docs, parent="original").status_code == 404
    assert create(client, roles, docs, invoice_id="unrelated").status_code == 201
    assert upload(client, roles, "unrelated", "INVOICE", docs["INVOICE"]).status_code == 409

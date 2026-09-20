import csv
from hashlib import sha256
from io import StringIO

from tallyguard.api import _csv_cell, create_app


def headers(token: str, correlation_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Correlation-ID": correlation_id,
    }


def test_accounting_ledger_exports_reconciled_payment_with_content_hash(tmp_path):
    app = create_app(database_path=tmp_path / "ledger.sqlite3", testing=True)
    client = app.test_client()
    operator = client.post("/api/demo/session", json={"role": "operator"}).get_json()[
        "access_token"
    ]
    approver = client.post("/api/demo/session", json={"role": "approver"}).get_json()[
        "access_token"
    ]
    auditor = client.post("/api/demo/session", json={"role": "auditor"}).get_json()[
        "access_token"
    ]
    run = client.post(
        "/api/demo/scenarios/clean-payment/run",
        headers=headers(operator, "ledger-run"),
    ).get_json()
    settled = client.post(
        f"/api/invoices/{run['invoice']['id']}/settle",
        json={"decision_id": run["decision"]["id"]},
        headers=headers(approver, "ledger-settle"),
    )
    assert settled.status_code == 200

    first = client.get(
        "/api/accounting/ledger.csv",
        headers=headers(auditor, "ledger-export-1"),
    )
    second = client.get(
        "/api/accounting/ledger.csv",
        headers=headers(auditor, "ledger-export-2"),
    )

    assert first.status_code == 200
    assert first.headers["Content-Disposition"] == 'attachment; filename="tallyguard-ledger.csv"'
    assert first.headers["X-TallyGuard-Ledger-Rows"] == "1"
    assert first.headers["X-TallyGuard-Ledger-SHA256"] == sha256(first.data).hexdigest()
    assert second.data == first.data
    parsed = list(csv.DictReader(StringIO(first.data.decode("utf-8-sig"))))
    assert len(parsed) == 1
    assert parsed[0]["invoice_id"] == run["invoice"]["id"]
    assert parsed[0]["decision_id"] == run["decision"]["id"]
    assert parsed[0]["amount_usdc"] == run["invoice"]["amount"]
    assert parsed[0]["network"] == "ARC-TESTNET"
    assert parsed[0]["settlement_status"] == "CONFIRMED"
    assert parsed[0]["transaction_hash"].startswith("0x")

    anonymous = client.get("/api/accounting/ledger.csv")
    assert anonymous.status_code == 401
    app.extensions["tallyguard_repository"].close()


def test_csv_cell_neutralizes_spreadsheet_formula_prefixes():
    assert _csv_cell("=HYPERLINK(\"https://attacker.invalid\")") == (
        "'=HYPERLINK(\"https://attacker.invalid\")"
    )
    assert _csv_cell("  +SUM(1,1)") == "'  +SUM(1,1)"
    assert _csv_cell("INV-100") == "INV-100"

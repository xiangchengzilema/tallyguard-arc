"""One-off, guarded Arc Testnet browser acceptance. Never run against mainnet.

Usage: provide Circle credentials in the process environment, then run with
--execute-testnet-transfer. This script does not load secrets from a sibling
project or persist operator sessions. It sends exactly one 0.01 USDC request
through the user, finance, approval, and receipt screens.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
OUTPUT = ROOT / "artifacts" / "browser-acceptance"
PORT = 18081
BASE = f"http://127.0.0.1:{PORT}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-testnet-transfer", action="store_true")
    parser.add_argument("--gate-only", action="store_true")
    parser.add_argument("--inspect-existing-report")
    parser.add_argument("--recipient")
    args = parser.parse_args()
    if sum((args.execute_testnet_transfer, args.gate_only, bool(args.inspect_existing_report))) != 1:
        raise SystemExit("Choose exactly one of --execute-testnet-transfer, --gate-only, or --inspect-existing-report.")
    if args.execute_testnet_transfer and (not args.recipient or not args.recipient.startswith("0x") or len(args.recipient) != 42):
        raise SystemExit("A previously verified Arc Testnet recipient is required.")
    if args.execute_testnet_transfer:
        for previous_path in OUTPUT.glob("*/report.json"):
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            if previous.get("transaction_hash") and previous.get("recipient", "").lower() == args.recipient.lower():
                raise SystemExit("A completed browser acceptance already exists for this recipient; no repeat transfer attempted.")
    for name in ("CIRCLE_WEB3_API_KEY", "CIRCLE_ENTITY_SECRET", "CIRCLE_WALLET_ID"):
        if not os.getenv(name):
            raise SystemExit(f"Missing {name}; no transfer attempted.")
    if not (ROOT / "web" / "dist" / "index.html").exists():
        raise SystemExit("Build web/dist before this acceptance run.")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUTPUT / run_id
    out.mkdir(parents=True, exist_ok=False)
    prior = None
    if args.inspect_existing_report:
        prior_path = Path(args.inspect_existing_report).resolve()
        if not prior_path.is_relative_to(OUTPUT.resolve()) or prior_path.name != "report.json":
            raise SystemExit("Only a prior local browser-acceptance report may be inspected.")
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        database = prior_path.parent / "acceptance.sqlite3"
        if not database.exists() or not prior.get("transaction_hash"):
            raise SystemExit("The prior confirmed local database and receipt are required.")
    else:
        database = out / "acceptance.sqlite3"
    report: dict[str, object] = {
        "run_id": run_id,
        "network": "ARC-TESTNET",
        "requested_amount_usdc": "0.01",
        "recipient": args.recipient,
        "stages": [],
    }

    def stage(name: str) -> None:
        report["stages"].append(name)
        (out / "progress.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(name, flush=True)

    env = os.environ.copy()
    env.update({
        "TALLYGUARD_MODE": "circle",
        "TALLYGUARD_ARC_NETWORK": "ARC-TESTNET",
        "TALLYGUARD_ALLOW_MAINNET": "false",
        "TALLYGUARD_MAX_TRANSFER_USDC": "0.01",
        "TALLYGUARD_DATABASE_PATH": str(database),
        "TALLYGUARD_ENABLE_DEMO_SESSIONS": "false",
        "PORT": str(PORT),
    })
    provision = subprocess.run(
        [str(PYTHON), "-m", "tallyguard.operator_setup", "--database", str(database),
         "--organization-id", "browser-acceptance", "--organization-name", "Browser Acceptance",
         "--session-hours", "1",
         "--confirm", "PROVISION-TALLYGUARD-OPERATORS"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
    )
    role_fields = {
        "ADMIN": "admin", "FINANCE_OPERATOR": "operator",
        "APPROVER": "approver", "AUDITOR": "auditor",
    }
    sessions = {
        role_fields[entry["role"]]: entry["bearer_token"]
        for entry in json.loads(provision.stdout)["sessions"]
    }
    stage("private role-separated sessions provisioned")
    server = subprocess.Popen(
        [str(PYTHON), "-m", "tallyguard.api"], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            if server.poll() is not None:
                raise RuntimeError("Local API exited before readiness.")
            try:
                with urlopen(f"{BASE}/api/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError("Local API did not become healthy.")
        with urlopen(f"{BASE}/api/readiness", timeout=3) as response:
            readiness = json.load(response)
        print(f"Readiness: {readiness['settlement_mode']}; demo_sessions={readiness['demo_sessions_enabled']}", flush=True)
        if readiness["settlement_mode"] != "circle-live" or readiness["demo_sessions_enabled"]:
            raise RuntimeError("Refusing browser acceptance against a simulated or public-demo server.")
        stage("live local API ready")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page.set_default_timeout(30000)
            page.goto(f"{BASE}/#login-finance", wait_until="networkidle")
            browser_readiness = page.evaluate("fetch('/api/readiness').then(r => r.json())")
            print(f"Browser readiness: {browser_readiness['settlement_mode']}; demo_sessions={browser_readiness['demo_sessions_enabled']}", flush=True)
            page.get_by_role("button", name="Enter finance backend").click()
            page.screenshot(path=str(out / "00-before-access.png"), full_page=True)
            page.locator("#operator-access-title").wait_for()
            assert page.locator(".environment-pill").inner_text() == "LIVE USDC"
            assert page.locator(".premium-sidenav__network").inner_text().find("CIRCLE + RPC LIVE") >= 0
            stage("live access gate labels verified")
            for role, token in sessions.items():
                page.locator(f"#operator-access-{role}").fill(token)
            page.get_by_role("button", name="Open private operations").click()
            page.get_by_text("Connect the role-separated finance team.").wait_for(state="hidden", timeout=30000)
            stage("browser connected to private live workspace")

            if prior:
                page.get_by_role("button", name="Paid (1)").wait_for(timeout=30000)
                page.screenshot(path=str(out / "00-finance-paid-list.png"), full_page=True)
                page.locator(".payables-table .payable-link").filter(has_text=prior["invoice_number"]).click()
                page.get_by_role("button", name="View confirmed receipt").wait_for(timeout=30000)
                assert "Paid on Arc" in page.locator(".review-pager").inner_text()
                assert "PAID" in page.locator(".invoice-parties").inner_text()
                page.screenshot(path=str(out / "01-reopened-finance-receipt.png"), full_page=True)
                stage("persisted finance receipt reopened without a new transfer")
                page.locator(".premium-topbar").get_by_role("button", name="Sign out").click()
                page.goto(f"{BASE}/#login-user", wait_until="domcontentloaded")
                page.get_by_role("button", name="Enter user portal").click()
                page.locator(".requester-receipt").wait_for()
                assert page.locator(".requester-receipt__transaction code").inner_text() == prior["transaction_hash"]
                page.screenshot(path=str(out / "02-reopened-user-receipt.png"), full_page=True)
                stage("persisted requester receipt reopened without a new transfer")
                browser.close()
                return

            page.locator(".premium-topbar").get_by_role("button", name="Sign out").click()
            page.goto(f"{BASE}/#login-user", wait_until="domcontentloaded")
            page.get_by_role("button", name="Enter user portal").click()
            if args.gate_only:
                assert "In this private workspace" in page.locator(".requester-summary").inner_text()
                page.screenshot(path=str(out / "01-private-user-portal.png"), full_page=True)
                stage("private user portal labels verified; no funds moved")
                browser.close()
                return
            page.locator(".requester-heading").get_by_role("button", name="New payment request").click()
            page.get_by_role("button", name="Use sample documents").click()
            page.locator(".evidence-edit-grid").wait_for(timeout=30000)
            for label, value in (
                ("Requested amount", "0.01"),
                ("PO authorized amount", "0.01"),
                ("Delivered value", "0.01"),
                ("Payout wallet", args.recipient),
            ):
                page.locator(".evidence-edit-grid label").filter(has_text=label).locator("input").fill(value)
            page.screenshot(path=str(out / "01-request-reviewed.png"), full_page=True)
            page.get_by_role("button", name="Seal and submit request").click()
            page.locator(".requester-detail h2").wait_for(timeout=120000)
            invoice_number = page.locator(".requester-detail h2").inner_text()
            report["invoice_number"] = invoice_number
            page.screenshot(path=str(out / "02-requester-queued.png"), full_page=True)
            stage("request submitted through user portal")

            page.locator(".requester-topbar").get_by_role("button", name="Sign out").click()
            page.goto(f"{BASE}/#login-finance", wait_until="domcontentloaded")
            page.get_by_role("button", name="Enter finance backend").click()
            page.locator(".payables-table .payable-link").filter(has_text=invoice_number).click()
            page.get_by_role("button", name="Submit for independent approval").click()
            page.get_by_role("button", name="Open approver portal").wait_for(timeout=30000)
            page.screenshot(path=str(out / "03-finance-awaiting-approval.png"), full_page=True)
            stage("finance created independent approval request")

            page.get_by_role("button", name="Open approver portal").click()
            page.locator(".approval-packet h2").filter(has_text=invoice_number).wait_for()
            page.locator(".approval-resolution textarea").fill("Verified three source documents, vendor wallet, and 0.01 USDC Testnet treasury impact.")
            page.get_by_role("button", name="Approve payment").click()
            page.get_by_text("Payment request approved").wait_for(timeout=30000)
            page.screenshot(path=str(out / "04-independent-approved.png"), full_page=True)
            stage("independent finance approver authorized request")

            page.get_by_role("button", name="Back to payment requests").click()
            page.locator(".payables-table .payable-link").filter(has_text=invoice_number).click()
            settle = page.get_by_role("button", name="Settle 0.01 USDC on Arc")
            settle.wait_for(timeout=30000)
            page.screenshot(path=str(out / "05-ready-to-settle.png"), full_page=True)
            settle.click()
            page.get_by_text("Paid 0.01 USDC", exact=False).first.wait_for(timeout=180000)
            page.screenshot(path=str(out / "06-finance-paid.png"), full_page=True)
            stage("Circle transfer and Arc RPC confirmation completed from browser")

            page.locator(".premium-topbar").get_by_role("button", name="Sign out").click()
            page.goto(f"{BASE}/#login-user", wait_until="domcontentloaded")
            page.get_by_role("button", name="Enter user portal").click()
            page.locator(".requester-receipt").wait_for(timeout=30000)
            tx_hash = page.locator(".requester-receipt__transaction code").inner_text()
            explorer = page.locator(".requester-receipt__transaction a").get_attribute("href")
            assert tx_hash.startswith("0x") and len(tx_hash) == 66
            assert explorer == f"https://explorer.testnet.arc.io/tx/{tx_hash}"
            assert "0.01" in page.locator(".requester-receipt").inner_text()
            report.update({"transaction_hash": tx_hash, "explorer_url": explorer, "receipt_visible": True})
            page.screenshot(path=str(out / "07-requester-final-receipt.png"), full_page=True)
            stage("final receipt visible in user portal")
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"REPORT {out / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()

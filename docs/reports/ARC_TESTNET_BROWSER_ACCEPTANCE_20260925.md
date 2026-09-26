# Arc Testnet browser-to-receipt acceptance — 2026-09-25

The private local website completed one real, capped Arc Testnet payment from
the user submission screen through the independent finance approval screen and
the final requester receipt. This was controlled engineering acceptance, not a
customer payment or Mainnet use.

## What was exercised

1. Opened the private `circle-live` website in a fresh Chrome browser and
   connected four short-lived, single-role sessions from one isolated tenant.
2. In the user portal, loaded the three sample source documents and confirmed
   the invoice, purchase order, delivery value, and a previously controlled
   recipient wallet at **0.01 USDC**. The original source bytes were retained
   as evidence; corrected values were recorded as confirmations.
3. Submitted invoice `TG-7E8EF119`. The user page showed the finance handoff
   and no payment before approval.
4. In the finance backend, opened that exact invoice and requested independent
   approval because no-touch settlement was disabled by policy.
5. In the approver portal, recorded a human approval note. Returned to the
   finance invoice screen and clicked **Settle 0.01 USDC on Arc**.
6. The user portal displayed **Paid · receipt ready**, the approver note,
   four completed progress steps, amount paid, network, block, and transaction
   link.

Circle reported completion. The server independently reconciled the exact
canonical USDC Transfer against Arc RPC, and a separate read-only RPC check
after the browser run independently reconfirmed recipient and amount.

- Network: `ARC-TESTNET` (chain ID `5042002`)
- Amount: `0.01 USDC`
- Confirmed block: `63900352`
- Transaction: [`0xe0e51c82…15bfa26c`](https://explorer.testnet.arc.io/tx/0xe0e51c82f7b6ccd901d2af60fa28d3d1118b9071a2aef9e74163969415bfa26c)
- Mainnet runtime authorization: disabled
- Browser report and seven stage screenshots: git-ignored
  `artifacts/browser-acceptance/20260925T081146Z/`
- The guarded browser runner is `tests/manual_live_browser_acceptance.py`.
  It needs explicit `--execute-testnet-transfer`, a previously verified
  recipient, valid environment-only Circle credentials, and caps the adapter
  at `0.01 USDC`. `--gate-only` exercises private access without moving funds.

## UX finding closed during acceptance

The first live-mode entry could render an empty finance page labelled
`SIMULATION` before operator sessions were connected. The access gate now
precedes private pages, preventing a false simulation display. Its header and
side navigation display `LIVE USDC` and `CIRCLE + RPC LIVE`; a separate
gate-only browser pass verified these labels and that the requester summary
uses `private workspace` instead of `demo workspace` in live mode. Screenshots
were visually inspected at a 1440 × 900 desktop viewport.

Reopening the settled invoice surfaced a second usability gap: the finance
table omitted paid records, and review lost its confirmed receipt after a page
reload. The finance table now includes recent paid requests under a `Paid` tab,
excludes them from settlement-ready batches, and reloads the durable payment
through a read-only, role-scoped API. A separate inspection of the same
accepted local database reopened both the finance and requester receipt with
the original transaction hash; **no second transfer was sent**. The finance
review now presents `Paid on Arc` and `PAID` instead of stale pre-settlement
labels. The final read-only screenshots of the paid finance list, reopened
finance review, and requester receipt are in the git-ignored
`artifacts/browser-acceptance/20260925T084119Z/` folder.

## Remaining boundary

This acceptance used a trusted local browser and operator-provisioned
sessions. The public judge deployment is still pending and must remain in
simulation mode. The user/finance portal selector is a presentation and
workflow boundary, **not** yet a production-grade external requester login or
independent browser identity system. Do not present this acceptance as a live
public payment service, Mainnet settlement, real customer traction, or proof
that production identity provisioning is complete.

# Public deployment acceptance — September 26, 2026

Public judge console: https://tallyguard-arc.onrender.com

Public source: https://github.com/xiangchengzilema/tallyguard-arc

Hosting: Render Free Docker web service, Singapore region

Deployment mode: **simulation** on an Arc Testnet-labelled interface; funds movement and Mainnet are disabled.

The public origin was tested from the local Windows host, outside Render, after deployment of commit `2ac4155` reached Live. The latest real-HTTP smoke run at `2026-09-26T14:19:32Z` passed **8/8** checks:

1. Built React judge console served over HTTPS.
2. API health returned `ok`.
3. Readiness confirmed database health, simulation, disabled funds movement, and disabled Mainnet.
4. One isolated workspace issued four distinct role-scoped sessions.
5. Three-way evidence yielded the expected deterministic `PAY` decision.
6. The approver created one confirmed **simulated** receipt and a reconciled invoice.
7. The accounting CSV matched invoice, decision, receipt, row count, and SHA-256 header.
8. All temporary role sessions were revoked.

The report was generated with `tallyguard-deployment-smoke --base-url https://tallyguard-arc.onrender.com` and saved at `artifacts/visual-qa-20260926/remote-linked-samples-smoke.json` (local, ignored). This is **synthetic deployment acceptance**, not customer traction or a real Arc transfer. In one public browser workspace, the requester and finance portals showed the same four preloaded sample invoice IDs and kept actual requester submissions separate. The read-only Arc activity page showed the separately verified synthetic test campaign: **50/50 terminal workflows, 40 confirmed Arc Testnet receipts, 5 declined, 5 held, and 0.40 test USDC transferred**. Each confirmed entry links to its exact Explorer transaction. The public finance workspace remains a simulator with no wallet credentials; it does not perform those 40 transfers. Render Free may spin down during inactivity, so its first request after idle can be slow. Its ephemeral SQLite database is appropriate only for this judge playground, not production persistence. The four sample IDs can change when a new demo workspace is created after reload.

The initial remote smoke attempt falsely reported a ledger mismatch because Render lowercased response-header names; an independent byte-level check showed the header and body SHA-256 were identical. The checker now reads headers case-insensitively. A later attempt timed out at the client's 10-second HTTPS limit even though the server logged HTTP 200; the checker now allows a free-tier wake and reports timeouts clearly. The final full run passed.

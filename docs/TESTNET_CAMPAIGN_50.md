# Controlled 50-agent Arc Testnet campaign

The locked private schedule completed on 2026-09-26. The final durable report
contains **50 terminal workflows: 40 confirmed Arc Testnet transfers of
0.01 test USDC each, five declined, and five held**. All 40 unique transaction
hashes and confirmed amounts match the settlement receipt table; no workflow
remains queued. Total transfer principal was 0.40 test USDC, excluding fees.
The public website exposes a redacted read-only snapshot of these results.
This is synthetic engineering traffic, not customer adoption.

## Exact experiment

- Fifty labelled synthetic agents submit evidence-bound invoice requests at
  unpredictable times over 24 hours. The schedule is fixed and SHA-256 checked
  after it is generated, so restarting the runner does not change due times.
- Twenty clean requests use a finance-enabled `0.01 USDC` no-touch limit and
  settle automatically. Twenty other clean requests exceed their vendor's
  zero autonomous limit, route to a separate approver role, then settle after
  a 2–15 minute review delay. Five are intentionally declined by the approver
  role. Five have insufficient delivery proof and remain on hold.
- At most **40 real Arc Testnet transfers × 0.01 USDC = 0.40 test USDC in
  transfer principal**, plus network fees, may move from the controlled
  treasury. Rejected and held requests do not pay.
- A generated approver-role action is still synthetic test data. It is **not**
  evidence that an independent human reviewed forty requests.
- The recorded consolidation inventory contains **37 confirmed controlled
  recipient wallets**, plus a separate treasury wallet. The user approved
  rotating 50 agents among these 37 recipients. Fifty distinct recipients
  would require additional wallet provisioning first.

## Execution boundary

The runner lives in `src/tallyguard/testnet_campaign.py`. It is locked to
`ARC-TESTNET`, disables Mainnet, disables public demo sessions, caps each
adapter transfer at `0.01 USDC`, requires Circle credentials only from the
process environment, and requires an explicit execution phrase. At startup it
checks the current Circle treasury address and balance against the locked
local inventory, Arc chain ID, and deployed canonical USDC contract. It uses
one tenant, role-separated operator/approver sessions, unique invoices and
source hashes, a `0.40 USDC` campaign cap, and a single-runner filesystem lock.
The backend's durable payment-intent idempotency and onchain receipt
reconciliation remain mandatory. Progress reports contain no bearer sessions
or Circle secrets.

The source files are generated synthetic JSON evidence, not customer invoices.
Each controlled test-wallet vendor is clearly labelled. They appear in the
same finance/requester database if a private local web server is pointed at
the campaign database. Do not expose that Circle-backed server publicly.

## Public product activity view

The website's `#activity` page and home-page activity preview read
`GET /api/public/arc-activity`. The endpoint validates the locked plan and
current atomic report, then publishes only workflow aliases, states, exact
0.01 USDC amounts, confirmed block numbers, and Arc Testnet Explorer links.
It never opens the private campaign database and does not return recipient
wallets, credentials, internal agent IDs, or bearer sessions. Planned but
unsubmitted workflows are explicitly marked scheduled and have no receipt.
The public view uses workflow/request metrics, never customer or human-user
adoption claims. The underlying test provenance remains in this internal
record and should be disclosed accurately if requested in a grant or judge
submission.

The deployed website packages the validated, redacted final snapshot at
`docs/reports/arc-testnet-public-activity.json`. Its interactive four-case
demo workspace is separate from this historical Testnet evidence. The user
portal can inspect those four sample invoice IDs and their matching finance
records without counting them as a visitor's own submissions. Never publish
the private SQLite database, raw plan, Circle-backed operator server, or
secrets to make the dashboard work.

## Completed run

- Locked plan: `artifacts/testnet-campaign/agent50-20260925-live-plan.json`.
- Durable database: `data/agent50-20260925-live.sqlite3`.
- Progress report: `artifacts/testnet-campaign/agent50-20260925-live-report.json`.
- The original private runner PID was `40980`; after a checked, idempotent
  restart the final runner PID was `31320`. Both sets of standard output and
  error logs remain in the artifacts directory.
- Repeating the first due `tick` reused the existing payment and transaction
  hash; it did not submit a second transfer.
- The quiet monitor `tallyguard-arc-testnet-50-agent-campaign-monitor` was
  paused after terminal-count and receipt verification.

## Operator procedure

From the repository root, with the existing `circle` extra installed:

1. Use the approved 37-wallet rotation and review the fixed 24-hour schedule
   and maximum `0.40 USDC` transfer principal before starting.
2. Make `CIRCLE_WEB3_API_KEY`, `CIRCLE_ENTITY_SECRET`, and `CIRCLE_WALLET_ID`
   available to the trusted process through an approved environment or local
   secret store. Do not paste values into chat, code, a plan, or a report.
3. Generate a new plan with a new campaign ID only for a genuinely new
   experiment. **Do not replace or rerandomize the active plan**. Add
   `--unique-wallets` only after a verified 50-wallet inventory exists.
4. The existing private guarded runner uses `run --plan
   artifacts/testnet-campaign/agent50-20260925-live-plan.json --database
   data/agent50-20260925-live.sqlite3 --report
   artifacts/testnet-campaign/agent50-20260925-live-report.json --confirm
   RUN-50-AGENTS-ON-ARC-TESTNET`. Do not start a concurrent copy. `tick`
   uses the same arguments but executes only jobs due right now, which is
   useful for a supervised restart after checking durable state.
5. Inspect the report and Arc transaction receipts; run `tick` again after a
   restart. Already-paid invoices reuse their durable receipt and are not
   submitted again. If a policy, wallet, network, amount, or receipt differs,
   the runner stops instead of making a substitute transfer.

Do not call the checked-in 50-tenant simulator proof a real Testnet campaign.
The two experiments establish different things and must remain labelled.

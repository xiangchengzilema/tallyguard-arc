# Controlled 50-agent Arc Testnet campaign

The locked live schedule is running in a private local process. At the first
independent check, **1/50 jobs had completed and one real 0.01 USDC Arc
Testnet transfer was confirmed.** The recipient
balance rose from 0.002484 to 0.012484 USDC. Transaction:
[`0x4f1b4160…b65e3680d`](https://explorer.testnet.arc.io/tx/0x4f1b416077bbf8117c74c4bc31757882fce494e81cb645f22506938b65e3680d).
The current counts and receipt hashes come from the progress report below,
not this historical first-check paragraph. This is synthetic engineering
traffic, not customer adoption.

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

This endpoint is currently verified on the local preview. A future public
host will need a controlled sync of the sanitized report (or equivalent
read-only feed); deploying the website without that source shows an honest
unavailable state. Never publish the private SQLite database, raw plan,
Circle-backed operator server, or secrets to make the dashboard work.

## Active run

- Locked plan: `artifacts/testnet-campaign/agent50-20260925-live-plan.json`.
- Durable database: `data/agent50-20260925-live.sqlite3`.
- Progress report: `artifacts/testnet-campaign/agent50-20260925-live-report.json`.
- Private runner was started as PID `40980`; this PID is only an initial
  observation and may change after a safe restart. Standard output and error
  logs are in the same artifacts directory with `-stdout.log` and
  `-stderr.log` suffixes.
- Repeating the first due `tick` reused the existing payment and transaction
  hash; it did not submit a second transfer.
- A quiet 30-minute Codex thread heartbeat monitors progress and reports only
  failure, completion, or required action. Automation ID:
  `tallyguard-arc-testnet-50-agent-campaign-monitor`.

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

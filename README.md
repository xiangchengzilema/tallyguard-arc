# TallyGuard

**Proof-before-payment accounts payable for AI-operated businesses on Arc.**

TallyGuard is an evidence-bound accounts-payable agent. It evaluates an invoice against its purchase order, delivery evidence, vendor identity, treasury budget, and a deterministic policy before it can schedule or execute a USDC payment on Arc.

The AI may interpret documents and recommend an action. It cannot override the policy engine or settlement controls.

## Product promise

Every payment answers four questions:

1. What evidence made this bill payable?
2. Which policy version authorized it?
3. Why did the agent pay, hold, reject, or escalate it?
4. What Arc transaction and reconciliation record completed it?

## Project boundaries

- This is a new hackathon project and repository. It does not modify Obol or any previous submission.
- Mature workflow patterns are used as references, but no third-party code, branding, screenshots, or proprietary assets will be copied.
- Secrets, wallet keys, Circle credentials, and RPC credentials must only come from environment variables.
- The public demo must remain useful in safe simulation mode when live credentials are unavailable.

## Planned surfaces

- Evidence ingestion and normalization
- Invoice/PO/delivery three-way matching
- Vendor wallet-change and duplicate-invoice protection
- Versioned deterministic policy engine
- Agent decision orchestration: `PAY`, `SCHEDULE`, `HOLD`, `REJECT`, `ESCALATE`
- Circle wallet and Arc USDC settlement adapter
- Idempotent payment execution and reconciliation
- Tamper-evident decision receipt and audit event chain
- Finance operations dashboard

## Current implementation status

- Multi-tenant domain boundary enforced across invoices, vendors, purchase orders, delivery evidence, treasury snapshots, and policy versions
- Authenticated multipart evidence API persists original PDF, PNG, JPEG, and JSON bytes with signature checks, SHA-256 addressing, invoice binding, and field-level provenance
- Persistent vendor directory API requires an explicit verification method and reference for onboarding or wallet replacement, and retains an append-only tenant-scoped wallet history
- Immutable policy versions, active-policy selection, field-level diffs, and source-referenced treasury snapshots persist across restarts and are exposed through role-scoped APIs
- Real uploaded invoice, purchase-order, and delivery evidence can be normalized against the verified vendor, active policy, and latest treasury snapshot to produce an idempotent auditable decision
- Credential-free evidence analyst emits a structured recommendation, confidence, reason codes, and immutable package citations while the separate policy engine remains the only payment authority
- Optional OpenAI-compatible hosted analyst uses a strict recommendation-only schema, omits raw wallet addresses from its prompt, rejects transaction-shaped output, and falls back safely without changing policy authority
- Deterministic payment policy with duplicate, wallet-change, PO, delivery, autonomy, daily-limit, reserve, and kill-switch controls
- Tamper-evident append-only audit chain
- Canonical Arc Mainnet/Testnet configuration
- Mainnet settlement locked behind an explicit runtime flag and approval reference
- The currently active policy kill switch is rechecked immediately before every new settlement; it blocks already-`READY` invoices without creating an intent, while completed receipt replays remain readable and never resubmit
- Payment-intent creation atomically rechecks the active route, autonomy cap, snapshot freshness, daily limit, and reserve floor while reserving every amount committed since the latest treasury snapshot; concurrent workers cannot spend the same headroom twice
- Thread-safe idempotent settlement kernel with exact recipient/amount/network reconciliation
- Deterministic Arc simulator for the public demo and load tests
- Circle developer-wallet adapter with UUID v4 idempotency, lifecycle polling, and hard spend cap
- Read-only Circle/Arc preflight verifies chain ID, USDC contract code, wallet state, network, and balance before live mode
- Explicit testnet-only acceptance runner proves real settlement, Arc reconciliation, durable receipt replay, and audit-chain integrity with a maximum 0.10 USDC transfer
- Independent Arc RPC verification of chain ID, successful receipt, and exact USDC transfer event
- Restart-safe payment intents and settlement receipts with exactly-once retry behavior
- Transient provider failures return a retryable 503, move the invoice to `SUBMISSION_FAILED`, retain the original intent and idempotency key, recheck current execution controls on retry, and record both failure and recovery in the audit chain
- Every provider attempt is durably classified as retryable, locked, reconciliation-mismatched, or confirmed; only an explicitly retryable latest attempt can re-enter automatic settlement
- The settlement exception center shows open retries, locked mismatches, recovered incidents, attempt count, correlation reference, and a one-way hash of the provider idempotency key without exposing the key itself
- A worker-restart recovery drill proves a provider-accepted transfer is recovered with the original durable idempotency key and only one provider-side transfer
- A durable autonomous AP runner scans up to 25 tenant invoices, explains the next action for every item, freezes the queue state and plan under separate SHA-256 hashes, and can safely route approval work, release due schedules, settle an independently approved exception, or retry an explicitly recoverable payment
- Agent-run execution requires the approver settlement permission; it revalidates invoice version, workflow status, latest policy decision, approval binding, schedule eligibility, and retry authorization immediately before each action, while recording routed, revalidated, stale, failed, skipped, and settled results independently
- Auditors can export a content-addressed Agent Run Proof Packet containing the frozen plan, execution outcomes, approval records, related audit events, plan-hash verification, and tenant-chain verification
- Authenticated multi-tenant Flask API, persistent workflows, seeded judge scenarios, and segregated approvals
- Approver-only governance overview combines the active policy authority with an enriched global exception inbox; approving advances an escalated invoice to `READY`, while rejection closes it as `REJECTED` without creating a payment intent
- Restart-safe opaque sessions stored only as SHA-256 token digests
- Restart-safe policy decisions and role-separated approval records; payment authorization is deterministically reconstructed rather than cached
- Every new decision seals the normalized evidence, vendor, treasury, policy, duplicate set, route, and evaluation date into a hashed replay snapshot; auditors can independently recompute all 11 bindings through `GET /api/decisions/<id>/replay`
- Administrators can run a non-mutating policy what-if against those exact sealed inputs; the simulation cannot change the original decision, create an approval, or reach settlement
- Auditors can download a content-addressed Payment Evidence Packet containing source metadata and hashes, sealed replay inputs, all 11 replay checks, approval state, reconciled Arc receipt, and invoice-scoped audit events
- Persistent per-tenant tamper-evident audit chains with an authenticated verification endpoint
- Credential-free automated unit and fault-injection suite
- Responsive React judge console built on Carbon, with eight deterministic risk and recovery scenarios and a fresh-evidence workflow
- The fresh-evidence path creates tenant-scoped vendor, policy, treasury, invoice, PO, and delivery records; uploads three hashed source files; evaluates them; and can produce a reconciled simulation receipt from one screen
- A bring-your-own-evidence path validates three JSON files locally, previews the extracted financial fields for human confirmation, then persists the original bytes and runs the same policy pipeline
- An auditor-only timeline filters the tenant hash chain to the active invoice and refreshes after evaluation, approval, and settlement so the judge can verify each state mutation on screen
- A tenant-scoped operations summary aggregates durable open exposure, blocked value, seven-day due risk, overdue value, reconciled value, treasury headroom, policy reserve, and a due-date-sorted invoice queue
- A finance-governance panel exposes the active policy hash, autonomy cap, daily limit, reserve floor, settlement route, kill-switch state, and role-separated approve/reject actions across pending exceptions
- The same governance view exposes server-calculated settlement capacity: observed balance, durable commitments, daily headroom, reserve floor, snapshot freshness, and the maximum new payment currently admissible
- A vendor trust directory joins legal identity, risk tier, autonomous ceiling, approved Arc payout wallet, invoice-wallet match status, and append-only verification history so wallet-change holds are explainable on screen
- The governance panel can stage a safer policy, activate it as a new immutable content-addressed version, and show the server-computed before/after diff without rewriting historical decisions
- Finance operators can select up to 25 `READY` invoices from the durable work queue and settle them as one batch; each item keeps its own authorization, idempotency key, receipt, and failure result, so one exception cannot mask or roll back the rest
- A tenant-scoped schedule runner refuses early execution, derives the release date from the sealed policy decision, and revalidates current vendor, policy, treasury, evidence, route, and duplicate controls before any due invoice can settle
- One-screen evidence review, deterministic rule trace, segregated approval, and settlement receipt flow
- Real-HTTP synthetic multi-tenant load harness with latency, isolation, duplicate-payment, and shared-treasury contention metrics
- Auditor-visible reliability panel loads the checked-in 10,000-workflow result through a content-addressed API and labels it explicitly as synthetic engineering evidence rather than customer traction
- Tenant-scoped sliding-window rate limits and bounded operational request metrics

## Run the judge console

```powershell
cd web
npm ci
npm run build
cd ..
.\.venv\Scripts\python.exe -m tallyguard.api
```

Open `http://127.0.0.1:8000`. The top summary and invoice queue are computed from persistent tenant data. **Plan first run** turns that queue into a durable, hashed action plan: cleared invoices can settle, due schedules can be released, explicit transient failures can recover, and escalations can be routed to the role-separated approval inbox. After a different user approves an exception, the next plan recognizes that exact approval binding and may settle it. **Execute safe actions** rechecks the frozen assumptions before every workflow or funds-moving action and records item-level outcomes; **Export proof** downloads the content-addressed plan, results, approval records, and related audit-chain evidence. `READY` rows can also be selected and settled as an independently idempotent batch; failed rows are selectable only when their durable latest attempt is explicitly retryable. `SCHEDULED` rows show their sealed earliest-release date; **Check schedules** will leave early items untouched and revalidate every due item against current controls before settlement. The **Settlement exception center** separates safe retries from locked mismatches and retains recovered incident history. The **Current payment authority** and **Exception inbox** keep policy limits and role-separated approval work visible across invoices. Use **Control lab** for the eight adversarial judge cases or **Live evidence** to create and evaluate a new immutable three-document package through the public API. After evaluation, **Verify replay** recomputes the decision from its sealed point-in-time inputs without consulting mutable current state. **Policy what-if sandbox** re-evaluates those same inputs under temporary controls without persisting the result or authorizing payment, while **Download evidence packet** exports the complete auditor artifact. Live evidence also accepts the sample files in `examples/evidence/` so a reviewer can inspect extracted values before committing them. The default public-safe mode uses the Arc simulator and clearly labels simulated receipts. Live Circle settlement is opt-in through environment variables documented in [Circle and Arc settlement boundary](docs/CIRCLE_ARC_SETTLEMENT.md).

Run a local reliability baseline:

```powershell
.\.venv\Scripts\python.exe -m tallyguard.loadtest `
  --organizations 10 --invoices 200 --concurrency 16 `
  --duplicate-storm 100 --treasury-contention 50 `
  --output docs\reports\load-test-baseline.json
```

The checked-in [10,000-workflow report](docs/reports/LOAD_TEST_10000.md) records
zero failed workflows, zero duplicate payments, 100/100 denied cross-tenant
reads, and 96/100 over-limit payments blocked while exactly four safe
reservations reached the provider. The smaller
[development baseline](docs/reports/LOAD_TEST_BASELINE.md) remains available for
fast regression checks.

## Deploy the public judge playground

The repository includes a multi-stage `Dockerfile` and `render.yaml`. The public
deployment intentionally starts in simulation mode, serves the built React
console from the API container, and exposes `/api/readiness` for platform health
checks. See [Deployment model](docs/DEPLOYMENT.md) for the single-process safety
constraint and the production migration boundary.

## Canonical planning documents

- [Competitor and implementation blueprint](docs/COMPETITOR_IMPLEMENTATION_BLUEPRINT.md)
- [Backend product specification](docs/BACKEND_PRODUCT_SPEC.md)
- [Frontend reference map](docs/FRONTEND_REFERENCE_MAP.md)
- [Implementation roadmap](docs/IMPLEMENTATION_ROADMAP.md)
- [Circle and Arc settlement boundary](docs/CIRCLE_ARC_SETTLEMENT.md)
- [Deployment model](docs/DEPLOYMENT.md)
- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [Security model and live-testing protocol](docs/SECURITY_MODEL.md)
- [Hackathon context and submission checklist](docs/HACKATHON_CONTEXT.md)

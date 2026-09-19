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
- Thread-safe idempotent settlement kernel with exact recipient/amount/network reconciliation
- Deterministic Arc simulator for the public demo and load tests
- Circle developer-wallet adapter with UUID v4 idempotency, lifecycle polling, and hard spend cap
- Read-only Circle/Arc preflight verifies chain ID, USDC contract code, wallet state, network, and balance before live mode
- Independent Arc RPC verification of chain ID, successful receipt, and exact USDC transfer event
- Restart-safe payment intents and settlement receipts with exactly-once retry behavior
- Authenticated multi-tenant Flask API, persistent workflows, seeded judge scenarios, and segregated approvals
- Restart-safe opaque sessions stored only as SHA-256 token digests
- Restart-safe policy decisions and role-separated approval records; payment authorization is deterministically reconstructed rather than cached
- Persistent per-tenant tamper-evident audit chains with an authenticated verification endpoint
- Credential-free automated unit and fault-injection suite
- Responsive React judge console built on Carbon, with seven deterministic risk scenarios and a fresh-evidence workflow
- The fresh-evidence path creates tenant-scoped vendor, policy, treasury, invoice, PO, and delivery records; uploads three hashed source files; evaluates them; and can produce a reconciled simulation receipt from one screen
- A bring-your-own-evidence path validates three JSON files locally, previews the extracted financial fields for human confirmation, then persists the original bytes and runs the same policy pipeline
- An auditor-only timeline filters the tenant hash chain to the active invoice and refreshes after evaluation, approval, and settlement so the judge can verify each state mutation on screen
- One-screen evidence review, deterministic rule trace, segregated approval, and settlement receipt flow
- Real-HTTP synthetic multi-tenant load harness with latency, isolation, and duplicate-payment metrics
- Tenant-scoped sliding-window rate limits and bounded operational request metrics

## Run the judge console

```powershell
cd web
npm ci
npm run build
cd ..
.\.venv\Scripts\python.exe -m tallyguard.api
```

Open `http://127.0.0.1:8000`. Use **Control lab** for the seven adversarial judge cases or **Live evidence** to create and evaluate a new immutable three-document package through the public API. Live evidence also accepts the sample files in `examples/evidence/` so a reviewer can inspect extracted values before committing them. The default public-safe mode uses the Arc simulator and clearly labels simulated receipts. Live Circle settlement is opt-in through environment variables documented in [Circle and Arc settlement boundary](docs/CIRCLE_ARC_SETTLEMENT.md).

Run a local reliability baseline:

```powershell
.\.venv\Scripts\python.exe -m tallyguard.loadtest `
  --organizations 100 --invoices 200 --concurrency 32 `
  --duplicate-storm 100 --output docs\reports\load-test-baseline.json
```

The checked-in [10,000-workflow report](docs/reports/LOAD_TEST_10000.md) records
zero failed workflows, zero duplicate payments, and 100/100 denied cross-tenant
reads across 19,301 real loopback HTTP requests. The smaller
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
- [Hackathon context and submission checklist](docs/HACKATHON_CONTEXT.md)

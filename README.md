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
- Deterministic payment policy with duplicate, wallet-change, PO, delivery, autonomy, daily-limit, reserve, and kill-switch controls
- Tamper-evident append-only audit chain
- Canonical Arc Mainnet/Testnet configuration
- Mainnet settlement locked behind an explicit runtime flag and approval reference
- Thread-safe idempotent settlement kernel with exact recipient/amount/network reconciliation
- Deterministic Arc simulator for the public demo and load tests
- Circle developer-wallet adapter with UUID v4 idempotency, lifecycle polling, and hard spend cap
- Independent Arc RPC verification of chain ID, successful receipt, and exact USDC transfer event
- Restart-safe payment intents and settlement receipts with exactly-once retry behavior
- Authenticated multi-tenant Flask API, persistent workflows, seeded judge scenarios, and segregated approvals
- Credential-free automated unit and fault-injection suite

## Canonical planning documents

- [Competitor and implementation blueprint](docs/COMPETITOR_IMPLEMENTATION_BLUEPRINT.md)
- [Backend product specification](docs/BACKEND_PRODUCT_SPEC.md)
- [Frontend reference map](docs/FRONTEND_REFERENCE_MAP.md)
- [Implementation roadmap](docs/IMPLEMENTATION_ROADMAP.md)
- [Circle and Arc settlement boundary](docs/CIRCLE_ARC_SETTLEMENT.md)
- [Hackathon context and submission checklist](docs/HACKATHON_CONTEXT.md)

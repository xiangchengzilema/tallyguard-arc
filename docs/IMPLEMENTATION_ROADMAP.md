# TallyGuard implementation roadmap

## Product target

TallyGuard is a multi-tenant accounts-payable control plane for AI-operated businesses. The agent may interpret documents and recommend actions, but deterministic controls own payment authorization. Every settlement must be replayable from evidence, policy version, approval state, and Arc transaction data.

## Network strategy

Arc Mainnet launched on September 16, 2026. TallyGuard will support both networks, with intentionally different safety profiles.

| Environment | Purpose | Money policy |
| --- | --- | --- |
| Simulation | UI demos, deterministic scenarios, high-volume load tests | No onchain transfer |
| Arc Testnet | End-to-end wallet, transaction, indexing, and failure testing | Automated test USDC within policy |
| Arc Mainnet | Final proof of real settlement | Dedicated low-balance wallet, explicit enable flag, capped amount, recorded approval |

Canonical Arc parameters:

| Network | Chain ID | RPC | Explorer |
| --- | ---: | --- | --- |
| Arc Mainnet | 5042 | `https://rpc.mainnet.arc.io` | `https://explorer.arc.io` |
| Arc Testnet | 5042002 | `https://rpc.testnet.arc.io` | `https://explorer.testnet.arc.io` |

USDC is Arc's native gas asset. Its native interface uses 18 decimals while the optional ERC-20 interface at `0x3600000000000000000000000000000000000000` uses 6 decimals. Application accounting will use decimal USDC values and the ERC-20 interface for token operations; conversion boundaries will be tested explicitly.

Official references:

- https://www.circle.com/pressroom/circle-launches-arc-mainnet-an-economic-operating-system-for-the-internet
- https://docs.arc.io/arc/references/connect-to-arc
- https://docs.arc.io/arc/references/contract-addresses
- https://docs.arc.io/arc/references/evm-differences

## Delivery sequence

### Milestone 1 — Control and settlement kernel

Status: complete (2026-09-20).

- Tenant isolation for every financial record
- Invoice/PO/delivery/vendor/treasury policy evaluation
- Mainnet/Testnet network configuration
- Mainnet safety gate
- Idempotency under concurrent duplicate requests
- Exact reconciliation of network, recipient, amount, transaction hash, and block
- Tamper-evident audit chain

Exit criteria:

- A 100-request duplicate storm creates exactly one provider submission.
- Cross-tenant evidence can never authorize a payment.
- A non-`PAY` decision can never reach a settlement adapter.
- Mainnet cannot run without both a runtime flag and approval reference.

### Milestone 2 — Evidence intake and three-way match

Status: complete (2026-09-20).

Implemented foundation:

- Opaque bearer sessions with hashed token storage, expiry, revocation, and tenant-aware RBAC
- Immutable PDF/image/JSON intake with MIME signature validation and SHA-256 content addressing
- Field-level extraction confidence and page/bounding-box/JSON-pointer provenance
- Tenant-safe, deterministic evidence-package manifest hashes
- Atomic duplicate detection using content hash, vendor/invoice number, business keys, and near-duplicate text
- Explicit fail-closed outcomes for missing PO and delivery evidence
- Evidence normalization into invoice, PO, and delivery domain records with confidence gates
- Verified vendor-wallet onboarding and append-only wallet replacement history

- Organization and user authentication
- Vendor onboarding and verified wallet history
- Invoice upload: PDF, image, JSON, and seeded fixtures
- Purchase-order import
- Delivery/acceptance evidence import
- Source file hashing and immutable evidence references
- Structured extraction confidence and field-level provenance
- Duplicate detection across invoice number, content hash, amount, vendor, and near-duplicate text

Exit criteria:

- Every extracted field links back to its source evidence.
- Changed vendor wallet, duplicate invoice, PO overage, and missing delivery are caught deterministically.

### Milestone 3 — Agent decision service

Status: in progress.

Implemented foundation:

- AI recommendations are stored as non-authoritative interpretation only
- Deterministic policy always owns the final action and visibly records disagreement
- Stable evidence-and-policy-bound decision IDs with tamper-evident audit events
- `PAY`, `SCHEDULE`, `HOLD`, `REJECT`, and `ESCALATE` control outcomes
- Immutable policy versions, content hashes, active-version lookup, and field-level diffs
- Segregated exception inbox: requester cannot self-approve and only pure escalations are overridable
- Approved decisions and payment intents must carry the exact same approval reference
- Durable invoice state machine with optimistic concurrency and immutable transition history

- Agent-generated evidence summary
- Recommended action with structured reason codes
- Deterministic policy override boundary
- `PAY`, `SCHEDULE`, `HOLD`, `REJECT`, and `ESCALATE` workflows
- Human approval inbox for exceptions
- Versioned policy editor with before/after diff
- Decision replay using the exact historical policy version

Exit criteria:

- The same evidence and policy always produce the same control result.
- Free-form model output is never accepted as a recipient, amount, network, or transaction payload.

### Milestone 4 — Circle wallets and Arc settlement

Status: in progress.

Implemented foundation:

- Lazy official Circle Developer-Controlled Wallets SDK bridge with environment-only credentials
- Explicit UUID v4 idempotency enforcement before live provider calls
- Circle lifecycle polling through `COMPLETE` with fail-closed terminal and timeout handling
- Independent live-adapter hard transfer cap in addition to organization policy limits
- Exact Circle reconciliation of network, recipient, amount, transaction ID, hash, and block
- Independent Arc RPC verification of chain ID, receipt success, canonical USDC contract, recipient, and six-decimal amount
- Circle/Arc block-height agreement check and fault-injection coverage
- Durable payment intents retain the original UUID v4 across process restarts and retries
- Durable settlement receipts prevent provider resubmission after a completed payment
- Invoice settlement advances through submitting, submitted, confirmed, and reconciled states
- Public demo remains credential-free through the deterministic simulator

- Circle wallet adapter behind the settlement protocol
- Arc Testnet USDC transfer
- Transaction state polling and deterministic-finality handling
- Arc Explorer links and transaction receipts
- Optional Arc memo linking transaction to a non-sensitive decision identifier
- Retry classification without double-payment
- Reconciliation of expected and confirmed payment data
- Mainnet adapter enabled only after Testnet acceptance tests pass

Mainnet launch procedure:

1. Create a dedicated hackathon wallet; never use a primary wallet.
2. Fund it with a deliberately small USDC balance.
3. Configure credentials locally through environment variables or a user-controlled signer; never paste a private key into chat or commit it.
4. Set a hard per-payment and total demo budget.
5. Require a recorded approval reference for every mainnet settlement during the hackathon.
6. Run one or a few low-value payments and preserve Explorer evidence.

Current acceptance status: implementation and credential-free fault tests pass. A real Arc
Testnet transfer remains intentionally pending until a dedicated Circle test wallet and locally
stored credentials are available.

### Milestone 5 — Multi-tenant finance API

Status: in progress.

Implemented foundation:

- Runnable Flask service with health and readiness probes
- Seeded role-separated judge sessions without committed credentials
- Tenant-scoped invoice create/read/list endpoints with opaque bearer authentication
- Stable status filtering and cursor pagination
- Correlation IDs and fail-closed JSON errors
- Seven deterministic judge scenarios exercise the real policy engine and persisted workflow
- Large-invoice scenario supports role-separated request and approval through the API
- Approver-only settlement endpoint binds invoice, decision, approval, payment intent, and receipt
- Auditor-readable receipt endpoint includes the corresponding Arc Explorer URL
- Repeated settlement requests return the persisted receipt without another provider submission

- Organizations, users, and role-based access
- Tenant-scoped repositories and queries
- Vendor, invoice, evidence, decision, approval, settlement, receipt, and audit APIs
- Cursor pagination and stable filters
- Per-tenant rate limits and quotas
- Request correlation IDs and structured logs
- Health, readiness, and metrics endpoints

Exit criteria:

- An authenticated tenant cannot read, mutate, approve, or replay another tenant's records.
- Every state mutation is traceable by request and audit IDs.

### Milestone 6 — Judge-ready finance dashboard

Status: in progress.

Implemented foundation:

- Responsive React console built with the Carbon enterprise design system
- Seven selectable scenarios backed by the real Flask API and policy engine
- Evidence summary, immutable source hash, versioned policy binding, and per-rule findings
- Clear visual separation between AI recommendation and deterministic authorization
- Role-separated large-invoice approval flow
- Settlement receipt with provider, network, block, transaction hash, and idempotency state
- Honest visual distinction between public simulation and live Arc proof
- Loading, empty, error, desktop, and mobile states

- Operations overview with payable exposure, due dates, held value, and treasury reserve
- Invoice work queue with fast filters
- Evidence match view modeled after mature AP review tools
- Decision timeline showing evidence, rules, and agent explanation separately
- Approval inbox for exceptions
- Settlement drawer with Arc transaction proof
- Vendor risk and wallet-change history
- Audit explorer with chain verification
- Seeded scenario switcher for fast judging

Exit criteria:

- A first-time reviewer can complete the clean-payment and fraud-prevention flows without instructions.
- The live product remains useful in simulation mode if wallet credentials are unavailable.

### Milestone 7 — Reliability and multi-user load testing

Status: in progress.

Implemented foundation:

- Repeatable real-HTTP load harness with ephemeral tenant data and no committed credentials
- Distinct operator and approver identities for every synthetic organization
- Mixed clean, hold, reject, schedule, escalation, approval, and settlement workflows
- Cross-tenant identifier attack probes
- Concurrent duplicate settlement storm with provider-submission accounting
- JSON output with throughput, p50/p95/p99, error rate, and duplicate-payment count
- 100-organization baseline: 200/200 workflows succeeded, 100/100 isolation probes denied, and 100 duplicate requests produced one provider submission
- The baseline discovered and verified a fix for a stale-version settlement race

Two distinct test classes will be reported honestly:

1. **Synthetic multi-tenant load** proves engineering reliability.
2. **Genuine pilot usage** proves traction and product value.

Synthetic load suite:

- At least 100 isolated organizations
- At least 10,000 invoices across clean and adversarial scenarios
- Concurrent ingestion, evaluation, approval, and receipt reads
- Duplicate submission storms against the same idempotency key
- Cross-tenant identifier attacks
- Slow provider, timeout, retry, malformed receipt, and wrong-recipient fault injection
- Database contention and worker restart recovery
- Measured throughput, p50/p95/p99 latency, error rate, and duplicate-payment count

The public report will call this a multi-tenant load test. It will not be represented as genuine customer traction. For traction, the goal is at least one real business or self-operated business workflow, which the event rules explicitly allow.

### Milestone 8 — Submission package

Status: in progress.

Implemented foundation:

- Multi-stage container builds the React console and Python service reproducibly
- Non-root Gunicorn runtime with public-safe simulation defaults
- Render Blueprint with readiness health check and explicit environment controls
- GitHub Actions verifies frontend build, Python tests, compilation, and secret scan
- Deployment boundary documents the temporary single-process and ephemeral SQLite constraints

- Public GitHub repository and reproducible setup
- Architecture and trust-boundary diagrams
- Live deployment with seeded judge account
- Under-three-minute demo video
- Mainnet/Testnet Explorer proof
- Load-test report and raw result artifact
- Genuine pilot summary
- Security model, limitations, and safe-demo statement

## Definition of done

The project is submission-ready only when:

- A clean invoice settles end to end on Arc Testnet.
- At least one deliberately low-value Arc Mainnet transaction is proven, if wallet access and event rules permit it.
- Duplicate, wrong-wallet, cross-tenant, and insufficient-evidence scenarios visibly fail closed.
- Concurrent duplicate calls produce no double payment.
- The live deployment is usable without private credentials.
- The repository, video, live URL, transaction evidence, and load-test report all resolve from a clean browser session.

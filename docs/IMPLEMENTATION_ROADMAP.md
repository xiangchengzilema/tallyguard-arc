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
- Session digests, expiry, roles, and revocation now survive service restarts
- Immutable PDF/image/JSON intake with MIME signature validation and SHA-256 content addressing
- Field-level extraction confidence and page/bounding-box/JSON-pointer provenance
- Tenant-safe, deterministic evidence-package manifest hashes
- Atomic duplicate detection using content hash, vendor/invoice number, business keys, and near-duplicate text
- Explicit fail-closed outcomes for missing PO and delivery evidence
- Evidence normalization into invoice, PO, and delivery domain records with confidence gates
- Verified vendor-wallet onboarding and append-only wallet replacement history
- Persistent vendor APIs enforce finance-operator RBAC, optimistic current-wallet checks, explicit verification references, tenant isolation, and restart-safe history
- Authenticated multipart evidence upload persists original bytes and links exactly one invoice, purchase order, and delivery document per invoice
- Structured JSON is extracted with exact JSON pointers; PDF/image observations require explicit page or bounding-box provenance
- Evidence downloads and metadata lists are tenant-scoped, size-capped, and audit recorded

- Organization and user authentication
- Vendor onboarding and verified wallet history (API complete)
- Invoice upload: PDF, image, JSON, and seeded fixtures (API complete)
- Purchase-order import
- Delivery/acceptance evidence import
- Source file hashing and immutable evidence references (API complete)
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
- Policy history and active selection now persist in SQLite and are exposed through admin-write/auditor-read APIs
- Source-referenced treasury snapshots persist as append-only tenant records and feed an authenticated summary API
- Segregated exception inbox: requester cannot self-approve and only pure escalations are overridable
- Approver-only governance overview returns the active immutable policy plus enriched pending exceptions, and a rejected exception durably closes its invoice without entering settlement
- Approved decisions and payment intents must carry the exact same approval reference
- Durable invoice state machine with optimistic concurrency and immutable transition history
- Durable policy decisions and optimistic approval records survive service restarts
- Approved settlement authorization is reconstructed from the bound decision and approval instead of trusted from process memory
- The current tenant kill switch is enforced again at execution time before any new intent or provider submission, including for invoices that became `READY` under an older policy; completed receipt replays remain idempotently readable
- Intent reservation atomically rechecks active asset/network, autonomy approval, treasury freshness, daily limit, and reserve floor; durable post-snapshot commitments prevent concurrent workers from double-spending the same headroom
- The authenticated evaluation API now normalizes real uploaded three-way-match evidence and binds it to the verified vendor, active policy, latest treasury snapshot, and Arc network
- Completed evaluations are idempotent and retain their original policy version even after a newer policy becomes active
- A protocol-based evidence analyst now produces structured recommendations with confidence, reason codes, and immutable package citations; it cannot construct a payment payload
- An optional OpenAI-compatible hosted adapter adds probabilistic document reasoning behind an environment-only credential boundary; its four-field schema forbids payment parameters, evidence citations are assigned locally, unsafe responses are rejected, and provider failure falls back deterministically
- Each decision seals its exact point-in-time evidence, vendor, treasury, policy, duplicate set, settlement route, and date; the auditor endpoint and console recompute and verify 11 independent bindings without consulting mutable current state
- An admin-only policy what-if endpoint re-evaluates the sealed historical inputs under an allowlisted temporary policy patch; it returns a comparison but never persists a decision, approval, audit event, or payment authorization
- A downloadable, SHA-256 content-addressed Payment Evidence Packet combines the sealed inputs, replay checks, source metadata, approval, settlement receipt, and invoice audit events into one portable auditor artifact

- Agent-generated evidence summary (credential-free and hosted structured adapters complete)
- Recommended action with structured reason codes
- Deterministic policy override boundary
- `PAY`, `SCHEDULE`, `HOLD`, `REJECT`, and `ESCALATE` workflows
- Human approval inbox for exceptions
- Versioned policy editor with before/after diff (complete)
- Decision replay using the exact historical policy version (complete)

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
- Read-only settlement preflight checks Arc RPC health, canonical USDC code, Circle wallet state/network/balance, explicit mainnet enablement, and an independent maximum transfer cap without signing or submitting a transaction
- Durable payment intents retain the original UUID v4 across process restarts and retries
- Durable settlement receipts prevent provider resubmission after a completed payment
- Invoice settlement advances through submitting, submitted, confirmed, and reconciled states
- Batch settlement keeps authorization, submission, reconciliation, and retry state isolated per invoice; partial failures return item-level remediation while completed items remain exactly-once
- Scheduled invoices expose their sealed release date and a tenant-scoped runner refuses early execution; at release it produces a fresh decision from current vendor, active policy, latest treasury, immutable evidence, and the current Arc route before creating any intent
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
- Authenticated batch settlement accepts at most 25 distinct invoices, isolates item failures, and reuses every completed item's durable receipt on retry
- Authenticated schedule execution scans only the caller's tenant, reports early items as waiting, and isolates revalidation or settlement failures per invoice
- Tenant-scoped sliding-window request budgets return explicit 429 and retry guidance
- Bounded operational metrics expose endpoint, status-class, and latency aggregates without financial labels
- Authenticated audit endpoint verifies a persistent per-tenant hash chain across decisions, approvals, and reconciled settlements
- Role-scoped policy-history, policy-diff, active-policy, treasury-snapshot, vendor, and wallet-history APIs
- Approver governance returns live settlement capacity from the active policy, latest treasury snapshot, and post-snapshot intent reservations

- Organizations, users, and role-based access
- Tenant-scoped repositories and queries
- Vendor, invoice, evidence, decision, approval, settlement, receipt, and audit APIs
- Cursor pagination and stable filters
- Per-tenant rate limits and durable treasury quotas (complete)
- Request correlation IDs and structured logs
- Health, readiness, and metrics endpoints (complete)

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
- A judge-facing live-evidence mode now creates a fresh tenant dataset, uploads invoice/PO/delivery JSON as immutable multipart evidence, invokes the production-shaped evaluation endpoint, shows the agent's exact evidence citations, and completes the same idempotent settlement flow used by the API
- Browser acceptance confirms the live path reaches a 12/12 deterministic `PAY` decision and a reconciled Arc-simulator receipt with no console errors
- Reviewers can select their own invoice, purchase-order, and delivery JSON files; client-side schema and relationship validation previews the extracted amounts, IDs, due date, and recipient before any record is persisted
- A checked-in three-document Atlas fixture provides a reproducible upload-review-evaluate path without requiring credentials
- The console reads audit data through a separate auditor session, filters it to the active invoice, verifies the full tenant chain, and refreshes after evaluation, approval, and reconciliation
- The audit view downloads the current invoice's Payment Evidence Packet and displays its server-computed content hash after a successful export
- The decision view includes a visibly non-persistent policy sandbox that can demonstrate an autonomy-cap or kill-switch change against the exact sealed inputs and compare the resulting action and reason codes
- Persistent operations metrics and a due-date-sorted work queue expose open exposure, blocked value, seven-day due risk, overdue items, and treasury headroom across all durable tenant invoices
- The work queue supports native selection of `READY` invoices and an idempotent batch-settlement action with explicit partial-success feedback
- Scheduled rows display their earliest release date and expose a schedule-run action whose result distinguishes waiting, settled, policy-revalidated, and failed items
- A persistent finance-governance panel surfaces the active policy hash and limits beside a global exception inbox with role-separated approve/reject actions; browser acceptance covers empty state, request, and rejection with no console errors
- The governance panel stages monetary, scheduling, and emergency-stop changes as a new immutable version and renders the server-computed field diff after activation
- A settlement-capacity module shows observed balance, durably committed amount, daily headroom, reserve floor, snapshot freshness, and the maximum currently admissible payment
- A vendor trust directory exposes legal identity, risk tier, autopay ceiling, exact invoice-wallet match state, and append-only wallet verification history; the wallet-change scenario visibly resolves to `MISMATCH — HOLD`

- Operations overview with payable exposure, due dates, held value, and treasury reserve (summary complete)
- Invoice work queue with fast filters (durable queue and batch selection complete; interactive filters optional)
- Evidence match view modeled after mature AP review tools
- Decision timeline showing evidence, rules, and agent explanation separately
- Approval inbox for exceptions (complete)
- Settlement drawer with Arc transaction proof
- Vendor risk and wallet-change history (complete)
- Audit explorer with chain verification (active-invoice timeline complete; global search remains optional)
- Seeded scenario switcher for fast judging
- User-supplied document picker and extraction review before evaluation (structured JSON complete; PDF/image OCR adapter remains optional)

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
- Concurrent independent payments contending for one immutable treasury snapshot and daily limit
- JSON output with throughput, p50/p95/p99, error rate, and duplicate-payment count
- 100-organization baseline: 200/200 workflows succeeded, 100/100 isolation probes denied, and 100 duplicate requests produced one provider submission
- Full 10,000-workflow run: 10,000/10,000 succeeded across 19,503 HTTP requests, 100/100 isolation probes denied, and 200 duplicate requests produced one provider submission
- One hundred independent 1,200 USDC payments raced for a shared 5,000 USDC daily limit: exactly four reservations reached the provider, 96 failed closed, and the atomic limit was preserved
- Current full-run throughput reached 15.347 workflows/second with 0.00% workflow errors while durably writing sessions, decisions, approvals, payments, and audit events; settlement P95 was 3.587 seconds under SQLite contention
- The baseline discovered and verified a fix for a stale-version settlement race
- The judge console now exposes the checked-in full-run artifact through an authenticated, SHA-256-addressed reliability API and an honest evidence panel with workflow, isolation, idempotency, atomic treasury contention, throughput, and P95 figures

Two distinct test classes will be reported honestly:

1. **Synthetic multi-tenant load** proves engineering reliability.
2. **Genuine pilot usage** proves traction and product value.

Synthetic load suite:

- At least 100 isolated organizations
- At least 10,000 invoices across clean and adversarial scenarios (complete)
- Concurrent ingestion, evaluation, approval, and receipt reads
- Duplicate submission storms against the same idempotency key
- Independent payment attempts racing for the same daily-limit and reserve headroom
- Cross-tenant identifier attacks
- Slow provider, timeout, retry, malformed receipt, and wrong-recipient fault injection
- Database contention and worker restart recovery
- Measured throughput, p50/p95/p99 latency, error rate, and duplicate-payment count

The public report and console call this a multi-tenant engineering load test. It is not represented as genuine customer traction. For traction, the goal is at least one real business or self-operated business workflow, which the event rules explicitly allow.

### Milestone 8 — Submission package

Status: in progress.

Implemented foundation:

- Multi-stage container builds the React console and Python service reproducibly
- Non-root Gunicorn runtime with public-safe simulation defaults
- Render Blueprint with readiness health check and explicit environment controls
- GitHub Actions verifies frontend build, Python tests, compilation, and secret scan
- Deployment boundary documents the temporary single-process and ephemeral SQLite constraints
- Architecture and trust-boundary diagrams document agent authority, tenant isolation, the evidence-to-payment sequence, runtime profiles, and production migration boundary
- Security model and live-test protocol enumerate protected assets, abuse cases, hard controls, secret handling, and honest demo limitations

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

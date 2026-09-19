# Backend product specification

## 1. System boundary

TallyGuard is split into three trust zones:

1. **Interpretation zone** — parses uploaded evidence and produces structured observations. It may be probabilistic.
2. **Control zone** — applies versioned deterministic rules and creates a signed payment intent. It must be reproducible.
3. **Settlement zone** — executes an approved intent through Circle/Arc and reconciles the confirmed transaction. It never accepts free-form model output as transaction parameters.

The interpretation zone defaults to a credential-free structured analyst. Setting
`TALLYGUARD_AGENT_MODE=openai-compatible` enables an optional hosted chat-completions
adapter. Its constrained output contains only an action recommendation, summary, reason
codes, and confidence. The adapter rejects extra fields, assigns evidence citations locally,
never transmits wallet addresses, and falls back to the local analyst if the provider fails.
The deterministic control zone remains authoritative in both modes.

## 2. Core entities

### Organization

- `id`, `name`, `base_currency`
- `daily_payment_limit`
- `minimum_cash_reserve`
- `autonomy_enabled`
- `kill_switch_enabled`

### Vendor

- `id`, `organization_id`, `legal_name`
- `approved_wallet_address`
- `wallet_verified_at`
- `risk_tier`
- `autopay_limit`
- `status`

### Invoice

- `id`, `organization_id`, `vendor_id`
- `invoice_number`, `currency`, `amount`, `due_date`
- `source_document_hash`
- `status`
- `submitted_at`

### PurchaseOrder

- `id`, `po_number`, `vendor_id`
- `currency`, `authorized_amount`
- `line_items`, `status`

### DeliveryEvidence

- `id`, `purchase_order_id`
- `evidence_type`, `delivered_at`
- `line_items`, `source_document_hash`

### PolicyVersion

- `id`, `organization_id`, `version`
- `rules_json`, `content_hash`
- `activated_at`, `retired_at`

### Decision

- `id`, `invoice_id`, `policy_version_id`
- `agent_recommendation`
- `policy_outcome`
- `final_action`
- `reason_codes`, `evidence_refs`
- `confidence`, `created_at`

### PaymentIntent

- `id`, `invoice_id`, `decision_id`
- `recipient`, `asset`, `amount`, `network`
- `idempotency_key`
- `scheduled_for`, `status`

### SettlementReceipt

- `id`, `payment_intent_id`
- `provider`, `provider_reference`
- `transaction_hash`, `block_number`
- `confirmed_amount`, `confirmed_recipient`
- `status`, `confirmed_at`

### AuditEvent

- `id`, `aggregate_type`, `aggregate_id`
- `event_type`, `event_payload`
- `previous_hash`, `event_hash`, `created_at`

## 3. Invoice state machine

```text
DRAFT
  -> EVIDENCE_PENDING
  -> EVALUATING
  -> READY | HOLD | REJECTED | ESCALATED
  -> SCHEDULED
  -> SUBMITTING
  -> SUBMITTED
  -> CONFIRMED
  -> RECONCILED
```

Failure states:

```text
SUBMISSION_FAILED
CONFIRMATION_TIMEOUT
RECONCILIATION_MISMATCH
CANCELLED
```

`PAID` is a presentation label for `RECONCILED`; it must never be inferred merely from an API request succeeding.

## 4. Deterministic checks

Minimum MVP rule set:

- Duplicate invoice fingerprint
- Vendor active and approved
- Recipient wallet exact match
- Asset and network allowlist
- Invoice amount within PO tolerance
- Delivered quantity/value sufficient for requested payment
- Currency consistency
- Invoice due date and optional early-pay discount validity
- Per-invoice autonomy limit
- Per-vendor rolling limit
- Organization daily payment limit
- Minimum post-payment reserve
- Kill switch
- Idempotency reservation before execution

## 5. Decision outcomes

| Outcome | Meaning |
| --- | --- |
| `PAY` | Evidence and policy allow immediate autonomous payment |
| `SCHEDULE` | Payable, but a later date is financially or contractually preferable |
| `HOLD` | A resolvable evidence or risk exception blocks execution |
| `REJECT` | Invoice is invalid, disallowed, or provably duplicated |
| `ESCALATE` | Policy requires a human decision, usually due to amount or risk tier |

Every non-pay outcome must include machine-readable reason codes and a human-readable remediation list.

## 6. API outline

```text
POST   /api/vendors
GET    /api/vendors
PATCH  /api/vendors/{id}/wallet
GET    /api/vendors/{id}/wallet-history
POST   /api/policies
GET    /api/policies
GET    /api/policies/active
GET    /api/policies/diff?from={version}&to={version}
POST   /api/treasury/snapshots
POST   /api/invoices
GET    /api/invoices
GET    /api/invoices/{id}
GET    /api/operations/overview
GET    /api/invoices/{id}/evidence-packet
POST   /api/invoices/{id}/evidence
POST   /api/invoices/{id}/evaluate
POST   /api/invoices/{id}/approve
POST   /api/invoices/{id}/settle
POST   /api/payment-batches/settle
POST   /api/schedules/run
GET    /api/invoices/{id}/receipt
GET    /api/decisions/{id}
GET    /api/decisions/{id}/replay
POST   /api/decisions/{id}/policy-simulation
GET    /api/approvals/pending
GET    /api/governance/overview
GET    /api/audit/events
GET    /api/reliability/report
GET    /api/treasury/summary
GET    /api/health
```

`POST /api/invoices/{id}/evaluate` binds the immutable evidence-package manifest to the
verified vendor record, active policy content hash, latest source-referenced treasury snapshot,
and current Arc route. Repeating an already completed evaluation returns its original decision;
activating a newer policy cannot silently rewrite that historical authorization.

`POST /api/payment-batches/settle` accepts 1-25 distinct invoice IDs. It does not create a
shared authorization or transaction: every item traverses the same single-invoice decision,
approval, intent, provider, reconciliation, and receipt boundary. A rejected item is reported
independently, while successful items remain committed and become receipt-reusing no-ops on retry.

`POST /api/schedules/run` requires settlement-execute permission and scans only `SCHEDULED`
invoices in the authenticated tenant. An item's earliest release date is derived from its sealed
decision inputs and cannot be supplied by the caller. Early items remain untouched. Due items are
re-evaluated with immutable evidence plus the current verified vendor, active policy, latest
treasury snapshot, duplicate set, and Arc route. Only a fresh `PAY` decision can advance to the
existing idempotent settlement path; a new hold, rejection, escalation, or schedule produces no
payment intent.

`POST /api/agent-runs` requires decision-run permission and creates a durable, content-addressed
plan for at most 25 tenant invoices. Planning cannot create an intent or call a provider. Auditor
endpoints expose the latest or a named run only inside the authenticated tenant.

`POST /api/agent-runs/{id}/execute` requires settlement-execute permission. It reloads every
executable item and compares invoice version, workflow state, decision, approval binding, schedule
eligibility, and retry classification with the frozen plan. `REQUIRE_APPROVAL` creates a durable
role-separated handoff without moving funds. `SETTLE_APPROVED` accepts only the exact approved
exception, while `RELEASE_SCHEDULE` requires the sealed release date to be due and produces a fresh
current-policy decision before settlement. Changed or newly blocked items become `STALE` or
`REVALIDATED`. Valid funds-moving items use the normal atomic, idempotent settlement orchestrator
and retain independent results when another item fails.

`GET /api/agent-runs/{id}/proof-packet` requires audit-read permission and returns a downloadable,
content-addressed JSON envelope. The packet includes the durable plan and results, referenced
approvals, related invoice/run audit events, a recomputed plan-hash verdict, and tenant audit-chain
verification. Tenant-scoped lookup returns 404 for a foreign run identifier.

`POST /api/demo/autonomy-showcase` requires decision-run permission and creates four isolated,
public-safe demo invoices through the normal scenario, persistence, policy, and audit paths. The
returned queue covers immediate settlement, segregated approval, wallet remediation, and an early
schedule. It never executes a payment; the caller must create and explicitly execute an agent run.

`GET /api/reliability/report` requires audit-read permission and returns the checked-in synthetic
multi-tenant workflow and Agent Run artifacts with separate SHA-256 content addresses. The
methodology in both responses explicitly distinguishes reliability evidence from customer traction
and states that no funds moved.

`GET /api/invoices/{id}/evidence-packet` requires audit-read permission and returns a downloadable
JSON envelope. Its packet hash covers canonical UTF-8 JSON containing the immutable invoice,
source evidence metadata and hashes, sealed replay inputs, replay verification, approval, optional
settlement proof, and invoice-scoped audit events. Raw uploaded files are not duplicated in the packet.

`POST /api/decisions/{id}/policy-simulation` requires policy-write permission and accepts only an
allowlisted subset of deterministic policy fields. It applies the temporary patch to the decision's
sealed replay snapshot, not current vendor or treasury state. The response compares the original and
simulated action, changed fields, reason codes, and full rule trace. It deliberately writes no policy,
decision, approval, audit event, payment intent, or receipt.

`GET /api/governance/overview` requires payment-approve permission. It returns the tenant's active
policy or an explicit `null` empty state together with enriched pending approval records containing
the bound invoice and decision. Resolving an approval uses optimistic version checks: approval moves
the invoice from `ESCALATED` to `READY`; rejection moves it to terminal `REJECTED`. Neither operation
creates a payment intent, and only the approved path can later satisfy settlement authorization.
When policy and treasury state exist, the response also includes settlement capacity derived from
the latest source snapshot plus every durable intent committed since that observation.

Every settlement request checks the current active policy's kill switch before creating a new
intent or calling the provider, even when the bound `PAY` decision predates the emergency policy.
A blocked execution writes a tenant audit event containing the active policy version and hash. If a
durable receipt already exists, the request returns that proof as an idempotent replay and does not
call the provider, so post-payment emergency stops do not hide completed reconciliation evidence.
For a new or incomplete intent, policy route, autonomy, snapshot freshness, daily spend, and reserve
headroom are rechecked inside the same write transaction that reserves the intent. A failed control
writes an auditable execution-control event and leaves no intent for the provider to submit.

## 7. Demo scenarios

The seeded demo should include:

1. Clean invoice — auto-paid and reconciled.
2. Duplicate invoice — rejected.
3. Wallet address changed — held with vendor-verification remediation.
4. Invoice exceeds PO — held.
5. Large invoice — escalated because it exceeds autonomy limit.
6. Valid invoice scheduled near its due date to preserve runway.

## 8. Test requirements

- Unit tests for every deterministic rule.
- State-transition tests that reject illegal transitions.
- Idempotency test proving two settlement requests produce at most one provider operation.
- Reconciliation mismatch tests for recipient, asset, amount, network, and failed transaction.
- Audit-chain verification test.
- Contract/provider integration tests run separately and skip without credentials.
- No test may depend on a public RPC for the default suite.

## 9. Delivery order

1. Domain models and persistence.
2. Evidence normalizer and three-way match.
3. Deterministic policy engine.
4. Decision service and audit chain.
5. Simulation settlement adapter and reconciliation.
6. Circle/Arc adapter.
7. API and seeded judge scenarios.
8. Frontend implementation using the separate reference map.

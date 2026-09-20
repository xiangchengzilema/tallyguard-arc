# Frontend reference map

This document is the binding UI reference for the frontend phase. It records which mature interaction to learn from and how it must be adapted to TallyGuard.

## Implemented workspace structure

The judge console is no longer one continuous demo page. It now uses five product-level workspaces while preserving a one-click three-minute path:

| Workspace | Purpose | Implemented modules |
| --- | --- | --- |
| Overview | Explain the product, show live boundaries, and start the judge flow | Editorial value proposition, four-stage control flow, runtime boundary, finance metrics, workflow entry points, bounded autonomous run |
| Payables | Perform day-to-day invoice review and settlement | Scenario/live-evidence rail, document and control workbench, queue, batch actions, schedules, incident recovery |
| Vendors | Review recipient identity before money moves | Vendor directory, verified Arc wallet, risk/autonomy facts, wallet history and change evidence |
| Policies | Define agent authority and handle exceptions | Runtime boundary, active policy, capacity, versioned policy editor, independent approval inbox |
| Audit | Prove what happened across the tenant | Searchable hash-linked event ledger, export/replay evidence, multi-tenant reliability results |

The desktop shell uses a persistent compact navigation rail. At phone widths it becomes a horizontally scrollable workspace switcher; each workspace remains a separate application state rather than an anchor into one long page.

## Extracted competitor observations

The following observations were taken from current public product pages and then translated, not copied:

- **Request Finance:** generous editorial spacing, a left application rail, and invoice/contact context presented together. TallyGuard uses this for the spacious overview and Vendors identity workspace.
- **Ramp:** a high-contrast primary action, stacked AP work queues, and separate review/payment lifecycle states. TallyGuard uses this for Payables, batch operations, and the approval inbox.
- **Safe:** an extremely restrained off-white shell with explicit transaction facts and minimal decoration. TallyGuard uses this for policy/settlement review and keeps chain details in receipts instead of the main queue.
- **Squads:** sparse operational chrome and recipient-oriented views. TallyGuard uses this for its compact navigation and wallet-trust directory.

Shared primitives observed across the references were neutral surfaces, one strong accent, thin separators, and an 8/16/24/32-style spacing rhythm. TallyGuard keeps its own teal evidence-control identity and Arc-specific proof model. Motion is limited to short workspace entry, active-navigation indication, and directional hover feedback; `prefers-reduced-motion` disables it.

## Visual direction

- Finance-operations product, not a crypto trading terminal.
- Warm off-white canvas, deep ink text, restrained teal for success and Arc violet for chain actions.
- Flat surfaces, thin borders, almost no shadows, no gradients.
- Tables and evidence carry the interface; KPI cards are secondary.
- Monospace is reserved for wallet addresses, hashes, rule IDs, and amounts requiring alignment.
- No blocking progress modal. Long work becomes a visible status row that can finish asynchronously.
- No fake live counters, animated network maps, or decorative terminal output.

## Page-by-page borrowing map

| TallyGuard page | Primary reference | Pattern to borrow | Arc/TallyGuard adaptation |
| --- | --- | --- | --- |
| Overview | Tulo + Request Finance | Ledger table surrounded by small treasury, invoice-status, and balance summaries | Show Arc USDC balance, seven-day payable amount, funds protected by policy, held invoices, and recent agent decisions |
| Payables Inbox | Ramp + Request Finance + Squads | Operational table, saved filters, status chips, bulk selection, due/overdue grouping | Tabs: `Ready`, `Needs review`, `Scheduled`, `Paid`; add evidence-completeness and policy-result columns |
| Invoice Decision | Stampli + Ramp | Document-centered review with collaboration and exception details around the invoice | Split view: source document on the left; extracted fields, three-way match, wallet verification, budget impact, and final action on the right |
| Decision Timeline | Payman + Safe transaction review | Show intent, policy evaluation, execution, and confirmation as separate stages | Timeline: `Extracted -> Matched -> Risk checked -> Policy evaluated -> Submitted -> Confirmed -> Reconciled` |
| Policies | Safe spending limits + AgentPayment billing rules | Vendor/token limits, periodic limits, allowlists, clear enabled state | Business-language policy builder for autonomy amount, daily budget, reserve, vendors, wallets, and manual-approval conditions |
| Vendors | Squads Recipients + Request Finance contacts | Recipient directory with amount/frequency/status and payment history | Verified Arc wallet, wallet-change warning, risk tier, autonomy limit, evidence history, last payment |
| Settlement Review | Safe transaction review | One explicit review screen before a controlled action, with recipient, token, amount, and policy effect | Display immutable payment-intent fields and Circle/Arc execution route; never let AI-generated prose replace the transaction facts |
| Audit Receipt | Tulo ledger + explorer receipt | Accounting entry and settlement record presented together | Evidence hashes, policy hash/version, decision reasons, Arc tx hash, block, confirmation, and reconciliation checks |
| Agent Command Bar | Payman | Natural-language query as a fast entry point | Secondary command bar for questions such as “What can be safely paid this week?”; it must navigate to structured records, not replace the application |

## Information architecture

```text
Overview
Payables
  - All
  - Ready
  - Needs review
  - Scheduled
  - Paid
Vendors
Treasury
Policies
Audit
```

## Payables table columns

```text
Invoice
Vendor
Amount
Due date
Evidence
Risk
Policy result
Payment status
```

Avoid exposing chain IDs and raw RPC terminology in this table. Those details belong in the settlement receipt.

## Invoice detail composition

### Header

- Vendor, invoice number, amount, due date
- Final action chip
- Primary action appropriate to the current state

### Left column

- Original invoice preview
- PO preview
- Delivery evidence preview

### Right column

- Extracted fields
- Three-way match matrix
- Vendor and wallet verification
- Duplicate check
- Treasury impact
- Policy results
- Agent explanation with evidence citations

### Bottom timeline

- Append-only audit events
- Arc settlement receipt after confirmation

## Interaction rules

- A red status must state both the cause and the exact remediation.
- “AI confidence” never overrides a deterministic failed check.
- Human approval is only offered when policy allows escalation; it is not a universal bypass.
- Payment buttons show asset, amount, recipient abbreviation, and network in the button-adjacent review area.
- Submitted, confirmed, and reconciled are separate statuses.
- When the public demo uses simulation, every affected screen displays `SIMULATION` visibly.
- Successful settlement always links to an explorer when a real Arc transaction exists.

## Judge-mode demo flow

The homepage should support a guided three-minute path:

1. Open a clean seeded invoice.
2. See every evidence check pass.
3. Let the agent choose `PAY` within its autonomy envelope.
4. Display the immutable payment intent.
5. Execute or replay the Arc testnet settlement.
6. End on the audit receipt.
7. Switch to a malicious wallet-change invoice and show it being held instantly.

The contrast between one autonomous success and one correctly blocked failure is more persuasive than a dashboard containing many shallow features.

## Explicit anti-patterns

- Dark cyberpunk console as the primary UI
- Full-screen loading/progress modals
- Chat-only interaction
- Nested cards inside cards
- More than one dominant CTA per state
- Status labels without an explanation
- Claiming `PAID` before chain confirmation and reconciliation
- Fake transaction hashes or unlabeled mock data
- Copying another product's layout, wording, brand colors, or assets verbatim

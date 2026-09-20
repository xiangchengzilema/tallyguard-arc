# Competitor and implementation blueprint

Last updated: 2026-09-19

## Decision

TallyGuard will not compete as a generic crypto treasury, invoice OCR tool, or AI wallet. Its product category is:

> Evidence-bound autonomous accounts payable on Arc.

The product must prove that a bill is payable before it moves USDC. The defensible unit is the **Payment Evidence Packet**: source documents, normalized facts, policy version, decision trace, settlement record, and reconciliation result bound together by hashes.

## Reference matrix

| Reference product | Mature capability to learn from | TallyGuard implementation on Arc | What we deliberately do differently |
| --- | --- | --- | --- |
| Request Finance | Invoice intake, approval states, stablecoin payouts, batch operations, audit trail, duplicate-payment awareness | Payables inbox, normalized invoices, explicit lifecycle, batch-ready payment intents, reconciliation records | Agent can make bounded decisions; every decision exposes evidence and policy, rather than only moving a human workflow online |
| Tulo | Multi-chain accounting, transaction classification, invoice-to-payment reconciliation, ERP-oriented ledger view | Arc transaction reconciliation, accounting-ready export, ledger entries tied to payment receipts | We focus on pre-payment evidence and controlled execution; Tulo is mainly accounting and review after transactions occur |
| Safe | Smart-account controls, signer thresholds, per-token spending allowance, modules and guards | Deterministic policy engine, daily/vendor/invoice limits, allowlists, emergency kill switch, optional contract guard/allowance layer | Business-document evidence is evaluated before the treasury control is invoked |
| Squads | Recipients, recurring payment schedules, subaccounts, due/overdue/paid states, review and approval queue | Vendor profiles, scheduled invoices, clear status model, review queue, future department budgets | Decisions are based on invoice evidence and risk, not only a preconfigured schedule |
| AgentPayment | A2A invoices, per-provider auto-pay rules, partial payments, retries, webhooks, idempotent agent-facing APIs | Vendor-scoped autonomy rules, payment intent API, idempotency keys, settlement webhooks, optional partial/scheduled payments | Rules incorporate PO, delivery, wallet-change, duplicate, budget, and compliance evidence rather than only amount/provider thresholds |
| Payman AI | Agent plans actions while an independent policy engine controls execution; kill switch and full decision audit | Separate probabilistic document/agent layer from deterministic policy and settlement layers; human escalation path | Built for business payables and Arc settlement instead of a general conversational banking assistant |
| Ramp / Stampli | OCR, coding, 2/3-way matching, fraud flags, approval routing, exception-first AP operations | Document extraction schema, deterministic three-way comparison, exception queue and concise approval summary | Evidence and policy decisions are hash-addressed and settlement is reconciled on Arc |
| Solana Payment Channels | Authorize a ceiling, meter activity, settle later, refund unused funds | Future option for repeated metered vendor services and batched settlement | Not part of the first MVP; invoice AP is the primary use case |

## Borrowing rules

We borrow proven **behavioral patterns**, not implementation or appearance:

- Recreate workflows from our own domain model.
- Do not copy source code, visual assets, product wording, icons, screenshots, or brand identity.
- Every borrowed pattern must gain an Arc-specific reason to exist.
- Avoid features included only to make the product appear large.
- A feature is complete only if it appears in the API, tests, demo data, UI state, and audit receipt where applicable.

## Arc-specific architecture mapping

| Product layer | Arc implementation |
| --- | --- |
| Treasury asset | USDC on Arc testnet; test-only fallback clearly labeled |
| Wallet execution | Circle developer-controlled wallet adapter where credentials are present; deterministic simulation adapter otherwise |
| Chain confirmation | Verify network, asset, sender, recipient, amount, status, block number, and transaction hash server-side |
| Business evidence | Canonical JSON representation of invoice, PO, delivery proof, vendor identity, and budget snapshot |
| Evidence integrity | SHA-256 content hashes plus a chained audit-event hash; optional onchain anchor after core flow is stable |
| Agent reasoning | Structured recommendation with cited evidence IDs and confidence; no direct private-key access |
| Policy enforcement | Versioned deterministic rules evaluated after the agent recommendation and immediately before settlement |
| Replay protection | Stable idempotency key derived from organization, vendor, invoice number, amount, currency, and document hash |
| Reconciliation | Payment intent is not `PAID` until the Arc transaction is confirmed and matched back to the invoice |

## Product differentiation checklist

The demo must visibly prove all of the following:

- [x] Same invoice submitted twice is stopped before settlement.
- [x] A changed vendor wallet is held even if every other invoice field matches.
- [x] Invoice, PO, and delivery evidence can independently agree or disagree.
- [x] AI cannot bypass a hard amount, budget, vendor, or wallet rule.
- [x] A normal low-risk invoice can complete without manual approval.
- [x] A reviewer can see exactly what would unlock a held invoice.
- [x] Every completed payment has a receipt and reconciliation result; real Arc/Circle proof remains the external Testnet acceptance gate.
- [x] Re-running a decision with the same evidence and policy produces the same policy outcome.
- [x] An auditor can verify an exported evidence packet without the application server and optionally re-prove a real transfer against Arc RPC.

## Primary sources

- Request Finance: https://www.requestfinance.com/
- Tulo: https://tulo.co/
- Safe agent spending limit: https://docs.safe.global/home/ai-agent-quickstarts/agent-with-spending-limit
- Safe spending-limit UI: https://help.safe.global/articles/3961440620-set-up-and-use-spending-limits
- Squads payments: https://docs.squads.so/main/navigating-your-squad/payments
- AgentPayment docs: https://agentpayment.network/docs
- Payman agentic banking: https://paymanai.com/agentic-banking
- Ramp accounts payable: https://ramp.com/accounts-payable
- Stampli AP automation: https://www.stampli.com/ap-automation/
- Solana payment channels: https://solana.com/payment-channels

# TallyGuard final submission copy

This file contains canonical copy for the Tameion Agents Hackathon form. Replace
only the explicitly marked external-evidence fields. Do not describe a pending
pilot, deployment, or Testnet transfer as complete.

## Project name

TallyGuard

## One-line description

Evidence-bound autonomous accounts payable for AI-operated businesses on Arc.

## Short description

TallyGuard reviews invoice, purchase-order, delivery, vendor-wallet, and
treasury evidence before an AI-operated business can pay USDC on Arc. An agent
interprets the evidence and plans work, while deterministic controls retain
payment authority. Every payment binds the source evidence, policy version,
approval state, Circle wallet operation, and independently verified Arc receipt
into a portable proof packet.

## Full project description

AI agents can prepare and execute financial work faster than a human finance
team can inspect it. That speed creates a control problem: a model may encounter
conflicting documents, prompt injection, a changed payout wallet, a duplicate
invoice, or a payment that exceeds its authority.

TallyGuard provides a bounded accounts-payable agent for AI-operated
businesses. It ingests immutable invoice, purchase-order, and delivery evidence;
checks the verified vendor payout identity and current treasury state; and
evaluates thirteen deterministic controls. The result is one of five explicit
actions: PAY, SCHEDULE, HOLD, REJECT, or ESCALATE.

The autonomous runner freezes the observed queue and proposed actions under
separate SHA-256 hashes. It may route exceptions, release due schedules, retry
an explicitly recoverable provider attempt, or settle only after the relevant
policy and approval checks pass again. Free-form model output never supplies the
amount, recipient, network, or payment authority.

For settlement, TallyGuard creates an idempotent Circle Developer Wallets
intent, polls the provider lifecycle, and independently verifies the exact
canonical-USDC transfer through Arc RPC. The reconciled invoice, policy,
approval, intent, receipt, audit events, and source hashes can be exported as a
content-addressed Payment Evidence Packet and verified without the application
database or server. The same responsive web console keeps the governed decision
and receipt operable at desktop, tablet, and 390px mobile widths without
separate clients.

## Why an agent is necessary

The agent converts a changing queue of financial work into a durable plan. It
summarizes source evidence, identifies the next safe action for each invoice,
routes human work only when policy requires it, and resumes eligible scheduled
or retryable work. Before execution, the service rechecks every frozen
assumption against current policy, wallet history, treasury capacity, approval
state, and workflow version. This gives operators autonomous throughput without
delegating financial authority to probabilistic text.

## Circle and Arc integration

- Circle Developer-Controlled Wallets submit USDC transfers with UUID v4
  idempotency and an independent hard transfer cap.
- TallyGuard polls Circle to a completed provider state and reconciles provider
  identifiers without trusting that status as final proof.
- Arc RPC independently verifies chain ID, receipt success, the canonical USDC
  contract, sender, recipient, six-decimal amount, transaction hash, and block.
- The guarded acceptance runner caps a real Arc Testnet transfer at 0.10 USDC,
  uses separate controlled treasury and recipient wallets, and produces JSON
  plus reviewer-facing proof artifacts.
- Arc Mainnet remains disabled by default and requires a dedicated low-balance
  wallet, explicit enable flag, hard limits, and a recorded approval reference.

## Innovation

TallyGuard treats payment authorization as a reproducible evidence problem. The
agent may reason about documents, but deterministic policy owns the financial
decision. The product joins three proof layers that usually remain separate:
source-document provenance, replayable financial controls, and independently
verified settlement. A reviewer can export the resulting packet and recompute
its evidence hash, policy hash, rule trace, decision ID, payment bindings, and
audit-event hashes offline.

## Reliability evidence

Synthetic engineering tests completed 10,000 of 10,000 mixed workflows across
100 isolated organizations. The run denied 100 of 100 cross-tenant reads, kept a
200-request duplicate storm to one provider submission and one transaction hash
while the accepted provider call was delayed 500 ms, and admitted only four of
100 simultaneous payments competing for a 5,000 USDC daily limit. A separate
50-tenant agent test verified every proof packet and reduced 100 concurrent
execute calls to one durable claim and one provider submission. These results
measure engineering reliability; they are not customer traction and moved no
funds.

## Genuine traction answer

Use exactly one of the following after the real pilot.

### If only the self-operated pilot is complete

We completed one genuine self-operated accounts-payable workflow using records
the operator was actually responsible for. The operator chose the acceptance
criteria before starting, recorded the prior-process and TallyGuard completion
times, and exported a verified Payment Evidence Packet. The resulting pilot
report content-addresses both the product proof and the operator attestation. We
present this as genuine self-operated usage, not as an external customer.

Insert only verified results:

- Pilot report: `PENDING_EXTERNAL`
- Criteria completed: `PENDING_EXTERNAL`
- Baseline time: `PENDING_EXTERNAL`
- TallyGuard time: `PENDING_EXTERNAL`
- Settlement evidence: `PENDING_EXTERNAL`

### If external operators also complete the flow

In addition to the self-operated pilot, `PENDING_EXTERNAL` external finance or
crypto operators completed the judge workflow. `PENDING_EXTERNAL` completed it
without assistance. The median time to a policy decision was
`PENDING_EXTERNAL`, and their structured feedback identified
`PENDING_EXTERNAL`. These are pilot users; do not call them paying customers
unless payment and permission to make that claim are separately documented.

## Repository, product, and media

- Public repository: `PENDING_EXTERNAL`
- Live judge console: `PENDING_EXTERNAL`
- Demo video under three minutes: `PENDING_EXTERNAL`
- Pitch deck: `submission/TallyGuard_Tameion_Pitch_v9.pptx`
- Arc Testnet transaction: `0xe8c6b06dbafbd55a7f9a5a9fbb276fe5b07ddf27be3ef84cc26281dcdf783adf`
- Verified Testnet artifact: `docs/reports/ARC_TESTNET_TREASURY_AND_WEB_SETTLEMENT_20260922.md`
- Pilot evidence: `PENDING_EXTERNAL`

## Technology

Python 3.13, Flask, SQLite, React, TypeScript, Carbon Design System, Circle
Developer-Controlled Wallets, Arc RPC, Docker, Gunicorn, and GitHub Actions.

## Safety and limitations

The public judge deployment runs in clearly labeled simulation mode so anyone
can exercise the full decision and reconciliation flow without credentials.
Each browser receives a fresh tenant and four role-separated sessions, so
concurrent judges cannot see or mutate one another's finance records.
Simulation receipts do not prove funds movement. SQLite and the in-process rate
limiter make the free single-process deployment a judge playground rather than
a horizontally scaled production service. Production migration requires
managed Postgres, shared rate limiting, external identity, secrets management,
and worker-restart acceptance across multiple processes.

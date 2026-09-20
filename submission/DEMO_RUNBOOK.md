# Three-minute demo runbook

Target duration: 2 minutes 45 seconds. Record at 1920 × 1080 with the browser at
100% zoom. Use the deployed judge console in a clean private window. Do not show
API credentials, wallet secrets, local environment files, or browser bookmarks.

## Pre-recording checklist

- Open the live console and allow a sleeping free instance to wake before
  recording.
- Confirm the runtime banner says `Judge simulation — no funds move` for the
  public interaction segment.
- Confirm the reliability panel loads both immutable reports.
- Prepare the verified Arc Testnet transaction in a separate Explorer tab only
  after the real acceptance transfer succeeds.
- Keep the current pitch deck available only as backup. The video should show
  the product, not narrate slides.
- Clear old demo data by redeploying or using a clean ephemeral instance so the
  queue starts empty.

## Script and actions

### 0:00–0:15 — Problem and authority boundary

Screen: top of the judge console.

Say:

> AI agents can process bills quickly, but a model should never invent the
> recipient, amount, or authority to pay. TallyGuard puts a deterministic,
> evidence-bound control layer between agent reasoning and Circle USDC on Arc.

Point briefly to the runtime boundary, deterministic authority, and Mainnet
lock.

### 0:15–0:45 — Durable autonomous plan

Action: click **Load mixed queue**, then **Plan current queue** if needed.

Say:

> One agent run scans the tenant queue and freezes both the observed state and
> its proposed actions under separate hashes. This queue contains a safe
> payment, an invoice needing independent approval, a wallet-risk hold, and a
> future schedule.

Show the four proposed actions and the plan hash.

### 0:45–1:05 — Safe execution

Action: click **Execute safe actions**.

Say:

> Execution rechecks the invoice version, current policy, approval binding,
> schedule eligibility, treasury capacity, and retry state. Only the cleared
> item settles. The exception is routed to another role, while the hold and
> future schedule stay blocked.

Show the item outcomes: settled, routed, and skipped.

### 1:05–1:35 — Evidence and policy decision

Action: return to **Control lab**, select **Clean three-way match**, and run it.

Say:

> This decision binds the invoice, purchase order, delivery evidence, verified
> vendor wallet, treasury snapshot, and policy version. Thirteen controls pass.
> The AI recommendation remains visible, but it has zero payment authority.

Show `13/13 clear`, the policy and evidence hashes, and the wallet cooldown
control. Click **Verify replay**.

### 1:35–1:55 — Failure protection

Action: run **Vendor wallet change held**.

Say:

> A changed payout address fails closed. TallyGuard records the mismatch and
> refuses to call the settlement provider. Even a verified replacement enters
> a two-day cooldown before a fresh decision may authorize payment.

Show the HOLD result and remediation.

### 1:55–2:15 — Portable proof

Action: return to the clean invoice, click **Download evidence packet**, then
show the displayed SHA-256. Briefly show **Export ledger**.

Say:

> Auditors can export a content-addressed Payment Evidence Packet and verify the
> evidence, policy, rule trace, decision, receipt, and audit hashes without the
> application server. Reconciled payments also export to a spreadsheet-safe
> accounting ledger with its own content hash.

### 2:15–2:30 — Reliability

Action: scroll to the checked-in reliability panel.

Say:

> Reproducible tests completed ten thousand workflows with no duplicate
> payments, denied every cross-tenant probe, and admitted only the payments that
> fit one shared treasury limit. These are synthetic engineering results, not
> customer traction.

### 2:30–2:45 — Real Arc proof and close

Use this segment only after the real Arc Testnet acceptance succeeds.

Action: show the acceptance artifact, then the Arc Explorer transaction.

Say:

> This capped Testnet payment used a dedicated Circle wallet and a separate
> controlled recipient. Circle completed the request, and TallyGuard
> independently matched the exact canonical-USDC Transfer on Arc. TallyGuard
> lets the agent do the work while evidence and policy retain control.

If the Testnet proof is still pending, do not substitute a simulated receipt.
End at 2:30 after the reliability panel and say:

> The live Circle adapter and independent Arc verifier are implemented. The
> public product stays in safe simulation mode until the dedicated Testnet
> credentials and funds are present.

## Editing notes

- Cut all load waits, pointer hunting, and repeated scrolling.
- Keep captions short: `SEALED EVIDENCE`, `13 CONTROLS`, `ROLE-SEPARATED`,
  `EXACTLY ONCE`, and `ARC VERIFIED` only when each claim is on screen.
- Never overlay `ARC VERIFIED` on a simulator receipt.
- Blur only accidental personal information. Re-record any frame containing a
  secret, seed phrase, entity secret, API key, or private wallet detail.
- Export 1080p H.264. Review the final upload from a logged-out browser before
  placing its URL in the submission form.

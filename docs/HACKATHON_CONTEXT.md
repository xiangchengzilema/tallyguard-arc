# Tameion Agents Hackathon context

## Event

- Name: Tameion Agents Hackathon
- Hosts: Canteen × Circle × Arc
- Format: online, two weeks
- Build window: September 27 through October 10, 2026
- Submission deadline: October 10, 2026 at 11:59 PM ET (approximately October 11 at 11:59 AM in Beijing)
- Luma: https://luma.com/ivroypr5
- Event brief: https://tameion.thecanteenapp.com/
- Submission form: https://forms.gle/BBWrdfuircrKiG2i6

## Project identity

- Name: TallyGuard
- One line: Evidence-bound autonomous accounts payable for AI-operated businesses on Arc.
- Primary RFB: AP/AR Automation Agent
- Secondary fit: Compliance Intelligence Agent
- Repository directory: `D:\币圈项目\arc空投\tallyguard-arc`
- Prior submissions are separate projects and are out of scope.

## Judging priorities

- 30% agentic sophistication
- 30% genuine traction
- 20% Circle tool usage
- 20% innovation

## Required final artifacts

- Public GitHub repository
- Recorded product walkthrough under three minutes
- Project description and traction answers

Strongly encouraged:

- Live product URL
- Arc transaction evidence
- Reproducible setup and architecture diagram
- Multi-tenant reliability report

## Current build status

- Luma registration completed
- Independent local Git repository created
- Product, backend, frontend-reference, and competitor blueprints written
- Multi-tenant policy and settlement safety kernel implemented
- Arc Mainnet and Testnet configuration implemented
- Immutable PDF/image/JSON evidence intake and field-level provenance implemented
- Tenant-scoped exact source-hash and invoice-fingerprint controls are live. Same-vendor invoice-number reuse and high-similarity sealed fields now route to independent review with the previous request ID, even when a file or number changes. PO and delivery records can support legitimate partial invoices, but cumulative overuse is held at evaluation and blocked again by an atomic payment-intent check. These are deterministic review/control signals, not an AI fraud verdict or full-document OCR.
- Missing PO and delivery evidence now fail closed with explicit remediation
- Agent recommendations are separated from deterministic, evidence-bound control decisions
- Versioned policy history, policy diffs, and scheduled-payment outcomes implemented
- Human exception approval is segregated and explicitly bound to settlement intent
- Authenticated Flask API now exposes health/readiness and tenant-scoped invoice workflows
- Vendor onboarding and wallet replacement now persist in SQLite with tenant-scoped read APIs, explicit verification references, stale-wallet rejection, and append-only history
- Immutable policy versions and source-referenced treasury snapshots now persist across restarts and are available through role-scoped APIs
- Real uploaded invoice, purchase-order, and delivery evidence now flows through the verified vendor, active policy, treasury snapshot, and Arc route into an immutable decision
- Every real evaluation now records a non-authoritative agent recommendation with confidence, reason codes, and immutable evidence-package citations beside the deterministic policy result
- The React judge console now includes a fresh-evidence mode that creates and uploads a new three-way-match package through the authenticated API instead of relying only on seeded scenarios
- Playwright browser acceptance verified the fresh package, complete deterministic policy trace, cited agent recommendation, and reconciled simulation receipt without console errors
- The live console now supports user-selected three-way-match JSON and labelled text-layer PDFs, validates cross-document IDs and USDC fields, previews normalized values plus page/confidence/hash provenance, and persists only after explicit confirmation
- A one-click browser sample loads three reproducible PDFs and runs the real authenticated extraction path, so a public reviewer does not need repository fixtures
- A role-separated auditor timeline exposes linked event hashes and verified-chain status, including the settlement reconciliation event after payment
- An optional hosted-model analyst is now available behind environment-only credentials; its output cannot include payment parameters and cannot override deterministic authorization
- A reproducible real-HTTP deployment smoke now verifies the built console, probes, three separate roles, deterministic decision, simulation settlement, reconciliation, hashed accounting export, and session revocation; its checked-in report is explicitly synthetic and CI reruns all eight checks
- Eight judge scenarios and the segregated large-invoice approval path are available through API
- Circle's official developer-wallet SDK is isolated behind a live settlement adapter
- Circle completion is independently reconciled against Arc RPC and the exact USDC transfer event
- Exported Payment Evidence Packets now have a standalone verifier that needs no application database or server; it recomputes the deterministic decision and can optionally re-prove a real transfer against Arc RPC
- Reconciled payments can be exported as a content-addressed, spreadsheet-safe accounting CSV from the judge console
- Live settlement requires UUID v4 idempotency and an adapter-level hard transfer cap
- The locked Testnet acceptance command completed a real `0.01 USDC` Circle developer-wallet transfer on Arc Testnet at block `63409495`; Arc RPC verified the exact canonical-USDC event, idempotent replay reused the durable receipt, and the public proof is recorded in `docs/reports/ARC_TESTNET_ACCEPTANCE_20260922.md`
- A separate private-browser acceptance on 2026-09-25 completed the full user submission → finance review → independent approval → finance click-to-settle → requester receipt path with a capped real `0.01 USDC` Arc Testnet transfer at block `63900352`; the exact canonical-USDC Transfer was independently rechecked against Arc RPC. See `docs/reports/ARC_TESTNET_BROWSER_ACCEPTANCE_20260925.md`. The public judge deployment is live at `https://tallyguard.online`, with the previous sslip.io hostname and `https://tallyguard-arc.onrender.com` retained as fallbacks; all public origins intentionally stay in simulation mode.
- The guarded 50-agent random-time Arc Testnet campaign completed 50 synthetic workflows: 40 confirmed transfers of `0.01` test USDC each, 5 declines, and 5 evidence holds. The user approved rotating controlled recipient wallets; unique transaction receipts were verified and public activity shows a read-only summary. See `docs/TESTNET_CAMPAIGN_50.md` and the locked report in `artifacts/testnet-campaign/`. This traffic must never be presented as genuine customers or human approvals.
- A genuine-pilot protocol and content-addressed report command bind operator-attested timing and predeclared acceptance criteria to a verified Payment Evidence Packet without calling self-operated usage a customer or simulation an onchain transfer
- The current ten-slide pitch deck is `submission/TallyGuard_Tameion_Pitch_v9.pptx`; it uses the evidence-control product capture, includes the 500 ms slow-provider concurrency evidence, reflects the eight-check deployment gate, and includes a desktop/390px comparison while accurately labeling the remaining Testnet, pilot, deployment, and video gates. The live product now evaluates 15 controls, including finance-configured single-payment and daily no-touch autonomy limits.
- Automated test suite passing
- Architecture, trust-boundary, evidence-to-payment, and tenant-isolation diagrams documented
- Security model covers agent prompt injection, wallet substitution, tenant isolation, idempotency, provider mismatch, mainnet gating, secret handling, and honest demo limitations
- Runtime safety boundary is visible in the judge console and sourced from the readiness API
- Live Circle operation no longer depends on public demo-session issuance: an
  explicit local command provisions four durable single-role identities and
  emits short-lived bearer sessions only to the trusted invoking terminal
- The live web console accepts that bundle through a private memory-only gate,
  verifies each expected role plus one shared tenant, and fails before loading
  finance data if a token is stale, swapped, or cross-tenant
- A durable bounded-autonomy runner plans a mixed tenant AP queue, hashes both observed state and proposed actions, routes policy escalations, releases due schedules through current-policy revalidation, and settles only a current `PAY`, an exactly bound independent approval, or an explicitly retryable attempt under the approver role
- The judge can export a content-addressed Agent Run Proof Packet with plan-hash verification, execution outcomes, approval bindings, related audit events, and tenant-chain verification
- A one-click autonomy showcase creates and plans a mixed four-invoice queue so judges and the demo video can show payment, approval routing, wallet-risk remediation, and due-date waiting without repetitive setup
- Desktop, 768px tablet, and 390px mobile browser acceptance completed the
  governed-decision path without horizontal overflow, console errors, or warnings
- Final media will keep the desktop finance workflow primary and reserve 8–10
  seconds for a 1440px → 768px → 390px comparison of the same evidence,
  decision, and receipt; this is one responsive web product rather than
  separate clients

Milestones 1, 2, 3, 4, 5, and 6 are complete. Milestone 7 awaits genuine operator evidence; Milestone 8 awaits
publication, deployment, and video. SQLite persistence now retains
tenant-scoped evidence, provenance, vendor wallet verification history, invoice state, optimistic versions, and transition history
across process restarts. The real Arc Testnet acceptance gate passed on 2026-09-22; no mainnet
credential or balance is needed for the hackathon demo.

## Submission discipline

### 2026-09-30 post-submission hardening

The existing public URL and repository remain unchanged. This maintenance
release fixes stale supplier-wallet authorization, simulator control resets,
unconfirmed reservations lost on balance refresh, and unverified manual live
balances. It also adds an immutable, tenant-scoped correction chain for held or
rejected invoices, safe partial-upload retries, and reliable checkbox selection.
Corrections keep the original evidence sealed and cannot branch off a request
with a payment intent. Historical Arc Testnet receipts are not recreated.

The public deployment remains simulation-only. Updating GitHub and deploying
the matching source/frontend build are separate steps for the native Tencent
service. These fixes do not require a new Arc application or changed submission
links; no external submission is automatically edited by this release.

- Submit an initial working version early and update it before the deadline.
- Preserve activity-window commits, Arc transaction hashes, load-test results, and pilot evidence.
- Distinguish synthetic reliability testing from genuine pilot usage.
- Keep judge paths short: seeded scenarios, visible decisions, visible failure protection, visible settlement proof.

## Post-hackathon Arc Request for Builders plan

After the Tameion Agents Hackathon submission is formally completed and proof
of submission is saved, submit TallyGuard through the Arc Request for Builders
support path as well.

- Official RFB: https://www.arc.io/blog/the-unfinished-business-of-finance-machine-commerce-and-global-money
- Primary funding target: Circle Developer Grants — https://www.circle.com/grant
- Early-stage fallback: Arc Microgrants — http://dorahacks.io/hackathon/arc-microgrants
- Current-stage non-target: Arc Builders Fund / investor-deck route; reconsider
  only after the product has genuine usage or company-scale traction.
- Architects Program is a separate Arc House community/program identity. It is
  not a substitute for the grant application.

RFB positioning:

> TallyGuard is intelligent accounts payable for agentic commerce: evidence-bound invoice approval, finance-configured autonomous spending controls, independent approval, and USDC settlement on Arc.

The product directly supports the `Agentic Economy` and `Intelligent Account`
frontiers and has a supporting fit with `Global Money / Programmable Trade
Workflows`. Do not present it as `Onchain Credit` without real lending,
collateral, or underwriting functionality.

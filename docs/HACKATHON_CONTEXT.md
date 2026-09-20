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
- Tenant-scoped exact and near-duplicate invoice detection implemented
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
- A reproducible real-HTTP deployment smoke now verifies the built console, probes, three separate roles, deterministic decision, simulation settlement, reconciliation, and hashed accounting export; its checked-in report is explicitly synthetic and CI reruns all seven checks
- Eight judge scenarios and the segregated large-invoice approval path are available through API
- Circle's official developer-wallet SDK is isolated behind a live settlement adapter
- Circle completion is independently reconciled against Arc RPC and the exact USDC transfer event
- Exported Payment Evidence Packets now have a standalone verifier that needs no application database or server; it recomputes the deterministic decision and can optionally re-prove a real transfer against Arc RPC
- Reconciled payments can be exported as a content-addressed, spreadsheet-safe accounting CSV from the judge console
- Live settlement requires UUID v4 idempotency and an adapter-level hard transfer cap
- The locked Testnet acceptance command uses the just-observed Circle wallet balance and will emit both JSON and reviewer-friendly Explorer proof artifacts after the first real transfer
- A genuine-pilot protocol and content-addressed report command bind operator-attested timing and predeclared acceptance criteria to a verified Payment Evidence Packet without calling self-operated usage a customer or simulation an onchain transfer
- The current ten-slide pitch deck is `submission/TallyGuard_Tameion_Pitch_v7.pptx`; it uses the current thirteen-control product capture, includes the 500 ms slow-provider concurrency evidence, and accurately labels the remaining Testnet, pilot, deployment, and video gates
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
- Desktop and 390px mobile browser acceptance completed the autonomous plan-to-settlement path with no console errors or warnings
- Final media will keep the desktop finance workflow primary and reserve 6–8
  seconds for a desktop-to-390px comparison of the same evidence, decision, and
  receipt; this is one responsive web product rather than separate clients

Milestones 1, 2, 3, 5, and 6 are complete. Milestone 4 awaits the external Circle
Testnet acceptance transfer; Milestone 7 awaits genuine operator evidence; Milestone 8 awaits
publication, deployment, and video. SQLite persistence now retains
tenant-scoped evidence, provenance, vendor wallet verification history, invoice state, optimistic versions, and transition history
across process restarts. A real Arc Testnet transfer is the next external acceptance gate; no
mainnet credential or balance is needed yet.

## Submission discipline

- Submit an initial working version early and update it before the deadline.
- Preserve activity-window commits, Arc transaction hashes, load-test results, and pilot evidence.
- Distinguish synthetic reliability testing from genuine pilot usage.
- Keep judge paths short: seeded scenarios, visible decisions, visible failure protection, visible settlement proof.

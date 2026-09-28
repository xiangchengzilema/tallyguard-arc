# Release and submission checklist

Deadline: October 10, 2026 at 11:59 PM ET, approximately October 11 at 11:59 AM
in Beijing. Finish the external steps at least 24 hours earlier.

## Repository release gate

- [x] Run `python -m pytest` and preserve the passing total: 276 tests on
  2026-09-21.
- [x] Run `python -m compileall -q src tests`.
- [x] Run `npm ci && npm run build` inside `web/`; npm reported zero
  vulnerabilities.
- [x] Run `tallyguard-deployment-smoke` against the built frontend; all eight
  public-safe HTTP checks passed with no funds moved.
- [x] Run the required current-tree scan and the full reachable-history secret
  scan before the release commit; inspect paths only and never print a matched value.
- [x] Confirm no `.env`, database, credential, wallet secret, or temporary pilot
  document is tracked.
- [x] Confirm README links resolve to tracked files available from a clean
  checkout; the release audit enforces this.
- [x] Create the public GitHub repository after explicit publication approval,
  then push the full activity-window history.
- [x] Verify the repository without browser credentials (HTTP 200 and README visible).

## Live judge console

- [x] Deploy the checked-in `render.yaml` in public-safe simulation mode.
- [x] Confirm `/api/health` returns `ok`.
- [x] Confirm `/api/readiness` reports simulation, disabled funds movement, and
  disabled Mainnet.
- [ ] Complete the mixed autonomous queue from a clean private browser.
- [ ] Complete clean-payment, wallet-change, and provider-recovery scenarios.
- [ ] Download an evidence packet and accounting ledger.
- [ ] Check desktop and mobile layout after the final deployment.
- [ ] Test the URL after idle spin-down and record the expected cold-start delay.
- [ ] Connect the public `#activity` view to a sanitized, read-only Arc Testnet
  progress source. Verify receipt hashes against Explorer, and confirm no
  private campaign database, wallet inventory, or Circle credential is
  published. If the source is unavailable, show the unavailable state rather
  than frozen counters.

## Real Arc Testnet acceptance

- [x] Store Circle credentials locally in environment variables. Never paste
  them into chat or commit them.
- [x] Create the isolated treasury and recipient wallet pair with the guarded
  setup command.
- [x] Fund only the dedicated Testnet treasury wallet.
- [x] Run read-only preflight and confirm wallet, network, balance, RPC, and USDC
  contract checks.
- [x] Execute one transfer of at most 0.10 Testnet USDC with the exact consent
  phrase.
- [x] Confirm the JSON and Markdown acceptance artifacts agree.
- [x] Complete a private local browser-to-receipt run on Arc Testnet: user
  uploads/reviews three sources, finance requests independent approval,
  approver authorizes, finance clicks final settlement, and the requester sees
  the confirmed `0.01 USDC` receipt. Preserve the screenshots and independent
  Arc RPC check in `docs/reports/ARC_TESTNET_BROWSER_ACCEPTANCE_20260925.md`.
- [ ] Run the standalone packet verifier with Arc RPC enabled.
- [ ] Open the Explorer URL in a logged-out browser.

## Optional controlled Mainnet proof

- [ ] Do not begin until every Testnet settlement-evidence item above passes.
- [ ] Create a dedicated low-balance Arc Mainnet treasury and separate controlled recipient.
- [ ] Keep `TALLYGUARD_MAX_TRANSFER_USDC` at or below `0.10` and the proof amount at or below `0.01`.
- [ ] Run the full read-only preflight with `ARC-MAINNET` and the explicit mainnet gate.
- [ ] Manually invoke `tallyguard-mainnet-acceptance` with the exact consent phrase.
- [ ] Verify requester and resolver identities differ, replay reuses the same receipt, and the Arc Explorer proof resolves publicly.
- [ ] Immediately disable the mainnet runtime gate after preserving the content-addressed report.

## Genuine usage evidence

- [ ] Choose a real self-operated or external pilot workflow.
- [ ] Write the acceptance criteria before starting.
- [ ] Redact private source-document data where necessary.
- [ ] Record baseline and TallyGuard completion times truthfully.
- [ ] Export the final Payment Evidence Packet.
- [ ] Complete the anonymized attestation and reporting consent.
- [ ] Generate the content-addressed pilot report.
- [ ] Keep synthetic test results separate from genuine usage claims.

## Presentation and video

- [x] Render and inspect all ten slides in `TallyGuard_Tameion_Pitch_v9.pptx`;
  package, layout, font, editable-table, and first-party import validation passed.
- [x] Keep only the current v9 deck in the public submission package; obsolete
  deck binaries remain recoverable from Git history but are not exposed to judges.
- [ ] Replace no pending claim with a placeholder that looks complete.
- [x] Record the product walkthrough from the final deployed commit; preserve
  the 3:12 owner cut and the separately reviewed 2:59 submission cut.
- [x] Keep the final product film under three minutes (2:59 at 1920 × 1080,
  30 fps, with recorded web interaction and narration).
- [x] Show both complete public-demo paths from recorded web interaction:
  independent approval followed by separate finance settlement, and a
  finance-enabled small-payment autonomous settlement. Show each requester's
  resulting status and receipt; label these filmed payments as simulation.
- [x] Keep the desktop/tablet/mobile comparison as optional source material,
  not a required insert in the web-first final cut.
- [x] Render and visually inspect the optional responsive insert at 1440 × 900
  (`assets/tallyguard-responsive-broll.webm`, 9 seconds, desktop → tablet →
  mobile → all three widths). It is intentionally outside the web-first cut.
- [x] Include one desktop/mobile comparison frame in the final deck or submission
  gallery so the responsive work remains visible outside the video (slide 10).
- [x] Include real Explorer proof only after independent verification succeeds;
  distinguish historical Testnet evidence from filmed simulation clicks.
- [x] Upload the 2:59 cut as an unlisted YouTube video and verify the watch
  page returns `playabilityStatus: OK` without account cookies:
  https://youtu.be/Jjc9D-k1UXY . YouTube rounds the UI runtime to `3:00`;
  the source container is 179.05 seconds, below the form's 180-second cap.

## Final form

- [ ] Fill the actual form from `TAMEION_FORM_FILL_GUIDE.md`; leave conditional
  pilot claims out when no pilot evidence exists. Do not convert the 50
  automated workflows into a human-user count.
- [ ] Describe the AP/AR Automation Agent fit and supporting compliance
  controls in the project text. The current form has no separate track picker.
- [ ] Recheck repository, live product, video, deck, Explorer, and pilot links.
- [x] Run `tallyguard-deployment-smoke --base-url <LIVE_URL> --output
  artifacts/remote-deployment-smoke-final.json` from outside the host and
  confirm all eight public workflow checks pass while funds movement and
  mainnet stay disabled. See `docs/reports/REMOTE_DEPLOYMENT_SMOKE_20260926.md`.
- [ ] Save screenshots of the completed form before submitting.
- [ ] Submit before the internal deadline, then reopen the confirmation page or
  email and preserve proof of submission.

## Post-hackathon Arc RFB follow-on

Begin this section only after the Tameion submission is complete and its
confirmation evidence has been preserved.

- [ ] Reuse the verified public repository, live product, video, deck, Arc
  Testnet transaction proof, and reliability evidence to prepare an Arc
  Request for Builders application package.
- [ ] Position TallyGuard as **intelligent accounts payable for agentic
  commerce**: evidence-bound invoice approval, finance-configured autonomous
  spending limits, independent approval, and USDC settlement on Arc.
- [ ] Lead with the `Agentic Economy` and `Intelligent Account` RFB themes;
  include `Global Money / Programmable Trade Workflows` as a supporting fit.
  Do not claim an `Onchain Credit` fit unless lending or underwriting is
  actually implemented.
- [ ] Recheck the current eligibility and terms, then apply first to the Circle
  Developer Grant program: https://www.circle.com/grant
- [ ] Treat Arc Microgrants as an early-stage fallback only, and do not submit
  overlapping applications if the current program terms prohibit it:
  http://dorahacks.io/hackathon/arc-microgrants
- [ ] Save the completed application, confirmation page or email, submission
  date, and any follow-up requirements in the project records.

# Release and submission checklist

Deadline: October 10, 2026 at 11:59 PM ET, approximately October 11 at 11:59 AM
in Beijing. Finish the external steps at least 24 hours earlier.

## Repository release gate

- [ ] Run `python -m pytest` and preserve the passing total.
- [ ] Run `python -m compileall -q src tests`.
- [ ] Run `npm ci && npm run build` inside `web/`.
- [ ] Run `tallyguard-deployment-smoke` against the built frontend.
- [ ] Run the required current-tree scan and the full reachable-history secret
  scan before the release commit; inspect paths only and never print a matched value.
- [ ] Confirm no `.env`, database, credential, wallet secret, or temporary pilot
  document is tracked.
- [ ] Confirm README links resolve from a clean checkout.
- [ ] Create the public GitHub repository only after explicit publication
  approval, then push the full activity-window history.
- [ ] Verify the repository from a logged-out browser.

## Live judge console

- [ ] Deploy the checked-in `render.yaml` in public-safe simulation mode.
- [ ] Confirm `/api/health` returns `ok`.
- [ ] Confirm `/api/readiness` reports simulation, disabled funds movement, and
  disabled Mainnet.
- [ ] Complete the mixed autonomous queue from a clean private browser.
- [ ] Complete clean-payment, wallet-change, and provider-recovery scenarios.
- [ ] Download an evidence packet and accounting ledger.
- [ ] Check desktop and mobile layout after the final deployment.
- [ ] Test the URL after idle spin-down and record the expected cold-start delay.

## Real Arc Testnet acceptance

- [ ] Store Circle credentials locally in environment variables. Never paste
  them into chat or commit them.
- [ ] Create the isolated treasury and recipient wallet pair with the guarded
  setup command.
- [ ] Fund only the dedicated Testnet treasury wallet.
- [ ] Run read-only preflight and confirm wallet, network, balance, RPC, and USDC
  contract checks.
- [ ] Execute one transfer of at most 0.10 Testnet USDC with the exact consent
  phrase.
- [ ] Confirm the JSON and Markdown acceptance artifacts agree.
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

- [ ] Open `TallyGuard_Tameion_Pitch_v8.pptx` and confirm all ten slides.
- [x] Keep only the current v8 deck in the public submission package; obsolete
  deck binaries remain recoverable from Git history but are not exposed to judges.
- [ ] Replace no pending claim with a placeholder that looks complete.
- [ ] Record the product walkthrough from the final deployed commit.
- [ ] Keep the final video under three minutes.
- [ ] Include an 8–10 second 1440px → 768px → 390px comparison of the same
  governed decision and receipt; describe it as one responsive web app, not
  three apps.
- [x] Include one desktop/mobile comparison frame in the final deck or submission
  gallery so the responsive work remains visible outside the video (slide 10).
- [ ] Include real Explorer proof only after independent verification succeeds.
- [ ] Upload the video and verify playback without account access.

## Final form

- [ ] Copy from `FINAL_SUBMISSION_COPY.md` and replace every
  `PENDING_EXTERNAL` value with a real result or remove the line.
- [ ] Select the AP/AR Automation Agent track and mention the secondary
  Compliance Intelligence fit in the description.
- [ ] Recheck repository, live product, video, deck, Explorer, and pilot links.
- [ ] Run `tallyguard-deployment-smoke --base-url <LIVE_URL> --output
  artifacts/remote-deployment-smoke.json` from outside the host and confirm all
  eight public workflow checks pass while funds movement and mainnet stay disabled.
- [ ] Save screenshots of the completed form before submitting.
- [ ] Submit before the internal deadline, then reopen the confirmation page or
  email and preserve proof of submission.

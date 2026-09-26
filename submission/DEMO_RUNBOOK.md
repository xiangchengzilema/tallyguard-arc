# Final web demo runbook

Target: 2:45–2:55, 1920 × 1080, browser at 100% zoom. Record only after the
deployed version and the same-request handoff have passed a complete rehearsal.
The public interactive workspace is a **simulation**. The separate Arc activity
page displays a verified, read-only Testnet snapshot with exact Explorer links.
Never describe a simulated public-workspace receipt as an onchain transfer.

## Before recording

- Wake the deployed site, then verify the home page, user portal, finance
  backend, invoice review, and `Arc activity` at the recording width. Inspect
  fresh screenshots for clipping, overlap, and stale figures.
- Submit one request through the user portal using the provided sample PDF
  sources. Adjust the visible key fields if needed. Keep its request ID and
  source hash on screen, and confirm the finance backend shows the **same ID**.
- Rehearse an amount that enters the independent-approval route. Verify that
  the requester sees its actual status and the finance decision. Do not assume
  the route from the amount alone; check the displayed policy result first.
- Confirm the public activity snapshot's processed/paid totals and open one
  `View on Arc Explorer` link in a logged-out tab. Record its snapshot time;
  scheduled, held, and declined requests must not have a transaction link.
- Keep API keys, wallet secrets, local files, browser bookmarks, and personal
  account details off screen. Keep the responsive insert optional; desktop web
  workflow and readable evidence take priority.

## Shot sequence

| Time | Screen and action | Point to make |
| --- | --- | --- |
| 0:00–0:15 | Home hero and three roles | TallyGuard is evidence-bound accounts payable for finance teams, not an unrestricted AI payer. |
| 0:15–0:45 | User portal: load sample sources, review/edit key fields, submit | The requester supplies evidence and sees exactly what is being requested. The source is sealed before evaluation. |
| 0:45–1:00 | User tracking page: request ID and status | Submission, checks, finance decision, settlement, and receipt are separate visible steps. |
| 1:00–1:35 | Finance backend: find the same request ID, open invoice review | Finance sees the original evidence, policy checks, payout destination, amount, and treasury effect. |
| 1:35–2:00 | Independent approval or a policy hold; show reason and requester feedback | AI can recommend; configured policy and the finance role retain authority. Approval is not the same as money sent. |
| 2:00–2:30 | Public `Arc activity`: processed/settled/not-paid states; select a paid entry and open its exact Explorer receipt | This is a separate read-only record of confirmed **Arc Testnet** USDC transfers. Do not conflate it with the simulated request just shown. |
| 2:30–2:50 | Reliability proof and closing frame | Show test counts as engineering evidence, not customer traction. End on the product and Arc proof. |

Leave five seconds for a clean close. Cut loading waits and pointer hunting. If
the same-request user → finance handoff, denial reason, or Explorer link fails
rehearsal, fix it before recording rather than covering the gap with narration.

The nine-second `assets/tallyguard-responsive-broll.webm` is available if it
improves the final edit. It shows three **web viewport widths**, not separate
desktop/mobile apps. The final walkthrough video and upload are still pending.

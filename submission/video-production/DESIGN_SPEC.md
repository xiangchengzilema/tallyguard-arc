# TallyGuard product film — v19 production spec

Status: final render and QA. Target: 03:12, 1920 × 1080, 30 fps.

Audience: Tameion judges and a possible company buyer. Show who submits a request, how evidence and AI controls operate, where finance authority begins, why a second approver is needed, and how the requester sees the final receipt. Recorded pages are the product; typography and motion frame the story without replacing the interface.

## Truth and safety rules

- The filmed manual and no-touch payment workflows are **public-workspace simulations**. No funds move when those demo buttons are clicked.
- Arc Activity and Explorer at the end are **historical, separate Testnet proof**. Those transactions are not the filmed requests.
- No keys, personal accounts, or secret-bearing settings in footage. No Mainnet operation or new Testnet transfer is needed for filming.
- The 50 runs are workflow tests, and the 40 transfers are 0.01 test USDC Testnet results — not customers or production usage.

## Story and shot map

| Time | Screen | What the viewer learns |
| --- | --- | --- |
| 00:00–00:14 | Animated six-pain opener | Fragmented proof, duplicates and wallet risk, slow approvals, unclear reasons, bank cutoffs, unchecked AI authority. |
| 00:14–00:32 | Real homepage animation, five steps | AI checks evidence while finance retains authority. |
| 00:32–00:43 | Requester evidence intake | Invoice, purchase order and delivery proof; human-confirmed fields. |
| 00:43–00:55 | Live submission and centered result modal | Same ID, exact waiting stage, next step, no funds sent. |
| 00:55–01:07 | Finance queue and invoice review | Selected row visibly checked; evidence and policy controls. |
| 01:07–01:18 | Independent approver portal | Separate authority and recorded decision reason. |
| 01:18–01:28 | Finance final settlement | Approval and payment are distinct actions; demo is simulated. |
| 01:28–01:36 | Requester receipt | Same ID, finance note, paid state and receipt. |
| 01:36–01:49 | Finance autonomy policy | Opt-in 50-per-request / 450-per-day caps and emergency stop. |
| 01:49–02:00 | 40 USDC request | Evidence and headroom checked against the active policy. |
| 02:00–02:08 | Automatic simulated settlement | In-limit request closes without independent approval. |
| 02:08–02:26 | Rejection and correction | Approver writes the wallet reason; requester opens it, clicks correction, sees the same reason. |
| 02:26–02:40 | Mixed outcomes | Settle, human approval, schedule, hold for remediation. |
| 02:40–02:54 | Arc / Circle context | Always-on programmable USDC, with actual historical Testnet evidence kept separate. |
| 02:54–03:05 | Historical Arc proof | Explorer receipt for a confirmed 0.01 test USDC transfer, separate from demo. |
| 03:05–03:12 | Brand close | Agent speed, finance control, proof on Arc. |

Visual tone: ink and restrained teal for the opener, ivory product surfaces, purple evidence connectors. English screen and narration; Chinese translation supplied only for owner review. A compact lower-third must leave the actual product controls visible.

## Acceptance checks

1. Verify `TG-33628CF4` and 1,486.25 USDC across submit modal, finance queue, approver, settlement, and requester receipt.
2. Verify the automatic request is 40 USDC and the visible policy is 50 per request / 450 per day.
3. Inspect a beginning/middle/end frame of every shot, including the five-step homepage, the modal, checked finance row, evidence arrows, receipt, and explorer. No blank loading state, clipped text, or incorrect status.
4. Check audio clips fit their shots; check final file duration, codecs, audio levels, and subtitle timing.
5. Obtain independent fresh-context QA of the final render before delivery. Fix any material finding and rerender.

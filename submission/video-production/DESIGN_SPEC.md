# TallyGuard product film — v16 production spec

Status: final render and QA. Target: 03:15, 1920 × 1080, 30 fps.

Audience: Tameion judges and a possible company buyer. Show who submits a request, how evidence and AI controls operate, where finance authority begins, why a second approver is needed, and how the requester sees the final receipt. Recorded pages are the product; typography and motion frame the story without replacing the interface.

## Truth and safety rules

- The two filmed end-to-end workflows are **public-workspace simulations**. No funds move when those demo buttons are clicked.
- Arc Activity and Explorer at the end are **historical, separate Testnet proof**. Those transactions are not the filmed requests.
- No keys, personal accounts, or secret-bearing settings in footage. No Mainnet operation or new Testnet transfer is needed for filming.
- The 50 runs are workflow tests, and the 40 transfers are 0.01 test USDC Testnet results — not customers or production usage.

## Story and shot map

| Time | Screen | What the viewer learns |
| --- | --- | --- |
| 00:00–00:08 | Animated pain opener | Fragmented evidence, delayed approvals, unclear payment state. |
| 00:08–00:26 | Real homepage animation, five steps | AI checks evidence while finance retains authority. |
| 00:26–00:39 | Requester evidence intake | Invoice, purchase order and delivery proof; human-confirmed fields. |
| 00:39–00:53 | Centered result modal | Same ID, exact waiting stage, next step, no funds sent. |
| 00:53–01:07 | Finance queue and invoice review | Selected row visibly checked; duplicate, vendor, wallet, timing and policy controls. |
| 01:07–01:20 | Independent approver portal | Separate authority and recorded decision reason. |
| 01:20–01:34 | Finance final settlement | Approval and payment are distinct actions; demo is simulated. |
| 01:34–01:45 | Requester receipt | Same ID, finance note, paid state and receipt. |
| 01:45–02:01 | Finance autonomy policy | Opt-in 50-per-request / 500-per-day caps and emergency stop. |
| 02:01–02:15 | 40 USDC request | Evidence and headroom checked against the active policy. |
| 02:15–02:28 | Automatic simulated settlement | In-limit request closes without independent approval. |
| 02:28–02:44 | Mixed outcomes | Settle, human approval, schedule, hold for remediation; each reason recorded. |
| 02:44–02:58 | Arc / Circle context | Always-on programmable USDC, with fiat caveat. |
| 02:58–03:09 | Historical Arc proof | 50 test workflows and 40 confirmed transfers, separate from demo. |
| 03:09–03:15 | Brand close | Agent speed, finance control, proof on Arc. |

Visual tone: ink and restrained teal for the opener, ivory product surfaces, purple evidence connectors. English screen and narration; Chinese translation supplied only for owner review. A compact lower-third must leave the actual product controls visible.

## Acceptance checks

1. Verify `TG-BE9B131D` and 1,486.25 USDC across submit modal, finance queue, approver, settlement, and requester receipt.
2. Verify the automatic request is 40 USDC and the visible policy is 50 per request / 500 per day.
3. Inspect a beginning/middle/end frame of every shot, including the five-step homepage, the modal, checked finance row, evidence arrows, receipt, and explorer. No blank loading state, clipped text, or incorrect status.
4. Check audio clips fit their shots; check final file duration, codecs, audio levels, and subtitle timing.
5. Obtain independent fresh-context QA of the final render before delivery. Fix any material finding and rerender.

# TallyGuard product film — production spec

Status: final QA. Target: 02:59, 1920 × 1080, 30 fps; hard ceiling under 03:00.

Audience: Tameion judges and a possible company buyer. The film must make clear who submits a request, what evidence/AI checks do, which decisions remain with finance, when an independent approver is required, and what receipt closes the loop. Recorded pages are the product; typography and subtle motion frame the story without replacing the actual interface.

## Truth and safety rules

- The two filmed end-to-end workflows are **public-workspace simulations**. No funds move when those buttons are clicked. A simulated paid status or receipt must not be represented as a Testnet transfer.
- Arc Activity and Explorer at the end are **historical, separate Testnet proof** from the synthetic campaign. Those transactions are not the filmed requests.
- Never show keys, wallets' private data, personal accounts, or secret-bearing settings. No Mainnet operation or new Testnet transfer is needed for filming.
- Describe the 50 runs as workflow tests and the 40 confirmed 0.01 test USDC transfers as Testnet results, not as customers or production use.

## Story and shot map

| Time | Screen | What the viewer learns |
| --- | --- | --- |
| 00:00–00:12 | Animated brand thesis over the actual site | Invoices move faster; finance keeps authority. |
| 00:12–00:25 | Requester evidence intake | Invoice, purchase order and delivery proof become one confirmed request. |
| 00:25–00:39 | Request submitted and tracked | The source proof is sealed; submission itself is not payment. |
| 00:39–00:53 | Finance reviews the same request | Duplicate, supplier, wallet, timing and policy checks explain the exception. |
| 00:53–01:06 | Separate approver records a decision | Human approval is distinct from settlement. |
| 01:06–01:20 | Finance executes the payment step | Simulated Arc settlement follows the approval. |
| 01:20–01:31 | Requester sees paid status and final receipt | The first simulated loop visibly closes on the applicant side. |
| 01:31–01:47 | Finance autonomy policy | Finance elects to enable small automatic payments and caps them at 50 per request / 500 per day. |
| 01:47–02:01 | New 40 USDC request | Evidence and headroom are checked against policy. |
| 02:01–02:14 | Automatic paid status and receipt | A safe small request closes without independent approval, still in simulation. |
| 02:14–02:30 | Mixed outcomes and exception handling | Other requests can wait, require review, be scheduled, or be held. |
| 02:30–02:44 | Arc Activity | Historical wallet/Testnet evidence is separate from the demo. |
| 02:44–02:53 | Arc Explorer | Exact historical transaction proof; 40 confirmed transfers. |
| 02:53–02:59 | Animated brand close | Agent speed, finance control, proof on Arc. |

Visual tone: ivory, ink, restrained teal actions, purple evidence connectors. English on-screen text and narration for judges; a complete Chinese review translation is supplied separately. The lower-third remains compact enough to keep product controls visible. Sound is narration-led with subtle Mixkit BGM; the narration-only variant allows review without licensed music.

## Acceptance checks

1. Verify the same request ID through manual submit, finance review, independent approval, settlement and applicant receipt.
2. Verify the automatic request is 40 USDC and the visible policy is 50 per request / 500 per day; an earlier 100 daily test hit the correct headroom guard and is not presented as successful.
3. Inspect at least one beginning/middle/end frame of every shot, plus the policy number, settlement status, applicant receipt and final explorer detail. No clipped labels, wrong request, blank loading state or mismatched voiceover.
4. Verify final file metadata, duration, and audio levels; review the complete render and narration-only variant.
5. Obtain a fresh independent QA review of the rendered file before delivery. Any material finding triggers a fix and a new full render.

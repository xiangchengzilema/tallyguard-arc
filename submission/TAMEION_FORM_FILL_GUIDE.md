# Tameion Agents submission — owner fill guide

Official form: https://forms.gle/BBWrdfuircrKiG2i6

The owner submits the form. Check identity and participation answers personally. The
filmed payment clicks run in the public **simulation** workspace; the separately
shown Arc Testnet transactions are historical, real **test-token** transfers.
The 50 automated workflows are engineering tests, not 50 human users.

## Identity and links

| Form field | Fill with |
| --- | --- |
| Email | Your preferred contact email; fill it yourself. |
| Project Name | `TallyGuard` |
| Github Handle | `xiangchengzilema` |
| Discord Handle | `xiangchengzilema` — verify this is still your current Discord handle. |
| Telegram Handle | Your own `@handle`; this field is required and I do not know it. |
| Twitter / X Profile | `https://x.com/xiangchengzile` (optional). |
| Number of Team Members | `1 (Solo)` if you are the only human team member. AI tooling is not an additional human member. |
| Team Members Names | Your preferred real name, entered by you. |
| Previous Canteen x Arc participation | `xiangchengzilema` — the channel has a Pythia / Agora video whose description says submitted, plus an Obol / Lepton demo. Confirm which events you officially submitted to; do not list an award unless you have proof. |
| Project Source Code | `https://github.com/xiangchengzilema/tallyguard-arc` |
| Existing-project comparison | `https://github.com/xiangchengzilema/tallyguard-arc/compare/7ab81cd...main` — pre-event baseline to the current branch; verify it resolves after the final push. |
| Project Live | `https://tallyguard-arc.onrender.com` |
| Project Video Demo | `https://youtu.be/Jjc9D-k1UXY` (unlisted 2:59 submission cut; not the 3:12 owner-review cut). |

## Continuing project / last two weeks

> TallyGuard is a continuing, independent project started on September 19, 2026, before the Tameion event. It is not being presented as a newly created product or a prior award winner. The builder previously submitted Pythia for Agora; an Obol demo was also made for Lepton, but confirm its submission status yourself. No award or grant is claimed here. During the Tameion window we tightened the requester/finance/approver journeys: a staged submission result, one visible request ID across handoffs, readable approval and rejection reasons, correction and resubmission, a finance-selected small-payment autonomy limit, and distinct approval versus final settlement. We also polished the public judge deployment and recorded product walkthrough. The comparison link isolates event-window code from the earlier baseline. The public site is a safe simulation; separately, a controlled Arc Testnet campaign completed 50 synthetic payment workflows with 40 confirmed 0.01-test-USDC transfers. This proves workflow coverage and testnet integration, not external customer adoption. Verify your own prior program and award history before submitting.

## Problem Statement

> Accounts payable still relies on scattered invoices, purchase orders, delivery proof, supplier records, email approvals, and bank handoffs. A fast AI agent can read this material but must not gain unchecked authority to move money. Duplicate evidence, changed payout wallets, unclear exception reasons, and delayed status updates create both fraud risk and operational drag. TallyGuard makes the evidence, decision boundary, and settlement trail explicit so finance can delegate routine review while retaining control over policy, exceptions, and funds.

## Project Description

> TallyGuard is an evidence-bound accounts-payable application for agentic commerce on Arc. A requester uploads invoice, purchase-order, and delivery evidence, reviews extracted fields, and tracks one request through review, human approval when required, settlement, and receipt. AI assists evidence interpretation and routing; deterministic controls check document agreement, duplicate use, supplier and wallet identity, treasury capacity, and policy. A separate approver records a reasoned decision on exceptions. Finance may optionally activate a versioned low-value auto-settlement policy with per-payment and daily caps and an emergency stop; it is off unless finance enables it. For eligible real Testnet settlements, Circle Developer-Controlled Wallets create an idempotent USDC transfer, and Arc RPC independently verifies the receipt. Auditors can export a portable evidence packet. The public judge site deliberately simulates funds movement; historical Arc Testnet receipts are displayed separately. The stack is Flask/Python, React/TypeScript, SQLite, Circle Developer Wallets, Arc RPC, Docker, and GitHub Actions.

## Traction — answer the form's *real people* question directly

> One human builder/operator has manually exercised the product, including submission and review flows. We have not verified an independent external customer or a paying company user, so we do not claim 50 human users. Engineering validation is separate: 50 synthetic payment workflows completed, including 40 confirmed Arc Testnet transfers of 0.01 test USDC each. The public repository and live judge site make the workflow available for additional evaluation. [If other real people have personally tried it, add only their verified number and feedback.]

## Optional fields

- **Arc OSS:** Opt in only if you personally commit to keeping the project open source. The reusable primitives are evidence provenance, deterministic AP controls, role separation, idempotent settlement, Arc receipt reconciliation, and offline-verifiable payment packets.
- **Circle / Arc feedback:** Circle's wallet and Arc's testnet made a real end-to-end proof possible. A clearer provider-state-to-onchain-receipt diagnostic path and more realistic treasury/approval examples would help finance-product builders. Edit this based on your own experience.
- **Feedback call:** Select only if you personally want the call.
- **General feedback:** Optional; write your own experience rather than boilerplate.

Before pressing Submit, open the video in a logged-out tab, verify the GitHub
comparison URL, and save screenshots of the completed form. Keep the resulting
confirmation page or email as proof of submission.

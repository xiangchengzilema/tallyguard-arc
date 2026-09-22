# Arc Testnet live acceptance — 2026-09-22

TallyGuard completed a controlled, self-operated `0.01 USDC` settlement through
Circle developer-controlled wallets on Arc Testnet. This is live Testnet proof,
not a simulated receipt, customer payment, mainnet payment, or revenue claim.

## Result

- Network: `ARC-TESTNET` (chain ID `5042002`)
- Circle wallet state before submission: `LIVE`
- Decision: `PAY`
- Provider: `circle-developer-wallets+arc-rpc`
- Amount delivered: `0.01 USDC`
- Transaction status: `CONFIRMED`
- Arc receipt status: `0x1`
- Block: `63409495`
- Transaction: [`0xa2814d5d...f8645a37`](https://explorer.testnet.arc.io/tx/0xa2814d5d98b77948f512ef02d5928b5d402c20f3ee7de78338063c93f8645a37)
- Acceptance report SHA-256: `1b80b088f6f44791b3141c0ed0eb5b33fa2511fbcf81f0cbe6343557f798e891`

## Independent checks

- The recipient balance increased from `6.398` to `6.408 USDC`.
- The treasury balance changed from `19.29325` to `19.281471 USDC`; the difference
  includes the `0.01 USDC` transfer and Arc Testnet transaction fee.
- Arc RPC returned the expected successful receipt and exact canonical-USDC
  transfer event.
- Replaying the same settlement request returned the same durable receipt rather
  than creating a second Circle transfer.
- The tenant audit chain remained valid across all eight recorded events.
- The release audit accepted the artifact as structurally complete real Arc
  Testnet evidence.

The detailed generated JSON, reviewer summary, and isolated SQLite acceptance
database remain under the git-ignored `artifacts/acceptance/` directory so live
operator artifacts are not accidentally published as application fixtures.

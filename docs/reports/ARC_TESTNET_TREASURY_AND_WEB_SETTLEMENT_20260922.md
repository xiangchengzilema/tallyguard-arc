# Arc Testnet treasury and web-settlement proof — 2026-09-22

TallyGuard now uses one Circle developer-controlled wallet as its Arc Testnet
treasury. The remaining controlled wallets are retained as requester/vendor test
destinations; they no longer act as independent treasury sources.

## Treasury consolidation

- Source wallets confirmed: `37 / 37`
- Failed transfers: `0`
- Consolidated amount: `559.399116 USDC`
- Treasury balance before consolidation: `19.281471 USDC`
- Treasury balance after consolidation: `578.680587 USDC`
- Each source retained a small Testnet fee reserve before gas.
- First consolidation transaction:
  [`0xf20b1fe3…09438e3`](https://explorer.testnet.arc.io/tx/0xf20b1fe317ca6dbe9b1f70a19405aca8eef0716390540eef09da6da6409438e3)
- Final consolidation transaction:
  [`0xc0c9a804…b2c8379`](https://explorer.testnet.arc.io/tx/0xc0c9a80490da4bd907f092fddfe94fabb87fefa916968075f38a05474b2c8379)

The detailed local report is stored under the git-ignored path
`artifacts/consolidation/live-20260922.json` so wallet inventory metadata is not
published as an application fixture.

## Web finance connection

The finance backend now has a server-side `POST /api/treasury/snapshots/refresh`
operation. It reads the configured Circle treasury balance, rejects non-LIVE or
wrong-network wallets, independently verifies the Arc chain ID and canonical
USDC deployment, and records a tenant-scoped treasury snapshot. The browser
never receives Circle credentials.

In `circle-live` mode the finance UI:

- labels the runtime `LIVE USDC` / `CIRCLE + RPC LIVE`;
- replaces demo-queue controls with `Refresh Arc balance`;
- refuses to invent a fallback treasury balance during invoice submission;
- refreshes the live treasury before evaluating a newly uploaded request; and
- sends final finance authorization through the same guarded settlement endpoint
  used by the acceptance runner.

## Post-consolidation settlement acceptance

A second controlled `0.01 USDC` acceptance payment was submitted from the new
treasury, completed by Circle, and independently reconciled against Arc RPC.

- Decision: `PAY`
- Provider: `circle-developer-wallets+arc-rpc`
- Status: `CONFIRMED`
- Block: `63414468`
- Transaction:
  [`0xe8c6b06d…f783adf`](https://explorer.testnet.arc.io/tx/0xe8c6b06dbafbd55a7f9a5a9fbb276fe5b07ddf27be3ef84cc26281dcdf783adf)
- Idempotent replay reused the same receipt: `true`
- Audit chain valid: `true`
- Acceptance report SHA-256:
  `d88ca0699d86d5918e7957382015b8c49e70bd5202c92c5e7c0239c5f55cd3f9`

This is controlled Arc Testnet engineering evidence, not a mainnet payment,
customer payment, production revenue, or permission to move real funds.

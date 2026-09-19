# Circle and Arc settlement boundary

TallyGuard uses Circle Developer-Controlled Wallets to originate an Arc USDC transfer, then independently verifies the resulting transaction through Arc JSON-RPC. A Circle `COMPLETE` response alone is not sufficient to mark an invoice paid.

## Runtime modes

| Mode | Purpose | Credentials | Moves funds |
| --- | --- | --- | --- |
| Simulation | Public judge scenarios and load tests | None | No |
| Arc Testnet | End-to-end acceptance testing | Circle test credentials and a dedicated test wallet | Test USDC only |
| Arc Mainnet | Low-value final proof | Dedicated low-balance wallet, explicit runtime flag, approval reference | Yes, under hard caps |

Mainnet remains disabled by default. The live adapter also enforces an independent per-transfer cap from `TALLYGUARD_MAX_TRANSFER_USDC`; this is separate from the organization policy limit.

TallyGuard deliberately uses `ARC-MAINNET` as its internal mainnet label, while Circle's
Wallets API uses `ARC`. The adapter maps between them explicitly and verifies Circle's
completed transaction against the mapped value. `ARC-TESTNET` is identical in both systems.

## Verification sequence

1. The deterministic decision engine must return `PAY`.
2. The payment intent must match the decision's organization, approval reference, recipient, amount, and network.
3. The live adapter requires a stored UUID v4 idempotency key.
4. Circle creates the transfer using the official SDK. The SDK generates a fresh entity-secret ciphertext for the request.
5. TallyGuard polls Circle through `INITIATED`, `CLEARED`, `QUEUED`, `SENT`, and `CONFIRMED` until `COMPLETE`.
6. `STUCK`, `FAILED`, `DENIED`, and `CANCELLED` fail closed. Polling exhaustion returns a retryable service-unavailable result, leaves the invoice in `SUBMISSION_FAILED`, and retains the original intent and provider idempotency key.
7. Every provider call creates a tenant-scoped durable attempt record. Transient unavailability is the only automatic-retry class; ambiguous errors are locked.
8. The completed Circle record must match the authorized network, recipient, and exact decimal amount.
9. Arc JSON-RPC must return the configured chain ID, a successful receipt, the same transaction hash, and a positive block number.
10. The transaction target must be Arc's canonical USDC ERC-20 interface.
11. The receipt must contain the exact USDC `Transfer` event for the authorized recipient and six-decimal atomic amount.
12. If Circle supplies a block height, it must equal the Arc RPC receipt block. Any mismatch is terminal and moves the invoice to `RECONCILIATION_MISMATCH`.

Only after all checks pass does TallyGuard create a confirmed settlement receipt.

The payment intent and receipt are stored in SQLite. One tenant/decision pair can own only one
intent, and its provider idempotency key is immutable. If the service restarts after Circle has
accepted or completed a transfer, the retry reuses that same key. If a confirmed receipt was
already stored, the provider is not called again.

Before retrying a failed submission, the server rechecks the currently active asset, network,
autonomy, kill-switch, treasury-freshness, daily-limit, and reserve controls in the same atomic
reservation transaction. A historical `PAY` decision therefore cannot bypass a newly engaged
emergency stop or tighter treasury limit.

The operations API exposes only a SHA-256 fingerprint of the provider idempotency key. The original
key remains in the server-side payment intent and is never returned by the exception-center endpoint.

## Local configuration

Install the optional Circle dependency:

```powershell
python -m pip install -e ".[circle,dev]"
```

Copy `.env.example` to a local, ignored `.env`. Select Circle mode and Arc Testnet,
keep mainnet disabled, set a deliberately small maximum transfer amount, and provide
`CIRCLE_WEB3_API_KEY`, `CIRCLE_ENTITY_SECRET`, and `CIRCLE_WALLET_ID` only in that local file.

Do not paste the entity secret, API key, private key, or recovery material into chat, issues, logs, screenshots, or committed files.

Live Circle mode disables `/api/demo/session` by default. Do not enable
`TALLYGUARD_ENABLE_DEMO_SESSIONS` on any deployment connected to a funded wallet. The public
judge deployment remains in credential-free simulation mode, where seeded demo identities are
safe and automatically available.

### Read-only preflight

Probe the configured Arc RPC, expected chain ID, latest block, and canonical USDC contract
without loading Circle credentials:

```powershell
tallyguard-preflight --network-only
```

After placing the three Circle values in local environment variables, run the full preflight:

```powershell
tallyguard-preflight
```

The full check reads the configured Circle wallet, verifies that it is `LIVE` on the selected
Arc network, and confirms that Circle returns the canonical USDC asset balance. It never signs,
estimates, or submits a transaction. Wallet addresses are redacted in terminal output, and
secrets are never printed. A full preflight returns a ready verdict only when every check passes.

For mainnet, the preflight additionally requires `TALLYGUARD_ALLOW_MAINNET=true` and refuses a
hard adapter cap above 5 USDC. This does not bypass the product's separate recorded-approval
requirement; it only establishes that the runtime configuration is internally consistent.

## Testnet acceptance gate

The acceptance runner is deliberately locked to Arc Testnet, capped at 0.10 USDC, and requires
an exact confirmation phrase. It creates a fresh isolated SQLite database, submits matching
invoice/PO/delivery evidence through the real API, obtains a deterministic `PAY` decision,
settles once through Circle, retries the same request, and verifies that the retry returns the
same durable receipt without a second provider submission. It then verifies the tenant audit
hash chain and writes an ignored JSON report alongside the temporary database.

After the full read-only preflight succeeds, run:

```powershell
tallyguard-acceptance `
  --confirm MOVE-TESTNET-USDC `
  --recipient 0xYOUR_CONTROLLED_TESTNET_RECIPIENT `
  --amount 0.01
```

This command **does move Arc Testnet USDC**. It refuses mainnet regardless of environment
configuration. Acceptance artifacts are written under `artifacts/acceptance/`, which is excluded
from Git.

Before any mainnet proof:

- confirm the Circle wallet is on `ARC-TESTNET`;
- fund it only with faucet/test USDC;
- execute a sub-dollar test payment to a controlled recipient;
- preserve the Circle transaction ID and Arc Explorer URL;
- rerun the same internal payment request and verify no second provider submission occurs;
- test wrong recipient, wrong amount, reverted receipt, missing receipt, delayed Circle state, and provider failure paths;
- run the full unit and multi-tenant load suites.

The mainnet flag must not be enabled until this gate passes.

## Official references

- Circle Developer-Controlled Wallets SDK: https://developers.circle.com/wallets/dev-controlled-wallets
- Create transfer transaction: https://developers.circle.com/api-reference/wallets/developer-controlled-wallets/create-developer-transaction-transfer
- Get transaction: https://developers.circle.com/api-reference/wallets/developer-controlled-wallets/get-transaction
- Arc network reference: https://docs.arc.io/arc/references/connect-to-arc
- Arc contract addresses: https://docs.arc.io/arc/references/contract-addresses

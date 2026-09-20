# Circle and Arc settlement boundary

TallyGuard uses Circle Developer-Controlled Wallets to originate an Arc USDC transfer, then independently verifies the resulting transaction through Arc JSON-RPC. A Circle `COMPLETE` response alone is not sufficient to mark an invoice paid.

## Runtime modes

| Mode | Purpose | Credentials | Moves funds |
| --- | --- | --- | --- |
| Simulation | Public judge scenarios and load tests | None | No |
| Arc Testnet | End-to-end acceptance testing | Circle test credentials and a dedicated test wallet | Test USDC only |
| Arc Mainnet | Low-value final proof | Dedicated low-balance wallet, explicit runtime flag, role-separated decision-bound approval | Yes, under hard caps |

Mainnet remains disabled by default. The live adapter also enforces an independent per-transfer cap from `TALLYGUARD_MAX_TRANSFER_USDC`; this is separate from the organization policy limit.

TallyGuard deliberately uses `ARC-MAINNET` as its internal mainnet label, while Circle's
Wallets API uses `ARC`. The adapter maps between them explicitly and verifies Circle's
completed transaction against the mapped value. `ARC-TESTNET` is identical in both systems.

## Verification sequence

1. The deterministic decision engine must return `PAY`.
2. Every mainnet `PAY` decision must first enter `POST /api/decisions/<id>/request-mainnet-approval`; a different user with the approver role must resolve that durable request.
3. The payment intent must match the decision's organization, approval reference, recipient, amount, and network.
4. The live adapter requires a stored UUID v4 idempotency key.
5. Circle creates the transfer using the official SDK. The SDK generates a fresh entity-secret ciphertext for the request.
6. TallyGuard polls Circle through `INITIATED`, `CLEARED`, `QUEUED`, `SENT`, and `CONFIRMED` until `COMPLETE`.
7. `STUCK`, `FAILED`, `DENIED`, and `CANCELLED` fail closed. Polling exhaustion returns a retryable service-unavailable result, leaves the invoice in `SUBMISSION_FAILED`, and retains the original intent and provider idempotency key.
8. Every provider call creates a tenant-scoped durable attempt record. Transient unavailability is the only automatic-retry class; ambiguous errors are locked.
9. The completed Circle record must match the authorized network, recipient, and exact decimal amount.
10. Arc JSON-RPC must return the configured chain ID, a successful receipt, the same transaction hash, and a positive block number.
11. The transaction target must be Arc's canonical USDC ERC-20 interface.
12. The transaction and receipt must agree on transaction hash, block number, and block hash, and the receipt block cannot be ahead of the RPC head.
13. The receipt must contain exactly one non-removed USDC `Transfer` event whose sender equals the transaction sender and whose recipient and six-decimal atomic amount exactly match the authorization.
14. If Circle supplies a block height, it must equal the Arc RPC receipt block. Any mismatch is terminal and moves the invoice to `RECONCILIATION_MISMATCH`.

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
The API, preflight, wallet setup, and Testnet acceptance commands load that file
automatically without overriding values that you explicitly exported in the terminal.

Do not paste the entity secret, API key, private key, or recovery material into chat, issues, logs, screenshots, or committed files.

If a dedicated Circle test wallet does not exist yet, first generate and register the entity
secret in the Circle Developer Console yourself. After storing the API key and entity secret in
local environment variables, the explicit setup command can create one new `EOA` wallet on
`ARC-TESTNET`:

```powershell
tallyguard-wallet-setup --confirm CREATE-ARC-TESTNET-WALLET --acceptance-pair
```

To reuse an existing wallet set, add `--wallet-set-id <id>` or set the non-secret
`CIRCLE_WALLET_SET_ID`. The command creates account resources but does not fund the wallet or
move USDC. Acceptance-pair mode creates separate treasury and controlled-recipient wallets, then
prints only their wallet IDs, public addresses, network, and state. Store the treasury wallet ID
locally as `CIRCLE_WALLET_ID`, store the recipient address as
`TALLYGUARD_ACCEPTANCE_RECIPIENT`, and fund only the treasury address from the Circle Faucet.
Omit `--acceptance-pair` when a single existing controlled recipient will be used instead.

The same provisioner can create a dedicated Arc Mainnet wallet or controlled pair, but only when
the network, runtime gate, and mainnet-specific phrase all agree. This creates Circle account
resources only; it does not fund a wallet or submit a transfer:

```powershell
$env:TALLYGUARD_ARC_NETWORK = "ARC-MAINNET"
$env:TALLYGUARD_ALLOW_MAINNET = "true"
tallyguard-wallet-setup `
  --network ARC-MAINNET `
  --confirm CREATE-ARC-MAINNET-WALLET `
  --acceptance-pair
```

Keep the returned addresses unfunded until the Testnet acceptance gate below passes. Then fund
only the dedicated treasury address with the deliberately small amount approved for the proof.

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

The preflight refuses a hard adapter cap above 0.10 USDC on either network. Mainnet additionally
requires `TALLYGUARD_ALLOW_MAINNET=true`. The live adapter and acceptance command also default
to this 0.10-USDC ceiling when the environment value is absent. This does not bypass the
product's separate recorded-approval
requirement; it only establishes that the runtime configuration is internally consistent. A
free-form request string is never accepted as authority: settlement reloads the approved record
and verifies its tenant, decision, and invoice bindings before creating a payment intent.

## Testnet acceptance gate

The acceptance runner is deliberately locked to Arc Testnet, capped at 0.10 USDC, and requires
an exact confirmation phrase. It creates a fresh isolated SQLite database, submits matching
invoice/PO/delivery evidence through the real API, obtains a deterministic `PAY` decision,
settles once through Circle, retries the same request, and verifies that the retry returns the
same durable receipt without a second provider submission. It then verifies the tenant audit
hash chain. The treasury snapshot is bound to the balance observed from Circle immediately before
the run rather than a hard-coded demo value. The command writes both an ignored JSON report and a
reviewer-friendly Markdown summary containing the Arc Explorer link and JSON SHA-256.

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

## Controlled Mainnet acceptance gate

The mainnet runner is a different command and cannot inherit the Testnet confirmation by mistake.
It refuses any amount above `0.01 USDC`, refuses an adapter cap above `0.10 USDC`, requires
`ARC-MAINNET`, requires `TALLYGUARD_ALLOW_MAINNET=true`, rejects a treasury self-transfer, runs the
full read-only Circle/Arc preflight, then creates a fresh evidence package and deterministic `PAY`
decision. An operator requests a durable mainnet approval and a separate approver identity resolves
it before the intent exists. The command then proves that replay returns the same receipt without a
second provider submission.

Only after Testnet acceptance has passed and the dedicated mainnet treasury contains a deliberately
small balance, run manually:

```powershell
tallyguard-mainnet-acceptance `
  --confirm MOVE-MAINNET-USDC `
  --recipient 0xYOUR_SEPARATE_CONTROLLED_MAINNET_RECIPIENT `
  --amount 0.01
```

This command **does move real Arc Mainnet USDC**. Do not run it merely to test CLI wiring; the unit
suite exercises the same path with a counting fake adapter. Generated evidence is stored under the
Git-ignored `artifacts/mainnet-acceptance/`. The summary labels the result as controlled,
self-operated proof—not customer traction or revenue—and only recognizes the real Circle + Arc RPC
provider as live evidence.

### Independent evidence-packet verification

An auditor can verify an exported Payment Evidence Packet without access to the application
database or server. The offline command recomputes the packet hash, sealed-input hash, policy
hash, deterministic rule trace, decision ID, payment bindings, and included audit-event hashes:

```powershell
tallyguard-verify-packet .\tallyguard-INVOICE-evidence-packet.json
```

For a real Circle-backed receipt, add `--verify-arc`. This makes read-only JSON-RPC calls and
requires the exact canonical-USDC Transfer event, sender, recipient, amount, transaction/block
agreement, and successful receipt already enforced by the live settlement adapter:

```powershell
tallyguard-verify-packet .\tallyguard-INVOICE-evidence-packet.json --verify-arc
```

Before any mainnet proof:

- confirm the Circle wallet is on `ARC-TESTNET`;
- fund it only with faucet/test USDC;
- execute a sub-dollar test payment to a controlled recipient;
- preserve the Circle transaction ID and Arc Explorer URL;
- rerun the same internal payment request and verify no second provider submission occurs;
- test wrong recipient, wrong amount, reverted receipt, missing receipt, delayed Circle state, and provider failure paths;
- run the full unit and multi-tenant load suites.
- create the dedicated mainnet wallet only with the separate mainnet confirmation phrase;
- request a durable mainnet approval for the exact `PAY` decision and have a different role resolve it.

The mainnet flag must not be enabled until this gate passes.

## Official references

- Circle Developer-Controlled Wallets SDK: https://developers.circle.com/wallets/dev-controlled-wallets
- Create transfer transaction: https://developers.circle.com/api-reference/wallets/developer-controlled-wallets/create-developer-transaction-transfer
- Get transaction: https://developers.circle.com/api-reference/wallets/developer-controlled-wallets/get-transaction
- Arc network reference: https://docs.arc.io/arc/references/connect-to-arc
- Arc contract addresses: https://docs.arc.io/arc/references/contract-addresses
- Arc unified USDC event indexing: https://docs.arc.io/integrate/infrastructure/indexing-events

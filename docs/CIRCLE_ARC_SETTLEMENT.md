# Circle and Arc settlement boundary

TallyGuard uses Circle Developer-Controlled Wallets to originate an Arc USDC transfer, then independently verifies the resulting transaction through Arc JSON-RPC. A Circle `COMPLETE` response alone is not sufficient to mark an invoice paid.

## Runtime modes

| Mode | Purpose | Credentials | Moves funds |
| --- | --- | --- | --- |
| Simulation | Public judge scenarios and load tests | None | No |
| Arc Testnet | End-to-end acceptance testing | Circle test credentials and a dedicated test wallet | Test USDC only |
| Arc Mainnet | Low-value final proof | Dedicated low-balance wallet, explicit runtime flag, approval reference | Yes, under hard caps |

Mainnet remains disabled by default. The live adapter also enforces an independent per-transfer cap from `TALLYGUARD_MAX_TRANSFER_USDC`; this is separate from the organization policy limit.

## Verification sequence

1. The deterministic decision engine must return `PAY`.
2. The payment intent must match the decision's organization, approval reference, recipient, amount, and network.
3. The live adapter requires a stored UUID v4 idempotency key.
4. Circle creates the transfer using the official SDK. The SDK generates a fresh entity-secret ciphertext for the request.
5. TallyGuard polls Circle through `INITIATED`, `CLEARED`, `QUEUED`, `SENT`, and `CONFIRMED` until `COMPLETE`.
6. `STUCK`, `FAILED`, `DENIED`, and `CANCELLED` fail closed. Polling exhaustion also fails closed.
7. The completed Circle record must match the authorized network, recipient, and exact decimal amount.
8. Arc JSON-RPC must return the configured chain ID, a successful receipt, the same transaction hash, and a positive block number.
9. The transaction target must be Arc's canonical USDC ERC-20 interface.
10. The receipt must contain the exact USDC `Transfer` event for the authorized recipient and six-decimal atomic amount.
11. If Circle supplies a block height, it must equal the Arc RPC receipt block.

Only after all checks pass does TallyGuard create a confirmed settlement receipt.

The payment intent and receipt are stored in SQLite. One tenant/decision pair can own only one
intent, and its provider idempotency key is immutable. If the service restarts after Circle has
accepted or completed a transfer, the retry reuses that same key. If a confirmed receipt was
already stored, the provider is not called again.

## Local configuration

Install the optional Circle dependency:

```powershell
python -m pip install -e ".[circle,dev]"
```

Copy `.env.example` to a local, ignored `.env`. Select Circle mode and Arc Testnet,
keep mainnet disabled, set a deliberately small maximum transfer amount, and provide
`CIRCLE_WEB3_API_KEY`, `CIRCLE_ENTITY_SECRET`, and `CIRCLE_WALLET_ID` only in that local file.

Do not paste the entity secret, API key, private key, or recovery material into chat, issues, logs, screenshots, or committed files.

## Testnet acceptance gate

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

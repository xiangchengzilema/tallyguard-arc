# Security model

## Protected assets

- Circle API credentials and entity secret
- Dedicated Arc wallet and its USDC balance
- Verified vendor payment addresses
- Original invoice, purchase-order, and delivery evidence
- Policy versions, treasury snapshots, approvals, payment intents, and settlement receipts
- Tenant identity and isolation guarantees

## Primary abuse cases and controls

| Abuse case | Control |
| --- | --- |
| Prompt or document asks the agent to redirect payment | Wallet address is omitted from hosted-agent prompts; model output schema rejects payment-shaped fields; policy and settlement use immutable records only |
| Invoice changes after approval | Original bytes and normalized package are SHA-256 bound; evidence is locked when evaluation begins |
| Duplicate or near-duplicate invoice | Exact content, business-key, and normalized-text duplicate controls run before settlement |
| Vendor wallet replaced silently | Append-only wallet history, explicit verification method/reference, optimistic current-wallet check, mismatch HOLD |
| Operator approves own exception | Requester and approver must be different users; approval version is checked atomically |
| Human tries to override fraud evidence | Only pure `ESCALATE` can be approved; HOLD and REJECT remain non-overridable |
| Cross-tenant identifier guessing | Authentication, authorization, and every repository query enforce organization scope |
| Retry causes double payment | Durable intent UUID v4, process lock, database uniqueness, provider idempotency, stored-receipt replay |
| Provider times out mid-workflow | Invoice moves to `SUBMISSION_FAILED`; the same durable intent/key is retried only after current policy and treasury controls pass again; failure and recovery are audited |
| Provider reports the wrong payment | Exact network, recipient, amount, hash, block, contract, and Transfer-log reconciliation |
| Ambiguous provider failure is retried automatically | Durable attempt classification permits automatic retry only for explicit transient unavailability; mismatches and unknown errors are locked |
| Autonomous plan executes after its inputs change | Every item binds invoice version, workflow state, decision ID, and retry flag; execution reloads and compares all bindings, marking changed items stale before settlement |
| Agent plan treats a recommendation as payment authority | The planner reads only persisted workflow and deterministic decision state; only `PAY` plus an allowed execution state can produce an executable action, and a settlement-authorized role must start execution |
| Agent bypasses role separation or pays an early schedule | Approval routing does not move funds; approved settlement reloads the exact approval and decision binding, and schedule release requires the sealed date to be due before a fresh policy evaluation can produce `PAY` |
| Agent-run evidence is edited after execution | Auditor export recomputes the plan hash, includes linked hash-chain events, and content-addresses the complete packet; foreign-tenant run IDs resolve as not found |
| Mainnet enabled accidentally | Off by default, explicit flag, operator-requested approval resolved by a different user, exact decision/invoice binding, adapter cap, read-only preflight |
| Public judge drains a funded wallet or interferes with another review | Public Blueprint uses simulation; each browser receives a fresh tenant containing four distinct role sessions; live mode disables demo sessions by default; workspace/session issuance has a separate anonymous rate limit; expired and revoked sessions are pruned and active sessions are bounded per principal; no secrets are committed |
| A removed credential is still exposed after publication | Release audit scans both the current tracked tree and every reachable Git patch for token, private-key, entity-secret, seed, password, and sensitive-filename indicators without echoing matched secret values |
| Live operator identity is silently changed | The local setup command verifies the existing organization name, display name, single role, and active state; any drift fails closed rather than overwriting authorization data |
| Live browser receives a swapped or cross-tenant role token | The private access gate validates each token through `/api/auth/session`, checks the exact required role, and requires one organization before loading protected data; tokens stay in page memory and reload clears them |
| Live browser is left authenticated after operator use | A header lock clears all in-memory finance state immediately and revokes the four server sessions; incomplete revocation stays visibly locked and relies on the bounded expiry |
| Oversized or disguised upload | Request-size ceiling, extension and MIME signature checks, bounded extracted fields |
| Audit history altered | Per-tenant append-only hash chain with verification endpoint and visible UI state |
| Request metadata leaks financial or credential data | Structured logs record only the route endpoint name, method, status, duration, and a syntax-validated correlation ID; paths, query strings, bodies, authorization headers, tenant IDs, and record IDs are excluded |
| Browser embeds, content sniffing, referrer leakage, or stale API caches expose judge data | Every response applies a restrictive same-origin CSP, frame denial, no-referrer and nosniff policies; HTTPS adds HSTS; API responses are never cached while fingerprinted frontend assets are immutable |
| Caller injects control characters or unbounded data into logs | External correlation IDs must match a 128-character allowlist or are replaced with a server-generated identifier before logging or response echo |

## Secret handling

Secrets are accepted only from environment variables. `.env`, databases, generated acceptance
artifacts, dependencies, browser traces, and build output are excluded from Git. Tests and CI scan
Python sources for common credential patterns. Terminal and preflight output never print API keys,
entity secrets, private keys, or recovery material; wallet addresses are redacted in preflight
output.

The live operator setup command is the one intentional exception to secret-free
terminal output: it emits newly generated opaque bearer tokens exactly once so
a trusted local operator can use them. SQLite stores only their SHA-256 digests.
The tokens expire within 24 hours and must not be redirected into a tracked
file, browser URL, frontend bundle, chat, or analytics system.
The private web console does not persist supplied bearer tokens in URLs,
`localStorage`, `sessionStorage`, cookies, or its compiled bundle. This reduces
residual exposure but does not defend against a compromised browser profile,
extension, operating system, or active cross-site scripting, so live operation
still requires a trusted browser and the short session lifetime.

## Safe live-testing protocol

1. Use a dedicated Circle developer-controlled wallet.
2. Start on Arc Testnet and fund only with test assets.
3. Run `tallyguard-preflight` before enabling the adapter.
4. Use `tallyguard-acceptance` with a controlled recipient and no more than 0.10 test USDC.
5. Preserve the resulting JSON report, Circle transaction ID, and Arc Explorer URL.
6. Prove a repeated settlement request returns the same receipt without another provider call.
7. Prove a transient failure retains one intent and becomes resolved after one confirmed retry, while wrong-recipient evidence becomes a terminal reconciliation mismatch.
8. Keep mainnet disabled until the testnet gate passes.
9. For any mainnet proof, use a new low-balance wallet, a sub-dollar amount, an explicit approval,
   and immediately disable the runtime flag afterward.

## Honest limitations

- The public demo uses simulated receipts and labels them visibly.
- The checked-in 10,000-workflow report is synthetic engineering evidence, not customer traction.
- Structured JSON and labelled text-layer PDF extraction are complete. Scanned PDFs and images
  require provenance-bound OCR or human fields; TallyGuard does not claim autonomous OCR accuracy.
- SQLite and in-process rate limiting are single-process hackathon choices, not the proposed
  production data plane.
- Hosted-model recommendations are optional and fall back deterministically; availability or
  model quality cannot weaken a hard control.

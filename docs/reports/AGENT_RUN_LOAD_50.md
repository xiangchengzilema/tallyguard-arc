# 50-tenant autonomous Agent Run load test

Generated on September 20, 2026 from the checked-in raw result
[`agent-run-load-50.json`](agent-run-load-50.json).

## Outcome

| Measure | Result |
| --- | ---: |
| Organizations | 50 |
| Mixed queue items | 200 |
| Successful Agent Runs | 50 / 50 |
| Failed Agent Runs | 0 |
| Policy-authorized settlements | 50 |
| Approval routes | 50 |
| Unsafe/non-due actions left unexecuted | 100 |
| Verified content-addressed proof packets | 50 |
| Cross-tenant proof probes denied | 50 / 50 |
| Concurrent duplicate execution calls | 100 |
| Durable execution-claim winners | 1 |
| Provider submissions from the duplicate storm | 1 |
| Duplicate Agent Run audit events | 0 |

## Latency

| Operation | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| Overall | 259 ms | 3,519 ms | 6,108 ms |
| Mixed queue seed | 3,146 ms | 6,249 ms | 6,282 ms |
| Agent planning | 397 ms | 481 ms | 781 ms |
| Agent execution | 1,418 ms | 1,438 ms | 1,443 ms |
| Proof export | 267 ms | 355 ms | 395 ms |
| Cross-tenant denial | 6 ms | 23 ms | 29 ms |
| Duplicate execution storm | 204 ms | 216 ms | 220 ms |

## What this proves

- Fifty isolated organizations concurrently used distinct operator, approver,
  and auditor sessions over the real loopback HTTP API.
- Every tenant received the same four-item queue: one deterministic payment,
  one human-approval escalation, one wallet-control hold, and one future schedule.
- The system moved only the fifty policy-cleared payments. It routed fifty
  exceptions and left all one hundred unauthorized or non-due actions untouched.
- Every completed run produced a content-addressed proof packet whose plan hash
  and tenant audit chain recomputed successfully.
- All fifty attempts to read another tenant's proof packet failed with `404`.
- One hundred callers concurrently attempted to execute the same plan. The
  database granted one durable execution lease, the provider saw one submission,
  and the audit chain retained one planned event and one executed event.

## Honest limitations

This is synthetic engineering evidence, not customer traction. The run used an
ephemeral SQLite database and the deterministic Arc simulator, so no funds
moved. It tests orchestration authority, isolation, idempotency, auditability,
and concurrency. The separate testnet acceptance procedure is responsible for
proving real Circle Wallet and Arc USDC settlement.

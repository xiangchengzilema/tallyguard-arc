# 10,000-workflow multi-tenant load test

Generated on September 20, 2026 from the checked-in raw result
[`load-test-10000.json`](load-test-10000.json).

## Outcome

| Measure | Result |
| --- | ---: |
| Organizations | 100 |
| Invoice workflows | 10,000 |
| Concurrent workers | 64 |
| Injected provider delay | 500 ms |
| HTTP requests | 19,503 |
| Successful workflows | 10,000 |
| Failed workflows | 0 |
| Workflow error rate | 0.00% |
| Throughput | 14.320 workflows/second |
| Cross-tenant probes denied | 100 / 100 |
| Duplicate-settlement requests | 200 |
| Provider submissions for duplicate storm | 1 |
| Unique transaction hashes for duplicate storm | 1 |
| Slow-provider idempotency preserved | Yes |
| Shared-treasury contention requests | 100 |
| Shared-treasury reservations admitted | 4 |
| Over-limit payments blocked | 96 |
| Provider submissions during contention | 4 |
| Atomic treasury limit preserved | Yes |
| Duplicate payments | 0 |

## Latency

| Operation | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| Overall | 2,031 ms | 3,499 ms | 3,742 ms |
| Scenario evaluation | 1,937 ms | 2,256 ms | 2,467 ms |
| Settlement | 3,246 ms | 3,658 ms | 3,870 ms |
| Approval request | 641 ms | 859 ms | 979 ms |
| Approval resolution | 964 ms | 1,188 ms | 1,311 ms |
| Cross-tenant denial | 5 ms | 22 ms | 28 ms |
| Duplicate-settlement storm | 434 ms | 1,057 ms | 1,219 ms |
| Atomic treasury reservation | 514 ms | 622 ms | 1,123 ms |

## What this proves

- Tenant boundaries held under concurrent, mixed-organization traffic.
- Idempotency converged 200 simultaneous retries onto one provider submission and
  one transaction hash while the accepted provider call remained in flight for
  an injected 500 ms delay.
- One hundred independent payments contended for the same 5,000 USDC daily
  limit and treasury snapshot. Exactly four 1,200 USDC reservations reached
  the provider; all 96 excess attempts failed closed before funds could move.
- Clean, scheduled, rejected, held, and escalated workflows remained deterministic.
- Role-separated approval and settlement completed without bypassing policy controls.
- Sessions, decisions, approvals, settlement records, and tamper-evident audit events
  were durably written during the run.

## Honest limitations

This is a synthetic engineering load test against real loopback HTTP, an ephemeral
SQLite database, and TallyGuard's deterministic Arc simulator. It moved no funds
and is not represented as customer traction. Settlement latency is the dominant
bottleneck because concurrent durable state and audit transitions contend on SQLite. A
production multi-instance service will move durable and transient state to
Postgres before claiming horizontal scalability.

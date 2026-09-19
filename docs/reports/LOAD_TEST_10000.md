# 10,000-workflow multi-tenant load test

Generated on September 20, 2026 from the checked-in raw result
[`load-test-10000.json`](load-test-10000.json).

## Outcome

| Measure | Result |
| --- | ---: |
| Organizations | 100 |
| Invoice workflows | 10,000 |
| Concurrent workers | 64 |
| HTTP requests | 19,503 |
| Successful workflows | 10,000 |
| Failed workflows | 0 |
| Workflow error rate | 0.00% |
| Throughput | 15.347 workflows/second |
| Cross-tenant probes denied | 100 / 100 |
| Duplicate-settlement requests | 200 |
| Provider submissions for duplicate storm | 1 |
| Unique transaction hashes for duplicate storm | 1 |
| Shared-treasury contention requests | 100 |
| Shared-treasury reservations admitted | 4 |
| Over-limit payments blocked | 96 |
| Provider submissions during contention | 4 |
| Atomic treasury limit preserved | Yes |
| Duplicate payments | 0 |

## Latency

| Operation | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| Overall | 1,754 ms | 3,436 ms | 3,693 ms |
| Scenario evaluation | 1,673 ms | 2,004 ms | 2,293 ms |
| Settlement | 3,195 ms | 3,587 ms | 3,885 ms |
| Approval request | 588 ms | 778 ms | 965 ms |
| Approval resolution | 1,070 ms | 1,319 ms | 1,501 ms |
| Cross-tenant denial | 5 ms | 16 ms | 24 ms |
| Duplicate-settlement storm | 400 ms | 533 ms | 604 ms |
| Atomic treasury reservation | 472 ms | 589 ms | 1,035 ms |

## What this proves

- Tenant boundaries held under concurrent, mixed-organization traffic.
- Idempotency converged 200 simultaneous retries onto one provider submission.
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

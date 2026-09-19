# 10,000-workflow multi-tenant load test

Generated on September 20, 2026 from the checked-in raw result
[`load-test-10000.json`](load-test-10000.json).

## Outcome

| Measure | Result |
| --- | ---: |
| Organizations | 100 |
| Invoice workflows | 10,000 |
| Concurrent workers | 64 |
| HTTP requests | 19,301 |
| Successful workflows | 10,000 |
| Failed workflows | 0 |
| Workflow error rate | 0.00% |
| Throughput | 17.985 workflows/second |
| Cross-tenant probes denied | 100 / 100 |
| Duplicate-settlement requests | 200 |
| Provider submissions for duplicate storm | 1 |
| Unique transaction hashes for duplicate storm | 1 |
| Duplicate payments | 0 |

## Latency

| Operation | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| Overall | 1,239 ms | 3,357 ms | 3,566 ms |
| Scenario evaluation | 1,182 ms | 1,437 ms | 1,601 ms |
| Settlement | 3,100 ms | 3,483 ms | 3,677 ms |
| Approval request | 568 ms | 767 ms | 940 ms |
| Approval resolution | 1,031 ms | 1,261 ms | 1,356 ms |
| Cross-tenant denial | 6 ms | 22 ms | 29 ms |
| Duplicate-settlement storm | 424 ms | 519 ms | 587 ms |

## What this proves

- Tenant boundaries held under concurrent, mixed-organization traffic.
- Idempotency converged 200 simultaneous retries onto one provider submission.
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

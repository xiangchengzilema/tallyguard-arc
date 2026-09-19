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
| Throughput | 24.403 workflows/second |
| Cross-tenant probes denied | 100 / 100 |
| Duplicate-settlement requests | 200 |
| Provider submissions for duplicate storm | 1 |
| Unique transaction hashes for duplicate storm | 1 |
| Duplicate payments | 0 |

## Latency

| Operation | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| Overall | 760 ms | 2,781 ms | 2,988 ms |
| Scenario evaluation | 688 ms | 930 ms | 1,033 ms |
| Settlement | 2,597 ms | 2,929 ms | 3,114 ms |
| Approval request | 14 ms | 25 ms | 34 ms |
| Approval resolution | 375 ms | 562 ms | 697 ms |
| Cross-tenant denial | 5 ms | 6 ms | 15 ms |
| Duplicate-settlement storm | 363 ms | 578 ms | 621 ms |

## What this proves

- Tenant boundaries held under concurrent, mixed-organization traffic.
- Idempotency converged 200 simultaneous retries onto one provider submission.
- Clean, scheduled, rejected, held, and escalated workflows remained deterministic.
- Role-separated approval and settlement completed without bypassing policy controls.

## Honest limitations

This is a synthetic engineering load test against real loopback HTTP, an ephemeral
SQLite database, and TallyGuard's deterministic Arc simulator. It moved no funds
and is not represented as customer traction. Settlement latency is the dominant
bottleneck because concurrent durable state transitions contend on SQLite. A
production multi-instance service will move durable and transient state to
Postgres before claiming horizontal scalability.

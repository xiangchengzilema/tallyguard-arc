# TallyGuard synthetic multi-tenant load baseline

Generated on 2026-09-20 (Asia/Shanghai) from the machine-readable result in `load-test-baseline.json`.

## Scope

This is a synthetic engineering reliability test. It is not presented as customer traction. The harness creates isolated organizations with distinct finance-operator, approver, and administrator sessions, then drives the real Flask API over loopback HTTP. Settlement uses the deterministic Arc simulator, so no funds move.

## Configuration

| Measure | Value |
| --- | ---: |
| Organizations | 10 |
| Invoice workflows | 200 |
| Concurrent workers | 16 |
| Duplicate settlement storm | 100 requests |
| Injected provider delay | 250 ms |
| Shared-treasury contention | 50 requests |
| HTTP requests | 593 |

The workload mixes clean payments, changed-wallet holds, duplicate-invoice rejections, scheduled payments, and large invoices requiring role-separated approval.

## Result

| Measure | Result |
| --- | ---: |
| Successful workflows | 200 / 200 |
| Workflow error rate | 0% |
| Protected workflows | 80 |
| Cross-tenant reads denied | 10 / 10 |
| Duplicate storm requests succeeded | 100 / 100 |
| Provider submissions during duplicate storm | 1 |
| Unique transaction hashes during duplicate storm | 1 |
| Slow-provider idempotency preserved | Yes |
| Shared-treasury reservations admitted | 4 / 50 |
| Over-limit payments blocked | 46 / 50 |
| Provider submissions during contention | 4 |
| Atomic treasury limit preserved | Yes |
| Duplicate payments | 0 |
| Workflow throughput | 10.353 / second |

Overall HTTP latency was 418.181 ms at p50, 834.853 ms at p95, and 2,936.773 ms at p99 on the local development server. These are baseline figures, not production capacity claims.

## What the baseline found

The duplicate storm held the accepted provider call open for 250 ms while 100 concurrent retries arrived, then converged every request onto one durable receipt, one transaction hash, and one provider submission. A separate contention phase prepared 50 independently payable invoices, then submitted them against one immutable treasury snapshot and a 5,000 USDC daily cap. Exactly four 1,200 USDC reservations were admitted; all 46 excess attempts failed closed before the payment provider.

## Remaining acceptance run

The checked-in 10,000-workflow acceptance run repeats the same isolation, idempotency, and shared-treasury contention controls at submission scale.

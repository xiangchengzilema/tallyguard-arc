# TallyGuard synthetic multi-tenant load baseline

Generated on 2026-09-20 (Asia/Shanghai) from the machine-readable result in `load-test-baseline.json`.

## Scope

This is a synthetic engineering reliability test. It is not presented as customer traction. The harness creates isolated organizations with distinct finance-operator and approver sessions, then drives the real Flask API over loopback HTTP. Settlement uses the deterministic Arc simulator, so no funds move.

## Configuration

| Measure | Value |
| --- | ---: |
| Organizations | 100 |
| Invoice workflows | 200 |
| Concurrent workers | 32 |
| Duplicate settlement storm | 100 requests |
| HTTP requests | 581 |

The workload mixes clean payments, changed-wallet holds, duplicate-invoice rejections, scheduled payments, and large invoices requiring role-separated approval.

## Result

| Measure | Result |
| --- | ---: |
| Successful workflows | 200 / 200 |
| Workflow error rate | 0% |
| Protected workflows | 80 |
| Cross-tenant reads denied | 100 / 100 |
| Duplicate storm requests succeeded | 100 / 100 |
| Provider submissions during duplicate storm | 1 |
| Unique transaction hashes during duplicate storm | 1 |
| Duplicate payments | 0 |
| Workflow throughput | 17.324 / second |

Overall HTTP latency was 333.986 ms at p50, 1,161.905 ms at p95, and 2,154.658 ms at p99 on the local development server. These are baseline figures, not production capacity claims.

## What the baseline found

The first duplicate-storm attempt exposed a stale invoice-version race: 19 of 20 callers safely returned while one received a retryable 409. No double payment occurred. The settlement entry path was then changed to converge on an in-flight state, recheck the durable receipt after a race, and return the same receipt to every caller. The recorded baseline is the post-fix run.

## Remaining acceptance run

Before submission, run the same harness against at least 100 organizations and 10,000 invoice workflows, retain the raw JSON, and compare latency and error-rate changes against this baseline.


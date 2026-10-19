# Demo schedule maintenance — 2026-10-19

This is post-deadline maintenance, not a change to the frozen hackathon
submission, its video, or historical Arc Testnet evidence.

## Cause and fix

The simulator used a fixed September 20 evaluation date and October 10 due
date. Its three-day lead released on October 7, while Agent Runs correctly
used the current application date. Newly generated examples therefore no
longer illustrated waiting, and two showcase/load regressions failed.

New demo cases now receive the same application date provider as planning
and schedule execution. Due dates are creation date + 20 days, with the
existing three-day lead (release on day 17). Policy version labels reflect
that creation date. Dates become immutable when the sample is persisted;
refreshing, restarting, or creating another sample does not move old dates.

The production scheduling rules, approval boundaries, provider idempotency,
schema, historical receipts, and public simulation-only boundary are unchanged.
Old overdue samples legitimately remain due; they are not silently rewritten.

## Local verification

- Reproduced the original two failing tests before editing.
- Full backend suite: **323 passed**, including concurrent multi-tenant Agent Runs.
- Frontend tests: **4 passed**; production build and Python compilation passed.
- Tracked-source and reachable-history credential scans passed.
- Real loopback HTTP deployment smoke: **8/8 passed**.
- Covered original/future dates, year rollover and leap-year rollover; schedule
  runner and Agent Run boundaries before/on/after release; repeated execution;
  current kill-switch revalidation; sealed historical decision replay after restart.
- Browser: new queue due November 8 with release November 5; early schedule run
  reports one waiting and zero settled; plan has two executable and two
  attention items; execution yields SETTLED, ROUTED, SKIPPED, SKIPPED.
- The 1920px schedule/plan view was visually inspected; no browser errors or
  warnings. Existing 1440px Agent Run header wraps excessively beside the
  action toolbar; no frontend styling was changed in this date-only fix.

Private local evidence: `artifacts/schedule-fix-20261019/` (Git-ignored).
Remote deployment and acceptance are recorded separately after execution.

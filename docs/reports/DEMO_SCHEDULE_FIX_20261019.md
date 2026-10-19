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

## Deployed runtime and public acceptance

Application release: `ac1c24cfa1fa8be59f51b43d1fc68e2916ca03d6`.
[GitHub CI passed](https://github.com/xiangchengzilema/tallyguard-arc/actions/runs/39216084259).

The first Git/front-end update exposed a second deployment defect: Gunicorn
loaded an older installed package from virtualenv `site-packages`, not the
updated `src` checkout. A restart and green readiness did not prove the fix was
live; a fresh browser still generated the fixed October 10 due date.

Built an offline-installable wheel from a clean Git archive, backed up the
installed package/metadata/entry points and a consistent database, then replaced
only the TallyGuard runtime. All **39 Python files** match the release after
CRLF/LF normalization, and installed bytes match the verified wheel exactly.
Wheel SHA-256: `2961be4da285239b7d0a9d4d23b3e7dec92c91e9d63d4f3bf40b37258a78c017`.

Verified on `https://tallyguard.online` at approximately 07:37 UTC, October 19:

- Eight public HTTPS smoke checks passed, including role separation, simulated
  settlement, ledger reconciliation and temporary-session revocation.
- A fresh browser created due dates **November 8** and release **November 5**.
- Early schedule execution: **1 scanned, 0 settled, 1 waiting, 0 revalidated**.
- Agent planning: **4 scanned, 2 executable, 2 attention**; execution:
  **SETTLED, ROUTED, SKIPPED, SKIPPED**. The scheduled item stayed unsubmitted.
- Finance and Agent Run screenshots inspected at 1920×1080; browser reports
  zero errors/warnings. The previously noted 1440px layout limitation remains.
- Readiness: ready, database ok, simulation, funds disabled, Mainnet false.
- Caddy PID and the existing port-8765 process/listener were unchanged.

Private rollback backups are under `/var/backups/tallyguard/20261019-ac1c24cfa1fa`
and `/var/backups/tallyguard/20261019-ac1c24c-runtime`; no database rollback was
performed. No Circle transfer, campaign rerun, submission edit, or video change
was made. Future deployments must follow the runtime verification steps in
[Tencent deployment instructions](../../deploy/tencent/README.md).

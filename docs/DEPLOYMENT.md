# Deployment model

TallyGuard ships as one container that builds the React judge console and serves
the static bundle from the Flask API. The checked-in Render Blueprint starts in
safe simulation mode and never requires wallet credentials.
The image also includes the immutable 10,000-workflow reliability JSON consumed
by the auditor-only `/api/reliability/report` endpoint and judge console.

## Public judge deployment

The container currently runs Gunicorn with one process and eight threads. Core
sessions, decisions, approvals, invoices, payment intents, and receipts are all
restart-safe. The remaining one-process constraint comes from the in-process
rate limiter; multiple threads still allow concurrent
judge traffic without presenting the service as horizontally scalable.

SQLite stores invoices, state transitions, payment intents, and receipts. On a
free ephemeral host the database can reset after a restart or idle spin-down.
This is acceptable for the seeded public judge playground, which creates each
scenario on demand. It is not an acceptable production persistence model.

## Production boundary

Before a real multi-instance deployment:

1. Replace local SQLite with managed Postgres.
2. Move the rate limiter to shared infrastructure.
3. Enable multiple Gunicorn workers and run worker-restart recovery tests.
4. Keep `TALLYGUARD_MODE=simulation` on public infrastructure unless a dedicated
   test wallet and environment-only Circle credentials are configured.

## Local container run

```bash
docker build -t tallyguard-arc .
docker run --rm -p 8000:8000 tallyguard-arc
```

Readiness is available at `/api/readiness`. A ready simulation deployment reports
the database, Arc network, and settlement adapter without exposing credentials.

# Deployment model

TallyGuard ships as one container that builds the React judge console and serves
the static bundle from the Flask API. The checked-in Render Blueprint starts in
safe simulation mode and never requires wallet credentials.

## Public judge deployment

The container runs Gunicorn with one process and eight threads. One process is a
deliberate correctness constraint: decision records and approval state are still
transient, although bearer sessions are already durable. Multiple threads allow
concurrent judge traffic, while one process prevents requests from landing on a
worker that does not own the corresponding transient state.

SQLite stores invoices, state transitions, payment intents, and receipts. On a
free ephemeral host the database can reset after a restart or idle spin-down.
This is acceptable for the seeded public judge playground, which creates each
scenario on demand. It is not an acceptable production persistence model.

## Production boundary

Before a real multi-instance deployment:

1. Move decisions and approvals into a shared durable database; sessions are already durable.
2. Replace local SQLite with managed Postgres.
3. Add tenant-aware distributed rate limiting.
4. Enable multiple Gunicorn workers and run worker-restart recovery tests.
5. Keep `TALLYGUARD_MODE=simulation` on public infrastructure unless a dedicated
   test wallet and environment-only Circle credentials are configured.

## Local container run

```bash
docker build -t tallyguard-arc .
docker run --rm -p 8000:8000 tallyguard-arc
```

Readiness is available at `/api/readiness`. A ready simulation deployment reports
the database, Arc network, and settlement adapter without exposing credentials.

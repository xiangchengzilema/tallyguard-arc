# Owned-domain deployment check — 2026-09-28

Public judge URL: https://tallyguard.online

Spaceship's authoritative DNS and a public recursive resolver returned the
apex A record `tallyguard.online → 43.162.123.129`. Caddy's only configuration
change added `tallyguard.online` alongside the existing sslip.io hostname on
the same `127.0.0.1:8180` reverse proxy. The previous Caddyfile was backed up
on the host, the new file validated, and Caddy was reloaded without restarting
the application or changing other listeners.

Checks from an external client:

- New HTTPS homepage returned HTTP 200 with certificate verification enabled.
- `/api/readiness` returned `status: ready`, `settlement_mode: simulation`,
  `funds_movement: disabled`, and `mainnet_enabled: false`.
- The old sslip.io HTTPS hostname still returned HTTP 200.
- Browser inspection showed the new-domain landing page, requester journey
  list, and finance payable queue rendering without visible layout errors.
- `tallyguard-deployment-smoke --base-url https://tallyguard.online` passed all
  eight checks, including role separation, deterministic decision, simulated
  one-time settlement, reconciled receipt, and session revocation.
- `tallyguard` and `caddy` remained active. The unrelated Python listener on
  port 8765 remained present; only Caddy's hostname list changed.

This is synthetic deployment acceptance. The public site does not move funds
and these checks are not real customer usage or new Arc Testnet transfers.

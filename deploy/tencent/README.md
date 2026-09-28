# Tencent Cloud public judge deployment

This deployment is a persistent, simulation-only public judge environment on
Ubuntu 24.04. It does not load Circle credentials or move funds. The current
temporary HTTPS origin is `https://tallyguard.43-162-123-129.sslip.io`;
`https://tallyguard-arc.onrender.com` remains a fallback.

`tallyguard.service` runs one Gunicorn process as the unprivileged `tallyguard`
user, bound only to `127.0.0.1:8180`. Its SQLite database lives at
`/var/lib/tallyguard/tallyguard.sqlite3` and survives application restarts.
`Caddyfile` proxies the public hostname to the loopback application and obtains
its HTTPS certificate automatically. Ports 80 and 443 must remain reachable for
certificate issuance and renewal. The existing server application on port 8765
is unrelated and must not be changed by TallyGuard maintenance.

The deployed source is checked out at `/opt/tallyguard-arc`; its built frontend
is `/opt/tallyguard-arc/web/dist`. Build the frontend locally and upload it with
the source revision as one release. Do not put private keys, API keys, or wallet
credentials in this directory or the systemd unit. Keep the public runtime in
simulation mode even though it displays separately verified historical Arc
Testnet proof.

Useful read-only checks:

```bash
systemctl status tallyguard caddy --no-pager
curl -fsS http://127.0.0.1:8180/api/readiness
curl -fsS https://tallyguard.43-162-123-129.sslip.io/api/readiness
```

Readiness must say `simulation`, `funds_movement: disabled`, and
`mainnet_enabled: false`. Run the public HTTPS smoke from a clean client:

```bash
tallyguard-deployment-smoke --base-url https://tallyguard.43-162-123-129.sslip.io
```

The smoke creates disposable synthetic demo records and revokes its sessions.
It does not prove a live transfer or real customer usage. Before changing to an
owned domain, point its DNS A record at the server, replace the hostname in
`Caddyfile`, validate and reload Caddy, and repeat HTTPS and browser QA. Keep
the old URL working until the new one is verified.

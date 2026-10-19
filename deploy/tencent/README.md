# Tencent Cloud public judge deployment

This deployment is a persistent, simulation-only public judge environment on
Ubuntu 24.04. It does not load Circle credentials or move funds. The public
HTTPS origin is `https://tallyguard.online`. The previous hostname
`https://tallyguard.43-162-123-129.sslip.io` remains available, and
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

## Runtime package deployment is required

The service imports the installed `tallyguard` package from the virtualenv's
`site-packages`, **not** `src/` from the Git checkout. A Git fast-forward,
frontend upload and service restart alone do not deploy backend changes.
This mismatch was found and corrected during October 19 maintenance.

For each authorized release:

1. Build a wheel from a clean archive of the tested Git commit (`pip wheel
   --no-deps`), alongside the matching frontend. Record both artifact hashes.
   Build dependencies may live in an isolated build environment; do not upgrade
   the production virtualenv's dependencies as a side effect.
2. Verify the target revision, clean checkout and wheel hash. Back up the actual
   installed package, distribution metadata and entry-point scripts, plus source
   and frontend. Stop only `tallyguard.service` and make a consistent SQLite
   backup using the backup API before replacing the runtime.
3. Install the verified wheel with the service interpreter:
   `/opt/tallyguard-arc/.venv/bin/python -m pip install --no-deps --no-index
   --force-reinstall /path/to/verified.whl`. Keep the existing service account,
   data path and simulation settings. Do not restart Caddy or unrelated apps.
4. Verify the module import location and **all** installed Python source files
   against the release; normalize CRLF/LF only when comparing a Windows-built
   wheel with a Linux checkout. Check installed bytes against the verified wheel
   separately. Run `pip check`, start TallyGuard, and check readiness.
5. Run public HTTPS smoke and fresh-workspace browser acceptance. Health alone
   cannot prove which code is active. Preserve old immutable demo records rather
   than rewriting them to make a screenshot appear current.

On failure, restore the backed-up runtime/source/frontend and restart only
TallyGuard. Never overwrite a database that may have accepted new requests.
The dated scripts under ignored `artifacts/` are version-specific evidence, not
general-purpose deployment commands; inspect their revision and path guards
before reuse.

October 19 acceptance: application `ac1c24c` matched all 39 installed Python
source files, eight public smoke checks passed, and a new mixed queue waited
until its newly generated release date. Caddy and the port-8765 process were
unchanged. See [maintenance report](../../docs/reports/DEMO_SCHEDULE_FIX_20261019.md).

Useful read-only checks:

```bash
systemctl status tallyguard caddy --no-pager
curl -fsS http://127.0.0.1:8180/api/readiness
curl -fsS https://tallyguard.online/api/readiness
```

Readiness must say `simulation`, `funds_movement: disabled`, and
`mainnet_enabled: false`. Run the public HTTPS smoke from a clean client:

```bash
tallyguard-deployment-smoke --base-url https://tallyguard.online
```

The smoke creates disposable synthetic demo records and revokes its sessions.
It does not prove a live transfer or real customer usage. Spaceship hosts the
apex A record for `tallyguard.online` at `43.162.123.129`. `Caddyfile` serves
the owned domain and previous hostname together. Validate and reload Caddy
after any future hostname change, then repeat HTTPS and browser QA.

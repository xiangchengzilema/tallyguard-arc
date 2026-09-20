# Live operator access

TallyGuard's public judge deployment intentionally uses simulation and may
issue isolated demo sessions. Live Circle mode disables that endpoint. A live
operator therefore needs durable, role-separated identities in the same SQLite
database used by the API.

`tallyguard-operator-setup` creates or verifies one organization and four
single-role principals:

- policy administrator
- finance operator
- payment approver
- audit reviewer

It then issues one opaque bearer session per principal. Only SHA-256 token
digests are stored in SQLite. Raw tokens are printed once to the invoking
terminal, expire after at most 24 hours, and are never written to a report by
the command.

The command performs no network request, wallet operation, or funds movement.

## Provision access

Point the command and the API at the same durable database:

```powershell
$env:TALLYGUARD_DATABASE_PATH = "data/tallyguard-live.sqlite3"

$access = .\.venv\Scripts\tallyguard-operator-setup.exe `
  --organization-id atlas-finance `
  --organization-name "Atlas Finance" `
  --session-hours 8 `
  --confirm PROVISION-TALLYGUARD-OPERATORS | ConvertFrom-Json
```

Keep `$access` only in the current trusted terminal session. Never paste the
JSON into chat, commit it, place it in a URL, store it in frontend source, or
send it to browser analytics. Example API use:

```powershell
$operator = ($access.sessions | Where-Object role -eq "FINANCE_OPERATOR").bearer_token
$headers = @{ Authorization = "Bearer $operator" }
Invoke-RestMethod http://127.0.0.1:8000/api/operations/overview -Headers $headers
```

Run the setup command again with the exact same organization identity to issue
fresh sessions. Existing names, roles, and active state are verified rather
than overwritten; any drift fails closed. Old sessions remain bounded by the
global per-principal retention policy and expire normally.

## Boundary

- Use the public judge console only in labelled simulation mode.
- Use these sessions for a trusted local or private operator workflow.
- The operator may prepare evidence and decisions but cannot approve or execute
  settlement.
- The approver may approve and execute, but cannot be the operator who requested
  a Mainnet approval.
- This provisioning step does not enable Mainnet. Mainnet still requires its
  separate runtime flag, decision-bound approval, hard caps, exact confirmation
  phrase, and acceptance command.

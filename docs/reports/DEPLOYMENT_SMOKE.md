# Real-HTTP deployment smoke

Run date: 2026-09-20  
Classification: synthetic deployment acceptance; not customer traction  
Funds moved: no  
Credentials required: no  
Settlement profile: simulation, Arc Mainnet disabled

## Result

All eight deployment checks passed against a production-shaped Flask
application bound to an ephemeral loopback TCP port:

1. The built React judge console returned HTTP 200 and its root marker.
2. The public health probe returned `ok`.
3. Readiness confirmed the database, simulation adapter, disabled funds
   movement, and disabled Mainnet gate.
4. Separate operator, approver, and auditor sessions were issued.
5. A clean three-way-match scenario produced a deterministic `PAY` decision.
6. The approver completed one simulated settlement; the receipt reached
   `CONFIRMED` and the invoice reached `RECONCILED`.
7. The auditor exported one accounting row whose invoice, decision,
   transaction, status, row count, and SHA-256 content address all matched.
8. All three temporary finance-role sessions were revoked before the run ended.

The raw machine-readable result is in
[`deployment-smoke.json`](deployment-smoke.json).

## Reproduce

Build the frontend, install the Python package, and run:

```bash
tallyguard-deployment-smoke --output docs/reports/deployment-smoke.json
```

The command fails closed if the runtime exposes a live funds-moving adapter or
enables Arc Mainnet. GitHub Actions runs the same smoke command after the
frontend build and Python test suite.

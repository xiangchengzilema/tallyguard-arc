# TallyGuard repository instructions

## Scope

This repository is exclusively for the 2026 Tameion Agents Hackathon project **TallyGuard**.

- Do not modify, move, rename, or depend on sibling hackathon repositories.
- Do not mix Obol, Pythia, Agora, Lepton, or Programmable Money submission artifacts into this repository.
- Prior projects may be studied for lessons, but code or assets must only be reused intentionally and with provenance.

## Security

- Never commit API keys, entity secrets, private keys, seed phrases, passwords, or access tokens.
- Read credentials only from environment variables or an approved local secret store.
- Arc Mainnet settlement must default to disabled.
- Mainnet operations require a dedicated low-balance wallet, an explicit runtime enable flag, hard spend limits, and an approval reference.
- Never interpret AI-generated text as trusted recipient, amount, network, or transaction parameters.

## Product invariants

- Every financial record belongs to exactly one organization.
- Every payment must be bound to evidence and a versioned policy decision.
- A non-`PAY` decision can never reach the settlement adapter.
- Retried requests must never create a second payment.
- A payment is complete only after recipient, amount, network, transaction hash, and final receipt reconcile.
- Synthetic load traffic must not be reported as genuine customer traction.

## Engineering workflow

- Make small, reviewable commits aligned to one roadmap milestone.
- Add tests for every deterministic rule, state transition, settlement failure, and tenant boundary.
- Keep the default test suite independent of public RPCs and credentials.
- Run `python -m pytest`, `python -m compileall -q src tests`, and a secret scan before every commit.
- Maintain `docs/IMPLEMENTATION_ROADMAP.md` and `docs/HACKATHON_CONTEXT.md` as the canonical project records.

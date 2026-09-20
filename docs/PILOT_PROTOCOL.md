# Genuine pilot protocol

TallyGuard separates genuine usage evidence from synthetic engineering tests.
The load-test and deployment-smoke reports prove reliability; they are not user
or customer traction. This protocol captures one real, self-operated or
external pilot without overstating what the evidence proves.

## Before the pilot

1. Choose `self-operated` or `external-pilot`. Never call a self-operated run a
   customer pilot.
2. Use a real workflow that the operator was actually responsible for. Redact
   source documents before uploading if they contain personal, banking, tax, or
   commercially sensitive information.
3. Write two to five acceptance criteria before starting, such as successful
   three-way matching, a correct policy hold, an approval route, or a complete
   evidence packet.
4. Record how the work is normally performed and a reasonable baseline time.
5. Decide separately whether settlement will be simulation, Arc Testnet, or a
   deliberately capped Arc Mainnet proof. Mainnet remains off without explicit
   approval.

## During the pilot

1. Start a timer before evidence review.
2. Run the workflow through the normal console, preserving the original
   evidence hashes and deterministic decision.
3. If settlement is appropriate, use the role-separated approver. Do not turn a
   held or rejected invoice into a payment merely to produce a transaction.
4. Export the Payment Evidence Packet after the final state.
5. Stop the timer and record only criteria actually met.

## Produce the report

Copy `examples/pilot/attestation.template.json` outside the tracked repository,
replace every placeholder with truthful, anonymized observations, and run:

```bash
tallyguard-pilot-report \
  --packet path/to/evidence-packet.json \
  --attestation path/to/pilot-attestation.json \
  --output-json artifacts/pilot-report.json \
  --output-markdown artifacts/PILOT_REPORT.md
```

For a real Arc receipt, append `--verify-arc`. The command then requires the
standalone evidence-packet verifier to confirm the exact canonical-USDC transfer
through Arc RPC before the report may call it independently verified.

The report content-addresses both the product proof and the operator
attestation. It derives the settlement claim from the verified packet: simulator
receipts are always labeled `simulation`; a provider receipt without Arc RPC
proof is labeled `provider-receipt-only`; only a successful independent RPC
check is labeled `arc-rpc-verified`.

## What the report does not prove

- Operator-attested timing is not an independent time-and-motion study.
- A self-operated workflow is genuine usage, not an external customer.
- A Testnet transfer is technical settlement evidence, not revenue.
- A simulation receipt is neither an onchain transfer nor revenue.
- An external pilot must not be described as a paying customer unless payment
  and permission to make that claim are separately documented.

# TallyGuard Figma handoff

- Figma: https://www.figma.com/design/NEjc8rYG930a9dI7yQdmIN
- File key: `NEjc8rYG930a9dI7yQdmIN`
- Design date: 2026-09-20

## Approved visual direction

- Keep the three desktop canvases as separate product surfaces. Do not combine them into a single three-column dashboard.
- `Invoice Review` is the visual master for background, product-shell, typography, borders, status colors, and density.
- `Landing` keeps its editorial hero and animated invoice → policy → approval → settlement sequence.
- `Payables` keeps the operational metrics, invoice table, filters, and summary drawer.
- Marketing display: Instrument Serif
- Product UI: Inter
- Chain, policy, receipt data: IBM Plex Mono
- Primary action: deep green
- Information: blue
- Arc/network context only: violet
- Explicit environment labels: `ARC TESTNET`, `SIMULATION`, and `TESTNET RECEIPT`

## Canonical judge flow

1. `Landing`: watch the product sequence and select Launch judge demo.
2. `Payables`: inspect operational metrics, filters, invoice states, and the Atlas Compute summary drawer.
3. Select Atlas Compute / `INV-2048` and open Review invoice.
4. `Invoice Review`: inspect the invoice, evidence links, policy `AP-v1.4`, and prepared payment intent.
5. Approve the prepared payment intent.
6. Submit to Arc Testnet.
7. View the separate confirmed receipt state and explorer link.

## Route map

- `/` → Landing / story and motion
- `/payables` → Payables / feature workspace
- `/payables/INV-2048` → Invoice Review / visual master
- `/payables/INV-2048/receipt` → Confirmed Arc receipt

The Figma pages remain separate. Cross-page navigation is implemented in the web application rather than duplicating all screens onto one Figma canvas.

## Canonical demo data

- Operator: Jordan Singh
- Vendor: Atlas Compute
- Invoice: `INV-2048`
- Amount: `2,480.00 USDC`
- Invoice date: Sep 10, 2026
- Due: Sep 24, 2026
- Terms: Net 14
- PO: `PO-7781`
- Delivery: `DLV-9321`
- Destination: `0x7e4a…9c21`
- Policy: `AP-v1.4`
- Network: Arc Testnet

## Figma screen nodes

- Foundations: `5:2`
- Landing desktop: `7:2`
- Payables desktop: `11:2`
- Invoice review desktop: `12:2`
- Confirmed receipt desktop: `18:9`
- Payables tablet: `15:2`
- Invoice review mobile: `15:133`

## Implementation notes

- Keep one primary action per financial decision surface.
- The Payables drawer summarizes risk and evidence; full approval only happens on Invoice Review.
- The payment intent is `PREPARED` before explicit settlement. Never present it as settled.
- Use a fixed/sticky action footer and internal scrolling at shorter desktop heights.
- Animate the landing product sequence through invoice → evidence/policy → approval → prepared settlement.

# TallyGuard three-surface design QA

## Visual sources and captures

All comparisons use the approved original Figma boards at their native 1440 × 1024 size and a Chromium viewport of 1440 × 1024 CSS pixels at DPR 1.

| Surface | Approved source | Implementation capture | Combined comparison |
| --- | --- | --- | --- |
| Landing | `D:\币圈项目\arc空投\figma-previews\tallyguard-01-landing.png` | `D:\币圈项目\arc空投\tallyguard-arc\artifacts\tallyguard-landing-1440-final2.png` | `D:\币圈项目\arc空投\tallyguard-arc\artifacts\qa-landing-side-by-side.png` |
| Payables | `D:\币圈项目\arc空投\figma-previews\tallyguard-02-payables.png` | `D:\币圈项目\arc空投\tallyguard-arc\artifacts\tallyguard-payables-1440-final2.png` | `D:\币圈项目\arc空投\tallyguard-arc\artifacts\qa-payables-side-by-side.png` |
| Invoice review | `D:\币圈项目\arc空投\figma-previews\tallyguard-03-invoice-review.png` | `D:\币圈项目\arc空投\tallyguard-arc\artifacts\tallyguard-review-1440-final.png` | `D:\币圈项目\arc空投\tallyguard-arc\artifacts\qa-review-side-by-side.png` |

States: public landing with animated workflow; selected ready-to-authorize invoice; cleared invoice review before payment-intent creation. Focused crops were not needed because each approved board and implementation capture share the same full-screen dimensions and expose all comparison regions without scrolling.

## Full comparison

- Landing: restored the original two-column composition, evidence cards, connector, five-step animated settlement panel, summary strip, success receipt band, and engineering proof strip. The headline, paragraph, actions, trust row, evidence cards, and process panel now align to the approved board. The unrelated dark footer was removed.
- Payables: retained the original navigation, four metrics, five filters, eight-row invoice table, evidence drawer, settlement controls, authorization action, and audit trail. Typography was increased to the approved visual density without changing the information architecture.
- Invoice review: restored the approved review header, invoice sheet, full-height decision rail, four semantic decision groups, agent recommendation, action row, and evidence timeline. Four measured source-to-decision connectors bind invoice evidence, vendor and payout identity, treasury timing, and policy authority without relying on fixed screen coordinates.
- Brand unification is limited to the approved off-white canvas, ink, evidence green, Arc violet, borders, and state fills. The three original layouts were not merged or simplified.
- A 2048 × 1078 regression capture (`artifacts/tallyguard-landing-2048-final.png`) confirms that the landing evidence cards and process panel do not overlap or drift on a wide desktop viewport.

## Iteration history

### Pass 1 — blocked

- [P1] Landing evidence cards intruded into the headline at the user's wide viewport.
- [P1] The review page compressed the invoice and decision content into the upper half of the screen, visually hiding detail.
- [P1] The implementation omitted the delivery-receipt connector and had only four evidence anchors.
- [P2] Payables typography was noticeably smaller than the approved board.

### Pass 2 — blocked

- Landing columns no longer overlapped, but copy and source cards were vertically offset from the approved board.
- Review geometry matched, but decision-row heights and invoice typography were still under-scaled.

### Pass 3 — passed

- Landing geometry aligns at 1440 × 1024 and remains stable at 2048 × 1078.
- Payables hierarchy, density, and drawer content match the approved board closely with no clipping; long state labels are kept on one line.
- Review sheet, grouped evidence rows, four measured connectors, recommendation, actions, and timeline occupy the same regions as the approved board.
- Production frontend build passed.
- Backend test suite passed from the project virtual environment.
- Secret scan found no `ghp_` token or hard-coded long `API_KEY` assignment in Python files.

final result: passed

## Human workflow regression — 2026-09-21

### Role-separated product flow

- Finance operator portal: invoice inbox, demo queue, source evidence, policy result, payment request, Arc settlement, and receipt remain one understandable applicant journey.
- Independent approver portal: pending requests are presented as sealed, read-only decision packets. The approver has a separate role header, queue, evidence checks, authority boundary, decision note, and approve/reject controls.
- Rejection was exercised end to end with a required remediation reason. The rejected request disappeared from the pending queue, settlement stayed locked, and the exact reason reappeared in the finance operator's invoice review.
- Approval was exercised end to end. The finance operator then settled the approved request in the simulation adapter and the lifecycle advanced through Arc settlement to an audit-ready receipt.
- Approval and rejection notes are not interchangeable: entering rejection mode disables accidental approval and offers an explicit return to review.
- API/action errors now remain visible on the finance inbox, invoice review, and approval portal instead of failing silently.

### Layout and responsive checks

- Desktop landing, finance inbox, invoice review, approval inbox, rejection result, and confirmed settlement states were visually inspected in Chromium.
- The landing role section is styled as three aligned responsibilities rather than raw document flow text.
- At a 390 × 844 viewport, the public landing stays readable, the finance inbox becomes a single-column workflow with a compact horizontal role navigation, invoice rows become cards, and the approval portal becomes a stacked review surface.
- The mobile invoice review removes decorative connector lines, stacks the document and decision packet, and keeps the lifecycle horizontally inspectable without forcing the entire application into a desktop-width canvas.
- Browser console inspection found no application-origin errors; the remaining warnings/errors came from installed wallet extensions.

### Verification

- `npm run build`: passed (TypeScript + Vite production build).
- Full backend `pytest -q`: passed to 100%.
- Motion lint scan found no `transition: all`, layout-dimension transitions, or `will-change` declarations.

workflow result: passed

## Two-portal desktop web regression — 2026-09-21

- Scope is now explicitly the desktop browser product. Native desktop packaging and further mobile adaptation are deferred.
- The public site routes into two visibly distinct browser surfaces: a requester-facing **User portal** and a controlled **Finance backend**.
- User portal owns new-request submission and request tracking. It does not expose approval, policy-administration, treasury, or settlement controls.
- Finance backend owns the payment-request inbox, evidence review, independent approval queue, policy controls, Arc settlement, receipts, and audit proof.
- Demo access uses role-intent entry screens instead of pretending that a real password flow already exists. The UI states that a production deployment would connect organization SSO and roles.
- A four-request finance queue was exercised in Chromium. A 2,200 USDC policy exception was routed to the independent approver, rejected with a required correction reason, and removed from the pending queue.
- The same rejected request then appeared in the User portal as **Needs changes**, with the exact finance reason and a **Submit corrected evidence** action. Terminal requests remain in requester history even though they are excluded from the finance work queue.
- Direct role-route access is guarded: requester and finance routes return to their respective role-intent screens when the active browser session has the wrong role.
- No native desktop or mobile-only UI was added in this pass.

two-portal web result: passed

## Desktop product completion pass — 2026-09-21

- The browser product remains deliberately split into a requester-facing User portal and a privileged Finance backend. Native desktop packaging and additional mobile work remain deferred.
- User portal was rechecked at desktop width with four durable requests: active, on-hold, scheduled, and returned-for-changes states remain readable; the selected rejection exposes the exact finance reason and a direct corrected-evidence action.
- New payment request was rechecked as a real four-stage journey: evidence intake, field confirmation, finance review, and Arc receipt. The sample-document path and requester navigation remain visible without exposing finance controls.
- Finance evidence control room, agent runs, vendor trust, policies, approval queue, and audit ledger were visually rechecked against the same editorial system.
- Removed the stale second column from Payment policies. Policy authority, treasury capacity, treasury attestation, and immutable version controls now occupy the full workspace instead of leaving an unexplained blank pane.
- Connected the landing walkthrough's `View details` control to the finance access flow so the public product tour contains no decorative dead action.
- Production frontend build, full backend tests, Python compilation, whitespace validation, secret scan, and motion-property scan all passed. The Vite build reports only its advisory single-chunk size warning.

desktop completion result: passed

## Code-closure desktop audit — 2026-09-21

- Captured the current 1440 × 900 browser flow from public landing through requester entry, request tracking, evidence submission, finance entry, payment-request inbox, invoice review, and approval controls.
- Invoice-review connectors now measure their source and target elements at runtime, remain aligned after resize and decision-panel scrolling, and disappear when a target group is outside the visible review rail.
- The complete 15-control checklist stays collapsed by default so the evidence sheet and four decision groups remain visible in one desktop viewport.
- Reduced the finance overview metric type at constrained desktop widths so `USDC` values remain complete instead of truncating.
- Accepted screenshots are stored under `artifacts/closure-2026-09-21/` and are intentionally ignored by Git; the audit record remains in this tracked file.

code-closure visual result: passed

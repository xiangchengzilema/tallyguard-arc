# Landing page design QA

## Comparison target

- Source visual truth: `C:\Users\55246\AppData\Local\Temp\codex-clipboard-6273351a-ec23-4b38-a7d0-36535c118270.png`
- Source pixels: 2549 × 1403; the supplied browser capture was displayed at a 2048 × 1127 equivalent aspect ratio.
- Rendered implementation: `D:\币圈项目\arc空投\tallyguard-arc\web\artifacts\landing-reference-viewport.png`
- Implementation viewport and pixels: 2048 × 1127 CSS pixels, DPR 1, 2048 × 1127 PNG.
- State: public overview, initial evidence-check stage, light theme, public judge simulation ready.
- Density normalization: both full views were fitted without cropping into equal 1000 × 620 evidence frames. The combined comparison is `D:\币圈项目\arc空投\tallyguard-arc\artifacts\design-qa-final-comparison.png`.
- Focused product-panel comparison: `D:\币圈项目\arc空投\tallyguard-arc\artifacts\design-qa-product-focus.png`.

## Full-view comparison evidence

- Fonts and typography: the local Manrope variable font now provides the wide, controlled grotesk character of the reference. The two-line headline has a 0.91 line height and restrained negative tracking; body text uses a calmer 1.58 line height. No external font request is required.
- Spacing and layout rhythm: both pages use a quiet top navigation, a generous two-column first fold, a large product demonstration panel, and a proof strip at the bottom of the viewport. TallyGuard intentionally replaces the reference email form and customer logos with product actions and verifiable engineering evidence.
- Colors and tokens: the reference's fluorescent yellow is not copied. TallyGuard keeps its own off-white, ink, and evidence-teal palette with accessible contrast and thin neutral separators.
- Image quality and assets: the reference uses an animated product UI rather than photography. TallyGuard therefore renders its own real product workflow using Carbon icons and live DOM state; it does not substitute decorative art, copied logos, or placeholder imagery.
- Copy and content: all text is specific to governed AP on Arc. The first fold communicates the product, the authority boundary, and the no-funds-move simulation status without hackathon-oriented implementation jargon.

## Focused region comparison evidence

- The product panel has the same visual job as the reference: explain the workflow without narration. Its four states progress from sealed evidence through policy, authority, and Arc proof. Active, complete, and pending rows are visibly distinct.
- The animation advances every 1.75 seconds, pauses when the panel leaves the viewport, uses opacity/transform-focused transitions, and resolves to a stable completed state under `prefers-reduced-motion`.
- Realistic invoice, vendor, amount, network, policy, and proof metadata replace generic row filler.

## Comparison history

### Pass 1 — blocked

- [P2] The first headline wrapped to five lines at 1440 × 900, making the hero too tall and pushing the proof strip below the first fold.
- Fix: shortened the product claim to `Evidence checked. Payments proved.`, tightened the type scale, and reduced hero height while preserving the two-column hierarchy.
- Post-fix evidence: `D:\币圈项目\arc空投\tallyguard-arc\web\artifacts\landing-desktop-pass2.png` shows the proof strip inside the 1440 × 900 viewport.

### Pass 2 — passed

- The 2048 × 1127 reference-ratio capture preserves the intended two-line headline, balanced product panel, and visible proof strip.
- Tablet 768 × 1024 and mobile 390 × 844 captures have no horizontal overflow; the mobile layout keeps both primary actions and the product walkthrough usable.
- Primary interaction tested: `Run governed payment` navigates to `#payables`, executes the selected safe scenario, and exposes the evaluation workbench.
- Animation progression tested from `Reading source evidence…` to `Running policy controls…`.
- Browser console errors: 0. Browser console warnings: 0.

## Residual P3 polish

- Carbon emits two informational feature-flag notices in development; these are not warnings or user-visible UI.
- The public hero deliberately remains quieter than the operational workspace so the transition into the dense AP product feels intentional.

final result: passed

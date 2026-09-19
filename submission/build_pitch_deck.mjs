import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SKILL_DIR = "C:/Users/55246/.codex/plugins/cache/openai-primary-runtime/presentations/26.909.12148/skills/presentations";
const BUILD_DIR = path.join(ROOT, ".codex-deck");
const FINAL_PPTX = path.join(ROOT, "submission", "TallyGuard_Tameion_Pitch_v3.pptx");
const RUNTIME_PYTHON = "C:/Users/55246/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe";

const { resolvePresentationFont, finalizePresentation } = await import(
  pathToFileURL(path.join(SKILL_DIR, "container_tools/artifact_tool_utils.mjs")).href,
);

await fs.mkdir(BUILD_DIR, { recursive: true });
await fs.mkdir(path.dirname(FINAL_PPTX), { recursive: true });

const fontFamily = resolvePresentationFont();
const monoFamily = resolvePresentationFont({ fontFamily: "Consolas" });

const C = {
  ink: "#091718",
  ink2: "#102326",
  paper: "#F4F7F5",
  white: "#FFFFFF",
  muted: "#5D6B6D",
  line: "#C9D5D2",
  teal: "#00A99D",
  aqua: "#58E0D0",
  mint: "#B9F5CC",
  amber: "#F2C14E",
  coral: "#F56C6C",
  blue: "#4A77FF",
  violet: "#A98BFF",
  soft: "#E5EFEC",
};

const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });

function addShape(slide, geometry, left, top, width, height, fill = "none", lineFill = "none", lineWidth = 0, radius = undefined) {
  const shape = slide.shapes.add({
    geometry,
    position: { left, top, width, height },
    fill,
    line: { style: "solid", fill: lineFill, width: lineWidth },
    ...(radius ? { borderRadius: radius } : {}),
  });
  return shape;
}

function addText(slide, text, left, top, width, height, options = {}) {
  const shape = addShape(slide, "textbox", left, top, width, height, "none", "none", 0);
  shape.text = text;
  shape.text.style = {
    typeface: options.typeface ?? fontFamily,
    fontSize: options.fontSize ?? 24,
    bold: options.bold ?? false,
    color: options.color ?? C.ink,
    alignment: options.alignment ?? "left",
    verticalAlignment: options.verticalAlignment ?? "top",
    autoFit: options.autoFit ?? "shrinkText",
    wrap: options.wrap ?? "square",
    lineSpacing: options.lineSpacing ?? 1,
    insets: options.insets ?? { top: 0, right: 0, bottom: 0, left: 0 },
  };
  return shape;
}

function addPill(slide, text, left, top, width, fill, color = C.ink, line = "none") {
  const pill = addShape(slide, "roundRect", left, top, width, 28, fill, line, line === "none" ? 0 : 1, "rounded-xl");
  pill.text = text;
  pill.text.style = {
    typeface: fontFamily,
    fontSize: 12,
    bold: true,
    color,
    alignment: "center",
    verticalAlignment: "middle",
    autoFit: "shrinkText",
    insets: { top: 2, right: 8, bottom: 2, left: 8 },
  };
  return pill;
}

function addRule(slide, left, top, width, color = C.line, height = 1) {
  return addShape(slide, "rect", left, top, width, height, color, "none", 0);
}

function addFooter(slide, number, dark = false) {
  const color = dark ? "#7EA09D" : C.muted;
  addText(slide, "TALLYGUARD  /  TAMEION AGENTS HACKATHON 2026", 48, 681, 560, 16, { fontSize: 10, bold: true, color });
  addText(slide, String(number).padStart(2, "0"), 1182, 679, 50, 18, { fontSize: 11, bold: true, color, alignment: "right" });
}

function addEyebrow(slide, text, dark = false) {
  addText(slide, text.toUpperCase(), 56, 42, 620, 22, { fontSize: 12, bold: true, color: dark ? C.aqua : "#007E76" });
}

function addTitle(slide, title, subtitle = "", dark = false) {
  addText(slide, title, 56, 72, 1160, 74, { fontSize: 43, bold: false, color: dark ? C.white : C.ink });
  if (subtitle) addText(slide, subtitle, 58, 148, 1120, 44, { fontSize: 18, color: dark ? "#B8CFCC" : C.muted });
}

async function addImage(slide, fileName, position, options = {}) {
  const bytes = new Uint8Array(await fs.readFile(path.join(ROOT, "submission", "assets", fileName)));
  return slide.images.add({
    blob: bytes,
    contentType: "image/png",
    alt: options.alt ?? fileName,
    fit: options.fit ?? "contain",
    position,
    ...(options.crop ? { crop: options.crop } : {}),
    geometry: options.geometry ?? "roundRect",
    borderRadius: options.borderRadius ?? "rounded-xl",
  });
}

function setNotes(slide, text) {
  slide.speakerNotes.textFrame.setText(text);
}

// 1. Cover
{
  const slide = presentation.slides.add();
  slide.background.fill = C.ink;
  addText(slide, "TALLYGUARD", 58, 46, 300, 24, { fontSize: 14, bold: true, color: C.aqua });
  addText(slide, "Proof before\npayment.", 58, 126, 530, 170, { fontSize: 62, bold: false, color: C.white, lineSpacing: 0.9 });
  addText(slide, "Evidence-bound autonomous accounts payable\nfor AI-operated businesses on Arc.", 62, 326, 500, 78, { fontSize: 22, color: "#B8CFCC", lineSpacing: 1.05 });
  addPill(slide, "PRIMARY RFB  ·  AP / AR AUTOMATION", 62, 450, 292, C.aqua, C.ink);
  addPill(slide, "COMPLIANCE INTELLIGENCE", 365, 450, 214, C.ink2, C.aqua, C.aqua);
  addText(slide, "Canteen × Circle × Arc  /  September 2026", 62, 636, 480, 24, { fontSize: 13, color: "#7EA09D" });
  addShape(slide, "roundRect", 628, 70, 590, 574, C.ink2, "#23423F", 1, "rounded-2xl");
  await addImage(slide, "tallyguard-hero.png", { left: 650, top: 112, width: 546, height: 390 }, { alt: "TallyGuard judge workspace", fit: "contain" });
  addShape(slide, "rect", 628, 540, 590, 104, C.ink2, "none", 0);
  addText(slide, "DETERMINISTIC AUTHORITY", 654, 562, 250, 18, { fontSize: 11, bold: true, color: C.aqua });
  addText(slide, "AI explains. Policy authorizes. Arc proves.", 654, 590, 510, 24, { fontSize: 17, color: C.white });
  setNotes(slide, "Product screenshot: local TallyGuard judge workspace. Project sources: docs/HACKATHON_CONTEXT.md and docs/ARCHITECTURE.md.");
}

// 2. Problem
{
  const slide = presentation.slides.add();
  slide.background.fill = C.paper;
  addEyebrow(slide, "The control gap");
  addTitle(slide, "Agents can move faster than finance can verify.", "Accounts payable needs machine speed without surrendering financial authority.");
  const bandY = 244;
  const widths = [364, 364, 364];
  const lefts = [56, 458, 860];
  const colors = [C.coral, C.amber, C.violet];
  const labels = ["UNTRUSTED INPUT", "AMBIGUOUS AUTHORITY", "WEAK SETTLEMENT PROOF"];
  const statements = [
    "Invoices, emails, PDFs, prompts, and wallet-change requests can all carry conflicting or manipulated instructions.",
    "A model recommendation is useful context, but it cannot decide whether company funds are legally or operationally authorized.",
    "A provider status alone does not prove the exact USDC transfer landed on the intended Arc network and recipient.",
  ];
  for (let i = 0; i < 3; i += 1) {
    addRule(slide, lefts[i], bandY, widths[i], colors[i], 7);
    addText(slide, String(i + 1).padStart(2, "0"), lefts[i], bandY + 28, 70, 46, { fontSize: 30, color: colors[i] });
    addText(slide, labels[i], lefts[i] + 82, bandY + 35, 278, 24, { fontSize: 13, bold: true, color: C.ink });
    addText(slide, statements[i], lefts[i], bandY + 104, widths[i] - 8, 176, { fontSize: 21, color: C.ink, lineSpacing: 1.08 });
  }
  addRule(slide, 56, 574, 1134, C.line, 1);
  addText(slide, "The missing primitive", 56, 598, 240, 24, { fontSize: 15, bold: true, color: C.muted });
  addText(slide, "A verifiable decision boundary between evidence and money movement.", 300, 592, 890, 38, { fontSize: 28, color: C.ink });
  addFooter(slide, 2);
  setNotes(slide, "Problem framing derived from docs/SECURITY_MODEL.md and docs/BACKEND_PRODUCT_SPEC.md. No market-size claim is made.");
}

// 3. Product flow
{
  const slide = presentation.slides.add();
  slide.background.fill = C.ink;
  addEyebrow(slide, "One bounded workflow", true);
  addTitle(slide, "From source evidence to independently verified settlement.", "Every step produces an immutable record. Every fund-moving action is revalidated.", true);

  const nodes = [
    { x: 58, w: 190, n: "01", t: "Seal evidence", d: "PDF, image, or JSON\nwith field provenance" },
    { x: 286, w: 190, n: "02", t: "Apply policy", d: "12 deterministic\nfinancial controls" },
    { x: 514, w: 190, n: "03", t: "Plan actions", d: "PAY, schedule, hold,\nreject, or escalate" },
    { x: 742, w: 190, n: "04", t: "Move USDC", d: "Circle wallet with\nidempotent intent" },
    { x: 970, w: 250, n: "05", t: "Prove on Arc", d: "Exact Transfer event,\nnetwork, asset, recipient" },
  ];
  const shapes = [];
  for (const node of nodes) {
    const box = addShape(slide, "roundRect", node.x, 254, node.w, 230, C.ink2, "#28514D", 1, "rounded-2xl");
    shapes.push(box);
    addText(slide, node.n, node.x + 18, 275, 46, 28, { fontSize: 17, bold: true, color: C.aqua });
    addText(slide, node.t, node.x + 18, 326, node.w - 36, 52, { fontSize: 23, bold: true, color: C.white });
    addText(slide, node.d, node.x + 18, 402, node.w - 36, 56, { fontSize: 15, color: "#AFC8C5" });
  }
  for (const x of [255, 483, 711, 939]) addShape(slide, "rightArrow", x, 360, 24, 18, C.aqua, "none", 0);
  addText(slide, "AUTHORITY NEVER COMES FROM THE MODEL", 58, 528, 380, 24, { fontSize: 13, bold: true, color: C.amber });
  addText(slide, "The AI may classify and explain. Deterministic controls, human approvals, and sealed records decide whether funds can move.", 58, 560, 1110, 52, { fontSize: 20, color: C.white });
  addFooter(slide, 3, true);
  setNotes(slide, "Architecture source: docs/ARCHITECTURE.md. Settlement source: docs/CIRCLE_ARC_SETTLEMENT.md.");
}

// 4. Evidence and decision
{
  const slide = presentation.slides.add();
  slide.background.fill = C.paper;
  addEyebrow(slide, "Deterministic decision");
  addTitle(slide, "The judge can inspect why an invoice is payable.", "The result is replayable from sealed evidence, policy version, and treasury state.");
  addShape(slide, "roundRect", 56, 214, 706, 420, C.white, C.line, 1, "rounded-xl");
  await addImage(slide, "decision-evidence.png", { left: 70, top: 228, width: 678, height: 392 }, { alt: "Evidence package and deterministic payment decision", fit: "cover", crop: { left: 0, top: 0.03, right: 0, bottom: 0.25 } });
  const claims = [
    ["12 / 12", "controls clear", "Vendor, wallet, PO, delivery, route, limits, reserve, and timing."],
    ["SHA-256", "source binding", "The decision carries the evidence manifest, policy hash, and replay snapshot."],
    ["0", "AI payment authority", "The agent can recommend and explain, but cannot set amount, recipient, or authorization."],
  ];
  let y = 224;
  for (const [value, label, desc] of claims) {
    addText(slide, value, 816, y, 150, 44, { fontSize: 34, color: C.teal });
    addText(slide, label.toUpperCase(), 978, y + 7, 235, 22, { fontSize: 12, bold: true, color: C.ink });
    addText(slide, desc, 816, y + 58, 392, 76, { fontSize: 17, color: C.muted });
    y += 132;
  }
  addFooter(slide, 4);
  setNotes(slide, "Screenshot captured from the local product. Supporting implementation: src/tallyguard/decisions.py, src/tallyguard/policies.py, src/tallyguard/evidence.py.");
}

// 5. Bounded agent run
{
  const slide = presentation.slides.add();
  slide.background.fill = C.ink;
  addEyebrow(slide, "Agentic sophistication", true);
  addTitle(slide, "One agent run. Every control rechecked.", "A durable plan hashes observed state and proposed actions before any execution.", true);
  addShape(slide, "roundRect", 56, 210, 1168, 398, C.ink2, "#28514D", 1, "rounded-xl");
  await addImage(slide, "agent-executed.png", { left: 72, top: 226, width: 1136, height: 366 }, { alt: "Executed bounded autonomous accounts payable run", fit: "contain" });
  addPill(slide, "SETTLED", 70, 620, 108, C.mint, C.ink);
  addText(slide, "Policy-cleared payment", 190, 624, 220, 20, { fontSize: 14, color: C.white });
  addPill(slide, "ROUTED", 448, 620, 108, "#E4D8FF", "#4A22B8");
  addText(slide, "Segregated approval", 568, 624, 220, 20, { fontSize: 14, color: C.white });
  addPill(slide, "SKIPPED", 824, 620, 108, "#D8E7FF", "#214FB4");
  addText(slide, "Schedule and hold preserved", 944, 624, 260, 20, { fontSize: 14, color: C.white });
  addFooter(slide, 5, true);
  setNotes(slide, "Screenshot captured from the local product. Implementation: src/tallyguard/autonomy.py and src/tallyguard/persistence.py. The execution lease prevents multiple workers from executing the same durable plan.");
}

// 6. Decision matrix
{
  const slide = presentation.slides.add();
  slide.background.fill = C.paper;
  addEyebrow(slide, "Native control matrix");
  addText(slide, "Five outcomes. Only one grants immediate\npayment authority.", 56, 72, 1160, 98, { fontSize: 40, color: C.ink, lineSpacing: 0.92 });
  addText(slide, "The decision engine fails closed and tells the operator exactly what happens next.", 58, 180, 1120, 32, { fontSize: 17, color: C.muted });

  const table = slide.tables.add({
    rows: 6,
    columns: 4,
    left: 56,
    top: 236,
    width: 1168,
    height: 368,
    columnWidths: [170, 310, 430, 258],
    values: [
      ["OUTCOME", "TRIGGER", "SYSTEM ACTION", "WHO CAN RELEASE"],
      ["PAY", "All mandatory controls pass", "Create an idempotent payment intent and settle", "Policy engine"],
      ["SCHEDULE", "Valid invoice, future payment window", "Wait, then revalidate against current policy", "Policy engine at due time"],
      ["HOLD", "Evidence, wallet, or treasury risk", "Block settlement and request remediation", "Operator after correction"],
      ["REJECT", "Duplicate or prohibited condition", "Permanently deny this payment request", "No automatic release"],
      ["ESCALATE", "Valid evidence above autonomy limit", "Bind a separate human approval to the intent", "Independent approver"],
    ],
  });
  table.borders.assign({ style: "solid", fill: C.line, width: 1 });
  table.cells.block({ row: 0, column: 0, rowCount: 1, columnCount: 4 }).assign({
    fill: C.ink,
    textStyle: { typeface: fontFamily, fontSize: 14, bold: true, color: C.aqua },
    margins: { top: 10, right: 12, bottom: 10, left: 12 },
    verticalAlignment: "middle",
  });
  table.cells.block({ row: 1, column: 0, rowCount: 5, columnCount: 4 }).assign({
    fill: C.white,
    textStyle: { typeface: fontFamily, fontSize: 16, color: C.ink },
    margins: { top: 10, right: 12, bottom: 10, left: 12 },
    verticalAlignment: "middle",
  });
  const outcomeColors = [C.mint, "#D8E7FF", "#FFF0C7", "#FFD8D8", "#E4D8FF"];
  for (let r = 1; r < 6; r += 1) {
    table.getCell(r, 0).fill = outcomeColors[r - 1];
    table.getCell(r, 0).text.style = { typeface: fontFamily, fontSize: 17, bold: true, color: C.ink };
  }
  addText(slide, "Editable control matrix", 56, 624, 240, 20, { fontSize: 12, bold: true, color: C.muted });
  addText(slide, "Non-PAY outcomes never call the settlement provider.", 780, 620, 444, 24, { fontSize: 16, bold: true, color: "#007E76", alignment: "right" });
  addFooter(slide, 6);
  setNotes(slide, "Decision semantics are implemented in src/tallyguard/models.py, src/tallyguard/policy.py, and src/tallyguard/autonomy.py.");
}

// 7. Circle + Arc
{
  const slide = presentation.slides.add();
  slide.background.fill = C.paper;
  addEyebrow(slide, "Circle and Arc");
  addText(slide, "Settlement is accepted only after independent Arc\nverification.", 56, 72, 1160, 98, { fontSize: 40, color: C.ink, lineSpacing: 0.92 });
  addText(slide, "Provider completion is necessary, then TallyGuard verifies the exact onchain result.", 58, 180, 1120, 32, { fontSize: 17, color: C.muted });
  const y = 278;
  const specs = [
    [70, 220, "1", "PAY intent", "Sealed amount, recipient, asset, network, idempotency key"],
    [342, 220, "2", "Circle wallet", "Submit USDC transfer and poll provider lifecycle"],
    [614, 220, "3", "Arc RPC", "Fetch the transaction receipt from the expected chain"],
    [886, 324, "4", "Exact Transfer proof", "Match contract, topic, sender, recipient, amount, and success"],
  ];
  const boxes = [];
  for (const [x, w, n, t, d] of specs) {
    const box = addShape(slide, "roundRect", x, y, w, 214, C.white, C.line, 1, "rounded-2xl");
    boxes.push(box);
    addPill(slide, String(n), x + 18, y + 18, 36, n === "4" ? C.mint : C.soft, C.ink);
    addText(slide, t, x + 18, y + 68, w - 36, 48, { fontSize: 24, bold: true, color: C.ink });
    addText(slide, d, x + 18, y + 124, w - 36, 66, { fontSize: 15, color: C.muted });
  }
  for (const x of [300, 572, 844]) addShape(slide, "rightArrow", x, y + 98, 32, 18, C.teal, "none", 0);
  addShape(slide, "roundRect", 70, 536, 1140, 70, "#E9F7F4", "#A9DAD3", 1, "rounded-xl");
  addText(slide, "CURRENT GATE", 92, 556, 130, 18, { fontSize: 11, bold: true, color: "#007E76" });
  addText(slide, "Adapter and verifier are implemented. A real Arc Testnet USDC transfer remains the next external acceptance test.", 226, 550, 954, 34, { fontSize: 18, color: C.ink });
  addFooter(slide, 7);
  setNotes(slide, "Implementation: src/tallyguard/circle_arc.py and src/tallyguard/settlement.py. Technical reference: docs/CIRCLE_ARC_SETTLEMENT.md. The slide intentionally labels the real Testnet transfer as pending.");
}

// 8. Reliability evidence
{
  const slide = presentation.slides.add();
  slide.background.fill = C.ink;
  addEyebrow(slide, "Checked-in engineering evidence", true);
  addTitle(slide, "The safety invariants survive concurrency.", "Two reproducible HTTP stress suites ship with immutable JSON reports.", true);
  addShape(slide, "roundRect", 56, 200, 1168, 414, C.ink2, "#28514D", 1, "rounded-xl");
  await addImage(slide, "reliability.png", { left: 72, top: 216, width: 1136, height: 382 }, { alt: "TallyGuard reliability report", fit: "contain" });
  addText(slide, "10,000 / 10,000 workflows", 70, 626, 270, 22, { fontSize: 15, bold: true, color: C.aqua });
  addText(slide, "0 duplicate payments", 368, 626, 220, 22, { fontSize: 15, bold: true, color: C.aqua });
  addText(slide, "100 / 100 tenant breaches denied", 616, 626, 282, 22, { fontSize: 15, bold: true, color: C.aqua });
  addText(slide, "100 contenders → 1 execution", 920, 626, 280, 22, { fontSize: 15, bold: true, color: C.aqua });
  addFooter(slide, 8, true);
  setNotes(slide, "Sources: docs/reports/load-test-10000.json and docs/reports/agent-run-load-50.json. These are synthetic engineering tests, not customer traction. Settlement used the deterministic Arc simulator and moved no funds.");
}

// 9. Genuine traction plan
{
  const slide = presentation.slides.add();
  slide.background.fill = C.paper;
  addEyebrow(slide, "Traction without theater");
  addTitle(slide, "Turn one real AP workflow into judge-verifiable evidence.", "We will not label generated traffic as users. The pilot records real operator behavior and real acceptance criteria.");
  const stages = [
    [56, "NOW", "Self-operated pilot", "Run a genuine invoice, PO, delivery record, wallet, and treasury snapshot through the product.", "Evidence completion · decision replay · operator time"],
    [446, "NEXT", "External review", "Ask 2–3 finance or crypto operators to complete the judge path and record structured feedback.", "Completion rate · time-to-decision · blocked-risk recall"],
    [836, "BEFORE SUBMISSION", "Real Arc proof", "Execute a capped Testnet transfer, reconcile the receipt, and publish the proof packet and demo clip.", "Circle status · Arc tx hash · exact Transfer match"],
  ];
  for (let i = 0; i < stages.length; i += 1) {
    const [x, phase, title, desc, metric] = stages[i];
    addText(slide, String(i + 1).padStart(2, "0"), x, 238, 60, 44, { fontSize: 30, color: C.teal });
    addPill(slide, phase, x + 74, 242, i === 2 ? 170 : 94, i === 2 ? C.amber : C.soft, C.ink);
    addText(slide, title, x, 310, 330, 42, { fontSize: 25, bold: true, color: C.ink });
    addText(slide, desc, x, 370, 332, 120, { fontSize: 18, color: C.muted });
    addRule(slide, x, 518, 332, C.line, 1);
    addText(slide, "MEASURE", x, 538, 88, 18, { fontSize: 11, bold: true, color: "#007E76" });
    addText(slide, metric, x, 566, 332, 48, { fontSize: 15, color: C.ink });
  }
  addFooter(slide, 9);
  setNotes(slide, "This is the honest traction plan for the current build. Synthetic reliability data remains separately labeled. Deadline source: docs/HACKATHON_CONTEXT.md.");
}

// 10. Close
{
  const slide = presentation.slides.add();
  slide.background.fill = C.ink;
  addText(slide, "TALLYGUARD", 58, 46, 300, 24, { fontSize: 14, bold: true, color: C.aqua });
  addText(slide, "Approve the evidence.\nAutomate the payment.", 58, 112, 690, 142, { fontSize: 52, color: C.white, lineSpacing: 0.92 });
  addText(slide, "A finance control plane for AI-operated businesses, built for Circle USDC on Arc.", 62, 290, 660, 60, { fontSize: 22, color: "#B8CFCC" });
  addRule(slide, 62, 402, 1128, "#28514D", 1);
  addText(slide, "READY", 62, 434, 100, 18, { fontSize: 11, bold: true, color: C.aqua });
  addText(slide, "Multi-tenant product · deterministic controls · bounded agent runs · proof packets · stress evidence", 62, 464, 1100, 30, { fontSize: 20, color: C.white });
  addText(slide, "NEXT PROOF", 62, 528, 120, 18, { fontSize: 11, bold: true, color: C.amber });
  addText(slide, "Capped Arc Testnet USDC transfer · external operator feedback · final under-3-minute walkthrough", 62, 558, 1100, 30, { fontSize: 20, color: C.white });
  addPill(slide, "TAMEION AGENTS HACKATHON", 62, 626, 236, C.aqua, C.ink);
  addText(slide, "Submission deadline  ·  October 10, 2026 11:59 PM ET", 328, 630, 600, 22, { fontSize: 14, color: "#7EA09D" });
  addFooter(slide, 10, true);
  setNotes(slide, "Event information: https://tameion.thecanteenapp.com/ and docs/HACKATHON_CONTEXT.md. GitHub and live-demo links should be added only after the user approves publication and deployment.");
}

const candidatePath = path.join(BUILD_DIR, "TallyGuard_Tameion_Pitch_v3.candidate.pptx");
await (await PresentationFile.exportPptx(presentation)).save(candidatePath);

const requirements = {
  explicitTotalSlideCount: 10,
  requiredNativeTableOwnerSlides: [6],
  requiredNativeChartOwnerSlides: [],
};
const fontPolicy = { basis: "design", families: [fontFamily, monoFamily] };
const expectedSlideSizeEmu = "12192000,6858000";

const result = await finalizePresentation({
  ...requirements,
  workspaceDir: ROOT,
  candidatePath,
  finalPath: FINAL_PPTX,
  pythonExecutable: RUNTIME_PYTHON,
  integrityValidatorPath: path.join(SKILL_DIR, "container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath: path.join(SKILL_DIR, "container_tools/inspect_presentation_layout_geometry.py"),
  layoutArgs: [
    "--expected-slide-size-emu", expectedSlideSizeEmu,
    "--validate-bullet-geometry",
    "--validate-heading-fit",
    "--require-native-table-slide", "6",
  ],
  requiredNativeTableOwnerSlides: requirements.requiredNativeTableOwnerSlides,
  fontPolicy,
  verifyArtifactToolImport: true,
  receiptPath: path.join(BUILD_DIR, "TallyGuard_Tameion_Pitch_v3.validation.json"),
});

console.log(JSON.stringify({ fontFamily, monoFamily, finalPath: FINAL_PPTX, result }, null, 2));

import { type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  Button,
  Content,
  Header,
  HeaderGlobalBar,
  HeaderGlobalAction,
  HeaderName,
  InlineLoading,
  InlineNotification,
  Modal,
  Search,
  Select,
  SelectItem,
  SkeletonText,
  Tag,
  TextInput,
  Theme,
} from '@carbon/react';
import {
  ArrowRight,
  Dashboard,
  CheckmarkFilled,
  Document,
  DocumentSecurity,
  Download,
  Launch,
  ListChecked,
  Locked,
  Money,
  PlayFilled,
  Renew,
  Rule,
  SettingsAdjust,
  Time,
  UserProfile,
  UserMultiple,
  Wallet,
  WarningAltFilled,
} from '@carbon/icons-react';
import {
  ApiError,
  OperatorAccessRequired,
  activatePolicyVersion,
  bootstrap,
  bootstrapWithSessions,
  createAgentRun,
  downloadAccountingLedger,
  downloadAgentRunProof,
  downloadEvidenceDocument,
  downloadEvidencePacket,
  executeAgentRun,
  fetchAuditEvents,
  fetchArcActivity,
  fetchConfirmedPayment,
  fetchDecisionApproval,
  fetchGovernanceOverview,
  fetchInvoiceEvidence,
  fetchInvoiceAudit,
  fetchInvoiceRun,
  fetchOperationsOverview,
  fetchPolicyHistory,
  fetchSettlementIncidents,
  fetchVendorDirectory,
  loadSamplePdfEvidence,
  onboardVendor,
  recordTreasurySnapshot,
  refreshLiveTreasurySnapshot,
  requestApproval,
  reviewEvidenceFiles,
  resolveApproval,
  revokeOperatorSessions,
  runLiveEvidenceWorkflow,
  runUploadedEvidenceWorkflow,
  runScenario,
  runDueSchedules,
  rotateVendorWallet,
  seedAutonomyShowcase,
  settleInvoice,
  settlePaymentBatch,
  simulateDecisionPolicy,
  verifyDecisionReplay,
} from './api';
import { ArcActivityPage, ArcActivityPreview } from './ArcActivity';
import type {
  Approval,
  ArcActivityResponse,
  AgentRun,
  AgentAction,
  AuditSearchRequest,
  AuditSearchResult,
  AuditTrail,
  BootstrapContext,
  BootstrapData,
  DecisionAction,
  EvidenceDocument,
  EvidenceFileBundle,
  EvidenceFieldOverrides,
  EvidenceFileReview,
  GovernanceOverview,
  OperationsOverview,
  OperationsInvoice,
  Payment,
  PaymentBatch,
  PolicyActivation,
  ActivePolicy,
  PolicyDraft,
  PolicySimulation,
  ReliabilityEvidence,
  ReplayVerification,
  RuleResult,
  RuleDisposition,
  RunResult,
  ScheduleRun,
  Scenario,
  SettlementBatchItem,
  SettlementIncidentOverview,
  TreasurySnapshotRecord,
  VendorOnboardingDraft,
  VendorTrustRecord,
  VendorWalletRotationDraft,
} from './types';

const ACTION_TAG: Record<DecisionAction, 'green' | 'red' | 'magenta' | 'purple' | 'blue'> = {
  PAY: 'green',
  HOLD: 'magenta',
  REJECT: 'red',
  ESCALATE: 'purple',
  SCHEDULE: 'blue',
};

const ACTION_LABEL: Record<DecisionAction, string> = {
  PAY: 'Approved for payment',
  HOLD: 'Payment held',
  REJECT: 'Payment rejected',
  ESCALATE: 'Approval required',
  SCHEDULE: 'Scheduled',
};

const QUEUE_DECISION_LABEL: Record<DecisionAction, string> = {
  PAY: 'Approved',
  HOLD: 'On hold',
  REJECT: 'Rejected',
  ESCALATE: 'Review',
  SCHEDULE: 'Scheduled',
};

const shorten = (value: string, head = 8, tail = 6) =>
  value.length > head + tail + 3 ? `${value.slice(0, head)}...${value.slice(-tail)}` : value;

const formatMoney = (value: string) =>
  new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(Number(value));

const RULE_PRESENTATION: Record<string, { title: string; why: string; passNext: string }> = {
  TENANT_BOUNDARY_OK: { title: 'Organization boundary verified', why: 'Every invoice, supplier, policy, and treasury record must belong to this company workspace.', passNext: 'Continue to payment safety controls.' },
  TENANT_BOUNDARY_VIOLATION: { title: 'Organization boundary failed', why: 'Cross-company records could expose data or authorize the wrong payment.', passNext: 'Stop and rebuild the request with records from one organization.' },
  KILL_SWITCH_CLEAR: { title: 'Emergency stop is off', why: 'Finance has not paused automated payment processing for this workspace.', passNext: 'Continue to duplicate and supplier checks.' },
  KILL_SWITCH_ACTIVE: { title: 'Payments paused by finance', why: 'The emergency stop prevents any automated settlement while an incident is reviewed.', passNext: 'Wait for a treasury owner to reopen payment processing.' },
  INVOICE_UNIQUE: { title: 'No duplicate invoice found', why: 'The source fingerprint has not appeared in another payment request.', passNext: 'Continue to supplier identity checks.' },
  DUPLICATE_INVOICE: { title: 'Duplicate invoice blocked', why: 'The same source evidence has already been recorded and must not be paid twice.', passNext: 'Open the existing request or submit corrected evidence.' },
  VENDOR_INVOICE_NUMBER_REUSED: { title: 'Possible duplicate — review required', why: 'This supplier invoice number appears in another request even though the document or amount changed. The earlier request ID is shown in the finding.', passNext: 'Compare both sealed evidence packets and payment histories. An independent approver must document whether this is a correction or a duplicate.' },
  NEAR_DUPLICATE_INVOICE_FIELDS: { title: 'Near-matching invoice — review required', why: 'The supplier, amount, due date, and extracted fields closely resemble a previous request despite a different invoice number. Similarity is a review signal, not proof of fraud.', passNext: 'Compare both source documents, service periods, and payment history before recording a decision.' },
  PO_CUMULATIVE_EXCEEDED: { title: 'Purchase order would be overused', why: 'Other active requests already use part of this purchase-order authorization; the combined amount is too high.', passNext: 'Correct or cancel an overlapping request, or attach an approved PO amendment and re-evaluate.' },
  DELIVERY_CUMULATIVE_EXCEEDED: { title: 'Delivery proof would be overused', why: 'The combined requests exceed the value accepted in this delivery record.', passNext: 'Provide new delivery proof or correct the overlapping invoice before payment.' },
  VENDOR_APPROVED: { title: 'Supplier is approved', why: 'The supplier is active and matches the identity named on the invoice.', passNext: 'Verify the payout wallet.' },
  VENDOR_ID_MISMATCH: { title: 'Supplier identity does not match', why: 'The invoice and selected supplier record point to different identities.', passNext: 'Attach the correct supplier record or correct the invoice.' },
  VENDOR_INACTIVE: { title: 'Supplier is inactive', why: 'Inactive suppliers cannot receive controlled payments.', passNext: 'Finance must re-verify and reactivate the supplier.' },
  VENDOR_WALLET_VERIFIED: { title: 'Payout wallet verified', why: 'The invoice destination matches the independently verified supplier wallet.', passNext: 'Continue to wallet-change and settlement-route checks.' },
  VENDOR_WALLET_CHANGED: { title: 'Payout wallet changed', why: 'A new destination could indicate fraud or an unapproved supplier update.', passNext: 'Verify the change outside the invoice and update the supplier profile.' },
  WALLET_CHANGE_COOLDOWN_CLEAR: { title: 'No recent wallet change', why: 'The supplier payout address is not inside a security cooldown.', passNext: 'Continue to the allowed settlement route.' },
  WALLET_CHANGE_COOLDOWN_COMPLETE: { title: 'Wallet security cooldown complete', why: 'The required waiting period after a verified wallet change has elapsed.', passNext: 'Continue to the allowed settlement route.' },
  WALLET_CHANGE_COOLDOWN_ACTIVE: { title: 'Wallet change is cooling down', why: 'Recently changed payout addresses require time for independent review.', passNext: 'Wait until the displayed release date, then re-evaluate.' },
  SETTLEMENT_ROUTE_ALLOWED: { title: 'USDC route is allowed', why: 'The request uses the asset and Arc network permitted by company policy.', passNext: 'Continue to purchase-order matching.' },
  SETTLEMENT_ROUTE_NOT_ALLOWED: { title: 'Settlement route is not allowed', why: 'The requested asset or network falls outside the active finance policy.', passNext: 'Use the permitted asset and network or change policy through finance.' },
  PO_MATCHED: { title: 'Purchase order matches', why: 'Supplier, currency, and amount are covered by an authorized purchase order.', passNext: 'Continue to delivery evidence.' },
  MISSING_PURCHASE_ORDER: { title: 'Purchase order missing', why: 'Finance cannot prove the company authorized this spend.', passNext: 'Attach the authorized purchase order.' },
  INVOICE_EXCEEDS_PO: { title: 'Invoice exceeds purchase order', why: 'The requested amount is higher than the authorized purchasing limit.', passNext: 'Amend the purchase order or submit a corrected invoice.' },
  DELIVERY_MATCHED: { title: 'Delivery evidence matches', why: 'The accepted goods or services cover the amount requested for payment.', passNext: 'Continue to authority and treasury limits.' },
  MISSING_DELIVERY_EVIDENCE: { title: 'Delivery evidence missing', why: 'Finance cannot verify that the billed goods or services were accepted.', passNext: 'Attach delivery or acceptance evidence.' },
  DELIVERY_VALUE_INSUFFICIENT: { title: 'Delivered value is too low', why: 'The accepted value does not cover the amount requested on the invoice.', passNext: 'Add delivery proof or reduce the requested amount.' },
  AUTONOMY_LIMIT_OK: { title: 'Within automated authority', why: 'The amount is below both supplier and company autonomous-payment limits.', passNext: 'Continue to daily spend and reserve checks.' },
  AUTONOMY_LIMIT_EXCEEDED: { title: 'Independent approval required', why: 'The amount exceeds what the policy engine may authorize on its own.', passNext: 'Send the sealed decision packet to a finance approver.' },
  DAILY_LIMIT_OK: { title: 'Daily spend limit remains safe', why: 'Paying this request would keep today’s total inside company policy.', passNext: 'Verify the minimum treasury reserve.' },
  DAILY_LIMIT_EXCEEDED: { title: 'Daily spend limit exceeded', why: 'Paying now would exceed the company’s daily payment ceiling.', passNext: 'Schedule later or obtain a governed policy change.' },
  DAILY_AUTONOMY_LIMIT_OK: { title: 'Daily no-touch allowance remains available', why: 'Today’s automatic payments plus this request stay inside the finance-configured no-touch allowance.', passNext: 'Verify the hard treasury limits.' },
  DAILY_AUTONOMY_LIMIT_EXCEEDED: { title: 'Daily no-touch allowance reached', why: 'The evidence can still be valid, but today’s automatic-payment allowance would be exceeded.', passNext: 'Route this request to a finance approver.' },
  AUTONOMOUS_PAYMENTS_ENABLED: { title: 'No-touch settlement authorized', why: 'Finance explicitly enabled automatic payment under the active limits.', passNext: 'Verify the configured single-payment and daily allowances.' },
  AUTONOMOUS_PAYMENTS_DISABLED: { title: 'Finance final review required', why: 'No-touch settlement is currently disabled. AI may verify the evidence, but it cannot release funds.', passNext: 'Send the verified request to a finance approver.' },
  MINIMUM_RESERVE_OK: { title: 'Treasury reserve preserved', why: 'The projected balance remains above the required cash reserve.', passNext: 'Check payment timing and prepare the finance action.' },
  MINIMUM_RESERVE_BREACH: { title: 'Treasury reserve would be breached', why: 'This payment would leave less cash than company policy permits.', passNext: 'Fund treasury, schedule later, or approve a policy change.' },
  PAYMENT_TIMING_IMMEDIATE: { title: 'Payment timing is allowed', why: 'The active policy permits payment as soon as all other controls pass.', passNext: 'Create a payment intent after finance authorization.' },
  PAYMENT_SCHEDULED_FOR_DUE_DATE: { title: 'Payment scheduled for due date', why: 'Company policy preserves cash until the configured payment window.', passNext: 'Keep the request queued until the scheduled date.' },
  PAYMENT_DUE: { title: 'Invoice is inside the payment window', why: 'The due date is now close enough for settlement under policy.', passNext: 'Create a payment intent after finance authorization.' },
};

function rulePresentation(rule: RuleResult) {
  const fallbackTitle = rule.code.toLowerCase().replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
  const configured = RULE_PRESENTATION[rule.code];
  return {
    title: configured?.title ?? fallbackTitle,
    why: configured?.why ?? rule.message,
    next: rule.remediation ?? configured?.passNext ?? (rule.disposition === 'PASS' ? 'Continue to the next control.' : 'Finance must resolve this exception.'),
  };
}

function settlementStatusLabel(item: OperationsInvoice) {
  if (item.settled_amount_usdc) return `Paid ${formatMoney(item.settled_amount_usdc)} ${item.currency} — receipt ready`;
  if (item.settlement_status === 'AUTHORIZED_NOT_SENT') return 'Approved — waiting for finance to send';
  if (item.settlement_retryable) return 'Transfer needs retry';
  return 'No funds sent yet';
}

const LIVE_STEPS = [
  ['01', 'Verify vendor', 'Bind a signed Arc wallet proof to a tenant vendor.'],
  ['02', 'Lock controls', 'Activate an immutable policy and treasury snapshot.'],
  ['03', 'Ingest evidence', 'Hash and upload invoice, PO, and delivery JSON.'],
  ['04', 'Evaluate', 'Compare the agent opinion with deterministic controls.'],
] as const;

const TEAM_ROLES = [
  ['Admin', 'Defines tenant membership and governance configuration; cannot silently replace another role.'],
  ['Finance operator', 'Ingests evidence, plans agent work, and requests settlement or policy exceptions.'],
  ['Approver', 'Independently resolves bound exceptions and authorizes eligible settlement actions.'],
  ['Auditor', 'Reads decisions, receipts, proof packets, and the tenant-wide hash-linked ledger.'],
] as const;

type PortalRole = 'requester' | 'finance';
const isDemoScenarioInvoice = (item: OperationsInvoice) => /^invoice_(?:clean_payment|duplicate_invoice|wallet_change|po_overage|missing_delivery|large_invoice|scheduled_payment|provider_recovery)_/.test(item.id);
type WorkspaceView =
  | 'overview'
  | 'activity'
  | 'login-user'
  | 'login-finance'
  | 'portal'
  | 'submit'
  | 'finance'
  | 'payables'
  | 'review'
  | 'evidence'
  | 'approvals'
  | 'automation'
  | 'vendors'
  | 'policies'
  | 'audit';

const REQUESTER_VIEWS: WorkspaceView[] = ['portal', 'submit'];
const FINANCE_VIEWS: WorkspaceView[] = ['finance', 'payables', 'review', 'evidence', 'approvals', 'automation', 'vendors', 'policies', 'audit'];
const PORTAL_ROLE_KEY = 'tallyguard-demo-role';

const readWorkspaceView = (): WorkspaceView => {
  const candidate = window.location.hash.replace('#', '');
  return ['overview', 'activity', 'login-user', 'login-finance', 'portal', 'submit', 'finance', 'payables', 'review', 'evidence', 'approvals', 'automation', 'vendors', 'policies', 'audit'].includes(candidate)
    ? candidate as WorkspaceView
    : 'overview';
};

const readPortalRole = (): PortalRole | null => {
  const role = window.sessionStorage.getItem(PORTAL_ROLE_KEY);
  return role === 'requester' || role === 'finance' ? role : null;
};

const WORKSPACE_VIEWS = [
  { id: 'overview', label: 'Overview', helper: 'Command center', icon: Dashboard },
  { id: 'payables', label: 'Payables', helper: 'Evidence & settlement', icon: ListChecked },
  { id: 'evidence', label: 'Evidence', helper: 'Ingest & evaluate', icon: Document },
  { id: 'approvals', label: 'Approvals', helper: 'Independent review', icon: CheckmarkFilled },
  { id: 'automation', label: 'Agent runs', helper: 'Plan & execute', icon: Rule },
  { id: 'vendors', label: 'Vendors', helper: 'Wallet trust', icon: UserProfile },
  { id: 'policies', label: 'Policies', helper: 'Controls & approvals', icon: SettingsAdjust },
  { id: 'audit', label: 'Audit', helper: 'Proof & reliability', icon: DocumentSecurity },
] as const;

function WorkspaceNavigation({
  active,
  disabled,
  badges,
  onChange,
}: {
  active: WorkspaceView;
  disabled: boolean;
  badges: Partial<Record<WorkspaceView, string>>;
  onChange: (view: WorkspaceView) => void;
}) {
  return (
    <aside className="workspace-navigation">
      <div className="workspace-navigation__brand" aria-hidden="true">
        <span>TG</span>
        <div><strong>Finance OS</strong><small>Arc settlement</small></div>
      </div>
      <nav aria-label="Finance workspace">
        {WORKSPACE_VIEWS.map((view) => {
          const Icon = view.icon;
          const isActive = active === view.id;
          return (
            <button
              key={view.id}
              type="button"
              className={isActive ? 'workspace-navigation__item is-active' : 'workspace-navigation__item'}
              aria-current={isActive ? 'page' : undefined}
              disabled={disabled}
              onClick={() => onChange(view.id)}
            >
              <Icon size={18} aria-hidden="true" />
              <span><strong>{view.label}</strong><small>{view.helper}</small></span>
              {badges[view.id] ? <em>{badges[view.id]}</em> : null}
            </button>
          );
        })}
      </nav>
      <div className="workspace-navigation__boundary">
        <span className="live-dot" aria-hidden="true" />
        <div><strong>Controls online</strong><small>Tenant isolated</small></div>
      </div>
    </aside>
  );
}

function WorkspacePageHeader({
  index,
  title,
  description,
  meta,
}: {
  index: string;
  title: string;
  description: string;
  meta: string;
}) {
  return (
    <header className="workspace-page-header">
      <div>
        <span className="eyebrow">{index} / TallyGuard workspace</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="workspace-page-header__meta">
        <span>{meta}</span>
        <small>Evidence-bound · Role-separated</small>
      </div>
    </header>
  );
}

const LANDING_FLOW = [
  {
    label: 'Read source evidence',
    detail: 'Invoice, purchase order, delivery proof',
    meta: '3 sources',
    icon: Document,
  },
  {
    label: 'Match evidence',
    detail: 'Invoice · PO · delivery receipt',
    meta: 'Matched',
    icon: ListChecked,
  },
  {
    label: 'Apply policy controls',
    detail: 'Deterministic rules and approvals',
    meta: 'Authorized',
    icon: Locked,
  },
  {
    label: 'Settle on Arc',
    detail: '2,480.00 USDC → Atlas Compute',
    meta: 'Arc Testnet',
    icon: Wallet,
  },
  {
    label: 'Create audit receipt',
    detail: 'Immutable record with decision hash',
    meta: 'Pending',
    icon: DocumentSecurity,
  },
] as const;

function LandingFlowDemo({ ready, network, onOpen }: { ready: boolean; network: string; onOpen: () => void }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [activeStep, setActiveStep] = useState(0);
  const [isVisible, setIsVisible] = useState(true);

  useEffect(() => {
    const root = rootRef.current;
    if (!root || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(([entry]) => setIsVisible(entry.isIntersecting), { threshold: 0.25 });
    observer.observe(root);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reducedMotion) {
      setActiveStep(LANDING_FLOW.length - 1);
      return;
    }
    if (!isVisible) return;
    const timer = window.setInterval(() => {
      setActiveStep((step) => (step + 1) % LANDING_FLOW.length);
    }, 1750);
    return () => window.clearInterval(timer);
  }, [isVisible]);

  return (
    <div className="landing-demo" ref={rootRef} aria-label="Animated TallyGuard payment control walkthrough">
      <div className="landing-demo__topbar">
        <span className="landing-demo__tenant">TallyGuard&nbsp;&nbsp;/&nbsp;&nbsp;AP-2048</span>
        <span className={ready ? 'landing-demo__network is-ready' : 'landing-demo__network'}>
          <i aria-hidden="true" /> {network}
        </span>
      </div>
      <div className="landing-demo__body">
        <div className="landing-demo__sources" aria-label="Source evidence">
          <article><Document size={20} /><strong>Invoice</strong><span>INV-2048</span><small>From Atlas Compute</small></article>
          <article><Document size={20} /><strong>Purchase Order</strong><span>PO-7781</span><small>Approved</small></article>
          <article><DocumentSecurity size={20} /><strong>Delivery Proof</strong><span>Receipt #DLV-9321</span><small>Delivered Sep 20, 2026</small></article>
        </div>
        <div className="landing-demo__connector" aria-hidden="true"><span /></div>
        <div className="landing-demo__process">
          {LANDING_FLOW.map((step, index) => {
            const Icon = step.icon;
            const state = index < activeStep ? 'is-complete' : index === activeStep ? 'is-active' : 'is-pending';
            return (
              <article className={state} key={step.label}>
                <div className="landing-demo__step"><CheckmarkFilled size={15} aria-hidden="true" /><span>{index + 1}</span></div>
                <div><strong>{step.label}</strong><small>{step.detail}</small></div>
                <span>{index <= activeStep ? step.meta : 'Waiting'}</span>
                <Icon className="landing-demo__row-icon" size={16} aria-hidden="true" />
              </article>
            );
          })}
          <div className="landing-demo__summary">
            <span><small>Invoice</small><strong>INV-2048</strong></span>
            <span><small>Vendor</small><strong>Atlas Compute</strong></span>
            <span><small>Amount</small><strong>2,480.00 USDC</strong></span>
            <span><small>Due date</small><strong>Sep 24, 2026</strong></span>
          </div>
          <div className="landing-demo__footer">
            <CheckmarkFilled size={18} aria-hidden="true" />
            <span><strong>Payment queued for settlement on Arc Testnet</strong><small>Evidence matched · Policy authorized · Awaiting finalization</small></span>
            <button type="button" onClick={onOpen}>View details <ArrowRight size={14} /></button>
          </div>
        </div>
      </div>
    </div>
  );
}

function LandingHeader({
  busy,
  onNavigate,
  onTeam,
}: {
  busy: boolean;
  onNavigate: (view: WorkspaceView) => void;
  onTeam: () => void;
}) {
  return (
    <header className="landing-header">
      <div className="landing-header__inner">
        <button type="button" className="landing-brand" onClick={() => onNavigate('overview')} aria-label="TallyGuard home">
          <span aria-hidden="true">T</span><strong>TallyGuard</strong>
        </button>
        <nav aria-label="Product navigation">
          <button type="button" onClick={() => onNavigate('login-user')}>Submit & track</button>
          <button type="button" onClick={() => onNavigate('login-finance')}>Finance controls</button>
          <button type="button" onClick={() => onNavigate('activity')}>Arc activity</button>
          <a href="/api/openapi.json" target="_blank" rel="noreferrer">Developers</a>
          <button type="button" onClick={onTeam}>Roles</button>
        </nav>
        <div className="landing-header__actions">
          <button type="button" className="landing-header__team" onClick={() => onNavigate('login-user')}>User portal</button>
          <button type="button" className="landing-header__workspace" disabled={busy} onClick={() => onNavigate('login-finance')}>
            Finance sign in <ArrowRight size={16} aria-hidden="true" />
          </button>
        </div>
      </div>
    </header>
  );
}

function LandingPage({
  ready,
  network,
  operations,
  reliability,
  activity,
  activityError,
  busy,
  onNavigate,
}: {
  ready: boolean;
  network: string;
  operations: OperationsOverview | null;
  reliability: ReliabilityEvidence | null;
  activity: ArcActivityResponse | null;
  activityError: string | null;
  busy: boolean;
  onNavigate: (view: WorkspaceView) => void;
}) {
  const workflows = reliability?.report.summary.successful_workflows ?? 10_000;
  const tenants = reliability?.report.configuration.organizations ?? 100;
  const duplicates = reliability?.report.summary.duplicate_payment_count ?? 0;
  return (
    <main className="landing-page">
      <section className="landing-hero" aria-labelledby="landing-title">
        <div className="landing-hero__copy">
          <span className="landing-kicker"><i aria-hidden="true" /> For finance teams using autonomous agents</span>
          <h1 id="landing-title">Invoices in.<br /><span>Controlled payments out.</span></h1>
          <p>
            TallyGuard takes an AP operator from supplier invoice to evidence checks, independent approval,
            Arc USDC settlement, and an audit-ready receipt—without giving AI unchecked payment authority.
          </p>
          <div className="landing-hero__actions">
            <button type="button" className="landing-primary" disabled={busy} onClick={() => onNavigate('login-user')}>
              Open user portal <ArrowRight size={18} aria-hidden="true" />
            </button>
            <button type="button" className="landing-secondary" onClick={() => onNavigate('login-finance')}>
              Finance sign in
            </button>
          </div>
          <div className="landing-hero__trust">
            <CheckmarkFilled size={16} aria-hidden="true" /><span>AI reads the documents</span><i />
            <CheckmarkFilled size={16} aria-hidden="true" /><span>Policy controls authority</span><i />
            <CheckmarkFilled size={16} aria-hidden="true" /><span>Humans resolve exceptions</span>
          </div>
        </div>
        <div className="landing-hero__product">
          <LandingFlowDemo ready={ready} network={network} onOpen={() => onNavigate('login-finance')} />
        </div>
      </section>

      <section className="landing-proof" aria-label="Verified product evidence">
        <div className="landing-proof__lead"><span>PROVEN UNDER LOAD</span><strong>Engineering evidence, not a vanity counter.</strong></div>
        <div><strong>{workflows.toLocaleString()}</strong><span>workflows stress-tested</span></div>
        <div><strong>{duplicates}</strong><span>duplicate payments</span></div>
        <div><strong>{tenants}</strong><span>isolated tenants</span></div>
        <div><strong>USDC</strong><span>Arc settlement rail</span></div>
      </section>

      <ArcActivityPreview activity={activity} error={activityError} onOpen={() => onNavigate('activity')} />

      <section className="landing-roles" aria-labelledby="landing-roles-title">
        <header>
          <span>ONE PAYMENT · SEPARATE RESPONSIBILITIES</span>
          <h2 id="landing-roles-title">Built for a finance team, not a single all-powerful account.</h2>
          <p>The daily path stays simple. Advanced controls remain available without taking over the main product experience.</p>
        </header>
        <div>
          <article><strong>01</strong><h3>Requester</h3><p>Submits an invoice or payment request, then follows every handoff and sees exactly why finance rejected it.</p><button type="button" onClick={() => onNavigate('login-user')}>Open user portal <ArrowRight size={15} /></button></article>
          <article><strong>02</strong><h3>Finance team</h3><p>Reviews evidence and policy exceptions, approves or rejects requests, and authorizes Arc settlement.</p><button type="button" onClick={() => onNavigate('login-finance')}>Open finance backend <ArrowRight size={15} /></button></article>
          <article><strong>03</strong><h3>Auditor & admin</h3><p>Defines policy, verifies vendor wallets, and replays the evidence and settlement record after the fact.</p><button type="button" onClick={() => onNavigate('login-finance')}>Inspect finance controls <ArrowRight size={15} /></button></article>
        </div>
      </section>

    </main>
  );
}

function PortalLoginPage({
  intent,
  busy,
  onContinue,
  onBack,
}: {
  intent: PortalRole;
  busy: boolean;
  onContinue: (role: PortalRole) => void;
  onBack: () => void;
}) {
  const requester = intent === 'requester';
  return (
    <main className="portal-login">
      <header className="portal-login__header">
        <button type="button" className="premium-brand" onClick={onBack}><span>T</span><strong>TallyGuard</strong></button>
        <button type="button" onClick={onBack}>Back to product</button>
      </header>
      <div className="portal-login__layout">
        <section className="portal-login__story">
          <span className="landing-kicker"><i /> ROLE-SEPARATED ACCESS</span>
          <h1>{requester ? 'Submit once.\nTrack every handoff.' : 'Control every payment.\nProve every decision.'}</h1>
          <p>{requester
            ? 'The user portal keeps request submission and status tracking simple. Approval authority and settlement controls stay out of reach.'
            : 'The finance backend combines evidence review, independent approval, policy controls, Arc settlement, and audit proof.'}</p>
          <ol>
            {(requester ? [
              ['01', 'Submit evidence', 'Upload an invoice, purchase order, and delivery proof.'],
              ['02', 'Follow progress', 'See AI checks, finance review, approval, settlement, and receipt.'],
              ['03', 'Resolve exceptions', 'Rejected requests show a clear reason and next step.'],
            ] : [
              ['01', 'Review evidence', 'Inspect extracted invoice fields and their source documents.'],
              ['02', 'Apply authority', 'Approve or reject exceptions with a recorded decision note.'],
              ['03', 'Settle and audit', 'Move approved USDC on Arc and preserve a replayable receipt.'],
            ]).map(([index, title, detail]) => <li key={index}><span>{index}</span><div><strong>{title}</strong><small>{detail}</small></div></li>)}
          </ol>
        </section>
        <section className="portal-login__panel" aria-labelledby="portal-login-title">
          <span className={requester ? 'portal-login__role is-requester' : 'portal-login__role is-finance'}>{requester ? <UserProfile size={22} /> : <Locked size={22} />}</span>
          <span className="portal-login__eyebrow">{requester ? 'USER PORTAL' : 'FINANCE BACKEND'}</span>
          <h2 id="portal-login-title">{requester ? 'Continue as requester' : 'Continue as finance controller'}</h2>
          <p>{requester ? 'For employees, vendors, and operators requesting a controlled payment.' : 'For finance, approvers, treasury, and audit teams.'}</p>
          <div className="portal-login__identity"><span>{requester ? 'MC' : 'JS'}</span><div><strong>{requester ? 'Morgan Chen' : 'Jordan Singh'}</strong><small>{requester ? 'Operations requester · Atlas Compute' : 'Finance controller · TallyGuard Labs'}</small></div><CheckmarkFilled size={18} /></div>
          <button type="button" className="portal-login__continue" disabled={busy} onClick={() => onContinue(intent)}>{busy ? 'Preparing secure workspace…' : `Enter ${requester ? 'user portal' : 'finance backend'}`} <ArrowRight size={17} /></button>
          <small className="portal-login__note"><Locked size={13} /> Hackathon demo access. Production deployments connect SSO and organization roles.</small>
          <button type="button" className="portal-login__switch" onClick={() => onContinue(requester ? 'finance' : 'requester')}>I need the {requester ? 'finance backend' : 'user portal'} instead</button>
        </section>
      </div>
    </main>
  );
}

function RequesterTopbar({
  active,
  onNavigate,
  onSignOut,
}: {
  active: 'portal' | 'submit';
  onNavigate: (view: WorkspaceView) => void;
  onSignOut: () => void;
}) {
  return (
    <header className="requester-topbar">
      <button type="button" className="premium-brand" onClick={() => onNavigate('portal')}><span>T</span><strong>TallyGuard</strong></button>
      <nav aria-label="User portal">
        <button type="button" className={active === 'portal' ? 'is-active' : ''} onClick={() => onNavigate('portal')}>Requests</button>
        <button type="button" className={active === 'submit' ? 'is-active' : ''} onClick={() => onNavigate('submit')}>New request</button>
      </nav>
      <div className="requester-topbar__account"><span>MC</span><div><strong>Morgan Chen</strong><small>Operations requester</small></div><button type="button" onClick={onSignOut}>Sign out</button></div>
    </header>
  );
}

function requesterStatus(item: OperationsInvoice, approval: Approval | null | undefined) {
  const normalized = item.status.toUpperCase();
  if (approval?.status === 'REJECTED' || item.decision_action === 'REJECT') return { key: 'rejected', label: 'Needs changes', helper: 'Finance returned this request' };
  if (normalized.includes('RECONCIL') || normalized.includes('SETTLED') || normalized.includes('PAID')) return { key: 'paid', label: item.settlement_provider === 'arc-simulator' ? 'Demo complete · receipt ready' : 'Paid · receipt ready', helper: item.settlement_provider === 'arc-simulator' ? 'No funds moved in this demo' : 'Arc settlement confirmed' };
  if (approval?.status === 'APPROVED') return { key: 'approved', label: 'Approved by finance', helper: 'Waiting for finance settlement' };
  if (approval?.status === 'PENDING') return { key: 'review', label: 'Independent approval', helper: 'Waiting for an approver' };
  if (item.decision_action === 'ESCALATE') return { key: 'finance-review', label: 'In finance review', helper: 'Finance must request approval' };
  if (item.decision_action === 'HOLD') return { key: 'hold', label: 'On hold', helper: 'More evidence is required' };
  if (item.decision_action === 'SCHEDULE' || normalized.includes('SCHEDULE')) return { key: 'scheduled', label: 'Scheduled', helper: `Due ${item.due_date}` };
  if (item.decision_action === 'PAY') return { key: 'approved', label: 'Policy checks passed', helper: 'Waiting for finance settlement' };
  return { key: 'submitted', label: 'Submitted', helper: 'Evidence is entering review' };
}

function requesterDecisionCopy(item: OperationsInvoice, approval: Approval | null | undefined) {
  const status = requesterStatus(item, approval);
  if (status.key === 'paid') return {
    tone: 'paid',
    title: `${item.settlement_provider === 'arc-simulator' ? 'Demo payment completed' : 'Paid'} ${formatMoney(item.settled_amount_usdc ?? item.amount)} ${item.currency}`,
    body: item.settlement_provider === 'arc-simulator' ? 'The demo payment is complete. The simulation receipt records the full workflow; no real funds moved.' : item.settlement_transaction_hash ? `Arc receipt ${shorten(item.settlement_transaction_hash, 12, 10)} confirms the transfer.` : 'The transfer is complete and the finance receipt is available.',
    next: 'No action is required. Keep the receipt for your records.',
  };
  if (status.key === 'rejected') return {
    tone: 'rejected',
    title: approval?.status === 'REJECTED' ? 'Returned by finance — no funds were sent' : 'Stopped by payment controls — no funds were sent',
    body: approval?.resolution_note || (item.decision_findings?.length ? item.decision_findings.map((finding) => finding.message).join(' ') : 'This request did not pass the current payment controls.'),
    next: approval?.status === 'REJECTED'
      ? 'Follow the finance note and submit a corrected new request. The original stays sealed.'
      : item.decision_remediation?.[0] ?? item.decision_findings?.find((finding) => finding.remediation)?.remediation ?? 'Correct the evidence and submit a new request.',
  };
  if (status.key === 'hold') return {
    tone: 'hold',
    title: 'Payment is on hold — no funds were sent',
    body: item.decision_findings?.length ? item.decision_findings.map((finding) => finding.message).join(' ') : 'One or more evidence or treasury controls require attention before finance can proceed.',
    next: item.decision_remediation?.[0] ?? 'Finance will identify the missing evidence or policy exception.',
  };
  if (status.key === 'review') return {
    tone: 'review',
    title: 'Waiting for an independent finance decision',
    body: `${formatMoney(item.amount)} ${item.currency} is requested. The evidence is sealed, but no transfer has been authorized.`,
    next: 'An approver will either approve the request or return it with a reason.',
  };
  if (status.key === 'finance-review') return {
    tone: 'review',
    title: 'Waiting for finance review',
    body: `${formatMoney(item.amount)} ${item.currency} is above the automatic-payment boundary. The evidence is sealed, but finance has not requested independent approval yet.`,
    next: 'Finance will review the evidence and either request approval or return the request with a reason.',
  };
  if (status.key === 'approved' || status.key === 'scheduled') return {
    tone: 'approved',
    title: status.key === 'scheduled'
      ? 'Approved and scheduled'
      : approval?.status === 'APPROVED'
        ? 'Finance approved — awaiting settlement'
        : 'Policy checks passed — awaiting settlement',
    body: approval?.status === 'APPROVED'
      ? `Finance authorized ${formatMoney(item.amount)} ${item.currency}. ${settlementStatusLabel(item)}.`
      : `The request passed the active payment policy for ${formatMoney(item.amount)} ${item.currency}. ${settlementStatusLabel(item)}.`,
    next: status.key === 'scheduled' ? `Finance will release it on ${item.scheduled_for ?? item.due_date}.` : 'Finance must create and confirm the Arc payment before this becomes Paid.',
  };
  return {
    tone: 'submitted',
    title: 'Request received — no funds were sent',
    body: `${formatMoney(item.amount)} ${item.currency} is being checked against the uploaded evidence.`,
    next: 'The next visible update will show the policy result and finance handoff.',
  };
}

function RequesterProgress({ item, approval }: { item: OperationsInvoice; approval: Approval | null | undefined }) {
  const status = requesterStatus(item, approval);
  const stopped = status.key === 'rejected' || status.key === 'hold';
  const paid = status.key === 'paid';
  const approved = status.key === 'approved' || status.key === 'scheduled' || paid;
  const decisionComplete = !stopped && (item.decision_action !== 'ESCALATE'
    || approval?.status === 'APPROVED'
    || approval?.status === 'REJECTED');
  const steps = [
    ['Submitted', true],
    ['Evidence checked', Boolean(item.decision_id)],
    [approval ? 'Independent approval' : 'Finance review', decisionComplete],
    [item.settlement_provider === 'arc-simulator' ? 'Demo settlement' : 'Arc settlement', paid],
  ] as const;
  return <div className={`requester-progress ${stopped ? 'is-stopped' : ''}`}>
    {steps.map(([label, complete], index) => <div className={complete ? 'is-complete' : index === (item.decision_id ? approved ? 3 : 2 : 1) ? 'is-current' : ''} key={label}><span>{complete ? '✓' : index + 1}</span><small>{label}</small></div>)}
  </div>;
}

function isSettlementReady(item: OperationsInvoice) {
  if (item.settlement_status === 'CONFIRMED') return false;
  return Boolean(item.decision_id) && (
    item.settlement_retryable
    || item.decision_action === 'PAY'
    || (item.decision_action === 'ESCALATE' && item.approval_status === 'APPROVED')
  );
}

function needsFinanceAttention(item: OperationsInvoice) {
  if (item.settlement_status === 'CONFIRMED') return false;
  if (item.decision_action === 'ESCALATE') return item.approval_status !== 'APPROVED';
  return item.decision_action === 'HOLD' || item.decision_action === 'REJECT';
}

function financeStatusLabel(item: OperationsInvoice) {
  if (item.settlement_status === 'CONFIRMED') return 'Paid · receipt ready';
  if (item.settlement_retryable) return 'Retry settlement';
  if (item.decision_action === 'ESCALATE') {
    if (item.approval_status === 'APPROVED') return 'Approved · ready to settle';
    if (item.approval_status === 'REJECTED') return 'Returned by approver';
    if (item.approval_status === 'PENDING') return 'Awaiting approver';
    return 'Finance review required';
  }
  return item.decision_action ? ACTION_LABEL[item.decision_action] : item.status.replaceAll('_', ' ');
}

function financeDecisionLabel(item: OperationsInvoice) {
  if (item.decision_action === 'ESCALATE' && item.approval_status === 'APPROVED') return 'Approved by finance';
  if (item.decision_action === 'ESCALATE' && item.approval_status === 'REJECTED') return 'Rejected by approver';
  return item.decision_action ? ACTION_LABEL[item.decision_action] : 'Awaiting evaluation';
}

function RequesterPortalPage({
  operations,
  approvals,
  busy,
  settlementMode,
  onNavigate,
  onStartCorrection,
  onSignOut,
}: {
  operations: OperationsOverview | null;
  approvals: Record<string, Approval | null>;
  busy: boolean;
  settlementMode: BootstrapData['readiness']['settlement_mode'];
  onNavigate: (view: WorkspaceView) => void;
  onStartCorrection: (item: OperationsInvoice) => void;
  onSignOut: () => void;
}) {
  const allRequests = operations?.recent_requests ?? operations?.work_queue ?? [];
  const submittedQueue = allRequests.filter((item) => !isDemoScenarioInvoice(item));
  const exampleQueue = settlementMode === 'circle-live' ? [] : allRequests.filter(isDemoScenarioInvoice);
  const [viewingExamples, setViewingExamples] = useState<boolean | null>(null);
  const showExamples = exampleQueue.length > 0 && (viewingExamples ?? submittedQueue.length === 0);
  const queue = showExamples ? exampleQueue : submittedQueue;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = queue.find((item) => item.id === selectedId) ?? queue[0] ?? null;
  const selectedApproval = selected?.decision_id ? approvals[selected.decision_id] : null;
  const selectedDecision = selected ? requesterDecisionCopy(selected, selectedApproval) : null;
  const activeCount = submittedQueue.filter((item) => {
    const linked = item.decision_id ? approvals[item.decision_id] : null;
    return !['rejected', 'paid'].includes(requesterStatus(item, linked).key);
  }).length;
  const returnedCount = submittedQueue.filter((item) => {
    const linked = item.decision_id ? approvals[item.decision_id] : null;
    return requesterStatus(item, linked).key === 'rejected';
  }).length;
  return (
    <div className="requester-app">
      <RequesterTopbar active="portal" onNavigate={onNavigate} onSignOut={onSignOut} />
      <main className="requester-dashboard">
        <header className="requester-heading"><div><span>USER PORTAL</span><h1>{showExamples ? 'Explore payment journeys' : 'Your payment requests'}</h1><p>{showExamples ? 'Follow the same four sample request IDs shown in the finance backend, then submit your own documents to run the complete handoff.' : 'Submit supporting documents, follow finance review, and see every decision without exposing treasury controls.'}</p></div><button type="button" disabled={busy} onClick={() => onNavigate('submit')}><Document size={17} /> New payment request</button></header>
        <section className="requester-summary" aria-label="Request summary">
          {exampleQueue.length > 0 ? <article><small>Linked sample journeys</small><strong>{exampleQueue.length}</strong><span>Same records in both portals</span></article> : <article><small>Active requests</small><strong>{activeCount}</strong><span>Moving through controls</span></article>}
          <article><small>{exampleQueue.length > 0 ? 'Submitted by you' : 'Returned for changes'}</small><strong>{exampleQueue.length > 0 ? submittedQueue.length : returnedCount}</strong><span>{exampleQueue.length > 0 ? 'Separate from the sample journeys' : 'Reason and next step included'}</span></article>
          <article><small>{exampleQueue.length > 0 ? 'Your active requests' : 'Total submitted'}</small><strong>{exampleQueue.length > 0 ? activeCount : submittedQueue.length}</strong><span>{settlementMode === 'circle-live' ? 'In this private workspace' : 'In this demo workspace'}</span></article>
        </section>
        {exampleQueue.length > 0 ? <nav className="requester-case-switch" aria-label="Request collections"><button type="button" className={showExamples ? 'is-active' : ''} aria-pressed={showExamples} onClick={() => { setViewingExamples(true); setSelectedId(null); }}>Explore sample journeys <span>{exampleQueue.length}</span></button><button type="button" className={!showExamples ? 'is-active' : ''} aria-pressed={!showExamples} onClick={() => { setViewingExamples(false); setSelectedId(null); }}>My submitted requests <span>{submittedQueue.length}</span></button><small>Sample journeys are preloaded examples, not requests submitted by your account.</small></nav> : null}
        <div className="requester-workspace">
          <section className="requester-list" aria-label={showExamples ? 'Sample payment journeys' : 'Your payment requests'}>
            <header><strong>{showExamples ? 'Sample request journeys' : 'Recent requests'}</strong><span>{queue.length} total</span></header>
            {queue.length ? queue.map((item) => {
              const linkedApproval = item.decision_id ? approvals[item.decision_id] : null;
              const status = requesterStatus(item, linkedApproval);
              return <button type="button" className={selected?.id === item.id ? 'is-selected' : ''} key={item.id} onClick={() => setSelectedId(item.id)}>
                <span className={`requester-status is-${status.key}`}><i />{status.label}</span>
                <strong>{item.invoice_number}</strong>
                <small>{showExamples ? 'Sample case · ' : ''}{item.vendor_id.replaceAll('-', ' ')} · due {item.due_date}</small>
                <b>{formatMoney(item.amount)} {item.currency}</b>
                <em>{status.helper}</em>
              </button>;
            }) : <div className="requester-empty"><Document size={28} /><strong>No requests yet</strong><p>Your submitted payment requests and their finance decisions will appear here.</p><button type="button" onClick={() => onNavigate('submit')}>Create first request</button></div>}
          </section>
          <section className="requester-detail">
            {selected ? <>
              <header><div><span>{showExamples ? 'PRELOADED SAMPLE · SHARED WITH FINANCE' : 'PAYMENT REQUEST'}</span><h2>{selected.invoice_number}</h2><p>{showExamples ? 'Example created ' : 'Submitted '}{new Date(selected.created_at).toLocaleString()}</p></div><span className={`requester-status is-${requesterStatus(selected, selectedApproval).key}`}><i />{requesterStatus(selected, selectedApproval).label}</span></header>
              <RequesterProgress item={selected} approval={selectedApproval} />
              {selectedDecision ? <section className={`requester-decision is-${selectedDecision.tone}`}><span>{selectedDecision.tone === 'rejected' ? '×' : selectedDecision.tone === 'hold' || selectedDecision.tone === 'review' ? '!' : '✓'}</span><div><strong>{selectedDecision.title}</strong><p>{selectedDecision.body}</p><small>Next: {selectedDecision.next}</small></div></section> : null}
              {selectedApproval?.status === 'APPROVED' ? <section className="requester-feedback is-approved"><CheckmarkFilled size={20} /><div><strong>Finance approval note</strong><p>{selectedApproval.resolution_note ?? 'Finance approved this request for settlement.'}</p><small>{selectedApproval.resolved_by_user_id ? `Approved by ${selectedApproval.resolved_by_user_id}` : 'Approved by an independent finance reviewer'}{selectedApproval.resolved_at ? ` · ${new Date(selectedApproval.resolved_at).toLocaleString()}` : ''}</small></div></section> : null}
              {(selectedApproval?.status === 'REJECTED' || selected.decision_action === 'REJECT') ? <section className="requester-feedback is-rejected"><WarningAltFilled size={20} /><div><strong>{selectedApproval?.status === 'REJECTED' ? 'Finance returned this request' : 'Automated controls stopped this request'}</strong><p>{selectedApproval?.resolution_note || (selected.decision_findings?.length ? selected.decision_findings.map((finding) => finding.message).join(' ') : 'This request did not pass the current payment policy.')}</p>{selectedApproval?.status === 'REJECTED' ? <p><strong>How to fix: </strong>Follow the finance note and submit a corrected new request. The original stays sealed.</p> : selected.decision_remediation?.length ? <p><strong>How to fix: </strong>{selected.decision_remediation.join(' ')}</p> : null}{selectedApproval ? <small>{selectedApproval.resolved_by_user_id ? `Returned by ${selectedApproval.resolved_by_user_id}` : 'Returned by an independent finance reviewer'}{selectedApproval.resolved_at ? ` · ${new Date(selectedApproval.resolved_at).toLocaleString()}` : ''}</small> : null}<button type="button" onClick={() => onStartCorrection(selected)}>Correct and resubmit <ArrowRight size={14} /></button><small>The original evidence stays sealed. Your correction creates a new request.</small></div></section> : null}
              <dl className="requester-facts"><div><dt>Vendor</dt><dd>{selected.vendor_id.replaceAll('-', ' ')}</dd></div><div><dt>Requested amount</dt><dd>{formatMoney(selected.amount)} {selected.currency}</dd></div><div><dt>Due date</dt><dd>{selected.due_date}</dd></div><div><dt>Current step</dt><dd>{requesterStatus(selected, selectedApproval).helper}</dd></div></dl>
              <section className="requester-proof"><div><DocumentSecurity size={20} /><span><strong>Evidence record</strong><small>{selected.decision_id ? 'Documents hashed and decision sealed' : 'Waiting for evidence evaluation'}</small></span></div><code>{selected.decision_id ? shorten(selected.decision_id, 14, 10) : shorten(selected.source_document_hash, 14, 10)}</code></section>
              {selected.settled_amount_usdc && selected.settlement_transaction_hash ? <section className="requester-receipt" aria-labelledby="requester-receipt-title">
                <header><div><DocumentSecurity size={20} /><span><small>FINAL RECEIPT</small><strong id="requester-receipt-title">{selected.settlement_provider === 'arc-simulator' ? 'Demo payment completed' : 'Arc payment confirmed'}</strong></span></div><em>{selected.settlement_provider === 'arc-simulator' ? 'SIMULATED' : 'CONFIRMED'}</em></header>
                <dl>
                  <div><dt>{selected.settlement_provider === 'arc-simulator' ? 'Amount simulated' : 'Amount paid'}</dt><dd>{formatMoney(selected.settled_amount_usdc)} {selected.currency}</dd></div>
                  <div><dt>Network</dt><dd>{selected.settlement_provider === 'arc-simulator' ? 'Arc Testnet simulation' : selected.settlement_network?.replaceAll('-', ' ') ?? 'Arc'}</dd></div>
                  <div><dt>Block</dt><dd>{selected.settlement_provider === 'arc-simulator' ? 'Simulated' : selected.settlement_block_number ? `#${selected.settlement_block_number.toLocaleString()}` : 'Confirmed'}</dd></div>
                  <div><dt>{selected.settlement_provider === 'arc-simulator' ? 'Recorded' : 'Confirmed'}</dt><dd>{selected.settlement_confirmed_at ? new Date(selected.settlement_confirmed_at).toLocaleString() : 'Receipt recorded'}</dd></div>
                </dl>
                <div className="requester-receipt__transaction"><span><small>{selected.settlement_provider === 'arc-simulator' ? 'Simulation receipt ID' : 'Transaction hash'}</small><code>{selected.settlement_transaction_hash}</code></span>{selected.settlement_provider !== 'arc-simulator' && selected.settlement_explorer_url ? <a href={selected.settlement_explorer_url} target="_blank" rel="noreferrer">View on Arc <Launch size={14} /></a> : null}</div>
                <p>{selected.settlement_provider === 'arc-simulator' ? 'Demo settlement receipt generated by the Arc simulator. No real funds moved in this workspace.' : `Settlement provider: ${selected.settlement_provider ?? 'Arc'}. This receipt records the confirmed transfer.`}</p>
              </section> : null}
              <aside className="requester-boundary"><Locked size={17} /><p><strong>What you can see</strong><span>Status, finance feedback, settlement progress, and the final receipt.</span></p><small>Approval and payment controls are only available in the finance backend.</small></aside>
            </> : <div className="requester-detail__empty"><Document size={32} /><h2>Your request journey starts here.</h2><p>Upload the invoice and supporting evidence. TallyGuard will show each handoff from submission to Arc receipt.</p><button type="button" onClick={() => onNavigate('submit')}>New payment request</button></div>}
          </section>
        </div>
      </main>
    </div>
  );
}

function RequesterSubmitPage({
  data,
  busy,
  error,
  correctionSource,
  correctionFeedback,
  onNavigate,
  onSignOut,
  onEvaluate,
}: {
  data: BootstrapData | null;
  busy: boolean;
  error: string | null;
  correctionSource: OperationsInvoice | null;
  correctionFeedback: string | null;
  onNavigate: (view: WorkspaceView) => void;
  onSignOut: () => void;
  onEvaluate: (
    files: EvidenceFileBundle,
    overrides: EvidenceFieldOverrides,
    onComplete?: (result: RunResult) => void,
  ) => void;
}) {
  const [submissionStarted, setSubmissionStarted] = useState(false);
  const [submissionStage, setSubmissionStage] = useState(1);
  const [submittedRun, setSubmittedRun] = useState<RunResult | null>(null);
  const [resultOpen, setResultOpen] = useState(false);
  const submitEvidence = (files: EvidenceFileBundle, overrides: EvidenceFieldOverrides) => {
    setSubmissionStarted(true);
    setSubmissionStage(2);
    setSubmittedRun(null);
    setResultOpen(true);
    onEvaluate(files, overrides, (result) => {
      setSubmittedRun(result);
      setSubmissionStage(4);
      setResultOpen(true);
    });
  };
  const submitSteps = [
    ['Add evidence', 'Choose or replace three source files'],
    ['Seal request', 'Persist verified bytes and extracted fields'],
    ['Policy result', 'Run deterministic finance controls'],
    ['Decision visible', 'See the outcome and next handoff'],
  ] as const;
  const action = submittedRun?.decision.final_action;
  const autoPaid = submittedRun?.autopay?.status === 'SETTLED';
  const autoSimulated = autoPaid && submittedRun?.autopay?.payment?.receipt.provider === 'arc-simulator';
  const blocked = action === 'REJECT' || action === 'HOLD';
  const issue = submittedRun?.decision.rules.find((rule) => rule.disposition === 'REJECT' || rule.disposition === 'HOLD');
  const resultTitle = autoPaid ? autoSimulated ? 'Demo payment completed' : 'Payment confirmed on Arc' : action === 'REJECT' ? 'Request returned for correction' : action === 'HOLD' ? 'Request paused for review' : action === 'ESCALATE' ? 'Finance review required' : action === 'SCHEDULE' ? 'Payment scheduled' : 'Checks passed — awaiting settlement';
  const nextAction = autoPaid ? autoSimulated ? 'Your simulation receipt is available in My requests. No funds moved.' : 'Your final receipt is available in My requests.' : action === 'REJECT' ? 'Correct the evidence and submit a new request. The original remains sealed for audit.' : action === 'HOLD' ? 'Finance will review the flagged evidence before any payment can be authorized.' : action === 'ESCALATE' ? 'Finance must review the evidence, then request independent approval or return the request with a reason. No funds have been sent.' : action === 'SCHEDULE' ? 'Finance will revalidate the request and release it on the scheduled date. No funds have been sent.' : 'Finance must complete settlement before this request is marked Paid.';
  return (
    <div className="requester-app">
      <RequesterTopbar active="submit" onNavigate={onNavigate} onSignOut={onSignOut} />
      <main className="requester-submit">
        <header><button type="button" onClick={() => onNavigate('portal')}>← My requests</button><span>{correctionSource ? `CORRECTING ${correctionSource.invoice_number}` : 'NEW PAYMENT REQUEST'}</span><h1>{correctionSource ? 'Submit corrected evidence' : 'Submit an invoice for payment'}</h1><p>{correctionSource ? 'The previous request remains sealed. Upload corrected documents, review the extracted fields, and submit a new request.' : 'Add the three records finance needs. AI extracts the fields, but you confirm them before anything is saved.'}</p></header>
        {correctionSource ? <aside className="requester-correction"><WarningAltFilled size={20} /><div><strong>Returned request · {correctionSource.invoice_number}</strong><p>{correctionFeedback || correctionSource.decision_findings?.map((finding) => finding.message).join(' ') || 'Review the source evidence and the finance feedback before retrying.'}</p><small>Start with sample documents or upload your own. Confirm every corrected field before sealing.</small></div></aside> : null}
        <div className="requester-submit__steps">{submitSteps.map(([label, detail], index) => <span className={index + 1 < submissionStage ? 'is-complete' : index + 1 === submissionStage ? 'is-active' : ''} key={label}><b>{index + 1 < submissionStage ? '✓' : index + 1}</b><em>{label}<small>{detail}</small></em></span>)}</div>
        {error ? <InlineNotification kind="error" title="Submission stopped" subtitle={error} lowContrast /> : null}
        <section className="requester-submit__card">
          {data ? <LiveEvidenceWorkbench busy={busy} submitted={Boolean(submittedRun)} operatorToken={data.sessions.operator} onEvaluate={submitEvidence} /> : <div className="premium-loading"><SkeletonText heading /><SkeletonText paragraph lineCount={5} /></div>}
        </section>
        {submissionStarted && busy ? <aside className="requester-submit__processing"><span className="requester-submit__pulse" /><div><strong>{submissionStage === 2 ? 'Sealing your evidence' : 'Running payment controls'}</strong><small>The uploaded bytes are being hashed and matched. A clean request settles automatically only when both no-touch limits remain available.</small></div></aside> : null}
        {submittedRun && !busy ? <aside className="requester-submit__saved" role="status"><CheckmarkFilled size={22} /><span><strong>{submittedRun.invoice.invoice_number} · {resultTitle}</strong><small>{nextAction}</small></span><button type="button" onClick={() => setResultOpen(true)}>Review outcome <ArrowRight size={14} /></button></aside> : null}
      </main>
      <Modal open={resultOpen && submissionStarted} passiveModal modalHeading={submittedRun ? resultTitle : error ? 'Request could not be submitted' : 'Reviewing your request'} modalLabel="PAYMENT REQUEST" className="request-result-modal" onRequestClose={() => setResultOpen(false)}>
        {submittedRun ? <div className="request-result">
          <div className={`request-result__hero ${blocked ? 'is-blocked' : autoPaid ? 'is-paid' : 'is-pending'}`}><span>{blocked ? '!' : autoPaid ? '✓' : '→'}</span><div><strong>{submittedRun.invoice.invoice_number}</strong><p>{formatMoney(submittedRun.invoice.amount)} {submittedRun.invoice.currency} requested · {autoPaid ? autoSimulated ? 'Demo receipt recorded · no funds moved' : 'Arc receipt confirmed' : 'No funds sent yet'}</p></div></div>
          <ol className="request-result__steps" aria-label="Request progress">
            <li className="is-complete"><b>✓</b><span><strong>Evidence received</strong><small>Three source files sealed</small></span></li>
            <li className="is-complete"><b>✓</b><span><strong>Controls evaluated</strong><small>{submittedRun.decision.rules.length} policy checks recorded</small></span></li>
            <li className={blocked ? 'is-blocked' : autoPaid ? 'is-complete' : 'is-current'}><b>{blocked ? '!' : autoPaid ? '✓' : '3'}</b><span><strong>{blocked ? 'Action needed' : autoPaid ? 'Payment authorized' : action === 'ESCALATE' ? 'Awaiting finance review' : action === 'SCHEDULE' ? 'Scheduled for release' : 'Finance handoff'}</strong><small>{blocked ? issue?.message ?? ACTION_LABEL[action ?? 'HOLD'] : autoPaid ? 'Within finance-configured autonomy limits' : nextAction}</small></span></li>
            <li className={autoPaid ? 'is-complete' : ''}><b>{autoPaid ? '✓' : '4'}</b><span><strong>{autoSimulated ? 'Demo settlement & receipt' : 'Arc settlement & receipt'}</strong><small>{autoPaid ? autoSimulated ? 'Simulated · no funds moved' : shorten(submittedRun.autopay?.payment?.receipt.transaction_hash ?? '', 12, 10) : 'Not yet confirmed'}</small></span></li>
          </ol>
          {blocked ? <div className="request-result__reason"><strong>Why it stopped</strong><p>{issue?.message ?? submittedRun.decision.reason_codes.join(' · ')}</p><small>{issue?.remediation ?? submittedRun.decision.remediation[0] ?? nextAction}</small></div> : null}
          <p className="request-result__next"><strong>What happens next</strong>{nextAction}</p>
          <div className="request-result__actions"><button type="button" className="is-secondary" onClick={() => setResultOpen(false)}>Stay on this page</button><button type="button" onClick={() => onNavigate('portal')}>View request timeline <ArrowRight size={16} /></button></div>
        </div> : error ? <div className="request-result__error" role="alert"><WarningAltFilled size={24} /><div><strong>Nothing was submitted</strong><p>{error}</p><small>Review the issue, then try again. No payment was sent.</small></div></div> : <div className="request-result__working" role="status" aria-live="polite"><span className="requester-submit__pulse" /><div><strong>Sealing evidence and checking controls</strong><p>The three source files are being verified. The result will appear here as soon as the decision is recorded.</p></div></div>}
      </Modal>
    </div>
  );
}

function ProductTopbar({
  review = false,
  sectionLabel,
  roleLabel = 'Finance operator',
  roleHelper = 'Payment requests',
  onBack,
  onSignOut,
  searchValue,
  onSearch,
  settlementMode = 'simulation',
}: {
  review?: boolean;
  sectionLabel?: string;
  roleLabel?: string;
  roleHelper?: string;
  onBack?: () => void;
  onSignOut?: () => void;
  searchValue?: string;
  onSearch?: (value: string) => void;
  settlementMode?: BootstrapData['readiness']['settlement_mode'];
}) {
  return (
    <header className="premium-topbar">
      <button type="button" className="premium-brand" onClick={onBack} aria-label="TallyGuard home"><span>T</span><strong>TallyGuard</strong></button>
      {review || sectionLabel ? (
        <button type="button" className="premium-breadcrumb" onClick={onBack}>AP&nbsp;&nbsp;/&nbsp;&nbsp;{review ? 'Invoice review' : sectionLabel} <span>⌄</span></button>
      ) : (
        <label className="premium-search"><span>⌕</span><input aria-label="Search payables" placeholder="Search invoices, vendors, or hashes..." value={searchValue} onChange={(event) => onSearch?.(event.target.value)} /><kbd>⌘ K</kbd></label>
      )}
      <div className="premium-operator">
        <><span className="environment-pill">{settlementMode === 'circle-live' ? 'LIVE USDC' : 'SIMULATION'}</span><i /><span>Arc Testnet</span></>
        <b>{roleLabel.slice(0, 2).toUpperCase()}</b><span><strong>{roleLabel}</strong><small>{roleHelper}</small></span><span>⌄</span>
        {onSignOut ? <button type="button" className="premium-signout" onClick={onSignOut}>Sign out</button> : null}
      </div>
    </header>
  );
}

function PremiumSideNav({ active, onNavigate, settlementMode = 'simulation' }: { active: WorkspaceView; onNavigate: (view: WorkspaceView) => void; settlementMode?: BootstrapData['readiness']['settlement_mode'] }) {
  const groups = [
    {
      label: 'Finance',
      items: [
        { id: 'finance' as const, label: 'Payment requests', icon: ListChecked },
        { id: 'approvals' as const, label: 'Approval queue', icon: CheckmarkFilled },
      ],
    },
    {
      label: 'Controls & proof',
      items: [
        { id: 'evidence' as const, label: 'Evidence lab', icon: Document },
        { id: 'automation' as const, label: 'Agent runs', icon: Rule },
        { id: 'vendors' as const, label: 'Vendors', icon: UserMultiple },
        { id: 'policies' as const, label: 'Policies', icon: DocumentSecurity },
        { id: 'audit' as const, label: 'Audit', icon: Locked },
      ],
    },
  ];
  return (
    <aside className="premium-sidenav">
      <nav aria-label="Workspace sections">
        {groups.map((group) => <div className="premium-sidenav__group" key={group.label}>
          <span>{group.label}</span>
          {group.items.map((item) => {
            const Icon = item.icon;
            return <button type="button" key={item.id} className={active === item.id ? 'is-active' : ''} onClick={() => onNavigate(item.id)}><Icon size={17} /><span>{item.label}</span></button>;
          })}
        </div>)}
      </nav>
      <div className="premium-sidenav__network"><i /> <span>Arc Testnet<small>{settlementMode === 'circle-live' ? 'CIRCLE + RPC LIVE' : 'SIMULATION'}</small></span></div>
      <small>TallyGuard v0.9.2</small>
    </aside>
  );
}

function PremiumPayablesPage({
  operations,
  arcActivity,
  incidents,
  batch,
  scheduleRun,
  ledgerExport,
  busy,
  onNavigate,
  onOpenReview,
  onSettleBatch,
  onRunSchedules,
  onExportLedger,
  onSeedShowcase,
  onRefreshTreasury,
  onSignOut,
  settlementMode,
}: {
  operations: OperationsOverview | null;
  arcActivity: ArcActivityResponse | null;
  incidents: SettlementIncidentOverview | null;
  batch: PaymentBatch | null;
  scheduleRun: ScheduleRun | null;
  ledgerExport: { hash: string; rows: number } | null;
  busy: boolean;
  onNavigate: (view: WorkspaceView) => void;
  onOpenReview: (invoice: OperationsInvoice) => void;
  onSettleBatch: (items: SettlementBatchItem[]) => void;
  onRunSchedules: () => void;
  onExportLedger: () => void;
  onSeedShowcase: () => void;
  onRefreshTreasury: () => void;
  onSignOut: () => void;
  settlementMode: BootstrapData['readiness']['settlement_mode'];
}) {
  const [tab, setTab] = useState<'all' | 'ready' | 'attention' | 'scheduled' | 'paid'>('all');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const queue = operations?.recent_requests ?? operations?.work_queue ?? [];
  const normalizedQuery = query.trim().toLowerCase();
  const tabMatches = (item: OperationsInvoice) => {
    if (tab === 'ready') return isSettlementReady(item);
    if (tab === 'attention') return needsFinanceAttention(item);
    if (tab === 'scheduled') return item.settlement_status !== 'CONFIRMED' && (item.decision_action === 'SCHEDULE' || item.status === 'SCHEDULED');
    if (tab === 'paid') return item.settlement_status === 'CONFIRMED';
    return true;
  };
  const filtered = queue.filter((item) => tabMatches(item) && (!normalizedQuery || [item.invoice_number, item.vendor_id, item.status, item.source_document_hash].some((value) => value.toLowerCase().includes(normalizedQuery))));
  useEffect(() => {
    if (!filtered.length || filtered.some((item) => item.id === selectedId)) return;
    setSelectedId(filtered[0].id);
    setBatchIds([filtered[0].id]);
    setDrawerOpen(true);
  }, [filtered, selectedId]);
  const selected = drawerOpen ? filtered.find((item) => item.id === selectedId) ?? null : null;
  const ready = queue.filter(isSettlementReady);
  const attention = queue.filter(needsFinanceAttention);
  const scheduled = queue.filter((item) => item.settlement_status !== 'CONFIRMED' && (item.decision_action === 'SCHEDULE' || item.status === 'SCHEDULED'));
  const paid = queue.filter((item) => item.settlement_status === 'CONFIRMED');
  const selectedBatch = queue.filter((item) => batchIds.includes(item.id) && isSettlementReady(item)).map((item) => ({
    invoice_id: item.id,
    decision_id: item.decision_id!,
    ...(item.approval_reference ? { approval_reference: item.approval_reference } : {}),
  }));
  const setBatchChecked = (id: string, checked: boolean) => setBatchIds((items) => checked ? [...new Set([...items, id])] : items.filter((item) => item !== id));
  const statusLabel = financeStatusLabel;
  const dueLabel = (value: string) => new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(`${value}T00:00:00`));
  const vendorLabel = (value: string) => value.replaceAll('-', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
  const verifiedActivity = arcActivity?.available ? arcActivity : null;
  const latestProof = verifiedActivity?.entries.find((entry) => entry.status === 'PAID' && entry.explorer_url);
  return (
    <div className="premium-app premium-payables">
      <ProductTopbar roleLabel="Finance team" roleHelper="Operations backend" onBack={() => onNavigate('finance')} onSignOut={onSignOut} searchValue={query} onSearch={setQuery} settlementMode={settlementMode} />
      <div className="premium-payables__layout">
        <PremiumSideNav active="finance" onNavigate={onNavigate} settlementMode={settlementMode} />
        <main className="premium-payables__main">
          <header className="payables-title"><div><span className="payables-title__eyebrow">FINANCE BACKEND</span><h1>Payment requests</h1><p>Review requests submitted by users, resolve policy exceptions, authorize settlement, and preserve an audit-ready record.</p></div><div className="payables-title__actions"><button type="button" onClick={onExportLedger} disabled={busy}><Download size={15} /> Export ledger</button>{settlementMode === 'circle-live' ? <button type="button" onClick={onRefreshTreasury} disabled={busy}><Renew size={15} /> Refresh Arc balance</button> : queue.length === 0 ? <button type="button" onClick={onSeedShowcase} disabled={busy}><PlayFilled size={15} /> Load demo queue</button> : null}<button type="button" className="is-primary" onClick={() => onNavigate('approvals')} disabled={busy}><CheckmarkFilled size={15} /> Review approvals</button></div></header>
          <section className="payables-metrics" aria-label="Payables overview">
            <article><span className="metric-icon is-blue"><Document size={18} /></span><div><small>Open exposure</small><strong>{formatMoney(operations?.open_exposure_usdc ?? '0')} USDC</strong><span>{operations?.invoice_count ?? 0} workspace invoices</span></div></article>
            <article><span className="metric-icon is-gold"><WarningAltFilled size={18} /></span><div><small>Needs attention</small><strong>{formatMoney(operations?.blocked_exposure_usdc ?? '0')} USDC</strong><span>{attention.length} controlled exceptions</span></div></article>
            <article><span className="metric-icon is-blue"><Time size={18} /></span><div><small>Due this week</small><strong>{formatMoney(operations?.due_next_7_days_usdc ?? '0')} USDC</strong><span>{operations?.due_next_7_days_count ?? 0} invoices</span></div></article>
            <article><span className="metric-icon is-green"><Money size={18} /></span><div><small>Treasury available</small><strong>{formatMoney(operations?.treasury_available_usdc ?? '0')} USDC</strong><span>{operations?.projected_after_open_usdc ? `${formatMoney(operations.projected_after_open_usdc)} projected` : 'Awaiting treasury snapshot'}</span></div></article>
          </section>
          {verifiedActivity ? <section className="finance-arc-proof" aria-label="Arc Testnet verification cases">
            <div className="finance-arc-proof__intro"><span>ARC TESTNET · READ-ONLY CASES</span><strong>{verifiedActivity.processed}+ payment workflow runs · {verifiedActivity.confirmed_payments} Arc Testnet settlements confirmed</strong><small>Separate verification history. The payable queue below is the current interactive demo workspace.</small></div>
            <div className="finance-arc-proof__actions"><span>{verifiedActivity.confirmed_principal_usdc} test USDC confirmed</span>{latestProof?.explorer_url ? <a href={latestProof.explorer_url} target="_blank" rel="noreferrer">Latest Arc proof ↗</a> : null}<button type="button" onClick={() => onNavigate('activity')}>View all {verifiedActivity.planned} cases <ArrowRight size={14} /></button></div>
          </section> : null}
          <div className="payables-tabs">
            {([
              ['all', `All payables (${queue.length})`],
              ['ready', `Ready (${ready.length})`],
              ['attention', `Needs review (${attention.length})`],
              ['scheduled', `Scheduled (${scheduled.length})`],
              ['paid', `Paid (${paid.length})`],
            ] as const).map(([id, label]) => <button type="button" key={id} className={tab === id ? 'is-active' : ''} onClick={() => setTab(id)}>{label}</button>)}
            <button type="button" className="payables-filter" onClick={onRunSchedules} disabled={busy}><Renew size={14} /> Run due schedules</button>
          </div>
          <section className="payables-table" aria-label="Invoice payables">
            <div className="payables-table__head"><span aria-label="Select invoices" title="Select invoices">✓</span><span>Invoice ↓</span><span>Vendor</span><span>Amount</span><span>Due ↕</span><span>Evidence</span><span>Decision</span><span>Status</span></div>
            {filtered.length ? filtered.map((item) => (
              <div className={batchIds.includes(item.id) ? 'payables-table__row is-selected' : 'payables-table__row'} key={item.id} onClick={() => { setSelectedId(item.id); setBatchIds([item.id]); setDrawerOpen(true); }} role="button" tabIndex={0} onKeyDown={(event) => { if (event.key === 'Enter') { setSelectedId(item.id); setBatchIds([item.id]); setDrawerOpen(true); } }}>
                <label className="row-check"><input type="checkbox" aria-label={`Select ${item.invoice_number}`} checked={batchIds.includes(item.id)} onChange={(event) => { setSelectedId(item.id); setDrawerOpen(true); setBatchChecked(item.id, event.target.checked); }} onClick={(event) => event.stopPropagation()} /><span>✓</span></label>
                <button type="button" className="payable-link" onClick={(event) => { event.stopPropagation(); setSelectedId(item.id); setBatchIds([item.id]); setDrawerOpen(true); if (item.decision_id) onOpenReview(item); }}><strong>{item.invoice_number}</strong><small>{shorten(item.id, 7, 4)}</small></button>
                <span className="vendor-cell"><strong>{vendorLabel(item.vendor_id)}</strong><small>{isDemoScenarioInvoice(item) ? 'Sample case · ' : ''}{shorten(item.payment_wallet_address, 7, 5)}</small></span>
                <strong>{formatMoney(item.amount)} {item.currency}</strong><span>{dueLabel(item.due_date)}</span>
                <span className={item.decision_id ? 'evidence-state is-good' : 'evidence-state'}><i />{item.decision_id ? 'Decision sealed' : 'Evidence needed'}</span>
                <span className="payable-decision">{item.approval_status === 'APPROVED' ? 'Approved' : item.decision_action ? QUEUE_DECISION_LABEL[item.decision_action] : '—'}</span><span className={`payable-status is-${(item.approval_status ?? item.decision_action ?? item.status).toLowerCase().replaceAll('_', '-')}`}>{statusLabel(item)}</span>
              </div>
            )) : <div className="payables-empty"><Document size={24} /><strong>No payment requests are waiting.</strong><span>{settlementMode === 'circle-live' ? 'User submissions appear here after evidence is confirmed. The treasury balance is read directly from Circle and verified against Arc.' : 'User submissions appear here after evidence is confirmed. Load a realistic queue to inspect the finance workflow.'}</span><div><button type="button" onClick={() => onNavigate('approvals')}>Open approvals</button>{settlementMode === 'circle-live' ? <button type="button" className="is-secondary" onClick={onRefreshTreasury} disabled={busy}>Refresh Arc balance</button> : <button type="button" className="is-secondary" onClick={onSeedShowcase} disabled={busy}>Load sample requests</button>}</div></div>}
            <footer><span>Showing {filtered.length} of {queue.length} invoices</span><span>{operations?.as_of ? `Snapshot ${new Date(operations.as_of).toLocaleString()}` : 'Waiting for live operations data'}</span></footer>
          </section>
          <section className="payables-batchbar"><div><strong>{batchIds.length} selected · {selectedBatch.length} ready for settlement</strong><span>Rows needing approval or correction can be selected for review, but cannot be paid. Ready items are revalidated before transfer.</span></div><button type="button" disabled={busy || selectedBatch.length === 0} onClick={() => onSettleBatch(selectedBatch)}>Settle ready selection on Arc <ArrowRight size={15} /></button></section>
          {batch ? <InlineNotification kind={batch.failed ? 'warning' : 'success'} title={`${batch.succeeded} of ${batch.requested} payments confirmed`} subtitle={`${batch.failed} failed · every item was revalidated before settlement`} lowContrast hideCloseButton /> : null}
          {scheduleRun ? <InlineNotification kind="info" title="Schedule runner completed" subtitle={`${scheduleRun.scanned} scanned · ${scheduleRun.settled} settled · ${scheduleRun.waiting} waiting · ${scheduleRun.revalidated} revalidated`} lowContrast hideCloseButton /> : null}
          {ledgerExport ? <InlineNotification kind="success" title="Accounting ledger exported" subtitle={`${ledgerExport.rows} rows · SHA-256 ${shorten(ledgerExport.hash, 14, 10)}`} lowContrast hideCloseButton /> : null}
          {incidents ? <SettlementIncidentCenter overview={incidents} /> : null}
        </main>
        <aside className="payable-drawer">
          {selected ? <>
            <div className="payable-drawer__head"><h2>{selected.invoice_number}</h2><button type="button" aria-label="Close invoice details" onClick={() => setDrawerOpen(false)}>×</button></div>
            <div className="vendor-summary"><span>{vendorLabel(selected.vendor_id).slice(0, 1)}</span><div><strong>{vendorLabel(selected.vendor_id)}</strong><small>{isDemoScenarioInvoice(selected) ? 'Sample workflow case' : 'Submitted through User portal'} · {new Date(selected.created_at).toLocaleString()}</small></div><em>{selected.decision_id ? '✓ Decision sealed' : 'Evidence pending'}</em></div>
            <div className="drawer-request-link"><span>{isDemoScenarioInvoice(selected) ? 'Preloaded example' : 'Matching user request'}</span><strong>{selected.invoice_number}</strong><small>{isDemoScenarioInvoice(selected) ? 'A complete decision path you can inspect without uploading a file.' : 'The same request ID, amount, and source hash appear in both portals.'}</small></div>
            <dl className="invoice-facts"><div><dt>Requested amount</dt><dd>{formatMoney(selected.amount)} {selected.currency}</dd></div><div><dt>Due date</dt><dd>{dueLabel(selected.due_date)}</dd></div><div><dt>Decision</dt><dd>{financeDecisionLabel(selected)}</dd></div><div><dt>Settlement</dt><dd>{settlementStatusLabel(selected)}</dd></div></dl>
            <section className="drawer-section"><header><strong>Evidence & decision</strong><span>{selected.decision_id ? 'Replayable' : 'Not evaluated'}</span></header><div className="drawer-evidence"><span>▤</span><strong>Source document</strong><small>{shorten(selected.source_document_hash, 14, 10)}</small><i>✓</i></div><div className="drawer-evidence"><span>♢</span><strong>Policy action</strong><small>{selected.decision_action ? ACTION_LABEL[selected.decision_action] : 'Awaiting evaluation'}</small><i>{selected.decision_id ? '✓' : '•'}</i></div></section>
            <section className="drawer-section"><header><strong>Settlement</strong><span className="arc-label"><i />Arc Testnet&nbsp;&nbsp;{settlementMode === 'circle-live' ? 'LIVE' : 'SIMULATION'}</span></header><div className="wallet-line"><span>Payout wallet</span><code>{shorten(selected.payment_wallet_address, 10, 8)}&nbsp; □</code><small>{selected.settlement_retryable ? 'Retryable incident detected' : selected.settled_amount_usdc ? settlementMode === 'circle-live' ? 'Circle transfer confirmed by independent Arc RPC proof' : 'Simulation receipt recorded · no funds moved' : 'No transfer has been submitted yet'}</small></div><div className="drawer-cards"><article><span>♢</span><small>Requested</small><strong>{formatMoney(selected.amount)} {selected.currency}</strong><p>{financeDecisionLabel(selected)}</p></article><article><span>▤</span><small>{settlementMode === 'circle-live' ? 'Actually sent' : 'Demo settled'}</small><strong>{selected.settled_amount_usdc ? `${formatMoney(selected.settled_amount_usdc)} ${selected.currency}` : '0.00 USDC'}</strong><p>{settlementMode === 'circle-live' && selected.settlement_transaction_hash ? shorten(selected.settlement_transaction_hash, 10, 8) : settlementMode === 'circle-live' ? 'No transaction yet' : 'No funds moved'}</p></article></div></section>
            <button type="button" className="drawer-primary" disabled={busy || !selected.decision_id} onClick={() => selected.decision_id && onOpenReview(selected)}>{busy ? 'Loading live record…' : selected.decision_id ? 'Review evidence & controls' : 'Waiting for requester evidence'}</button>
            <small className="simulation-note">{settlementMode === 'circle-live' ? 'Live mode: final authorization submits USDC through Circle and accepts the result only after independent Arc RPC verification.' : 'Public judge workspace is forced simulation. The same workflow supports configured Circle/Arc adapters.'}</small>
            <section className="audit-mini"><header><strong>Traceability</strong><button type="button" onClick={() => onNavigate('audit')}>Open audit ledger →</button></header><ol><li><i />Invoice persisted<small>Tenant-scoped workspace record</small><time>{new Date(selected.created_at).toLocaleString()}</time></li><li><i />Latest state recorded<small>{statusLabel(selected)}</small><time>{new Date(selected.updated_at).toLocaleString()}</time></li>{selected.decision_id ? <li><i />Decision snapshot sealed<small>{shorten(selected.decision_id, 12, 8)}</small><time>Replay available</time></li> : null}</ol></section>
          </> : <div className="payable-drawer__empty"><Document size={30} /><strong>Select a payable</strong><span>The drawer will show its persisted evidence, policy decision, treasury impact, and audit path.</span></div>}
        </aside>
      </div>
    </div>
  );
}

type LifecycleState = 'complete' | 'current' | 'pending' | 'rejected';

function PaymentLifecycle({ run, approval, payment, busy }: { run: RunResult | null; approval: Approval | null; payment: Payment | null; busy: string | null }) {
  const needsApproval = run?.decision.final_action === 'ESCALATE';
  const rejected = approval?.status === 'REJECTED';
  const approved = approval?.status === 'APPROVED';
  const evaluating = Boolean(busy?.toLowerCase().includes('evaluat'));
  const settling = Boolean(busy?.toLowerCase().includes('sett') || busy?.toLowerCase().includes('reconcil'));
  const steps: Array<{ label: string; detail: string; state: LifecycleState }> = [
    { label: 'Invoice received', detail: run ? run.invoice.invoice_number : 'Waiting for a supplier bill', state: run ? 'complete' : evaluating ? 'current' : 'pending' },
    { label: 'AI & policy checks', detail: run ? ACTION_LABEL[run.decision.final_action] : evaluating ? 'Reading evidence and applying controls' : 'Not started', state: run ? 'complete' : evaluating ? 'current' : 'pending' },
    { label: 'Independent approval', detail: !run ? 'Waiting for checks' : !needsApproval ? 'Not required by policy' : rejected ? 'Rejected with reason' : approved ? 'Approved by finance controller' : approval ? 'Waiting for approver' : 'Ready to submit', state: !run ? 'pending' : !needsApproval || approved ? 'complete' : rejected ? 'rejected' : 'current' },
    { label: payment?.receipt.provider === 'arc-simulator' ? 'Demo settlement' : 'Arc settlement', detail: payment ? `${formatMoney(payment.receipt.confirmed_amount_usdc)} USDC ${payment.receipt.provider === 'arc-simulator' ? 'simulated' : 'confirmed'}` : settling ? 'Submitting and reconciling' : rejected ? 'Locked after rejection' : 'Waiting for authorization', state: payment ? 'complete' : settling ? 'current' : 'pending' },
    { label: 'Receipt', detail: payment ? 'Audit-ready proof available' : 'Created after reconciliation', state: payment ? 'complete' : 'pending' },
  ];
  return (
    <footer className="payment-lifecycle" aria-label="Payment request progress">
      <div className="payment-lifecycle__label"><strong>Payment status</strong><small>{rejected ? 'Stopped by independent review' : payment ? payment.receipt.provider === 'arc-simulator' ? 'Simulation complete · no funds moved' : 'Complete and reconciled' : 'Every handoff is visible'}</small></div>
      {steps.map((step, index) => <article className={`is-${step.state}`} key={step.label}><span>{step.state === 'complete' ? '✓' : step.state === 'rejected' ? '×' : index + 1}</span><div><strong>{step.label}</strong><small>{step.detail}</small></div></article>)}
    </footer>
  );
}

type ReviewConnectorGeometry = {
  id: string;
  path: string;
  sourceX: number;
  sourceY: number;
};

const REVIEW_CONNECTOR_PAIRS = [
  { id: 'invoice-evidence', source: 'invoice', target: 'evidence' },
  { id: 'wallet-vendor', source: 'wallet', target: 'vendor' },
  { id: 'due-treasury', source: 'due', target: 'treasury' },
  { id: 'amount-authority', source: 'amount', target: 'authority' },
] as const;

function ReviewConnectors({ revision }: { revision: string }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [lines, setLines] = useState<ReviewConnectorGeometry[]>([]);

  useLayoutEffect(() => {
    const root = svgRef.current?.parentElement;
    if (!root) return undefined;
    const panel = root.querySelector<HTMLElement>('.review-decision-panel');
    const measure = () => {
      const rootRect = root.getBoundingClientRect();
      const width = root.clientWidth;
      const height = root.clientHeight;
      const nextLines = REVIEW_CONNECTOR_PAIRS.flatMap((pair) => {
        const source = root.querySelector<HTMLElement>(`[data-review-source="${pair.source}"]`);
        const target = root.querySelector<HTMLElement>(`[data-review-target="${pair.target}"]`);
        if (!source || !target) return [];
        const sourceRect = source.getBoundingClientRect();
        const targetRect = target.getBoundingClientRect();
        const panelRect = panel?.getBoundingClientRect();
        const targetViewportY = targetRect.top + targetRect.height / 2;
        if (panelRect && (targetViewportY < panelRect.top || targetViewportY > panelRect.bottom)) return [];
        const sourceX = sourceRect.right - rootRect.left + 7;
        const sourceY = sourceRect.top + sourceRect.height / 2 - rootRect.top;
        const targetX = targetRect.left - rootRect.left - 24;
        const targetY = targetRect.top + targetRect.height / 2 - rootRect.top;
        const horizontalRoom = Math.max(36, targetX - sourceX);
        const sourceControlX = sourceX + Math.max(28, horizontalRoom * 0.42);
        const targetControlX = targetX - Math.max(22, Math.min(48, horizontalRoom * 0.24));
        return [{
          id: pair.id,
          sourceX,
          sourceY,
          path: `M ${sourceX} ${sourceY} C ${sourceControlX} ${sourceY}, ${targetControlX} ${targetY}, ${targetX} ${targetY}`,
        }];
      });
      setSize({ width, height });
      setLines(nextLines);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    if (panel) observer.observe(panel);
    window.addEventListener('resize', measure);
    panel?.addEventListener('scroll', measure, { passive: true });
    measure();
    const animationFrame = window.requestAnimationFrame(measure);
    const settleTimer = window.setTimeout(measure, 160);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.clearTimeout(settleTimer);
      observer.disconnect();
      window.removeEventListener('resize', measure);
      panel?.removeEventListener('scroll', measure);
    };
  }, [revision]);

  const measured = Boolean(size.width && size.height);
  return (
    <svg ref={svgRef} className="evidence-connectors" viewBox={measured ? `0 0 ${size.width} ${size.height}` : '0 0 1 1'} preserveAspectRatio="none" aria-hidden="true">
      <defs><marker id="review-connector-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" /></marker></defs>
      {measured && revision !== 'empty:0' ? lines.map((line) => <g key={line.id}><path d={line.path} markerEnd="url(#review-connector-arrow)" /><circle cx={line.sourceX} cy={line.sourceY} r="4" /></g>) : null}
    </svg>
  );
}

function reviewControlGroup(code: string): 'evidence' | 'vendor' | 'authority' | 'treasury' {
  if (code.includes('DUPLICATE') || code.includes('INVOICE') || code.includes('PURCHASE_ORDER') || code.includes('DELIVERY')) return 'evidence';
  if (code.includes('VENDOR') || code.includes('WALLET') || code.includes('TENANT_BOUNDARY')) return 'vendor';
  if (code.includes('AUTONOMY') || code.includes('KILL_SWITCH') || code.includes('SETTLEMENT_ROUTE')) return 'authority';
  return 'treasury';
}

function InvoiceReviewPage({
  run,
  approval,
  payment,
  busy,
  auditTrail,
  replay,
  simulation,
  packetHash,
  evidenceDocuments,
  settlementStopped,
  settlementRetryNeeded,
  onNavigate,
  onBack,
  onRun,
  onRequestApproval,
  onSettle,
  onDownloadPacket,
  onDownloadSourceEvidence,
  onVerifyReplay,
  onSimulatePolicy,
  onSignOut,
  settlementMode,
}: {
  run: RunResult | null;
  approval: Approval | null;
  payment: Payment | null;
  busy: string | null;
  auditTrail: AuditTrail | null;
  replay: ReplayVerification | null;
  simulation: PolicySimulation | null;
  packetHash: string | null;
  evidenceDocuments: EvidenceDocument[];
  settlementStopped: boolean;
  settlementRetryNeeded: boolean;
  onNavigate: (view: WorkspaceView) => void;
  onBack: () => void;
  onRun: () => void;
  onRequestApproval: () => void;
  onSettle: () => void;
  onDownloadPacket: () => void;
  onDownloadSourceEvidence: (document: EvidenceDocument) => void;
  onVerifyReplay: () => void;
  onSimulatePolicy: (changes: Record<string, string | number | boolean | null>) => void;
  onSignOut: () => void;
  settlementMode: BootstrapData['readiness']['settlement_mode'];
}) {
  const settlementEligible = run?.decision.final_action === 'PAY' || (run?.decision.final_action === 'ESCALATE' && approval?.status === 'APPROVED');
  const approvalRejected = approval?.status === 'REJECTED';
  const action = !run ? onRun : run.decision.final_action === 'ESCALATE' && !approval ? onRequestApproval : approval?.status === 'PENDING' ? () => onNavigate('approvals') : settlementEligible ? onSettle : () => onNavigate('evidence');
  const label = busy ? busy : payment ? payment.receipt.provider === 'arc-simulator' ? 'View simulation receipt' : 'View confirmed receipt' : !run ? 'Review sample invoice' : run.decision.final_action === 'ESCALATE' && !approval ? 'Submit for independent approval' : approval?.status === 'PENDING' ? 'Open approver portal' : approvalRejected ? 'Rejected — review reason below' : settlementEligible ? `Settle ${formatMoney(run.invoice.amount)} USDC on Arc` : `${ACTION_LABEL[run.decision.final_action]} — settlement locked`;
  const handlePrimary = () => {
    if (payment) {
      if (payment.receipt.provider !== 'arc-simulator' && payment.receipt.explorer_url) {
        window.open(payment.receipt.explorer_url, '_blank', 'noopener,noreferrer');
      } else {
        const details = document.querySelector<HTMLDetailsElement>('.review-deep-dive');
        if (details) details.open = true;
        window.requestAnimationFrame(() => document.querySelector('.receipt-panel')?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
      }
      return;
    }
    action();
  };
  const invoice = run?.invoice;
  const decision = run?.decision;
  const reviewRules = decision?.rules ?? [];
  const duplicateReviewRule = reviewRules.find((rule) => rule.code === 'VENDOR_INVOICE_NUMBER_REUSED' || rule.code === 'NEAR_DUPLICATE_INVOICE_FIELDS');
  const cumulativeHoldRule = reviewRules.find((rule) => rule.code === 'PO_CUMULATIVE_EXCEEDED' || rule.code === 'DELIVERY_CUMULATIVE_EXCEEDED');
  const decisiveException = reviewRules.find((rule) => rule.disposition === 'HOLD' || rule.disposition === 'ESCALATE' || rule.disposition === 'REJECT');
  const agentReviewSummary = decision?.agent_disagreed
    ? `The document analyst suggested ${decision.agent_recommendation?.action ?? 'a different action'}, but the deterministic policy decision is ${decision.final_action}. ${decisiveException?.message ?? 'The policy controls below explain the override.'} The agent cannot authorize payment.`
    : decision?.agent_recommendation?.summary ?? 'The deterministic engine remains the sole payment authority.';
  const reviewGroups = ([
    ['evidence', 'Evidence package', 'Invoice, order, and delivery records are sealed and cross-checked.'],
    ['vendor', 'Vendor & payout', 'Supplier identity and payout destination are independently verified.'],
    ['treasury', 'Treasury & timing', 'Spend limits, reserves, and payment timing remain enforceable.'],
    ['authority', 'Policy authority', 'Finance-configured automation and approval boundaries control the action.'],
  ] as const).map(([key, title, detail]) => {
    const rules = reviewRules.filter((rule) => reviewControlGroup(rule.code) === key);
    const exception = rules.find((rule) => rule.disposition !== 'PASS');
    return {
      key,
      title: key === 'evidence' && duplicateReviewRule ? 'Possible duplicate invoice' : title,
      detail: key === 'evidence' && duplicateReviewRule ? 'Compare this request with the earlier invoice before authorizing payment.' : detail,
      rules,
      exception,
    };
  });
  const vendorName = invoice?.vendor_id.replaceAll('-', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase()) ?? 'Awaiting invoice';
  const decisionOutcome = approvalRejected
    ? { tone: 'rejected', title: 'Rejected — no funds sent', detail: approval?.resolution_note ?? 'The independent approver returned this request.' }
    : payment
      ? { tone: 'paid', title: `${payment.receipt.provider === 'arc-simulator' ? 'Demo payment completed' : 'Paid'} ${formatMoney(payment.receipt.confirmed_amount_usdc)} USDC`, detail: payment.receipt.provider === 'arc-simulator' ? 'Simulation receipt · no funds moved' : `Confirmed on ${payment.receipt.network} · ${shorten(payment.receipt.transaction_hash, 12, 10)}` }
      : approval?.status === 'PENDING'
        ? { tone: 'review', title: 'Waiting for independent approval', detail: `${formatMoney(invoice?.amount ?? '0')} USDC requested · no funds sent` }
        : settlementEligible
          ? { tone: 'approved', title: 'Approved for payment', detail: `${formatMoney(invoice?.amount ?? '0')} USDC authorized · transfer not sent yet` }
          : decision?.final_action === 'ESCALATE' && duplicateReviewRule
            ? { tone: 'review', title: 'Possible duplicate — review required', detail: duplicateReviewRule.message }
          : decision?.final_action === 'HOLD' && cumulativeHoldRule
            ? { tone: 'blocked', title: 'Evidence value exceeded — payment blocked', detail: cumulativeHoldRule.message }
          : decision?.final_action === 'SCHEDULE'
            ? { tone: 'scheduled', title: 'Scheduled — no funds sent', detail: 'Settlement remains locked until the policy-controlled payment window.' }
          : decision
            ? { tone: 'blocked', title: ACTION_LABEL[decision.final_action], detail: `${formatMoney(invoice?.amount ?? '0')} USDC requested · settlement is locked` }
            : { tone: 'pending', title: 'Awaiting evaluation', detail: 'No policy decision or payment exists yet' };
  return (
    <div className="premium-app premium-review">
      <ProductTopbar review roleLabel="Finance team" roleHelper="Evidence review" onBack={onBack} onSignOut={onSignOut} settlementMode={settlementMode} />
      <div className="review-layout">
        <aside className="review-nav"><nav>{([['Payment requests',Document,'finance'],['Approval queue',CheckmarkFilled,'approvals'],['Evidence lab',ListChecked,'evidence'],['Policies',DocumentSecurity,'policies'],['Agent runs',Rule,'automation'],['Audit',Locked,'audit']] as const).map(([name, Icon, view], index) => <button type="button" className={index === 0 ? 'is-active' : ''} key={name} onClick={() => onNavigate(view)}><Icon size={17} /><span>{name}</span></button>)}</nav><div><small>Finance backend<br />controller access</small><span><i />Testnet<br />Connected</span></div></aside>
        <main className="review-main">
          <header className="review-title"><div><h1>Invoice review</h1><p>{invoice ? `${invoice.invoice_number} · ${vendorName} · sealed evidence and policy state` : 'Verify evidence. Approve with confidence.'}</p></div><div className="review-pager"><strong>{payment ? payment.receipt.provider === 'arc-simulator' ? 'Demo settled' : 'Paid on Arc' : approval?.status === 'APPROVED' ? 'Approved by finance' : approvalRejected ? 'Rejected by approver' : decision ? ACTION_LABEL[decision.final_action] : 'No decision loaded'}</strong><button type="button" onClick={onBack}>View all invoices</button></div></header>
          <div className="review-workspace">
            <section className="invoice-stage">
              <article className="invoice-paper">
                <header><div className="atlas-mark">▲</div><div><h2>{vendorName}</h2><p>Verified supplier record on Arc.</p></div><strong>INVOICE</strong></header>
                <div className="invoice-parties"><div><strong>{vendorName}</strong><span>Tenant vendor identity<br /><span className="invoice-source-inline" data-review-source="wallet">Wallet {shorten(invoice?.payment_wallet_address ?? 'not available', 10, 8)}</span><br />Arc settlement network</span><strong>Bill To</strong><span><b>TallyGuard workspace</b><br />Role-separated finance operations<br />Evidence-bound settlement</span></div><dl><div><dt>Invoice No.</dt><dd className="is-linked" data-review-source="invoice">{invoice?.invoice_number ?? '—'}</dd></div><div><dt>Created</dt><dd>{invoice ? new Date(invoice.created_at).toLocaleDateString() : '—'}</dd></div><div><dt>Due Date</dt><dd className="is-linked" data-review-source="due">{invoice?.due_date ?? '—'}</dd></div><div><dt>Status</dt><dd>{payment ? payment.receipt.provider === 'arc-simulator' ? 'DEMO SETTLED' : 'PAID' : invoice?.status.replaceAll('_', ' ') ?? '—'}</dd></div><div className="spaced"><dt>Document proof</dt><dd className="is-linked">{shorten(invoice?.source_document_hash ?? 'not available', 8, 5)}</dd></div><div><dt>Decision</dt><dd>{shorten(decision?.id ?? 'not evaluated', 8, 5)}</dd></div><div><dt>Version</dt><dd>v{invoice?.version ?? 0}</dd></div></dl></div>
                <div className="invoice-lines"><div className="invoice-lines__head"><span>Description</span><span>Qty</span><span>Unit Price</span><span>Amount</span></div><div><span>Governed vendor payment<br />Immutable source evidence attached</span><span>1</span><span>{formatMoney(invoice?.amount ?? '0')}</span><span className="is-linked" data-review-source="amount">{formatMoney(invoice?.amount ?? '0')} {invoice?.currency ?? 'USDC'}</span></div></div>
                <div className="invoice-total"><span>Subtotal</span><strong>{formatMoney(invoice?.amount ?? '0')} {invoice?.currency ?? 'USDC'}</strong><span>Total Due</span><strong className="is-linked">{formatMoney(invoice?.amount ?? '0')} {invoice?.currency ?? 'USDC'}</strong></div>
                <footer>Source hash {shorten(invoice?.source_document_hash ?? 'not available', 16, 12)}</footer>
              </article>
            </section>
            <ReviewConnectors revision={`${decision?.id ?? 'empty'}:${reviewRules.length}`} />
            <aside className="review-decision-panel">
              <header><h2>Evidence and decision</h2><div><span>Policy&nbsp;&nbsp;<strong>{decision?.policy_version ?? 'Not evaluated'}</strong></span><small>Arc Testnet&nbsp; • &nbsp;{run?.correlation_id ? shorten(run.correlation_id, 10, 6) : 'Awaiting run'}</small></div></header>
              <section className={`review-outcome is-${decisionOutcome.tone}`}><span>{decisionOutcome.tone === 'rejected' || decisionOutcome.tone === 'blocked' ? '×' : decisionOutcome.tone === 'review' || decisionOutcome.tone === 'pending' || decisionOutcome.tone === 'scheduled' ? '!' : '✓'}</span><div><strong>{decisionOutcome.title}</strong><small>{decisionOutcome.detail}</small></div></section>
              {decision ? <>
                <div className="decision-checks__summary"><strong>{decision.rules.filter((rule) => rule.disposition === 'PASS').length} of {decision.rules.length} controls passed</strong><span>Four decision groups show what the invoice evidence was checked against.</span></div>
                <section className="review-key-controls" aria-label="Key decision groups">
                  {reviewGroups.map((group, index) => <article className={group.exception ? 'is-exception' : 'is-pass'} data-review-target={group.key} key={group.key}><span>{index + 1}</span><i>{group.exception ? '!' : '✓'}</i><div><strong>{group.title}</strong><p>{group.exception?.message ?? group.detail}</p></div><em>{group.exception ? group.exception.disposition : `${group.rules.length} passed`}</em></article>)}
                </section>
                <details className="decision-controls">
                  <summary><span>Complete policy checklist</span><strong>Review all {reviewRules.length} controls</strong></summary>
                  <div className="decision-checks">
                    {reviewRules.map((rule, index) => {
                      const copy = rulePresentation(rule);
                      return <article className={rule.disposition === 'PASS' ? 'is-pass' : 'is-exception'} key={`${rule.code}-${index}`}><span className="check-number">{index + 1}</span><i>{rule.disposition === 'PASS' ? '✓' : '!'}</i><div><strong>{copy.title}</strong><p>{rule.message}</p><details><summary>Why this matters · next action</summary><dl><div><dt>Why it matters</dt><dd>{copy.why}</dd></div><div><dt>Next system action</dt><dd>{copy.next}</dd></div></dl></details></div><em>{rule.disposition === 'PASS' ? 'Passed' : rule.disposition}</em></article>;
                    })}
                  </div>
                </details>
              </> : <div className="decision-empty"><Rule size={20} /><strong>No invoice has been reviewed yet.</strong><p>Open a sample invoice to see AI extraction, evidence matching, policy controls, and the payment decision together.</p><button type="button" onClick={onRun}>Review sample invoice</button></div>}
              {approvalRejected ? <section className="approval-rejection"><WarningAltFilled size={19} /><div><strong>Payment request rejected</strong><p>{approval.resolution_note ?? 'The approver rejected this request without an available note.'}</p><small>{approval.resolved_by_user_id ? `Resolved by ${approval.resolved_by_user_id}` : 'Independent approver'}{approval.requested_at ? ` · requested ${new Date(approval.requested_at).toLocaleString()}` : ''}</small></div></section> : null}
              <section className="agent-recommendation"><header><span>✦</span><strong>{decision?.agent_disagreed ? 'Policy overrode the agent' : 'Agent recommendation'}</strong><em>Cannot override policy</em></header><p>{agentReviewSummary}</p><div>{decision ? `${approval?.status === 'APPROVED' ? 'Original policy exception, resolved by independent approval' : ACTION_LABEL[decision.final_action]} · ${decision.reason_codes.join(' · ') || 'all mandatory controls passed'}` : 'Run an evaluation to create a replayable recommendation and policy decision.'}</div></section>
              <div className="review-actions"><button type="button" className="review-primary" disabled={Boolean(busy) || Boolean(run && !settlementEligible && run.decision.final_action !== 'ESCALATE')} onClick={handlePrimary}>{label}<ArrowRight size={16} /></button><button type="button" disabled={!run || Boolean(busy)} onClick={onDownloadPacket}><Download size={16} />Download evidence packet</button><button type="button" aria-label="Open audit ledger" onClick={() => onNavigate('audit')}><Locked size={16} /></button></div>
            </aside>
          </div>
        </main>
        <aside className={approvalRejected ? 'review-status is-rejected' : settlementEligible || payment ? 'review-status' : 'review-status is-blocked'}><div className="review-status__hero"><span>{approvalRejected ? '×' : settlementEligible || payment ? '✓' : '!'}</span><div><strong>{approvalRejected ? 'Rejected by approver' : payment ? payment.receipt.provider === 'arc-simulator' ? 'Demo payment completed' : 'Confirmed on Arc' : settlementEligible ? 'Approved for payment' : decision ? ACTION_LABEL[decision.final_action] : 'Awaiting evaluation'}</strong><small>{approvalRejected ? 'Reason is visible in the decision packet' : payment ? payment.receipt.provider === 'arc-simulator' ? 'Simulation receipt · no funds moved' : 'Reconciled receipt available' : settlementEligible ? 'Finance settlement is the next separate step' : approval?.status === 'PENDING' ? 'Waiting for independent approver' : 'Policy controls the next action'}</small></div></div></aside>
      </div>
      <PaymentLifecycle run={run} approval={approval} payment={payment} busy={busy} />
      {run ? <details className="review-deep-dive"><summary><span><small>ADVANCED VERIFICATION</small><strong>Evidence, replay, simulation and audit proof</strong></span><em>Open tools</em></summary><section className="review-detail-stack"><div className="review-detail-stack__intro"><span>DEEP VERIFICATION</span><h2>Inspect the controls behind this decision.</h2><p>Replay sealed inputs, download original evidence, run non-persistent policy what-if cases, and inspect the proof chain. These tools support audit work; they are not required for a normal approval.</p></div><div className="result-grid"><EvidencePanel run={run} documents={evidenceDocuments} busy={Boolean(busy)} onDownloadDocument={onDownloadSourceEvidence} /><DecisionPanel run={run} approval={approval} payment={payment} busy={busy} replay={replay} simulation={simulation} settlementStopped={settlementStopped} settlementRetryNeeded={settlementRetryNeeded} onRequestApproval={onRequestApproval} onApprove={() => onNavigate('approvals')} onSettle={onSettle} onVerifyReplay={onVerifyReplay} onSimulatePolicy={onSimulatePolicy} /></div>{payment ? <ReceiptPanel payment={payment} /> : null}{auditTrail ? <AuditTimeline trail={auditTrail} packetHash={packetHash} busy={Boolean(busy)} onDownloadPacket={onDownloadPacket} /> : null}</section></details> : null}
    </div>
  );
}

type ApprovalResolutionSnapshot = GovernanceOverview['pendingApprovals'][number];

function ApprovalPortalPage({
  governance,
  lastResolution,
  busy,
  onNavigate,
  onOpenReview,
  onResolve,
  onSignOut,
  settlementMode,
}: {
  governance: GovernanceOverview;
  lastResolution: ApprovalResolutionSnapshot | null;
  busy: string | null;
  onNavigate: (view: WorkspaceView) => void;
  onOpenReview: (item: GovernanceOverview['pendingApprovals'][number]) => void;
  onResolve: (item: GovernanceOverview['pendingApprovals'][number], approve: boolean, note: string) => void;
  onSignOut: () => void;
  settlementMode: BootstrapData['readiness']['settlement_mode'];
}) {
  const defaultApprovalNote = 'Evidence, vendor identity, amount, and treasury impact reviewed.';
  const [selectedId, setSelectedId] = useState<string | null>(governance.pendingApprovals[0]?.approval.id ?? null);
  const [resolutionNote, setResolutionNote] = useState(defaultApprovalNote);
  const [rejectMode, setRejectMode] = useState(false);
  useEffect(() => {
    if (lastResolution) setSelectedId(lastResolution.approval.id);
  }, [lastResolution?.approval.id]);
  useEffect(() => {
    if (selectedId && (governance.pendingApprovals.some((item) => item.approval.id === selectedId) || lastResolution?.approval.id === selectedId)) return;
    setSelectedId(governance.pendingApprovals[0]?.approval.id ?? null);
  }, [governance.pendingApprovals, selectedId, lastResolution?.approval.id]);
  const selected = governance.pendingApprovals.find((item) => item.approval.id === selectedId) ?? (selectedId ? null : governance.pendingApprovals[0] ?? null);
  const detail = selectedId === lastResolution?.approval.id ? lastResolution : selected ?? lastResolution;
  const detailRun: RunResult | null = detail ? { invoice: detail.invoice, decision: detail.decision, correlation_id: detail.approval.id } : null;
  const pendingAmount = governance.pendingApprovals.reduce((sum, item) => sum + Number(item.invoice.amount), 0);
  const rejected = detail?.approval.status === 'REJECTED';
  const approved = detail?.approval.status === 'APPROVED';
  const canApprove = Boolean(selected) && !rejectMode && resolutionNote.trim().length >= 3 && !busy;
  const canReject = Boolean(selected) && rejectMode && resolutionNote.trim().length >= 12 && !busy;
  const decide = (approve: boolean) => {
    if (!selected) return;
    if (!approve && !rejectMode) {
      setRejectMode(true);
      setResolutionNote('');
      return;
    }
    if (resolutionNote.trim().length < (approve ? 3 : 12)) return;
    onResolve(selected, approve, resolutionNote.trim());
  };
  return (
    <div className="premium-app premium-approvals">
      <ProductTopbar sectionLabel="Approval queue" roleLabel="Finance approver" roleHelper="Independent authority" onBack={() => onNavigate('finance')} onSignOut={onSignOut} settlementMode={settlementMode} />
      <div className="approval-portal__layout">
        <PremiumSideNav active="approvals" onNavigate={onNavigate} settlementMode={settlementMode} />
        <main className="approval-portal__main">
          <header className="approval-portal__title"><div><span>FINANCE BACKEND · INDEPENDENT AUTHORITY</span><h1>Approval queue</h1><p>Review sealed evidence and policy exceptions. Requesters cannot approve, reject, or alter this decision packet.</p></div><button type="button" onClick={() => onNavigate('finance')}>Back to payment requests <ArrowRight size={15} /></button></header>
          <section className="role-boundary"><Locked size={19} /><div><strong>Separation of duties is active</strong><span>You are acting as an approver. Submitted evidence is read-only, and every resolution requires a reason.</span></div><em>APPROVER SESSION</em></section>
          <section className="approval-portal__metrics"><article><small>Pending decisions</small><strong>{governance.pendingApprovals.length}</strong><span>Need a second role</span></article><article><small>Value awaiting review</small><strong>{formatMoney(String(pendingAmount))} USDC</strong><span>Not yet authorized</span></article><article><small>Current no-touch cap</small><strong>{governance.activePolicy ? formatMoney(governance.activePolicy.maximum_autonomous_payment_usdc) : '—'} USDC</strong><span>Active policy; sealed requests keep their original policy</span></article></section>
          <div className="approval-portal__workspace">
            <aside className="approval-queue" aria-label="Pending approval requests">
              <header><strong>Requests</strong><span>{governance.pendingApprovals.length} pending</span></header>
              {governance.pendingApprovals.length ? governance.pendingApprovals.map((item) => <button type="button" key={item.approval.id} className={selected?.approval.id === item.approval.id ? 'is-selected' : ''} onClick={() => { setSelectedId(item.approval.id); setRejectMode(false); setResolutionNote(defaultApprovalNote); }}><span className="approval-queue__status"><i />Awaiting review</span><strong>{item.invoice.invoice_number}</strong><small>{item.invoice.vendor_id.replaceAll('-', ' ')} · due {item.invoice.due_date}</small><b>{formatMoney(item.invoice.amount)} {item.invoice.currency}</b><em>{item.decision.reason_codes.join(' · ') || 'POLICY_EXCEPTION'}</em></button>) : <div className="approval-queue__empty"><CheckmarkFilled size={22} /><strong>No payments await approval.</strong><span>New policy exceptions submitted by users and processed by finance will appear here.</span></div>}
              {lastResolution ? <article className={`approval-queue__resolved is-${lastResolution.approval.status.toLowerCase()}`}><span>Most recent decision</span><strong>{lastResolution.invoice.invoice_number}</strong><small>{lastResolution.approval.status} · {lastResolution.approval.resolution_note}</small></article> : null}
            </aside>
            <section className="approval-packet">
              {detail ? <>
                <header><div><span>SEALED PAYMENT REQUEST</span><h2>{detail.invoice.invoice_number}</h2><p>{detail.invoice.vendor_id.replaceAll('-', ' ')} · submitted by {detail.approval.requested_by_user_id ?? 'finance operator'}</p></div><div className={`approval-packet__decision is-${detail.approval.status.toLowerCase()}`}><strong>{detail.approval.status === 'PENDING' ? ACTION_LABEL[detail.decision.final_action] : detail.approval.status}</strong><small>{detail.decision.policy_version}</small></div></header>
                <div className="approval-packet__facts"><div><span>Amount</span><strong>{formatMoney(detail.invoice.amount)} {detail.invoice.currency}</strong></div><div><span>Vendor wallet</span><code>{shorten(detail.invoice.payment_wallet_address, 10, 8)}</code></div><div><span>Due date</span><strong>{detail.invoice.due_date}</strong></div><div><span>Decision ID</span><code>{shorten(detail.decision.id, 10, 8)}</code></div></div>
                <div className="approval-packet__checks"><header><strong>Evidence and policy checks</strong><button type="button" onClick={() => onOpenReview(detail)}>Open full invoice review <ArrowRight size={14} /></button></header>{detail.decision.rules.slice(0, 5).map((rule, index) => <article key={`${rule.code}-${index}`}><span className={rule.disposition === 'PASS' ? 'is-pass' : 'is-exception'}>{rule.disposition === 'PASS' ? '✓' : '!'}</span><div><strong>{rulePresentation(rule).title}</strong><p>{rule.message}</p></div><em>{rule.disposition === 'PASS' ? 'Passed' : rule.disposition}</em></article>)}</div>
                {detail.approval.status === 'PENDING' ? <div className="approval-resolution"><label><span>{rejectMode ? 'Rejection reason' : 'Decision note'}</span><textarea value={resolutionNote} placeholder={rejectMode ? 'Explain what finance must correct before resubmitting…' : 'Record what you verified before approving…'} onChange={(event) => setResolutionNote(event.target.value)} /></label>{rejectMode && resolutionNote.trim().length < 12 ? <small>A clear rejection reason of at least 12 characters is required.</small> : null}<div><button type="button" className="approval-reject" disabled={Boolean(busy) || (rejectMode && !canReject)} onClick={() => decide(false)}>{rejectMode ? 'Confirm rejection' : 'Reject request'}</button><button type="button" className="approval-approve" disabled={rejectMode ? Boolean(busy) : !canApprove} onClick={() => { if (rejectMode) { setRejectMode(false); setResolutionNote(defaultApprovalNote); return; } decide(true); }}>{rejectMode ? 'Keep reviewing' : <>Approve payment <ArrowRight size={15} /></>}</button></div></div> : <div className={`approval-resolution__result is-${detail.approval.status.toLowerCase()}`}><span>{approved ? '✓' : rejected ? '×' : '•'}</span><div><strong>{approved ? 'Payment request approved' : rejected ? 'Payment request rejected' : detail.approval.status}</strong><p>{detail.approval.resolution_note ?? 'No resolution note was recorded.'}</p><small>{detail.approval.resolved_by_user_id ? `Resolved by ${detail.approval.resolved_by_user_id}` : 'Independent approver'}</small></div></div>}
              </> : <div className="approval-packet__empty"><CheckmarkFilled size={30} /><h2>Approval queue is clear.</h2><p>Only user requests that require independent finance authority appear here.</p><button type="button" onClick={() => onNavigate('finance')}>Open payment requests</button></div>}
            </section>
          </div>
        </main>
      </div>
      <PaymentLifecycle run={detailRun} approval={detail?.approval ?? null} payment={null} busy={busy} />
    </div>
  );
}

function StatusTag({ action }: { action: DecisionAction }) {
  return <Tag type={ACTION_TAG[action]}>{ACTION_LABEL[action]}</Tag>;
}

function RuleTag({ disposition }: { disposition: RuleDisposition }) {
  const presentation: Record<RuleDisposition, { type: 'green' | 'red' | 'magenta' | 'purple' | 'blue'; label: string }> = {
    PASS: { type: 'green', label: 'Passed' },
    HOLD: { type: 'magenta', label: 'Hold' },
    REJECT: { type: 'red', label: 'Rejected' },
    ESCALATE: { type: 'purple', label: 'Escalate' },
    SCHEDULE: { type: 'blue', label: 'Scheduled' },
  };
  const item = presentation[disposition];
  return <Tag type={item.type}>{item.label}</Tag>;
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="metric">
      <span className="metric__label">{label}</span>
      <strong>{value}</strong>
      <span className="metric__detail">{detail}</span>
    </div>
  );
}

function OperationsBand({ overview, sessionEvaluations }: { overview: OperationsOverview; sessionEvaluations: number }) {
  const projected = overview.projected_after_open_usdc;
  const snapshot = overview.treasury_available_usdc;
  const committed = overview.treasury_committed_since_snapshot_usdc;
  return (
    <div className="metrics-band" aria-label="Persistent finance operations summary">
      <Metric label="Open exposure" value={`${formatMoney(overview.open_exposure_usdc)} USDC`} detail={`${overview.invoice_count} durable invoices · ${sessionEvaluations} this session`} />
      <Metric label="Blocked value" value={`${formatMoney(overview.blocked_exposure_usdc)} USDC`} detail="Held, rejected, or approval-gated" />
      <Metric label="Due within 7 days" value={`${formatMoney(overview.due_next_7_days_usdc)} USDC`} detail={`${overview.due_next_7_days_count} invoices · ${overview.overdue_count} overdue`} />
      <Metric
        label="Projected liquidity"
        value={projected === null ? 'Awaiting treasury' : `${formatMoney(projected)} USDC`}
        detail={snapshot === null || committed === null
          ? 'Record a treasury snapshot'
          : `${formatMoney(snapshot)} snapshot · ${formatMoney(committed)} committed`}
      />
    </div>
  );
}

function ReliabilityPanel({ evidence }: { evidence: ReliabilityEvidence }) {
  const { report, artifact, agent_report: agentReport, agent_artifact: agentArtifact } = evidence;
  const settlementLatency = report.latency_ms.by_request['payment.settle'];
  const isolationLatency = report.latency_ms.by_request['security.cross_tenant_read'];
  return (
    <section className="reliability-panel" aria-label="Synthetic multi-tenant reliability evidence">
      <div className="reliability-panel__head">
        <div>
          <span className="eyebrow">Checked-in reliability evidence</span>
          <h2>10,000 workflows. Zero duplicate payments.</h2>
        </div>
        <Tag type="purple">Synthetic engineering test</Tag>
      </div>
      <div className="reliability-grid">
        <div><strong>{report.summary.successful_workflows.toLocaleString()}</strong><span>successful workflows</span><small>{report.configuration.organizations} isolated organizations · {report.configuration.concurrency} workers</small></div>
        <div><strong>{report.summary.http_requests.toLocaleString()}</strong><span>real HTTP requests</span><small>{report.summary.workflow_throughput_per_second.toFixed(3)} workflows / second</small></div>
        <div><strong>{report.summary.cross_tenant_attempts_denied}/{report.summary.cross_tenant_attempts}</strong><span>cross-tenant reads denied</span><small>P95 {isolationLatency?.p95.toFixed(0) ?? '—'} ms</small></div>
        <div><strong>{report.summary.duplicate_storm_requests} → {report.summary.duplicate_storm_provider_submissions}</strong><span>retry storm convergence</span><small>{report.configuration.slow_provider_delay_ms} ms provider delay · {report.summary.duplicate_storm_unique_transaction_hashes} transaction hash · {report.summary.slow_provider_idempotency_preserved ? 'preserved' : 'review required'}</small></div>
        <div><strong>{report.summary.treasury_contention_successes}/{report.summary.treasury_contention_requests}</strong><span>atomic reservations admitted</span><small>{report.summary.treasury_contention_denied} over-limit payments blocked · {report.summary.treasury_atomic_limit_preserved ? 'limit preserved' : 'review required'}</small></div>
      </div>
      <div className="reliability-latency">
        <div><span>Overall P95</span><strong>{report.latency_ms.overall.p95.toFixed(0)} ms</strong></div>
        <div><span>Settlement P95</span><strong>{settlementLatency?.p95.toFixed(0) ?? '—'} ms</strong></div>
        <div><span>Failed workflows</span><strong>{report.summary.failed_workflows}</strong></div>
        <div><span>Artifact proof</span><code>{shorten(artifact.sha256, 12, 10)}</code></div>
      </div>
      <div className="reliability-agent">
        <div className="reliability-agent__title">
          <span className="eyebrow">Agent orchestration stress</span>
          <strong>50 tenants planned and executed mixed AP queues in parallel.</strong>
          <small>Every run exported a verified proof packet. One hundred duplicate execute calls competed for one durable lease.</small>
        </div>
        <div><strong>{agentReport.summary.successful_agent_workflows}/{agentReport.configuration.organizations}</strong><span>agent runs passed</span><small>{agentReport.summary.mixed_queue_items} governed queue items</small></div>
        <div><strong>{agentReport.summary.cross_tenant_attempts_denied}/{agentReport.summary.cross_tenant_attempts}</strong><span>foreign proofs denied</span><small>{agentReport.summary.verified_proof_packets} packets verified</small></div>
        <div><strong>{agentReport.summary.duplicate_execution_requests} → {agentReport.summary.duplicate_execution_claim_winners}</strong><span>execution lease winners</span><small>{agentReport.summary.duplicate_execution_provider_submissions} provider submission</small></div>
        <div><strong>{agentReport.summary.protected_non_settlements}</strong><span>unsafe actions blocked</span><small>{agentReport.summary.approval_routes} routed for human approval</small></div>
      </div>
      <div className="reliability-panel__foot">
        <div><CheckmarkFilled size={16} /><span>Two immutable reports · workflow {shorten(artifact.sha256, 8, 6)} · agent {shorten(agentArtifact.sha256, 8, 6)}</span></div>
        <p>{report.methodology.note} {report.methodology.settlement}.</p>
      </div>
    </section>
  );
}

const AGENT_ACTION_LABEL: Record<AgentAction, string> = {
  SETTLE: 'Settle now',
  SETTLE_APPROVED: 'Settle approved',
  RETRY_SETTLEMENT: 'Recover payment',
  RELEASE_SCHEDULE: 'Release schedule',
  WAIT_SCHEDULE: 'Wait for schedule',
  REQUIRE_APPROVAL: 'Route to approver',
  REMEDIATE: 'Remediate evidence',
  INVESTIGATE: 'Manual investigation',
  COLLECT_EVIDENCE: 'Complete evidence',
};

function agentActionTag(action: AgentAction) {
  if (action === 'SETTLE' || action === 'SETTLE_APPROVED' || action === 'RETRY_SETTLEMENT' || action === 'RELEASE_SCHEDULE') return 'green' as const;
  if (action === 'INVESTIGATE') return 'red' as const;
  if (action === 'REQUIRE_APPROVAL') return 'purple' as const;
  if (action === 'REMEDIATE') return 'magenta' as const;
  return 'blue' as const;
}

function AutonomousRunPanel({
  run,
  busy,
  settlementStopped,
  onPlan,
  onExecute,
  onDownloadProof,
  onSeedShowcase,
  proofHash,
}: {
  run: AgentRun | null;
  busy: boolean;
  settlementStopped: boolean;
  onPlan: () => void;
  onExecute: () => void;
  onDownloadProof: () => void;
  onSeedShowcase: () => void;
  proofHash: string | null;
}) {
  const resultByInvoice = new Map(run?.results.map((result) => [result.invoice_id, result]) ?? []);
  return (
    <section className="agent-run" aria-label="Bounded autonomous accounts payable run">
      <div className="agent-run__lead">
        <div>
          <span className="eyebrow">Bounded autonomy / durable plan</span>
          <h2>One agent run. Every control rechecked.</h2>
          <p>The agent scans the tenant queue, explains each next action, and can route approvals, release due schedules, or settle only after the relevant controls pass. Amounts, recipients, and authority always come from sealed records.</p>
        </div>
        <div className="agent-run__actions">
          {run ? <Tag type={run.status === 'EXECUTED' ? 'green' : run.status === 'PARTIAL' ? 'warm-gray' : 'cyan'}>{run.status}</Tag> : <Tag type="cool-gray">No plan yet</Tag>}
          <Button size="sm" kind="tertiary" renderIcon={Rule} disabled={busy} onClick={onPlan}>
            {run ? 'Plan current queue' : 'Plan first run'}
          </Button>
          <Button size="sm" kind="tertiary" renderIcon={Renew} disabled={busy} onClick={onSeedShowcase}>
            Load mixed queue
          </Button>
          {run?.status === 'PLANNED' && run.summary.executable > 0 ? (
            <Button size="sm" renderIcon={PlayFilled} disabled={busy || settlementStopped} onClick={onExecute}>
              Execute {run.summary.executable} safe action{run.summary.executable === 1 ? '' : 's'}
            </Button>
          ) : null}
          {run ? (
            <Button size="sm" kind="ghost" renderIcon={Download} disabled={busy} onClick={onDownloadProof}>
              Export proof
            </Button>
          ) : null}
        </div>
      </div>
      {run ? (
        <>
          <div className="agent-run__proof">
            <div><span>Queue scanned</span><strong>{run.summary.scanned}</strong></div>
            <div><span>Executable</span><strong>{run.summary.executable}</strong></div>
            <div><span>Human attention</span><strong>{run.summary.requires_attention}</strong></div>
            <div><span>Plan proof</span><code>{shorten(run.plan_hash, 12, 10)}</code></div>
          </div>
          {run.items.length === 0 ? (
            <div className="agent-run__empty"><CheckmarkFilled size={20} /><span>No open work entered this bounded run.</span></div>
          ) : (
            <div className="agent-plan-list">
              {run.items.map((item, index) => {
                const result = resultByInvoice.get(item.invoice_id);
                return (
                  <article key={item.invoice_id}>
                    <span className="agent-plan-list__index">{String(index + 1).padStart(2, '0')}</span>
                    <div className="agent-plan-list__identity">
                      <strong>{item.invoice_number}</strong>
                      <small>{formatMoney(item.amount_usdc)} USDC · due {item.due_date}</small>
                    </div>
                    <div className="agent-plan-list__reason">
                      <strong>{item.reason_code.replaceAll('_', ' ')}</strong>
                      <span>{item.explanation}</span>
                    </div>
                    <div className="agent-plan-list__state">
                      <Tag type={agentActionTag(item.action)}>{AGENT_ACTION_LABEL[item.action]}</Tag>
                      {result ? <small className={`agent-result agent-result--${result.status.toLowerCase()}`}>{result.status}</small> : <small>{item.executable ? 'Revalidated at execution' : 'No funds authority'}</small>}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
          <div className="agent-run__boundary"><Locked size={16} /><span>{proofHash ? `Proof ${shorten(proofHash, 12, 10)} · ` : ''}Plan {shorten(run.id, 14, 8)} · state {shorten(run.state_hash, 12, 10)} · execution cannot override deterministic policy.</span></div>
        </>
      ) : (
        <div className="agent-run__empty"><Rule size={20} /><span>Create a plan to turn the current work queue into a traceable sequence of safe actions and human handoffs.</span></div>
      )}
    </section>
  );
}

function OperationsQueue({
  overview,
  busy,
  batch,
  scheduleRun,
  settlementStopped,
  ledgerExport,
  onExportLedger,
  onSettleBatch,
  onRunSchedules,
}: {
  overview: OperationsOverview;
  busy: boolean;
  batch: PaymentBatch | null;
  scheduleRun: ScheduleRun | null;
  settlementStopped: boolean;
  ledgerExport: { hash: string; rows: number } | null;
  onExportLedger: () => void;
  onSettleBatch: (items: SettlementBatchItem[]) => void;
  onRunSchedules: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [queueQuery, setQueueQuery] = useState('');
  const [queueView, setQueueView] = useState('ALL');
  const today = overview.as_of.slice(0, 10);
  const visibleQueue = overview.work_queue.filter((invoice) => {
    const searchable = `${invoice.invoice_number} ${invoice.vendor_id} ${invoice.id}`.toLowerCase();
    if (queueQuery.trim() && !searchable.includes(queueQuery.trim().toLowerCase())) return false;
    if (queueView === 'PAYABLE') {
      return invoice.status === 'READY' || (invoice.status === 'SUBMISSION_FAILED' && invoice.settlement_retryable);
    }
    if (queueView === 'ATTENTION') {
      return ['HOLD', 'ESCALATED', 'SUBMISSION_FAILED'].includes(invoice.status);
    }
    if (queueView === 'OVERDUE') return invoice.due_date < today;
    if (queueView === 'SCHEDULED') return invoice.status === 'SCHEDULED';
    return true;
  });
  const payable = visibleQueue.filter(isSettlementReady);
  const scheduled = overview.work_queue.filter((invoice) => invoice.status === 'SCHEDULED');
  useEffect(() => {
    const available = new Set(payable.map((invoice) => invoice.id));
    setSelected((current) => new Set([...current].filter((invoiceId) => available.has(invoiceId))));
  }, [overview]);
  const selectedItems = payable
    .filter((invoice) => selected.has(invoice.id))
    .map((invoice) => ({
      invoice_id: invoice.id,
      decision_id: invoice.decision_id!,
      ...(invoice.approval_reference ? { approval_reference: invoice.approval_reference } : {}),
    }));
  const toggle = (invoiceId: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(invoiceId)) next.delete(invoiceId); else next.add(invoiceId);
      return next;
    });
  };
  return (
    <section className="operations-queue" aria-label="Invoice work queue">
      <div className="operations-queue__head">
        <div><span className="eyebrow">Persistent operations</span><h2>Invoice work queue</h2></div>
        <div className="operations-queue__actions">
          <Tag type={overview.overdue_count > 0 ? 'red' : 'cool-gray'}>{overview.overdue_count} overdue</Tag>
          <Button size="sm" kind="ghost" renderIcon={Download} disabled={busy} onClick={onExportLedger}>
            Export ledger
          </Button>
          {scheduled.length > 0 ? (
            <Button size="sm" kind="tertiary" renderIcon={Time} disabled={busy} onClick={onRunSchedules}>
              Check {scheduled.length} schedule{scheduled.length === 1 ? '' : 's'}
            </Button>
          ) : null}
          {payable.length > 0 ? (
            <Button
              size="sm"
              renderIcon={Money}
              disabled={busy || settlementStopped || selectedItems.length === 0}
              onClick={() => onSettleBatch(selectedItems)}
            >
              Settle {selectedItems.length || ''} selected
            </Button>
          ) : null}
        </div>
      </div>
      {overview.work_queue.length > 0 ? (
        <div className="operations-queue__filters">
          <Search
            id="operations-queue-query"
            labelText="Search invoice queue"
            placeholder="Invoice, vendor, or record ID"
            value={queueQuery}
            onChange={(event) => setQueueQuery(event.target.value)}
          />
          <Select id="operations-queue-view" labelText="Queue view" value={queueView} onChange={(event) => setQueueView(event.target.value)}>
            <SelectItem value="ALL" text="All open work" />
            <SelectItem value="PAYABLE" text="Payable now" />
            <SelectItem value="ATTENTION" text="Needs attention" />
            <SelectItem value="OVERDUE" text="Overdue" />
            <SelectItem value="SCHEDULED" text="Scheduled" />
          </Select>
          <span>{visibleQueue.length} of {overview.work_queue.length} shown</span>
        </div>
      ) : null}
      {ledgerExport ? (
        <div className="ledger-export-proof">
          <CheckmarkFilled size={16} />
          <span>{ledgerExport.rows} reconciled row{ledgerExport.rows === 1 ? '' : 's'} · SHA-256 {shorten(ledgerExport.hash, 12, 10)}</span>
        </div>
      ) : null}
      {batch ? (
        <InlineNotification
          className="batch-result"
          kind={batch.failed > 0 ? 'warning' : 'success'}
          title={`${batch.succeeded}/${batch.requested} batch payments reconciled`}
          subtitle={batch.failed > 0 ? `${batch.failed} item failed independently; successful receipts remain final and retry-safe.` : 'Every selected invoice produced or reused one durable receipt.'}
          lowContrast
          hideCloseButton
        />
      ) : null}
      {scheduleRun ? (
        <InlineNotification
          className="batch-result"
          kind={scheduleRun.failed > 0 ? 'warning' : scheduleRun.settled > 0 ? 'success' : 'info'}
          title={`${scheduleRun.settled} settled · ${scheduleRun.waiting} waiting · ${scheduleRun.revalidated} revalidated`}
          subtitle={`Schedule runner checked ${scheduleRun.scanned} tenant-scoped invoice${scheduleRun.scanned === 1 ? '' : 's'} on ${scheduleRun.evaluated_on}; every due item was re-evaluated against current controls before settlement.`}
          lowContrast
          hideCloseButton
        />
      ) : null}
      {overview.work_queue.length === 0 ? (
        <div className="operations-queue__empty"><CheckmarkFilled size={20} /><span>No open invoices. Run a control case or upload evidence to populate the durable queue.</span></div>
      ) : visibleQueue.length === 0 ? (
        <div className="operations-queue__empty"><CheckmarkFilled size={20} /><span>No invoice matches this queue view. Clear the search or choose another operational filter.</span></div>
      ) : (
        <div className="operations-table" role="table" aria-label="Open invoices sorted by due date">
          <div className="operations-table__head" role="row">
            <span role="columnheader">Select</span><span role="columnheader">Invoice</span><span role="columnheader">Vendor</span><span role="columnheader">Due</span><span role="columnheader">Exposure</span><span role="columnheader">State</span>
          </div>
          {visibleQueue.map((invoice) => (
            <div className="operations-table__row" role="row" key={invoice.id}>
              <label className="batch-select" title={invoice.status === 'READY' || invoice.settlement_retryable ? 'Select for idempotent settlement or retry' : 'This incident is locked for manual investigation'}>
                <input
                  aria-label={`Select ${invoice.invoice_number} for batch settlement`}
                  type="checkbox"
                  checked={selected.has(invoice.id)}
                  disabled={busy || !(invoice.status === 'READY' || invoice.settlement_retryable) || !invoice.decision_id}
                  onChange={() => toggle(invoice.id)}
                />
              </label>
              <div role="cell"><strong>{invoice.invoice_number}</strong><code>{shorten(invoice.id, 12, 6)}</code></div>
              <code role="cell">{shorten(invoice.vendor_id, 13, 6)}</code>
              <span className="schedule-date" role="cell">{invoice.due_date}{invoice.scheduled_for ? <small>release {invoice.scheduled_for}</small> : null}</span>
              <strong role="cell">{formatMoney(invoice.amount)} {invoice.currency}</strong>
              <Tag type={invoice.status === 'READY' ? 'green' : invoice.status === 'SUBMISSION_FAILED' ? 'red' : invoice.status === 'HOLD' ? 'magenta' : invoice.status === 'ESCALATED' ? 'purple' : 'blue'}>{invoice.status}</Tag>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function SettlementIncidentCenter({ overview }: { overview: SettlementIncidentOverview }) {
  const stateTag = (state: SettlementIncidentOverview['items'][number]['state']) => {
    if (state === 'RESOLVED') return <Tag type="green">Resolved</Tag>;
    if (state === 'OPEN_RETRYABLE') return <Tag type="warm-gray">Safe retry</Tag>;
    return <Tag type="red">Locked</Tag>;
  };
  return (
    <section className="incident-center" aria-label="Settlement incidents and recovery status">
      <div className="incident-center__head">
        <div><span className="eyebrow">Provider recovery ledger</span><h2>Settlement exception center</h2></div>
        <div className="incident-summary" aria-label="Incident counts">
          <span><strong>{overview.summary.open_retryable}</strong> retryable</span>
          <span><strong>{overview.summary.locked}</strong> locked</span>
          <span><strong>{overview.summary.resolved}</strong> recovered</span>
        </div>
      </div>
      {overview.items.length === 0 ? (
        <div className="incident-empty"><CheckmarkFilled size={20} /><span>No settlement incidents have been recorded.</span></div>
      ) : (
        <div className="incident-list">
          {overview.items.slice(0, 6).map((incident) => (
            <article className="incident-card" key={incident.payment_intent_id}>
              <div className="incident-card__state">{stateTag(incident.state)}<span>{incident.attempt_count} attempt{incident.attempt_count === 1 ? '' : 's'}</span></div>
              <div className="incident-card__identity">
                <strong>{incident.invoice.invoice_number}</strong>
                <span>{formatMoney(incident.invoice.amount)} {incident.invoice.currency} · {incident.provider}</span>
              </div>
              <p>{incident.failed_attempt.error_message ?? 'Provider evidence requires manual review.'}</p>
              <div className="incident-card__proof">
                <span>Intent fingerprint</span><code>{shorten(incident.idempotency_fingerprint, 12, 10)}</code>
                <span>Correlation</span><code>{shorten(incident.failed_attempt.correlation_id, 12, 8)}</code>
              </div>
              <small>{incident.state === 'OPEN_RETRYABLE' ? 'The same durable payment intent may be retried; no new intent will be created.' : incident.state === 'RESOLVED' ? 'Recovered with one confirmed receipt and the original payment intent.' : 'Automatic retry is disabled. Investigate provider evidence before any manual action.'}</small>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function GovernancePanel({
  governance,
  policyHistory,
  treasurySnapshot,
  busy,
  policyActivation,
  onActivatePolicy,
  onRecordTreasury,
}: {
  governance: GovernanceOverview;
  policyHistory: ActivePolicy[];
  treasurySnapshot: TreasurySnapshotRecord | null;
  busy: boolean;
  policyActivation: PolicyActivation | null;
  onActivatePolicy: (draft: PolicyDraft) => void;
  onRecordTreasury: (availableUsdc: string, spentTodayUsdc: string, sourceReference: string) => void;
}) {
  const policy = governance.activePolicy;
  const capacity = governance.settlementCapacity;
  const defaultDraft = useCallback((): PolicyDraft => ({
    daily_payment_limit_usdc: policy?.daily_payment_limit_usdc ?? '5000',
    daily_autonomous_payment_limit_usdc: policy?.daily_autonomous_payment_limit_usdc ?? '1000',
    autonomous_payments_enabled: policy?.autonomous_payments_enabled ?? false,
    minimum_cash_reserve_usdc: policy?.minimum_cash_reserve_usdc ?? '3000',
    maximum_autonomous_payment_usdc: policy?.maximum_autonomous_payment_usdc ?? '300',
    po_amount_tolerance_usdc: policy?.po_amount_tolerance_usdc ?? '0',
    allowed_asset: policy?.allowed_asset ?? 'USDC',
    allowed_network: policy?.allowed_network ?? 'ARC-TESTNET',
    kill_switch_enabled: policy?.kill_switch_enabled ?? false,
    schedule_payments_before_due_days: policy?.schedule_payments_before_due_days ?? null,
  }), [policy]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [treasuryOpen, setTreasuryOpen] = useState(false);
  const [treasuryAvailable, setTreasuryAvailable] = useState(capacity?.snapshot_available_usdc ?? '10000');
  const [treasurySpent, setTreasurySpent] = useState(capacity?.snapshot_spent_today_usdc ?? '0');
  const [treasuryReference, setTreasuryReference] = useState('finance-ledger-attestation');
  const [draft, setDraft] = useState<PolicyDraft>(defaultDraft);
  useEffect(() => { setDraft(defaultDraft()); }, [defaultDraft]);
  useEffect(() => {
    if (!capacity) return;
    setTreasuryAvailable(capacity.snapshot_available_usdc);
    setTreasurySpent(capacity.snapshot_spent_today_usdc);
  }, [capacity]);
  const updateMoney = (field: keyof Pick<PolicyDraft, 'daily_payment_limit_usdc' | 'daily_autonomous_payment_limit_usdc' | 'minimum_cash_reserve_usdc' | 'maximum_autonomous_payment_usdc' | 'po_amount_tolerance_usdc'>, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
  };
  const changedCount = policy
    ? [
        policy.daily_payment_limit_usdc !== draft.daily_payment_limit_usdc,
        policy.daily_autonomous_payment_limit_usdc !== draft.daily_autonomous_payment_limit_usdc,
        policy.autonomous_payments_enabled !== draft.autonomous_payments_enabled,
        policy.minimum_cash_reserve_usdc !== draft.minimum_cash_reserve_usdc,
        policy.maximum_autonomous_payment_usdc !== draft.maximum_autonomous_payment_usdc,
        policy.po_amount_tolerance_usdc !== draft.po_amount_tolerance_usdc,
        policy.kill_switch_enabled !== draft.kill_switch_enabled,
        policy.schedule_payments_before_due_days !== draft.schedule_payments_before_due_days,
      ].filter(Boolean).length
    : 1;
  const canActivate = changedCount > 0 && [
    draft.daily_payment_limit_usdc,
    draft.daily_autonomous_payment_limit_usdc,
    draft.minimum_cash_reserve_usdc,
    draft.maximum_autonomous_payment_usdc,
    draft.po_amount_tolerance_usdc,
  ].every((value) => value.trim() !== '' && Number.isFinite(Number(value)) && Number(value) >= 0);
  const canRecordTreasury = [treasuryAvailable, treasurySpent].every((value) => value.trim() !== '' && Number.isFinite(Number(value)) && Number(value) >= 0) && treasuryReference.trim().length > 0;
  return (
    <section className="governance-panel" aria-label="Payment policy governance">
      <div className="governance-policy">
        <div className="governance-panel__head">
          <div><span className="eyebrow">Control governance</span><h2>Current payment authority</h2></div>
          <Tag type={policy?.kill_switch_enabled ? 'red' : policy ? 'green' : 'cool-gray'}>
            {policy?.kill_switch_enabled ? 'KILL SWITCH ACTIVE' : policy ? 'CONTROLS ACTIVE' : 'NO ACTIVE POLICY'}
          </Tag>
        </div>
        {policy ? (
          <div className="governance-policy__grid">
            <div><span>Policy</span><code>{policy.version}</code><small>{shorten(policy.content_hash, 10, 8)}</small></div>
            <div><span>No-touch settlement</span><strong>{policy.autonomous_payments_enabled ? 'Enabled by finance' : 'Disabled'}</strong><small>{policy.autonomous_payments_enabled ? `${formatMoney(policy.maximum_autonomous_payment_usdc)} USDC per payment` : 'Every payment requires finance approval'}</small></div>
            <div><span>Daily no-touch ceiling</span><strong>{formatMoney(policy.daily_autonomous_payment_limit_usdc)} USDC</strong><small>{formatMoney(policy.daily_payment_limit_usdc)} USDC hard daily limit</small></div>
            <div><span>Settlement route</span><strong>{policy.allowed_asset} · {policy.allowed_network}</strong><small>{policy.schedule_payments_before_due_days === null ? 'Immediate timing allowed' : `Release ${policy.schedule_payments_before_due_days} days before due`}</small></div>
          </div>
        ) : (
          <p className="governance-empty">Run a control case or create a production policy to establish the tenant's payment authority.</p>
        )}
        {capacity ? (
          <div className="settlement-capacity" aria-label="Durable settlement capacity">
            <div className="settlement-capacity__head">
              <div><span>Atomic treasury reservation</span><strong>{formatMoney(capacity.maximum_new_payment_usdc)} USDC available now</strong></div>
              <Tag type={capacity.snapshot_fresh ? 'green' : 'red'}>{capacity.snapshot_fresh ? 'SNAPSHOT FRESH' : 'SNAPSHOT STALE'}</Tag>
            </div>
            <div className="settlement-capacity__grid">
              <div><span>Observed balance</span><strong>{formatMoney(capacity.snapshot_available_usdc)}</strong></div>
              <div><span>Durably committed</span><strong>{formatMoney(capacity.committed_since_snapshot_usdc)}</strong></div>
              <div><span>Daily headroom</span><strong>{formatMoney(capacity.daily_remaining_usdc)}</strong></div>
              <div><span>No-touch headroom</span><strong>{formatMoney(capacity.autonomous_daily_remaining_usdc)}</strong></div>
              <div><span>Reserve floor</span><strong>{formatMoney(capacity.minimum_cash_reserve_usdc)}</strong></div>
            </div>
            <small>Snapshot #{capacity.treasury_snapshot_sequence} · {capacity.snapshot_age_seconds}s old. Competing workers reserve inside one database transaction before any provider call.</small>
          </div>
        ) : null}
        <button className="policy-editor__toggle treasury-toggle" type="button" onClick={() => setTreasuryOpen((open) => !open)}>
          <span>{treasuryOpen ? 'Close treasury attestation' : 'Record treasury snapshot'}</span>
          <span aria-hidden="true">{treasuryOpen ? '−' : '+'}</span>
        </button>
        {treasuryOpen ? (
          <div className="treasury-recorder">
            <div className="policy-editor__notice"><Money size={16} /><span>Finance operators attest observed USDC balance and spend. Settlement capacity subtracts durable commitments atomically.</span></div>
            <div className="treasury-recorder__fields">
              <label><span>Available USDC</span><input type="number" min="0" step="0.01" value={treasuryAvailable} onChange={(event) => setTreasuryAvailable(event.target.value)} /></label>
              <label><span>Spent today</span><input type="number" min="0" step="0.01" value={treasurySpent} onChange={(event) => setTreasurySpent(event.target.value)} /></label>
              <label><span>Source reference</span><input value={treasuryReference} onChange={(event) => setTreasuryReference(event.target.value)} /></label>
              <Button size="sm" disabled={busy || !canRecordTreasury} onClick={() => onRecordTreasury(treasuryAvailable, treasurySpent, treasuryReference)}>Record snapshot</Button>
            </div>
            {treasurySnapshot ? <small className="treasury-recorder__receipt"><CheckmarkFilled size={15} /> Snapshot #{treasurySnapshot.sequence} recorded from {treasurySnapshot.source_reference} at {new Date(treasurySnapshot.recorded_at).toLocaleString()}.</small> : null}
          </div>
        ) : null}
        <button className="policy-editor__toggle" type="button" onClick={() => setEditorOpen((open) => !open)}>
          <span>{editorOpen ? 'Close policy change' : 'Propose immutable policy version'}</span>
          <span aria-hidden="true">{editorOpen ? '−' : '+'}</span>
        </button>
        {editorOpen ? (
          <div className="policy-editor">
            <div className="policy-editor__notice">
              <Locked size={16} />
              <span>A new content-addressed version becomes active. Historical decisions remain bound to their original policy.</span>
            </div>
            <div className="policy-editor__fields">
              <label className="policy-editor__switch"><input type="checkbox" checked={draft.autonomous_payments_enabled} onChange={(event) => setDraft((current) => ({ ...current, autonomous_payments_enabled: event.target.checked }))} /><span>Enable AI no-touch settlement</span></label>
              <label><span>No-touch payment cap (USDC)</span><input type="number" min="0" step="0.01" value={draft.maximum_autonomous_payment_usdc} onChange={(event) => updateMoney('maximum_autonomous_payment_usdc', event.target.value)} /></label>
              <label><span>Daily no-touch ceiling (USDC)</span><input type="number" min="0" step="0.01" value={draft.daily_autonomous_payment_limit_usdc} onChange={(event) => updateMoney('daily_autonomous_payment_limit_usdc', event.target.value)} /></label>
              <label><span>Hard daily settlement limit (USDC)</span><input type="number" min="0" step="0.01" value={draft.daily_payment_limit_usdc} onChange={(event) => updateMoney('daily_payment_limit_usdc', event.target.value)} /></label>
              <label><span>Reserve floor (USDC)</span><input type="number" min="0" step="0.01" value={draft.minimum_cash_reserve_usdc} onChange={(event) => updateMoney('minimum_cash_reserve_usdc', event.target.value)} /></label>
              <label><span>PO tolerance (USDC)</span><input type="number" min="0" step="0.01" value={draft.po_amount_tolerance_usdc} onChange={(event) => updateMoney('po_amount_tolerance_usdc', event.target.value)} /></label>
              <label><span>Schedule lead (days)</span><input type="number" min="0" step="1" value={draft.schedule_payments_before_due_days ?? ''} placeholder="Immediate" onChange={(event) => setDraft((current) => ({ ...current, schedule_payments_before_due_days: event.target.value === '' ? null : Number(event.target.value) }))} /></label>
              <label className="policy-editor__switch"><input type="checkbox" checked={draft.kill_switch_enabled} onChange={(event) => setDraft((current) => ({ ...current, kill_switch_enabled: event.target.checked }))} /><span>Emergency settlement kill switch</span></label>
            </div>
            <div className="policy-editor__review">
              <div><span>Scope</span><strong>{draft.allowed_asset} · {draft.allowed_network}</strong></div>
              <div><span>Review</span><strong>{changedCount} proposed change{changedCount === 1 ? '' : 's'}</strong></div>
              <Button size="sm" disabled={busy || !canActivate} onClick={() => onActivatePolicy(draft)}>Activate new version</Button>
            </div>
          </div>
        ) : null}
        {policyActivation ? (
          <div className="policy-change-result">
            <div><CheckmarkFilled size={18} /><span>Server-verified policy diff</span><code>{shorten(policyActivation.policy.content_hash, 10, 8)}</code></div>
            {policyActivation.changes.length > 0 ? policyActivation.changes.map((change) => (
              <p key={change.field}><strong>{change.field.replaceAll('_', ' ')}</strong><span>{String(change.before)}</span><ArrowRight size={14} /><span>{String(change.after)}</span></p>
            )) : <p><span>Initial tenant policy activated.</span></p>}
          </div>
        ) : null}
        {policyHistory.length > 0 ? (
          <div className="policy-history" aria-label="Immutable policy version history">
            <header><span>Immutable policy history</span><small>{policyHistory.length} version{policyHistory.length === 1 ? '' : 's'}</small></header>
            {policyHistory.slice(0, 5).map((item, index) => (
              <article key={item.version}>
                <span>{String(index + 1).padStart(2, '0')}</span>
                <div><strong>{item.version}</strong><small>{new Date(item.activated_at).toLocaleString()} · {item.activated_by_user_id}</small></div>
                <code>{shorten(item.content_hash, 10, 8)}</code>
                {item.version === policy?.version ? <Tag type="green">ACTIVE</Tag> : <Tag type="cool-gray">SEALED</Tag>}
              </article>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}

function VendorTrustPanel({
  records,
  activeInvoice,
  busy,
  onOpenEvidence,
  onOnboard,
  onRotateWallet,
}: {
  records: VendorTrustRecord[];
  activeInvoice: RunResult['invoice'] | null;
  busy: boolean;
  onOpenEvidence?: () => void;
  onOnboard: (draft: VendorOnboardingDraft) => void;
  onRotateWallet: (vendor: VendorTrustRecord['vendor'], draft: VendorWalletRotationDraft) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(records[0]?.vendor.id ?? null);
  useEffect(() => {
    if (!selectedId || !records.some((item) => item.vendor.id === selectedId)) {
      setSelectedId(records[0]?.vendor.id ?? null);
    }
  }, [records, selectedId]);
  const selected = records.find((item) => item.vendor.id === selectedId) ?? records[0] ?? null;
  const invoiceApplies = selected && activeInvoice?.vendor_id === selected.vendor.id;
  const walletMatches = invoiceApplies
    ? activeInvoice.payment_wallet_address.toLowerCase() === selected.vendor.approved_wallet_address.toLowerCase()
    : null;
  const latestWalletEvent = selected && selected.walletHistory.length > 0
    ? selected.walletHistory[selected.walletHistory.length - 1]
    : null;
  const cooldownUntil = latestWalletEvent?.event_type === 'REPLACED'
    ? new Date(new Date(latestWalletEvent.verified_at).getTime() + 2 * 24 * 60 * 60 * 1000)
    : null;
  const cooldownActive = cooldownUntil !== null && cooldownUntil.getTime() > Date.now();
  const [vendorAction, setVendorAction] = useState<'closed' | 'onboard' | 'rotate'>(records.length === 0 ? 'onboard' : 'closed');
  const [vendorDraft, setVendorDraft] = useState<VendorOnboardingDraft>({
    id: '',
    legal_name: '',
    approved_wallet_address: '',
    autopay_limit: '1000',
    risk_tier: 'standard',
    active: true,
    verification_method: 'SIGNED_CHALLENGE',
    verification_reference: '',
  });
  const [rotationDraft, setRotationDraft] = useState<VendorWalletRotationDraft>({
    new_wallet: '',
    verification_method: 'SIGNED_CHALLENGE',
    verification_reference: '',
  });
  const validWallet = (value: string) => /^0x[a-fA-F0-9]{40}$/.test(value);
  const canOnboard = vendorDraft.id.trim().length > 0 && vendorDraft.legal_name.trim().length > 0 && validWallet(vendorDraft.approved_wallet_address) && Number(vendorDraft.autopay_limit) >= 0 && vendorDraft.verification_reference.trim().length > 0;
  const canRotate = Boolean(selected) && validWallet(rotationDraft.new_wallet) && rotationDraft.verification_reference.trim().length > 0 && rotationDraft.new_wallet.toLowerCase() !== selected?.vendor.approved_wallet_address.toLowerCase();

  return (
    <section className="vendor-trust" aria-label="Vendor payout identity control center">
      <div className="vendor-trust__head">
        <div><span className="eyebrow">Payout identity controls</span><h2>Vendor trust directory</h2></div>
        <Tag type={records.length > 0 ? 'teal' : 'cool-gray'}>{records.length} verified vendor{records.length === 1 ? '' : 's'}</Tag>
      </div>
      {selected ? (
        <div className="vendor-trust__body">
          <div className="vendor-trust__list" role="list" aria-label="Tenant vendors">
            {records.map((item) => (
              <button
                type="button"
                role="listitem"
                className={item.vendor.id === selected.vendor.id ? 'is-active' : ''}
                key={item.vendor.id}
                onClick={() => setSelectedId(item.vendor.id)}
              >
                <span>{item.vendor.legal_name}</span>
                <small>{item.vendor.risk_tier} risk · {formatMoney(item.vendor.autopay_limit)} USDC cap</small>
              </button>
            ))}
          </div>
          <div className="vendor-trust__detail">
            <div className="vendor-identity">
              <div><span>Legal entity</span><strong>{selected.vendor.legal_name}</strong><small>{selected.vendor.id}</small></div>
              <Tag type={selected.vendor.active ? 'green' : 'red'}>{selected.vendor.active ? 'ACTIVE' : 'SUSPENDED'}</Tag>
            </div>
            <div className="vendor-wallet-proof">
              <div><span>Approved Arc payout wallet</span><code>{selected.vendor.approved_wallet_address}</code></div>
              {walletMatches === null ? (
                <Tag type="cool-gray">NO ACTIVE INVOICE</Tag>
              ) : (
                <Tag type={walletMatches ? 'green' : 'red'}>{walletMatches ? 'INVOICE MATCH' : 'MISMATCH — HOLD'}</Tag>
              )}
            </div>
            <div className="vendor-trust__facts">
              <div><span>Risk tier</span><strong>{selected.vendor.risk_tier}</strong></div>
              <div><span>Autopay ceiling</span><strong>{formatMoney(selected.vendor.autopay_limit)} USDC</strong></div>
              <div><span>Wallet proofs</span><strong>{selected.walletHistory.length}</strong></div>
              <div>
                <span>Change control</span>
                <strong>{cooldownActive ? `Hold until ${cooldownUntil?.toLocaleDateString()}` : 'Cleared'}</strong>
              </div>
            </div>
            <div className="wallet-history">
              <div className="wallet-history__title"><Wallet size={17} /><span>Append-only wallet verification history</span></div>
              {selected.walletHistory.map((event, index) => (
                <article key={`${event.event_type}-${event.verified_at}-${index}`}>
                  <span className="wallet-history__rail" aria-hidden="true" />
                  <div><strong>{event.event_type === 'VERIFIED' ? 'Payout wallet verified' : 'Payout wallet replaced'}</strong><small>{new Date(event.verified_at).toLocaleString()} · {event.verification_method}</small></div>
                  <code>{shorten(event.wallet_address, 12, 10)}</code>
                  <small>{event.verification_reference}</small>
                </article>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="vendor-trust__empty"><Wallet size={20} /><span>No payout identity exists yet. Onboard one here, or let an evidence workflow create the vendor together with an invoice.</span>{onOpenEvidence ? <button type="button" onClick={onOpenEvidence}>Open evidence control room <ArrowRight size={15} /></button> : null}</div>
      )}
      <div className="vendor-controls">
        <div className="vendor-controls__head">
          <div><span className="eyebrow">Operator actions</span><h3>Manage payout identity</h3><p>Vendor writes require the finance-operator role and every wallet mutation becomes an append-only audit event.</p></div>
          <div><button type="button" className={vendorAction === 'onboard' ? 'is-active' : ''} onClick={() => setVendorAction(vendorAction === 'onboard' ? 'closed' : 'onboard')}>Onboard vendor</button><button type="button" disabled={!selected} className={vendorAction === 'rotate' ? 'is-active' : ''} onClick={() => setVendorAction(vendorAction === 'rotate' ? 'closed' : 'rotate')}>Replace wallet</button></div>
        </div>
        {vendorAction === 'onboard' ? (
          <div className="vendor-control-form">
            <label><span>Vendor ID</span><input placeholder="vendor-atlas" value={vendorDraft.id} onChange={(event) => setVendorDraft((current) => ({ ...current, id: event.target.value }))} /></label>
            <label><span>Legal name</span><input placeholder="Atlas Compute Inc." value={vendorDraft.legal_name} onChange={(event) => setVendorDraft((current) => ({ ...current, legal_name: event.target.value }))} /></label>
            <label className="is-wide"><span>Approved Arc wallet</span><input placeholder="0x… (40 hex characters)" value={vendorDraft.approved_wallet_address} onChange={(event) => setVendorDraft((current) => ({ ...current, approved_wallet_address: event.target.value }))} /></label>
            <label><span>Autopay ceiling</span><input type="number" min="0" step="0.01" value={vendorDraft.autopay_limit} onChange={(event) => setVendorDraft((current) => ({ ...current, autopay_limit: event.target.value }))} /></label>
            <label><span>Risk tier</span><select value={vendorDraft.risk_tier} onChange={(event) => setVendorDraft((current) => ({ ...current, risk_tier: event.target.value }))}><option value="low">Low</option><option value="standard">Standard</option><option value="high">High</option></select></label>
            <label><span>Verification method</span><select value={vendorDraft.verification_method} onChange={(event) => setVendorDraft((current) => ({ ...current, verification_method: event.target.value as VendorOnboardingDraft['verification_method'] }))}><option value="SIGNED_CHALLENGE">Signed challenge</option><option value="OUT_OF_BAND_CALL">Out-of-band call</option><option value="MANUAL_REVIEW">Manual review</option></select></label>
            <label><span>Proof reference</span><input placeholder="challenge:2026-09-21" value={vendorDraft.verification_reference} onChange={(event) => setVendorDraft((current) => ({ ...current, verification_reference: event.target.value }))} /></label>
            <div className="vendor-control-form__action"><small>{validWallet(vendorDraft.approved_wallet_address) ? 'Wallet format verified' : 'A 20-byte EVM address is required'}</small><Button size="sm" disabled={busy || !canOnboard} onClick={() => onOnboard(vendorDraft)}>Verify & onboard</Button></div>
          </div>
        ) : null}
        {vendorAction === 'rotate' && selected ? (
          <div className="vendor-control-form vendor-control-form--rotate">
            <div className="vendor-wallet-before"><span>Current approved wallet</span><code>{selected.vendor.approved_wallet_address}</code></div>
            <label className="is-wide"><span>Replacement Arc wallet</span><input placeholder="0x… (40 hex characters)" value={rotationDraft.new_wallet} onChange={(event) => setRotationDraft((current) => ({ ...current, new_wallet: event.target.value }))} /></label>
            <label><span>Verification method</span><select value={rotationDraft.verification_method} onChange={(event) => setRotationDraft((current) => ({ ...current, verification_method: event.target.value as VendorWalletRotationDraft['verification_method'] }))}><option value="SIGNED_CHALLENGE">Signed challenge</option><option value="OUT_OF_BAND_CALL">Out-of-band call</option><option value="MANUAL_REVIEW">Manual review</option></select></label>
            <label><span>Proof reference</span><input placeholder="rotation-ticket:…" value={rotationDraft.verification_reference} onChange={(event) => setRotationDraft((current) => ({ ...current, verification_reference: event.target.value }))} /></label>
            <div className="vendor-control-form__action"><small>Replacement triggers the configured cooldown before autonomous payment.</small><Button size="sm" kind="danger--tertiary" disabled={busy || !canRotate} onClick={() => onRotateWallet(selected.vendor, rotationDraft)}>Replace approved wallet</Button></div>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function RuntimeBoundary({
  readiness,
  workspaceId,
}: {
  readiness: BootstrapData['readiness'];
  workspaceId: string | null;
}) {
  const simulated = readiness.settlement_mode === 'simulation';
  return (
    <section className={simulated ? 'runtime-boundary' : 'runtime-boundary runtime-boundary--live'} aria-label="Runtime safety boundary">
      <div className="runtime-boundary__lead">
        <span className="runtime-boundary__marker" aria-hidden="true"><Locked size={18} /></span>
        <div>
          <span className="eyebrow">Runtime boundary</span>
          <strong>{simulated ? 'Judge simulation — no funds move' : 'Circle wallet execution enabled'}</strong>
          {simulated && workspaceId ? <small>Isolated browser workspace · {shorten(workspaceId, 12, 4)}</small> : null}
        </div>
      </div>
      <div className="runtime-boundary__fact">
        <span>Decision authority</span>
        <strong>Deterministic policy engine</strong>
        <small>AI recommendation cannot authorize payment</small>
      </div>
      <div className="runtime-boundary__fact">
        <span>Settlement proof</span>
        <strong>{simulated ? 'Deterministic receipt' : 'Circle + independent Arc RPC'}</strong>
        <small>{readiness.network} · mainnet {readiness.mainnet_enabled ? 'enabled' : 'locked'}</small>
      </div>
      <Tag type={simulated ? 'cool-gray' : 'green'}>{simulated ? 'SIMULATION' : 'LIVE USDC'}</Tag>
    </section>
  );
}

function OperatorAccessGate({
  context,
  busy,
  onConnect,
}: {
  context: BootstrapContext;
  busy: boolean;
  onConnect: (sessions: BootstrapData['sessions']) => void;
}) {
  const [sessions, setSessions] = useState<BootstrapData['sessions']>({
    admin: '',
    operator: '',
    approver: '',
    auditor: '',
  });
  const complete = Object.values(sessions).every((token) => token.trim().length > 0);
  const update = (role: keyof BootstrapData['sessions'], value: string) => {
    setSessions((current) => ({ ...current, [role]: value.trim() }));
  };

  return (
    <section className="operator-access" aria-labelledby="operator-access-title">
      <div className="operator-access__intro">
        <span className="operator-access__marker" aria-hidden="true"><Locked size={24} /></span>
        <div>
          <span className="eyebrow">Private live operations</span>
          <h2 id="operator-access-title">Connect the role-separated finance team.</h2>
          <p>
            Live Circle mode disables public demo identities. Paste the four short-lived sessions
            generated locally for this database. TallyGuard keeps them only in this page's memory
            and clears them on reload.
          </p>
        </div>
      </div>
      <form
        className="operator-access__form"
        onSubmit={(event) => {
          event.preventDefault();
          if (complete) onConnect(sessions);
        }}
      >
        <TextInput
          id="operator-access-admin"
          type="password"
          labelText="Policy administrator session"
          autoComplete="new-password"
          value={sessions.admin}
          onChange={(event) => update('admin', event.currentTarget.value)}
        />
        <TextInput
          id="operator-access-operator"
          type="password"
          labelText="Finance operator session"
          autoComplete="new-password"
          value={sessions.operator}
          onChange={(event) => update('operator', event.currentTarget.value)}
        />
        <TextInput
          id="operator-access-approver"
          type="password"
          labelText="Payment approver session"
          autoComplete="new-password"
          value={sessions.approver}
          onChange={(event) => update('approver', event.currentTarget.value)}
        />
        <TextInput
          id="operator-access-auditor"
          type="password"
          labelText="Audit reviewer session"
          autoComplete="new-password"
          value={sessions.auditor}
          onChange={(event) => update('auditor', event.currentTarget.value)}
        />
        <div className="operator-access__action">
          <div>
            <strong>{context.readiness.network} · Circle live</strong>
            <small>TallyGuard never places tokens in a URL, persistent browser storage, or frontend bundle.</small>
          </div>
          <Button type="submit" renderIcon={ArrowRight} disabled={!complete || busy}>
            {busy ? 'Verifying access' : 'Open private operations'}
          </Button>
        </div>
      </form>
    </section>
  );
}

function ScenarioRail({
  scenarios,
  activeKey,
  busy,
  onSelect,
  onRun,
  mode,
  onModeChange,
  onRunLive,
}: {
  scenarios: Scenario[];
  activeKey: string;
  busy: boolean;
  onSelect: (key: string) => void;
  onRun: (key: string) => void;
  mode: 'scenario' | 'live';
  onModeChange: (mode: 'scenario' | 'live') => void;
  onRunLive: () => void;
}) {
  return (
    <aside className="scenario-rail" aria-label="Evaluation workspace">
      <div className="workflow-switch" role="tablist" aria-label="Evaluation mode">
        <button
          className={mode === 'scenario' ? 'workflow-switch__tab workflow-switch__tab--active' : 'workflow-switch__tab'}
          type="button"
          role="tab"
          aria-selected={mode === 'scenario'}
          onClick={() => onModeChange('scenario')}
        >
          Control lab
        </button>
        <button
          className={mode === 'live' ? 'workflow-switch__tab workflow-switch__tab--active' : 'workflow-switch__tab'}
          type="button"
          role="tab"
          aria-selected={mode === 'live'}
          onClick={() => onModeChange('live')}
        >
          Live evidence
        </button>
      </div>
      {mode === 'scenario' ? (
        <>
          <div className="section-heading">
            <span className="eyebrow">Judge scenario library</span>
            <h2>Test the controls</h2>
            <p>Each case runs through the same evidence, policy, approval, and settlement code used by the API.</p>
          </div>
          <Button
            className="run-button"
            renderIcon={busy ? Renew : PlayFilled}
            disabled={busy}
            onClick={() => onRun(activeKey)}
          >
            {busy ? 'Evaluating controls' : 'Run selected scenario'}
          </Button>
          <div className="scenario-list">
            {scenarios.map((scenario, index) => {
              const active = scenario.key === activeKey;
              return (
                <button
                  className={`scenario-row${active ? ' scenario-row--active' : ''}`}
                  key={scenario.key}
                  type="button"
                  onClick={() => onSelect(scenario.key)}
                >
                  <span className="scenario-row__index">{String(index + 1).padStart(2, '0')}</span>
                  <span className="scenario-row__copy">
                    <strong>{scenario.title}</strong>
                    <small>{scenario.description}</small>
                  </span>
                  <StatusTag action={scenario.expected_action} />
                </button>
              );
            })}
          </div>
        </>
      ) : (
        <div className="live-rail">
          <div className="section-heading">
            <span className="eyebrow">Production-shaped path</span>
            <h2>Run fresh evidence</h2>
            <p>This creates new tenant records and uploads three immutable documents through the public API.</p>
          </div>
          <Button
            className="run-button"
            renderIcon={busy ? Renew : PlayFilled}
            disabled={busy}
            onClick={onRunLive}
          >
            {busy ? 'Building evidence package' : 'Run live evidence workflow'}
          </Button>
          <ol className="live-steps">
            {LIVE_STEPS.map(([index, title, detail]) => (
              <li key={index}>
                <span>{index}</span>
                <div><strong>{title}</strong><small>{detail}</small></div>
              </li>
            ))}
          </ol>
          <div className="live-boundary"><Locked size={16} /><span>No private keys. Settlement remains a separate approver action.</span></div>
        </div>
      )}
    </aside>
  );
}

function EmptyWorkbench({ scenario }: { scenario?: Scenario }) {
  return (
    <section className="empty-workbench">
      <div className="empty-workbench__icon"><Rule size={32} /></div>
      <span className="eyebrow">Ready for evaluation</span>
      <h2>{scenario?.title ?? 'Select a scenario'}</h2>
      <p>{scenario?.description ?? 'Choose a case from the scenario library to begin.'}</p>
      <div className="flow-preview" aria-label="Evaluation flow">
        <span><Document size={16} /> Evidence</span>
        <ArrowRight size={16} />
        <span><Rule size={16} /> Policy</span>
        <ArrowRight size={16} />
        <span><Wallet size={16} /> Arc settlement</span>
      </div>
    </section>
  );
}

function LiveEvidenceWorkbench({
  busy,
  submitted = false,
  operatorToken,
  onEvaluate,
}: {
  busy: boolean;
  submitted?: boolean;
  operatorToken: string;
  onEvaluate: (files: EvidenceFileBundle, overrides: EvidenceFieldOverrides) => void;
}) {
  const [files, setFiles] = useState<Partial<EvidenceFileBundle>>({});
  const [review, setReview] = useState<EvidenceFileReview | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [loadingSample, setLoadingSample] = useState(false);
  const [sourceMode, setSourceMode] = useState<'sample' | 'custom' | null>(null);
  const [inputVersion, setInputVersion] = useState(0);
  const [fieldEdits, setFieldEdits] = useState<EvidenceFieldOverrides>({});

  useEffect(() => {
    if (!files.invoice || !files.purchaseOrder || !files.delivery) {
      setReview(null);
      setReviewError(null);
      return;
    }
    const bundle: EvidenceFileBundle = {
      invoice: files.invoice,
      purchaseOrder: files.purchaseOrder,
      delivery: files.delivery,
    };
    let cancelled = false;
    setReviewing(true);
    setReview(null);
    setReviewError(null);
    reviewEvidenceFiles(bundle, operatorToken)
      .then((result) => { if (!cancelled) setReview(result); })
      .catch((reason: unknown) => {
        if (!cancelled) setReviewError(reason instanceof Error ? reason.message : 'Could not review these files.');
      })
      .finally(() => { if (!cancelled) setReviewing(false); });
    return () => { cancelled = true; };
  }, [files.delivery, files.invoice, files.purchaseOrder, operatorToken]);

  const updateFile = (key: keyof EvidenceFileBundle, file?: File) => {
    if (file) setSourceMode('custom');
    setFieldEdits((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    setFiles((current) => ({ ...current, [key]: file }));
  };
  const clearFiles = () => {
    setFiles({});
    setReview(null);
    setReviewError(null);
    setSourceMode(null);
    setFieldEdits({});
    setInputVersion((value) => value + 1);
  };
  const loadSample = () => {
    setLoadingSample(true);
    setReviewError(null);
    loadSamplePdfEvidence()
      .then((sampleFiles) => {
        const suffix = crypto.randomUUID().replaceAll('-', '').slice(0, 8);
        const reference = suffix.toUpperCase();
        setFiles(sampleFiles);
        setSourceMode('sample');
        setFieldEdits({
          invoice: {
            invoice_id: `invoice-${suffix}`,
            invoice_number: `TG-${reference}`,
          },
          purchaseOrder: {
            purchase_order_id: `po-${suffix}`,
            po_number: `PO-${reference}`,
          },
          delivery: {
            delivery_id: `delivery-${suffix}`,
            purchase_order_id: `po-${suffix}`,
          },
        });
        setInputVersion((value) => value + 1);
      })
      .catch((reason: unknown) => {
        setReviewError(reason instanceof Error ? reason.message : 'Could not load the sample PDFs.');
      })
      .finally(() => setLoadingSample(false));
  };
  const completeBundle = files.invoice && files.purchaseOrder && files.delivery
    ? { invoice: files.invoice, purchaseOrder: files.purchaseOrder, delivery: files.delivery }
    : null;
  const fieldValue = (document: keyof EvidenceFileBundle, field: string, fallback: string) => fieldEdits[document]?.[field] ?? fallback;
  const editField = (document: keyof EvidenceFileBundle, field: string, value: string) => {
    setFieldEdits((current) => ({
      ...current,
      [document]: { ...current[document], [field]: value },
    }));
  };
  const editVendor = (value: string) => {
    setFieldEdits((current) => ({
      ...current,
      invoice: { ...current.invoice, vendor_id: value },
      purchaseOrder: { ...current.purchaseOrder, vendor_id: value },
    }));
  };
  const visibleCorrectionFields = new Set(['invoice_number', 'vendor_id', 'amount', 'due_date', 'payment_wallet_address', 'po_number', 'authorized_amount', 'delivered_value']);
  const correctionCount = Object.values(fieldEdits).reduce(
    (total, item) => total + Object.keys(item ?? {}).filter((field) => visibleCorrectionFields.has(field)).length,
    0,
  );

  return (
    <section className="live-workbench">
      <div className="live-workbench__intro">
        <div className="empty-workbench__icon"><Document size={32} /></div>
        <div>
          <span className="eyebrow">Upload your own documents</span>
          <h2>Your invoice. Your supporting proof.</h2>
          <p>Choose an invoice, purchase order, and delivery record from your computer. You can replace any file until you confirm the request; after confirmation, the exact bytes are sealed for audit and corrections become a new request.</p>
          <div className="sample-evidence-actions">
            <Button
              className="sample-evidence-button"
              disabled={busy || loadingSample}
              kind="tertiary"
              size="sm"
              renderIcon={loadingSample ? Renew : Download}
              onClick={loadSample}
            >
              {loadingSample ? 'Preparing sample documents' : 'Use sample documents'}
            </Button>
            {Object.keys(files).length ? <button type="button" className="clear-evidence-button" disabled={busy} onClick={clearFiles}>Clear all and upload my own</button> : null}
          </div>
          <small className="sample-evidence-note">Start with a prepared document set, or replace any card with files from your computer.</small>
        </div>
      </div>
      <div className={`evidence-source-banner is-${sourceMode ?? 'empty'}`}><span>{sourceMode === 'sample' ? 'SAMPLE DOCUMENTS' : sourceMode === 'custom' ? 'YOUR FILES' : 'READY FOR UPLOAD'}</span><p>{sourceMode === 'sample' ? 'A prepared three-document set is ready. Review the extracted details below or replace any source.' : sourceMode === 'custom' ? 'At least one file came from your computer. Confirm all three filenames before submission.' : 'Select each document card below; nothing is uploaded until all three files are ready.'}</p></div>
      <div className="upload-grid">
        {([
          ['invoice', 'Invoice', 'JSON / text PDF · invoice ID · vendor · amount · wallet'],
          ['purchaseOrder', 'Purchase order', 'JSON / text PDF · PO ID · vendor · authorized amount'],
          ['delivery', 'Delivery evidence', 'JSON / text PDF · delivery ID · PO ID · delivered value'],
        ] as const).map(([key, label, hint]) => (
          <label className={files[key] ? 'upload-slot upload-slot--ready' : 'upload-slot'} key={key}>
            <span>{files[key] ? <CheckmarkFilled size={18} /> : <Document size={18} />}</span>
            <strong>{label}</strong>
            <small>{files[key]?.name ?? hint}</small>
            <em>{files[key] ? 'Replace file' : 'Choose file'}</em>
            <input
              key={`${key}-${inputVersion}`}
              aria-label={`Upload ${label}`}
              accept="application/json,application/pdf,.json,.pdf"
              type="file"
              onChange={(event) => updateFile(key, event.target.files?.[0])}
            />
          </label>
        ))}
      </div>
      {reviewing ? <InlineLoading description="Extracting and validating immutable evidence" status="active" /> : null}
      {reviewError ? (
        <InlineNotification kind="error" title="Evidence review stopped" subtitle={reviewError} lowContrast hideCloseButton />
      ) : null}
      {review ? (
        <div className="evidence-review" aria-label="Extracted evidence review">
          <div className="evidence-review__head">
            <div><span className="eyebrow">Extraction review</span><h3>{fieldValue('invoice', 'invoice_number', review.invoiceNumber)}</h3></div>
            <Tag type="teal">{correctionCount ? `${correctionCount} confirmed fields` : `${review.extractionMethods.join(' + ')} extracted`}</Tag>
          </div>
          <div className="evidence-edit-note"><div><strong>Confirm the payment request</strong><p>Correct extracted values before submission. TallyGuard keeps the original file hash and records every corrected value as a manual confirmation.</p></div><span>{sourceMode === 'sample' ? 'Review before submission' : 'Original files stay unchanged'}</span></div>
          <div className="evidence-edit-grid">
            <label><span>Invoice number</span><input value={fieldValue('invoice', 'invoice_number', review.invoiceNumber)} onChange={(event) => editField('invoice', 'invoice_number', event.target.value)} /><small>{fieldEdits.invoice?.invoice_number ? 'User confirmed' : 'Extracted from invoice'}</small></label>
            <label><span>Supplier ID</span><input value={fieldValue('invoice', 'vendor_id', review.vendorId)} onChange={(event) => editVendor(event.target.value)} /><small>{fieldEdits.invoice?.vendor_id ? 'Applied to invoice and PO' : 'Matched across evidence'}</small></label>
            <label><span>Requested amount</span><div className="money-input"><input inputMode="decimal" value={fieldValue('invoice', 'amount', review.amount)} onChange={(event) => editField('invoice', 'amount', event.target.value)} /><b>{review.currency}</b></div><small>{fieldEdits.invoice?.amount ? 'User confirmed' : 'Extracted from invoice'}</small></label>
            <label><span>Due date</span><input type="date" value={fieldValue('invoice', 'due_date', review.dueDate)} onChange={(event) => editField('invoice', 'due_date', event.target.value)} /><small>{fieldEdits.invoice?.due_date ? 'User confirmed' : 'Extracted from invoice'}</small></label>
            <label><span>Purchase order</span><input value={fieldValue('purchaseOrder', 'po_number', review.purchaseOrderNumber)} onChange={(event) => editField('purchaseOrder', 'po_number', event.target.value)} /><small>{fieldEdits.purchaseOrder?.po_number ? 'User confirmed' : 'Extracted from PO'}</small></label>
            <label><span>PO authorized amount</span><div className="money-input"><input inputMode="decimal" value={fieldValue('purchaseOrder', 'authorized_amount', review.authorizedAmount)} onChange={(event) => editField('purchaseOrder', 'authorized_amount', event.target.value)} /><b>{review.currency}</b></div><small>{fieldEdits.purchaseOrder?.authorized_amount ? 'User confirmed' : 'Extracted from PO'}</small></label>
            <label><span>Delivered value</span><div className="money-input"><input inputMode="decimal" value={fieldValue('delivery', 'delivered_value', review.deliveredValue)} onChange={(event) => editField('delivery', 'delivered_value', event.target.value)} /><b>{review.currency}</b></div><small>{fieldEdits.delivery?.delivered_value ? 'User confirmed' : 'Extracted from delivery proof'}</small></label>
            <label className="is-wide"><span>Payout wallet</span><input value={fieldValue('invoice', 'payment_wallet_address', review.walletAddress)} onChange={(event) => editField('invoice', 'payment_wallet_address', event.target.value)} /><small>{fieldEdits.invoice?.payment_wallet_address ? 'User confirmed — finance will verify against the supplier profile' : 'Extracted and matched to the supplier profile'}</small></label>
          </div>
          <div className="evidence-review__sources" aria-label="Extraction provenance">
            {review.documents.map((document) => (
              <article key={document.evidenceType}>
                <div>
                  <span>{document.evidenceType.replaceAll('_', ' ')}</span>
                  <strong>{document.filename}</strong>
                </div>
                <dl>
                  <div><dt>Method</dt><dd>{document.extractionMethods.join(' + ')}</dd></div>
                  <div><dt>Fields</dt><dd>{document.fieldCount}</dd></div>
                  <div><dt>Confidence</dt><dd>{Math.round(Number(document.minimumConfidence) * 100)}% min</dd></div>
                  <div><dt>Source</dt><dd>{document.pageNumbers.length ? `p. ${document.pageNumbers.join(', ')}` : 'JSON pointers'}</dd></div>
                </dl>
                <code>sha256:{shorten(document.contentHash, 10, 10)}</code>
              </article>
            ))}
          </div>
          <div className="review-action">
            <p>Confirm these extracted values before creating immutable tenant records.</p>
            <Button
              disabled={!completeBundle || busy || submitted}
              renderIcon={busy ? Renew : submitted ? CheckmarkFilled : ArrowRight}
              onClick={() => { if (completeBundle && !submitted) onEvaluate(completeBundle, fieldEdits); }}
            >
              {busy ? 'Sealing evidence' : submitted ? 'Request submitted' : 'Seal and submit request'}
            </Button>
          </div>
        </div>
      ) : null}
      <div className="flow-preview" aria-label="Live evidence flow">
        <span><Document size={16} /> 3 source files</span>
        <ArrowRight size={16} />
        <span><Rule size={16} /> Deterministic controls</span>
        <ArrowRight size={16} />
        <span><Wallet size={16} /> Approval boundary</span>
      </div>
    </section>
  );
}

function EvidencePanel({
  run,
  documents = [],
  busy = false,
  onDownloadDocument,
}: {
  run: RunResult;
  documents?: EvidenceDocument[];
  busy?: boolean;
  onDownloadDocument?: (document: EvidenceDocument) => void;
}) {
  const invoice = run.invoice;
  const decision = run.decision;
  const passedRules = decision.rules.filter((item) => item.disposition === 'PASS').length;
  return (
    <section className="evidence-panel">
      <div className="panel-title">
        <div>
          <span className="eyebrow">Evidence package</span>
          <h2>{invoice.invoice_number}</h2>
        </div>
        <Tag type="cool-gray">v{invoice.version}</Tag>
      </div>

      <div className="invoice-summary">
        <div>
          <span>Vendor</span>
          <code>{shorten(invoice.vendor_id, 14, 8)}</code>
        </div>
        <div>
          <span>Amount</span>
          <strong>{formatMoney(invoice.amount)} USDC</strong>
        </div>
        <div>
          <span>Due date</span>
          <strong>{invoice.due_date}</strong>
        </div>
        <div>
          <span>Recipient</span>
          <code>{shorten(invoice.payment_wallet_address, 10, 8)}</code>
        </div>
      </div>

      <div className="integrity-strip">
        <Locked size={18} />
        <div>
          <strong>Immutable source binding</strong>
          <span>SHA-256 {shorten(invoice.source_document_hash, 12, 10)}</span>
        </div>
        <Tag type="teal">{passedRules}/{decision.rules.length} clear</Tag>
      </div>

      {documents.length > 0 ? (
        <div className="source-evidence-list" aria-label="Original source evidence">
          <header><span>Original source evidence</span><small>{documents.length} immutable document{documents.length === 1 ? '' : 's'}</small></header>
          {documents.map((document) => (
            <article key={document.id}>
              <span className="source-evidence-list__icon"><Document size={17} /></span>
              <div><strong>{document.filename}</strong><small>{document.evidence_type.replaceAll('_', ' ')} · {document.fields.length} extracted fields · {(document.byte_size / 1024).toFixed(1)} KB</small></div>
              <code>{shorten(document.content_sha256, 10, 8)}</code>
              {onDownloadDocument ? <button type="button" disabled={busy} onClick={() => onDownloadDocument(document)} aria-label={`Download ${document.filename}`}><Download size={16} /></button> : null}
            </article>
          ))}
        </div>
      ) : null}

      <div className="rule-table" role="table" aria-label="Policy rule results">
        <div className="rule-table__head" role="row">
          <span role="columnheader">Control</span>
          <span role="columnheader">Finding</span>
          <span role="columnheader">Result</span>
        </div>
        {decision.rules.map((rule, index) => (
          <div className="rule-table__row" role="row" key={`${rule.code}-${index}`}>
            <div role="cell">
              {rule.disposition === 'PASS' ? <CheckmarkFilled className="icon-success" /> : <WarningAltFilled className="icon-warning" />}
              <code>{rule.code}</code>
            </div>
            <span role="cell">{rule.message}</span>
            <div role="cell"><RuleTag disposition={rule.disposition} /></div>
          </div>
        ))}
      </div>
    </section>
  );
}

function DecisionPanel({
  run,
  approval,
  payment,
  busy,
  replay,
  simulation,
  settlementStopped,
  settlementRetryNeeded,
  onRequestApproval,
  onApprove,
  onSettle,
  onVerifyReplay,
  onSimulatePolicy,
}: {
  run: RunResult;
  approval: Approval | null;
  payment: Payment | null;
  busy: string | null;
  replay: ReplayVerification | null;
  simulation: PolicySimulation | null;
  settlementStopped: boolean;
  settlementRetryNeeded: boolean;
  onRequestApproval: () => void;
  onApprove: () => void;
  onSettle: () => void;
  onVerifyReplay: () => void;
  onSimulatePolicy: (changes: Record<string, string | number | boolean | null>) => void;
}) {
  const { decision } = run;
  const isPayable = decision.final_action === 'PAY';
  const isEscalated = decision.final_action === 'ESCALATE';
  const approvalGranted = approval?.status === 'APPROVED';

  return (
    <aside className="decision-panel">
      <div className="decision-panel__hero">
        <span className="eyebrow">Deterministic decision</span>
        <StatusTag action={decision.final_action} />
        <h2>{ACTION_LABEL[decision.final_action]}</h2>
        <p>{decision.reason_codes.length > 0 ? decision.reason_codes.join(' · ') : 'ALL_MANDATORY_CONTROLS_PASSED'}</p>
      </div>

      {decision.scheduled_for ? (
        <div className="schedule-boundary">
          <Time size={18} />
          <div><span>Earliest release</span><strong>{decision.scheduled_for}</strong><small>The schedule runner refuses early execution and rechecks current policy, vendor, treasury, and evidence before paying.</small></div>
        </div>
      ) : null}

      <div className="policy-binding">
        <div><span>Policy version</span><code>{decision.policy_version}</code></div>
        <div><span>Policy hash</span><code>{shorten(decision.policy_content_hash, 10, 8)}</code></div>
        <div><span>Evidence manifest</span><code>{shorten(decision.evidence_manifest_hash, 10, 8)}</code></div>
        <div><span>Replay snapshot</span><code>{shorten(decision.replay_input_hash ?? 'unavailable', 10, 8)}</code></div>
      </div>

      <div className="replay-proof">
        <div>
          <span className="eyebrow">Independent reproduction</span>
          <strong>{replay ? (replay.verified ? 'Decision reproduced exactly' : 'Replay mismatch detected') : 'Recompute from the sealed inputs'}</strong>
          <small>{replay ? `${replay.checks.filter((check) => check.passed).length}/${replay.checks.length} bindings verified` : 'No current vendor, treasury, or policy state is consulted.'}</small>
        </div>
        <Button
          kind="tertiary"
          size="sm"
          renderIcon={replay?.verified ? CheckmarkFilled : Renew}
          disabled={busy !== null || !decision.replayable}
          onClick={onVerifyReplay}
        >
          {replay ? 'Verify again' : 'Verify replay'}
        </Button>
      </div>

      <div className="policy-sandbox">
        <div className="policy-sandbox__head">
          <div><span>Policy what-if sandbox</span><strong>Re-run the sealed evidence without changing production state.</strong></div>
          <Tag type="cool-gray">Not persisted</Tag>
        </div>
        <div className="policy-sandbox__actions">
          <Button size="sm" kind="tertiary" disabled={busy !== null} onClick={() => onSimulatePolicy({ maximum_autonomous_payment_usdc: '500' })}>Cap autonomy at 500 USDC</Button>
          <Button size="sm" kind="danger--tertiary" disabled={busy !== null} onClick={() => onSimulatePolicy({ kill_switch_enabled: true })}>Engage kill switch</Button>
        </div>
        {simulation ? (
          <div className="policy-sandbox__result">
            <div><StatusTag action={simulation.original_action} /><ArrowRight size={16} /><StatusTag action={simulation.simulated_action} /></div>
            <small>{simulation.changed_fields.map((item) => `${item.field}: ${String(item.before)} → ${String(item.after)}`).join(' · ')}</small>
            <code>{simulation.reason_codes.join(' · ') || 'ALL_MANDATORY_CONTROLS_PASSED'}</code>
          </div>
        ) : null}
      </div>

      {decision.agent_recommendation ? (
        <div className="agent-note">
          <div className="agent-note__head">
            <span>Agent recommendation</span>
            <Tag type={decision.agent_disagreed ? 'magenta' : 'teal'}>
              {decision.agent_disagreed ? 'Overruled by policy' : `${Number(decision.agent_recommendation.confidence) * 100}% confidence`}
            </Tag>
          </div>
          <p>{decision.agent_recommendation.summary}</p>
          {decision.agent_recommendation.evidence_refs.length > 0 ? (
            <div className="agent-note__refs">
              <span>Evidence citations</span>
              <div>
                {decision.agent_recommendation.evidence_refs.map((reference) => (
                  <code key={reference}>{shorten(reference, 12, 10)}</code>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {decision.remediation.length > 0 ? (
        <div className="remediation">
          <strong>Required remediation</strong>
          <ul>{decision.remediation.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
        </div>
      ) : null}

      <div className="action-stack">
        {settlementRetryNeeded ? (
          <InlineNotification
            kind="warning"
            title="Provider attempt failed safely"
            subtitle="The payment intent and idempotency key remain durable. Retry rechecks current policy and treasury controls before reusing the same intent."
            lowContrast
            hideCloseButton
          />
        ) : null}
        {isEscalated && !approval ? (
          <Button renderIcon={UserMultiple} onClick={onRequestApproval} disabled={busy !== null}>
            Route to independent approver
          </Button>
        ) : null}
        {isEscalated && approval?.status === 'PENDING' ? (
          <Button renderIcon={ArrowRight} onClick={onApprove} disabled={busy !== null}>
            Open independent approval portal
          </Button>
        ) : null}
        {(isPayable || approvalGranted) && !payment ? (
          settlementStopped ? (
            <div className="blocked-action"><Locked size={18} /><span>The current policy kill switch blocks every new settlement, including decisions approved under an older version.</span></div>
          ) : (
            <Button renderIcon={Money} onClick={onSettle} disabled={busy !== null}>
              {settlementRetryNeeded ? 'Retry same payment intent' : 'Settle USDC on Arc'}
            </Button>
          )
        ) : null}
        {busy ? <InlineLoading description={busy} status="active" /> : null}
        {!isPayable && !isEscalated ? (
          <div className="blocked-action"><Locked size={18} /><span>Settlement path is cryptographically unavailable for this outcome.</span></div>
        ) : null}
      </div>

      {approval ? (
        <div className="approval-record">
          <span className="eyebrow">Approval record</span>
          <div><strong>{approval.status}</strong><code>{shorten(approval.id, 12, 8)}</code></div>
          <small>{approval.resolution_note ?? 'Awaiting a role-separated review.'}</small>
        </div>
      ) : null}
    </aside>
  );
}

function ReceiptPanel({ payment }: { payment: Payment }) {
  const receipt = payment.receipt;
  const simulated = receipt.provider === 'arc-simulator';
  return (
    <section className="receipt-panel">
      <div className="receipt-panel__title">
        <div className="receipt-icon"><CheckmarkFilled size={24} /></div>
        <div><span className="eyebrow">{simulated ? 'Reconciled simulation' : 'Reconciled settlement'}</span><h2>{formatMoney(receipt.confirmed_amount_usdc)} USDC {simulated ? 'simulated' : 'confirmed'}</h2></div>
        <Tag type={simulated ? 'cool-gray' : 'green'}>{simulated ? 'SIMULATED' : receipt.status}</Tag>
      </div>
      <div className="receipt-grid">
        <div><span>Execution provider</span><strong>{receipt.provider}</strong></div>
        <div><span>Arc network</span><strong>{receipt.network}</strong></div>
        <div><span>Block</span><strong>{simulated ? 'Simulated' : `#${receipt.block_number.toLocaleString()}`}</strong></div>
        <div><span>Exactly-once guard</span><strong>{payment.reused_receipt ? 'Existing receipt reused' : 'New intent persisted'}</strong></div>
        <div className="receipt-grid__wide"><span>{simulated ? 'Simulation receipt ID' : 'Transaction hash'}</span><code>{receipt.transaction_hash}</code></div>
      </div>
      {simulated ? (
        <InlineNotification
          kind="info"
          title="Credential-free judge mode"
          subtitle="This receipt exercised the durable intent, authorization, idempotency, and reconciliation path without moving funds. Circle mode adds independent Arc RPC proof."
          lowContrast
          hideCloseButton
        />
      ) : (
        <Button kind="tertiary" renderIcon={Launch} href={receipt.explorer_url} target="_blank" rel="noreferrer">
          Verify on Arc explorer
        </Button>
      )}
    </section>
  );
}

function AuditTimeline({
  trail,
  packetHash,
  busy,
  onDownloadPacket,
}: {
  trail: AuditTrail;
  packetHash: string | null;
  busy: boolean;
  onDownloadPacket: () => void;
}) {
  const items = [...trail.events].reverse().slice(0, 10);
  return (
    <section className="audit-panel" aria-label="Tamper-evident audit trail">
      <div className="audit-panel__head">
        <div>
          <span className="eyebrow">Independent audit view</span>
          <h2>Every mutation leaves a linked proof.</h2>
        </div>
        <div className="audit-panel__actions">
          <Tag type={trail.chainValid ? 'green' : 'red'}>
            {trail.chainValid ? 'Chain verified' : 'Chain invalid'}
          </Tag>
          <Button size="sm" kind="tertiary" renderIcon={Download} disabled={busy} onClick={onDownloadPacket}>
            Download evidence packet
          </Button>
        </div>
      </div>
      <div className="audit-list">
        {items.map((event) => (
          <div className="audit-event" key={`${event.sequence}-${event.event_hash}`}>
            <span className="audit-event__sequence">#{String(event.sequence).padStart(3, '0')}</span>
            <div>
              <strong>{event.event_type.replaceAll('_', ' ')}</strong>
              <small>{event.aggregate_type} · {new Date(event.created_at).toLocaleString()}</small>
            </div>
            <code>{shorten(event.event_hash, 10, 8)}</code>
          </div>
        ))}
      </div>
      <div className="audit-panel__foot">
        <Locked size={16} />
        <span>{packetHash ? `Downloaded packet ${shorten(packetHash, 12, 10)} · ` : ''}Each SHA-256 event hash commits to the prior hash, timestamp, aggregate, and payload.</span>
      </div>
    </section>
  );
}

const AUDIT_EVENT_OPTIONS = [
  'AGENT_RUN_EXECUTED',
  'AGENT_RUN_PLANNED',
  'APPROVAL_REQUESTED',
  'APPROVAL_RESOLVED',
  'AUTONOMY_SHOWCASE_SEEDED',
  'EVIDENCE_INGESTED',
  'INVOICE_INGESTED',
  'PAYMENT_CONFIRMED',
  'POLICY_ACTIVATED',
  'POLICY_DECISION_RECORDED',
  'POLICY_EVALUATED',
  'SCHEDULE_RELEASE_EVALUATED',
  'SETTLEMENT_BLOCKED_BY_ACTIVE_KILL_SWITCH',
  'SETTLEMENT_BLOCKED_BY_EXECUTION_CONTROL',
  'SETTLEMENT_PROVIDER_UNAVAILABLE',
  'SETTLEMENT_RECONCILED',
  'TREASURY_SNAPSHOT_RECORDED',
  'VENDOR_ONBOARDED',
  'VENDOR_WALLET_REPLACED',
];

const AUDIT_AGGREGATE_OPTIONS = [
  'agent_run',
  'approval',
  'decision',
  'evidence',
  'payment',
  'policy',
  'schedule',
  'settlement_gate',
  'treasury_snapshot',
  'vendor',
  'vendor_wallet_change',
];

function toIsoTimestamp(value: string): string | undefined {
  if (!value) return undefined;
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? undefined : timestamp.toISOString();
}

function AuditExplorer({
  result,
  busy,
  onSearch,
  onLoadMore,
}: {
  result: AuditSearchResult;
  busy: boolean;
  onSearch: (filters: AuditSearchRequest) => void;
  onLoadMore: () => void;
}) {
  const [query, setQuery] = useState('');
  const [eventType, setEventType] = useState('');
  const [aggregateType, setAggregateType] = useState('');
  const [createdAfter, setCreatedAfter] = useState('');
  const [createdBefore, setCreatedBefore] = useState('');
  const search = () => onSearch({
    query,
    eventType,
    aggregateType,
    createdAfter: toIsoTimestamp(createdAfter),
    createdBefore: toIsoTimestamp(createdBefore),
    limit: 12,
  });

  return (
    <section className="tenant-audit" aria-label="Searchable tenant audit ledger">
      <div className="tenant-audit__head">
        <div>
          <span className="eyebrow">Tenant-wide audit ledger</span>
          <h2>Search every decision, handoff, and settlement proof.</h2>
          <p>Filters run server-side inside the active tenant boundary. Correlation IDs in canonical payloads are searchable without weakening the hash chain.</p>
        </div>
        <Tag type={result.chainValid ? 'green' : 'red'}>{result.chainValid ? 'Full chain verified' : 'Chain invalid'}</Tag>
      </div>
      <div className="tenant-audit__filters">
        <Search
          id="tenant-audit-query"
          labelText="Search audit events"
          placeholder="Aggregate ID, event, invoice, correlation ID…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter') search(); }}
        />
        <Select id="tenant-audit-event" labelText="Event type" value={eventType} onChange={(event) => setEventType(event.target.value)}>
          <SelectItem value="" text="All events" />
          {AUDIT_EVENT_OPTIONS.map((value) => <SelectItem key={value} value={value} text={value.replaceAll('_', ' ')} />)}
        </Select>
        <Select id="tenant-audit-aggregate" labelText="Object type" value={aggregateType} onChange={(event) => setAggregateType(event.target.value)}>
          <SelectItem value="" text="All objects" />
          {AUDIT_AGGREGATE_OPTIONS.map((value) => <SelectItem key={value} value={value} text={value.replaceAll('_', ' ')} />)}
        </Select>
        <TextInput id="tenant-audit-after" type="datetime-local" labelText="From" value={createdAfter} onChange={(event) => setCreatedAfter(event.target.value)} />
        <TextInput id="tenant-audit-before" type="datetime-local" labelText="To" value={createdBefore} onChange={(event) => setCreatedBefore(event.target.value)} />
        <Button kind="primary" size="md" disabled={busy} onClick={search}>Search ledger</Button>
      </div>
      <div className="tenant-audit__meta">
        <span>{result.events.length} events loaded · newest first</span>
        <code>{result.filters.query || result.filters.event_type || result.filters.aggregate_type || 'unfiltered latest page'}</code>
      </div>
      <div className="tenant-audit__events">
        {result.events.length ? result.events.map((event) => {
          const correlation = typeof event.payload.correlation_id === 'string' ? event.payload.correlation_id : null;
          return (
            <article key={`${event.sequence}-${event.event_hash}`}>
              <span>#{String(event.sequence).padStart(4, '0')}</span>
              <div>
                <strong>{event.event_type.replaceAll('_', ' ')}</strong>
                <small>{event.aggregate_type} · {event.aggregate_id}</small>
                {correlation ? <code>{correlation}</code> : null}
              </div>
              <div>
                <time>{new Date(event.created_at).toLocaleString()}</time>
                <code>{shorten(event.event_hash, 10, 8)}</code>
              </div>
            </article>
          );
        }) : <div className="tenant-audit__empty">No tenant event matches these filters.</div>}
      </div>
      <div className="tenant-audit__foot">
        <span>Every result retains its original sequence and SHA-256 link to the complete tenant chain.</span>
        <Button kind="ghost" size="sm" disabled={busy || !result.page.has_more} onClick={onLoadMore}>
          {result.page.has_more ? 'Load older events' : 'End of result set'}
        </Button>
      </div>
    </section>
  );
}

function PremiumWorkspaceFrame({
  active,
  index,
  title,
  description,
  meta,
  settlementMode = 'simulation',
  onNavigate,
  onSignOut,
  children,
}: {
  active: Exclude<WorkspaceView, 'overview' | 'login-user' | 'login-finance' | 'portal' | 'submit' | 'finance' | 'payables' | 'review'>;
  index: string;
  title: string;
  description: string;
  meta: string;
  settlementMode?: BootstrapData['readiness']['settlement_mode'];
  onNavigate: (view: WorkspaceView) => void;
  onSignOut?: () => void;
  children: ReactNode;
}) {
  return (
    <div className="premium-app premium-workspace">
      <ProductTopbar sectionLabel={title} roleLabel="Finance team" roleHelper="Controls & proof" onBack={() => onNavigate('finance')} onSignOut={onSignOut} settlementMode={settlementMode} />
      <div className="premium-workspace__layout">
        <PremiumSideNav active={active} onNavigate={onNavigate} settlementMode={settlementMode} />
        <main className="premium-workspace__main">
          <header className="premium-page-title">
            <span>{index}</span>
            <div><h1>{title}</h1><p>{description}</p></div>
            <em>{meta}</em>
          </header>
          {children}
          <footer className="premium-page-footer"><Locked size={15} /><span>Tenant scoped · Versioned policy · Idempotent settlement · Independent Arc proof</span><a href="/api/openapi.json" target="_blank" rel="noreferrer">OpenAPI 3.1 ↗</a></footer>
        </main>
      </div>
    </div>
  );
}

function App() {
  const [data, setData] = useState<BootstrapData | null>(null);
  const [accessContext, setAccessContext] = useState<BootstrapContext | null>(null);
  const [selectedKey, setSelectedKey] = useState('clean-payment');
  const [run, setRun] = useState<RunResult | null>(null);
  const [history, setHistory] = useState<RunResult[]>([]);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [payment, setPayment] = useState<Payment | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<'scenario' | 'live'>('scenario');
  const [auditTrail, setAuditTrail] = useState<AuditTrail | null>(null);
  const [evidenceDocuments, setEvidenceDocuments] = useState<EvidenceDocument[]>([]);
  const [replay, setReplay] = useState<ReplayVerification | null>(null);
  const [operations, setOperations] = useState<OperationsOverview | null>(null);
  const [incidents, setIncidents] = useState<SettlementIncidentOverview | null>(null);
  const [governance, setGovernance] = useState<GovernanceOverview | null>(null);
  const [batch, setBatch] = useState<PaymentBatch | null>(null);
  const [scheduleRun, setScheduleRun] = useState<ScheduleRun | null>(null);
  const [packetHash, setPacketHash] = useState<string | null>(null);
  const [simulation, setSimulation] = useState<PolicySimulation | null>(null);
  const [policyActivation, setPolicyActivation] = useState<PolicyActivation | null>(null);
  const [policyHistory, setPolicyHistory] = useState<ActivePolicy[]>([]);
  const [treasurySnapshot, setTreasurySnapshot] = useState<TreasurySnapshotRecord | null>(null);
  const [vendorDirectory, setVendorDirectory] = useState<VendorTrustRecord[]>([]);
  const [settlementRetryNeeded, setSettlementRetryNeeded] = useState(false);
  const [agentRun, setAgentRun] = useState<AgentRun | null>(null);
  const [agentProofHash, setAgentProofHash] = useState<string | null>(null);
  const [ledgerExport, setLedgerExport] = useState<{ hash: string; rows: number } | null>(null);
  const [auditSearch, setAuditSearch] = useState<AuditSearchResult | null>(null);
  const [lastApprovalResolution, setLastApprovalResolution] = useState<ApprovalResolutionSnapshot | null>(null);
  const [teamOpen, setTeamOpen] = useState(false);
  const [activeView, setActiveView] = useState<WorkspaceView>(readWorkspaceView);
  const [portalRole, setPortalRole] = useState<PortalRole | null>(readPortalRole);
  const portalRoleRef = useRef<PortalRole | null>(portalRole);
  const [requestApprovals, setRequestApprovals] = useState<Record<string, Approval | null>>({});
  const [correctionSource, setCorrectionSource] = useState<OperationsInvoice | null>(null);
  const [arcActivity, setArcActivity] = useState<ArcActivityResponse | null>(null);
  const [arcActivityError, setArcActivityError] = useState<string | null>(null);

  const refreshArcActivity = useCallback(async () => {
    try {
      const result = await fetchArcActivity();
      setArcActivity(result);
      setArcActivityError(result.available ? null : 'The Arc activity source is not connected to this deployment yet.');
    } catch (reason) {
      setArcActivity(null);
      setArcActivityError(reason instanceof Error ? reason.message : 'The Arc activity feed could not be loaded.');
    }
  }, []);

  useEffect(() => {
    if (!['overview', 'activity', 'finance', 'payables'].includes(activeView)) return;
    void refreshArcActivity();
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refreshArcActivity();
    }, 20_000);
    return () => window.clearInterval(interval);
  }, [activeView, refreshArcActivity]);

  const installBootstrap = useCallback((result: BootstrapData) => {
    // A workspace owns every invoice, decision, approval, and proof. Clear any
    // detail state before swapping role bundles so a hot reload or reconnect
    // can never display records from one tenant with sessions from another.
    setRun(null);
    setHistory([]);
    setApproval(null);
    setPayment(null);
    setAuditTrail(null);
    setEvidenceDocuments([]);
    setReplay(null);
    setBatch(null);
    setScheduleRun(null);
    setPacketHash(null);
    setSimulation(null);
    setPolicyActivation(null);
    setTreasurySnapshot(null);
    setSettlementRetryNeeded(false);
    setAgentProofHash(null);
    setLedgerExport(null);
    setLastApprovalResolution(null);
    setRequestApprovals({});
    setData(result);
    setOperations(result.operations);
    setIncidents(result.incidents);
    setAgentRun(result.agentRun);
    setGovernance(result.governance);
    setPolicyHistory(result.policyHistory);
    setVendorDirectory(result.vendorDirectory);
    setAuditSearch(result.auditSearch);
    setSelectedKey(result.scenarios[0]?.key ?? 'clean-payment');
  }, []);

  useEffect(() => {
    let cancelled = false;
    bootstrap()
      .then((result) => {
        if (!cancelled) installBootstrap(result);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        if (reason instanceof OperatorAccessRequired) {
          setAccessContext(reason.context);
          setError(null);
          return;
        }
        setError(reason instanceof Error ? reason.message : 'Could not initialize TallyGuard.');
      });
    return () => { cancelled = true; };
  }, [installBootstrap]);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'auto' });
  }, [activeView]);

  useEffect(() => {
    const handleHashChange = () => setActiveView(readWorkspaceView());
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  useEffect(() => {
    if (!data || !operations) return;
    let cancelled = false;
    const decisionIds = (operations.recent_requests ?? operations.work_queue).flatMap((item) => item.decision_id ? [item.decision_id] : []);
    void Promise.all(decisionIds.map(async (decisionId) => [decisionId, await fetchDecisionApproval(decisionId, data.sessions.auditor)] as const))
      .then((entries) => { if (!cancelled) setRequestApprovals(Object.fromEntries(entries)); })
      .catch(() => { if (!cancelled) setRequestApprovals({}); });
    return () => { cancelled = true; };
  }, [data, operations]);

  const act = useCallback(async (label: string, operation: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await operation();
    } catch (reason) {
      const message = reason instanceof ApiError ? `${reason.code}: ${reason.message}` : reason instanceof Error ? reason.message : 'The action failed.';
      setError(message);
    } finally {
      setBusy(null);
    }
  }, []);

  const handleOperatorConnect = useCallback((sessions: BootstrapData['sessions']) => {
    if (!accessContext) return;
    void act('Verifying role-separated operator access', async () => {
      const result = await bootstrapWithSessions(sessions, accessContext);
      installBootstrap(result);
      setAccessContext(null);
    });
  }, [accessContext, act, installBootstrap]);

  const handleOperatorDisconnect = useCallback(() => {
    if (!data || data.readiness.demo_sessions_enabled) return;
    const context: BootstrapContext = {
      scenarios: data.scenarios,
      readiness: data.readiness,
    };
    const sessions = data.sessions;
    setData(null);
    setAccessContext(context);
    setRun(null);
    setHistory([]);
    setApproval(null);
    setPayment(null);
    setMode('scenario');
    setAuditTrail(null);
    setEvidenceDocuments([]);
    setReplay(null);
    setOperations(null);
    setIncidents(null);
    setGovernance(null);
    setBatch(null);
    setScheduleRun(null);
    setPacketHash(null);
    setSimulation(null);
    setPolicyActivation(null);
    setPolicyHistory([]);
    setTreasurySnapshot(null);
    setVendorDirectory([]);
    setSettlementRetryNeeded(false);
    setAgentRun(null);
    setAgentProofHash(null);
    setLedgerExport(null);
    setAuditSearch(null);
    setLastApprovalResolution(null);
    setError(null);
    setActiveView('overview');
    void revokeOperatorSessions(sessions).catch((reason: unknown) => {
      const message = reason instanceof ApiError
        ? `${reason.code}: ${reason.message}`
        : 'Private browser access was cleared, but server session revocation did not complete.';
      setError(message);
    });
  }, [data]);

  const handleAuditSearch = useCallback((filters: AuditSearchRequest) => {
    if (!data) return;
    void act('Searching the tenant audit ledger', async () => {
      setAuditSearch(await fetchAuditEvents(data.sessions.auditor, filters));
    });
  }, [act, data]);

  const handleLoadMoreAudit = useCallback(() => {
    if (!data || !auditSearch?.page.next_before_sequence) return;
    void act('Loading older audit events', async () => {
      const next = await fetchAuditEvents(data.sessions.auditor, {
        query: auditSearch.filters.query ?? undefined,
        eventType: auditSearch.filters.event_type ?? undefined,
        aggregateType: auditSearch.filters.aggregate_type ?? undefined,
        aggregateId: auditSearch.filters.aggregate_id ?? undefined,
        createdAfter: auditSearch.filters.created_after ?? undefined,
        createdBefore: auditSearch.filters.created_before ?? undefined,
        beforeSequence: auditSearch.page.next_before_sequence ?? undefined,
        limit: auditSearch.page.limit,
      });
      setAuditSearch({ ...next, events: [...auditSearch.events, ...next.events] });
    });
  }, [act, auditSearch, data]);

  const refreshAuditLedger = useCallback(async () => {
    if (!data) return;
    const filters = auditSearch?.filters;
    setAuditSearch(await fetchAuditEvents(data.sessions.auditor, {
      query: filters?.query ?? undefined,
      eventType: filters?.event_type ?? undefined,
      aggregateType: filters?.aggregate_type ?? undefined,
      aggregateId: filters?.aggregate_id ?? undefined,
      createdAfter: filters?.created_after ?? undefined,
      createdBefore: filters?.created_before ?? undefined,
      limit: auditSearch?.page.limit ?? 12,
    }));
  }, [auditSearch, data]);

  const handleRun = useCallback((key: string, focusResult = false) => {
    if (!data) return;
    void act('Evaluating immutable evidence and policy rules', async () => {
      const result = await runScenario(key, data.sessions.operator);
      setRun(result);
      setHistory((items) => [result, ...items].slice(0, 12));
      setApproval(null);
      setPayment(result.autopay?.payment ?? null);
      setSettlementRetryNeeded(false);
      setReplay(null);
      setSimulation(null);
      setAuditTrail(await fetchInvoiceAudit(result.invoice.id, data.sessions.auditor));
      setEvidenceDocuments(await fetchInvoiceEvidence(result.invoice.id, data.sessions.auditor));
      setPacketHash(null);
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setVendorDirectory(await fetchVendorDirectory(data.sessions.auditor));
      await refreshAuditLedger();
      if (focusResult) {
        window.requestAnimationFrame(() => {
          const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
          const workbench = document.getElementById('evaluation-workbench');
          workbench?.focus({ preventScroll: true });
          workbench?.scrollIntoView({ behavior, block: 'start' });
        });
      }
    });
  }, [act, data, refreshAuditLedger]);

  const handleRequestApproval = useCallback(() => {
    if (!data || !run) return;
    void act('Creating a role-separated approval request', async () => {
      setApproval(await requestApproval(run.decision.id, data.sessions.operator));
      setAuditTrail(await fetchInvoiceAudit(run.invoice.id, data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setVendorDirectory(await fetchVendorDirectory(data.sessions.auditor));
      await refreshAuditLedger();
    });
  }, [act, data, refreshAuditLedger, run]);

  const handleRunLive = useCallback(() => {
    if (!data) return;
    void act('Creating fresh records and evaluating uploaded evidence', async () => {
      const result = await runLiveEvidenceWorkflow(
        data.sessions.admin,
        data.sessions.operator,
      );
      setRun(result);
      setHistory((items) => [result, ...items].slice(0, 12));
      setApproval(null);
      setPayment(null);
      setSettlementRetryNeeded(false);
      setReplay(null);
      setSimulation(null);
      setAuditTrail(await fetchInvoiceAudit(result.invoice.id, data.sessions.auditor));
      setEvidenceDocuments(await fetchInvoiceEvidence(result.invoice.id, data.sessions.auditor));
      setPacketHash(null);
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setVendorDirectory(await fetchVendorDirectory(data.sessions.auditor));
      await refreshAuditLedger();
    });
  }, [act, data, refreshAuditLedger]);

  const handleUploadedEvidence = useCallback((files: EvidenceFileBundle, overrides: EvidenceFieldOverrides = {}, onComplete?: (result: RunResult) => void) => {
    if (!data) return;
    void act('Persisting and evaluating your reviewed evidence', async () => {
      const result = await runUploadedEvidenceWorkflow(
        data.sessions.admin,
        data.sessions.operator,
        files,
        overrides,
        data.readiness.settlement_mode,
      );
      setRun(result);
      setHistory((items) => [result, ...items].slice(0, 12));
      setApproval(null);
      setPayment(null);
      setSettlementRetryNeeded(false);
      setReplay(null);
      setSimulation(null);
      onComplete?.(result);
      setAuditTrail(await fetchInvoiceAudit(result.invoice.id, data.sessions.auditor));
      setEvidenceDocuments(await fetchInvoiceEvidence(result.invoice.id, data.sessions.auditor));
      setPacketHash(null);
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setVendorDirectory(await fetchVendorDirectory(data.sessions.auditor));
      await refreshAuditLedger();
    });
  }, [act, data, refreshAuditLedger]);

  const handleResolveInboxApproval = useCallback((item: GovernanceOverview['pendingApprovals'][number], approve: boolean, note: string) => {
    if (!data) return;
    void act(approve ? 'Approving the exception as a separate role' : 'Rejecting the policy exception', async () => {
      const resolved = await resolveApproval(
        item.approval,
        data.sessions.approver,
        approve,
        note,
      );
      setLastApprovalResolution({ ...item, approval: resolved });
      if (approval?.id === resolved.id) setApproval(resolved);
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      if (run?.invoice.id === item.invoice.id) {
        setAuditTrail(await fetchInvoiceAudit(item.invoice.id, data.sessions.auditor));
      }
      await refreshAuditLedger();
    });
  }, [act, approval, data, refreshAuditLedger, run]);

  const handleActivatePolicy = useCallback((draft: PolicyDraft) => {
    if (!data || !governance) return;
    void act('Activating an immutable policy version and verifying its diff', async () => {
      const activation = await activatePolicyVersion(
        governance.activePolicy,
        draft,
        data.sessions.admin,
      );
      setPolicyActivation(activation);
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setPolicyHistory(await fetchPolicyHistory(data.sessions.auditor));
      await refreshAuditLedger();
    });
  }, [act, data, governance, refreshAuditLedger]);

  const handleRecordTreasury = useCallback((availableUsdc: string, spentTodayUsdc: string, sourceReference: string) => {
    if (!data) return;
    void act('Recording an operator-attested treasury snapshot', async () => {
      setTreasurySnapshot(await recordTreasurySnapshot(availableUsdc, spentTodayUsdc, sourceReference, data.sessions.operator));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      await refreshAuditLedger();
    });
  }, [act, data, refreshAuditLedger]);

  const handleRefreshLiveTreasury = useCallback(() => {
    if (!data) return;
    void act('Reading the Circle treasury and verifying Arc Testnet', async () => {
      setTreasurySnapshot(await refreshLiveTreasurySnapshot(data.sessions.operator));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      await refreshAuditLedger();
    });
  }, [act, data, refreshAuditLedger]);

  const handleOnboardVendor = useCallback((draft: VendorOnboardingDraft) => {
    if (!data) return;
    void act('Verifying and onboarding a tenant-scoped vendor', async () => {
      await onboardVendor(draft, data.sessions.operator);
      setVendorDirectory(await fetchVendorDirectory(data.sessions.auditor));
      await refreshAuditLedger();
    });
  }, [act, data, refreshAuditLedger]);

  const handleRotateVendorWallet = useCallback((vendor: VendorTrustRecord['vendor'], draft: VendorWalletRotationDraft) => {
    if (!data) return;
    void act('Replacing the approved wallet with append-only verification proof', async () => {
      await rotateVendorWallet(vendor, draft, data.sessions.operator);
      setVendorDirectory(await fetchVendorDirectory(data.sessions.auditor));
      await refreshAuditLedger();
    });
  }, [act, data, refreshAuditLedger]);

  const handleSettle = useCallback(() => {
    if (!data || !run) return;
    void act('Persisting intent, settling USDC, and reconciling Arc proof', async () => {
      try {
        setPayment(await settleInvoice(run, data.sessions.approver, approval?.status === 'APPROVED' ? approval.id : undefined));
        setSettlementRetryNeeded(false);
      } catch (reason) {
        setSettlementRetryNeeded(true);
        throw reason;
      } finally {
        setAuditTrail(await fetchInvoiceAudit(run.invoice.id, data.sessions.auditor));
        setOperations(await fetchOperationsOverview(data.sessions.auditor));
        setIncidents(await fetchSettlementIncidents(data.sessions.auditor));
        setGovernance(await fetchGovernanceOverview(data.sessions.approver));
        await refreshAuditLedger();
      }
    });
  }, [act, approval, data, refreshAuditLedger, run]);

  const handleVerifyReplay = useCallback(() => {
    if (!data || !run) return;
    void act('Recomputing the decision from its sealed input snapshot', async () => {
      setReplay(await verifyDecisionReplay(run.decision.id, data.sessions.auditor));
    });
  }, [act, data, run]);

  const handleSimulatePolicy = useCallback((changes: Record<string, string | number | boolean | null>) => {
    if (!data || !run) return;
    void act('Re-evaluating sealed evidence under an alternate policy', async () => {
      setSimulation(await simulateDecisionPolicy(run.decision.id, changes, data.sessions.admin));
    });
  }, [act, data, run]);

  const handleSettleBatch = useCallback((items: SettlementBatchItem[]) => {
    if (!data || items.length === 0) return;
    void act(`Reconciling ${items.length} selected Arc payments`, async () => {
      setBatch(await settlePaymentBatch(items, data.sessions.approver));
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setIncidents(await fetchSettlementIncidents(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      await refreshAuditLedger();
    });
  }, [act, data, refreshAuditLedger]);

  const handlePlanAgentRun = useCallback(() => {
    if (!data) return;
    void act('Planning a bounded autonomous accounts-payable run', async () => {
      setAgentRun(await createAgentRun(data.sessions.operator));
      setAgentProofHash(null);
      await refreshAuditLedger();
    });
  }, [act, data, refreshAuditLedger]);

  const handleSeedAgentShowcase = useCallback(() => {
    if (!data) return;
    void act('Building a mixed autonomous accounts-payable queue', async () => {
      await seedAutonomyShowcase(data.sessions.operator);
      setAgentRun(await createAgentRun(data.sessions.operator));
      setAgentProofHash(null);
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setVendorDirectory(await fetchVendorDirectory(data.sessions.auditor));
      await refreshAuditLedger();
    });
  }, [act, data, refreshAuditLedger]);

  const handleExecuteAgentRun = useCallback(() => {
    if (!data || !agentRun) return;
    void act('Revalidating and executing policy-cleared agent actions', async () => {
      setAgentRun(await executeAgentRun(agentRun.id, data.sessions.approver));
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setIncidents(await fetchSettlementIncidents(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      await refreshAuditLedger();
    });
  }, [act, agentRun, data, refreshAuditLedger]);

  const handleDownloadAgentProof = useCallback(() => {
    if (!data || !agentRun) return;
    void act('Assembling a content-addressed agent proof packet', async () => {
      setAgentProofHash(await downloadAgentRunProof(agentRun.id, data.sessions.auditor));
    });
  }, [act, agentRun, data]);

  const handleRunSchedules = useCallback(() => {
    if (!data) return;
    void act('Revalidating due schedules against current controls', async () => {
      setScheduleRun(await runDueSchedules(data.sessions.approver));
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      await refreshAuditLedger();
    });
  }, [act, data, refreshAuditLedger]);

  const handleExportLedger = useCallback(() => {
    if (!data) return;
    void act('Exporting a content-addressed accounting ledger', async () => {
      setLedgerExport(await downloadAccountingLedger(data.sessions.auditor));
    });
  }, [act, data]);

  const handleDownloadPacket = useCallback(() => {
    if (!data || !run) return;
    void act('Assembling a content-addressed payment evidence packet', async () => {
      setPacketHash(await downloadEvidencePacket(run.invoice.id, data.sessions.auditor));
    });
  }, [act, data, run]);

  const handleOpenInvoice = useCallback((invoice: OperationsInvoice) => {
    if (!data || !invoice.decision_id) {
      navigateToView('evidence');
      return;
    }
    void act(`Opening ${invoice.invoice_number} with its sealed decision`, async () => {
      const result = await fetchInvoiceRun(invoice, invoice.decision_id!, data.sessions.auditor);
      const persistedPayment = invoice.settlement_status === 'CONFIRMED' && invoice.settlement_payment_intent_id
        ? await fetchConfirmedPayment(invoice.settlement_payment_intent_id, data.sessions.auditor)
        : null;
      setRun(persistedPayment ? { ...result, invoice: persistedPayment.invoice } : result);
      setApproval(await fetchDecisionApproval(invoice.decision_id!, data.sessions.auditor));
      setPayment(persistedPayment);
      setSettlementRetryNeeded(invoice.settlement_retryable);
      setReplay(null);
      setSimulation(null);
      setPacketHash(null);
      setAuditTrail(await fetchInvoiceAudit(invoice.id, data.sessions.auditor));
      setEvidenceDocuments(await fetchInvoiceEvidence(invoice.id, data.sessions.auditor));
      navigateToView('review');
    });
  }, [act, data]);

  const handleOpenApprovalItem = useCallback((item: GovernanceOverview['pendingApprovals'][number]) => {
    if (!data) return;
    void act(`Opening ${item.invoice.invoice_number} for full evidence review`, async () => {
      const result = await fetchInvoiceRun(item.invoice, item.decision.id, data.sessions.auditor);
      setRun(result);
      setApproval(item.approval);
      setPayment(null);
      setSettlementRetryNeeded(false);
      setReplay(null);
      setSimulation(null);
      setPacketHash(null);
      setAuditTrail(await fetchInvoiceAudit(item.invoice.id, data.sessions.auditor));
      setEvidenceDocuments(await fetchInvoiceEvidence(item.invoice.id, data.sessions.auditor));
      navigateToView('review');
    });
  }, [act, data]);

  const handleDownloadSourceEvidence = useCallback((document: EvidenceDocument) => {
    if (!data) return;
    void act(`Downloading ${document.filename}`, async () => {
      await downloadEvidenceDocument(document, data.sessions.auditor);
    });
  }, [act, data]);

  const selectedScenario = useMemo(
    () => data?.scenarios.find((item) => item.key === selectedKey),
    [data, selectedKey],
  );
  const visibleReadiness = data?.readiness ?? accessContext?.readiness;
  const workspaceBadges: Partial<Record<WorkspaceView, string>> = {
    finance: operations ? String(operations.invoice_count) : undefined,
    payables: operations ? String(operations.invoice_count) : undefined,
    approvals: governance?.pendingApprovals.length ? String(governance.pendingApprovals.length) : undefined,
    vendors: vendorDirectory.length > 0 ? String(vendorDirectory.length) : undefined,
    audit: auditSearch?.events.length ? String(auditSearch.events.length) : undefined,
  };
  const setView = (view: WorkspaceView) => {
    if (window.location.hash !== `#${view}`) window.location.hash = view;
    setActiveView(view);
  };
  const navigateToView = (view: WorkspaceView) => {
    if (view === 'submit') setCorrectionSource(null);
    if (REQUESTER_VIEWS.includes(view) && portalRoleRef.current !== 'requester') {
      setView('login-user');
      return;
    }
    if (FINANCE_VIEWS.includes(view) && portalRoleRef.current !== 'finance') {
      setView('login-finance');
      return;
    }
    setView(view);
  };
  const startCorrection = (item: OperationsInvoice) => {
    setCorrectionSource(item);
    setView('submit');
  };
  const enterPortal = (role: PortalRole) => {
    window.sessionStorage.setItem(PORTAL_ROLE_KEY, role);
    portalRoleRef.current = role;
    setPortalRole(role);
    setView(role === 'requester' ? 'portal' : 'finance');
  };
  const signOutPortal = () => {
    window.sessionStorage.removeItem(PORTAL_ROLE_KEY);
    portalRoleRef.current = null;
    setPortalRole(null);
    setView('overview');
  };
  const requiresRequesterLogin = REQUESTER_VIEWS.includes(activeView) && portalRole !== 'requester';
  const requiresFinanceLogin = FINANCE_VIEWS.includes(activeView) && portalRole !== 'finance';
  return (
    <Theme theme="g10">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      {activeView === 'overview' || activeView === 'activity' ? (
        <LandingHeader
          busy={busy !== null}
          onNavigate={navigateToView}
          onTeam={() => setTeamOpen(true)}
        />
      ) : null}

      <Modal
        open={teamOpen}
        passiveModal
        modalHeading="Four roles. One isolated finance workspace."
        modalLabel="Role-separated access"
        onRequestClose={() => setTeamOpen(false)}
      >
        <div className="team-boundary">
          <p>
            Every browser receives four distinct, short-lived sessions inside one fresh tenant.
            Tokens stay in page memory, and no role can bypass the controls assigned to another.
          </p>
          <div className="team-boundary__workspace">
            <span>Current workspace</span>
            <code>{data?.workspaceId ?? 'Waiting for isolated workspace'}</code>
          </div>
          <div className="team-boundary__roles">
            {TEAM_ROLES.map(([name, description], index) => (
              <article key={name}>
                <span>{String(index + 1).padStart(2, '0')}</span>
                <div>
                  <strong>{name}</strong>
                  <p>{description}</p>
                </div>
              </article>
            ))}
          </div>
          <small>Public simulation only · no wallet credentials · no funds move</small>
        </div>
      </Modal>

      <Content id="main-content" tabIndex={-1} className={activeView === 'overview' || activeView === 'activity' ? 'landing-content' : 'premium-content'}>
        {(['finance', 'payables', 'review', 'approvals'] as WorkspaceView[]).includes(activeView) && error ? (
          <InlineNotification
            className="workspace-error"
            kind="error"
            title="Action stopped"
            subtitle={error}
            lowContrast
            onCloseButtonClick={() => setError(null)}
          />
        ) : null}
        {activeView === 'login-user' || requiresRequesterLogin ? (
          <PortalLoginPage intent="requester" busy={busy !== null} onContinue={enterPortal} onBack={() => setView('overview')} />
        ) : activeView === 'login-finance' || requiresFinanceLogin ? (
          <PortalLoginPage intent="finance" busy={busy !== null} onContinue={enterPortal} onBack={() => setView('overview')} />
        ) : activeView === 'overview' ? (
          <>
            {error ? (
              <InlineNotification
                className="landing-error"
                kind="error"
                title="Live workspace unavailable"
                subtitle={error}
                lowContrast
                onCloseButtonClick={() => setError(null)}
              />
            ) : null}
            <LandingPage
              ready={visibleReadiness?.status === 'ready'}
              network={visibleReadiness?.network ?? 'ARC-TESTNET'}
              operations={operations}
              reliability={data?.reliability ?? null}
              activity={arcActivity}
              activityError={arcActivityError}
              busy={busy !== null}
              onNavigate={navigateToView}
            />
          </>
        ) : activeView === 'activity' ? (
          <ArcActivityPage activity={arcActivity} error={arcActivityError} onBack={() => navigateToView('overview')} onRefresh={() => void refreshArcActivity()} />
        ) : accessContext ? (
          <PremiumWorkspaceFrame active="policies" index="Secure access" title="Connect the finance team" description="Four role-separated sessions unlock one isolated operating workspace. Tokens stay in this page's memory and are never published or stored in the browser." meta="Private operator mode" settlementMode={accessContext.readiness.settlement_mode} onNavigate={navigateToView} onSignOut={signOutPortal}>
            <OperatorAccessGate context={accessContext} busy={busy !== null} onConnect={handleOperatorConnect} />
          </PremiumWorkspaceFrame>
        ) : !data ? (
          <div className="premium-loading" aria-label="Loading finance workspace"><SkeletonText heading width="32%" /><SkeletonText paragraph lineCount={8} /></div>
        ) : activeView === 'portal' ? (
          <RequesterPortalPage operations={operations} approvals={requestApprovals} busy={busy !== null} settlementMode={data.readiness.settlement_mode} onNavigate={navigateToView} onStartCorrection={startCorrection} onSignOut={signOutPortal} />
        ) : activeView === 'submit' ? (
          <RequesterSubmitPage data={data} busy={busy !== null} error={error} correctionSource={correctionSource} correctionFeedback={correctionSource?.decision_id ? requestApprovals[correctionSource.decision_id]?.resolution_note ?? null : null} onNavigate={navigateToView} onSignOut={signOutPortal} onEvaluate={handleUploadedEvidence} />
        ) : activeView === 'finance' || activeView === 'payables' ? (
          <PremiumPayablesPage
            operations={operations}
            arcActivity={arcActivity}
            incidents={incidents}
            batch={batch}
            scheduleRun={scheduleRun}
            ledgerExport={ledgerExport}
            busy={busy !== null}
            onNavigate={navigateToView}
            onOpenReview={handleOpenInvoice}
            onSettleBatch={handleSettleBatch}
            onRunSchedules={handleRunSchedules}
            onExportLedger={handleExportLedger}
            onSeedShowcase={handleSeedAgentShowcase}
            onRefreshTreasury={handleRefreshLiveTreasury}
            onSignOut={signOutPortal}
            settlementMode={data?.readiness.settlement_mode ?? 'simulation'}
          />
        ) : activeView === 'review' ? (
          <InvoiceReviewPage
            run={run}
            approval={approval}
            payment={payment}
            busy={busy}
            auditTrail={auditTrail}
            replay={replay}
            simulation={simulation}
            packetHash={packetHash}
            evidenceDocuments={evidenceDocuments}
            settlementStopped={Boolean(governance?.activePolicy?.kill_switch_enabled)}
            settlementRetryNeeded={settlementRetryNeeded}
            onNavigate={navigateToView}
            onBack={() => navigateToView('finance')}
            onRun={() => handleRun('clean-payment')}
            onRequestApproval={handleRequestApproval}
            onSettle={handleSettle}
            onDownloadPacket={handleDownloadPacket}
            onDownloadSourceEvidence={handleDownloadSourceEvidence}
            onVerifyReplay={handleVerifyReplay}
            onSimulatePolicy={handleSimulatePolicy}
            onSignOut={signOutPortal}
            settlementMode={data?.readiness.settlement_mode ?? 'simulation'}
          />
        ) : activeView === 'approvals' && governance ? (
          <ApprovalPortalPage
            governance={governance}
            lastResolution={lastApprovalResolution}
            busy={busy}
            onNavigate={navigateToView}
            onOpenReview={handleOpenApprovalItem}
            onResolve={handleResolveInboxApproval}
            onSignOut={signOutPortal}
            settlementMode={data?.readiness.settlement_mode ?? 'simulation'}
          />
        ) : activeView === 'evidence' ? (
          <PremiumWorkspaceFrame active="evidence" index="01 / Evidence intake" title="Evidence control room" description="Inspect real invoice evidence or exercise the complete control library, then inspect the sealed decision behind each user request." meta={`${data.scenarios.length} control cases`} settlementMode={data.readiness.settlement_mode} onNavigate={navigateToView} onSignOut={signOutPortal}>
            {error ? <InlineNotification kind="error" title="Action stopped" subtitle={error} lowContrast onCloseButtonClick={() => setError(null)} /> : null}
            {visibleReadiness ? <RuntimeBoundary readiness={visibleReadiness} workspaceId={data.workspaceId} /> : null}
            <div className="premium-evidence-grid">
              <ScenarioRail scenarios={data.scenarios} activeKey={selectedKey} busy={busy !== null} onSelect={setSelectedKey} onRun={handleRun} mode={mode} onModeChange={setMode} onRunLive={handleRunLive} />
              <div className="premium-evidence-stage">
                {mode === 'live' ? <LiveEvidenceWorkbench busy={busy !== null} operatorToken={data.sessions.operator} onEvaluate={handleUploadedEvidence} /> : run ? <EvidencePanel run={run} documents={evidenceDocuments} busy={busy !== null} onDownloadDocument={handleDownloadSourceEvidence} /> : <EmptyWorkbench scenario={selectedScenario} />}
                {run ? <button type="button" className="premium-continue" onClick={() => navigateToView('review')}>Open decision review <ArrowRight size={16} /></button> : null}
              </div>
            </div>
          </PremiumWorkspaceFrame>
        ) : activeView === 'automation' ? (
          <PremiumWorkspaceFrame active="automation" index="02 / Agent execution" title="Autonomous run center" description="Plan a bounded queue, show every proposed action, execute only policy-cleared work, and export one content-addressed proof packet." meta={agentRun ? `${agentRun.summary.scanned} invoices scanned` : 'Ready to plan'} settlementMode={data.readiness.settlement_mode} onNavigate={navigateToView} onSignOut={signOutPortal}>
            {error ? <InlineNotification kind="error" title="Action stopped" subtitle={error} lowContrast onCloseButtonClick={() => setError(null)} /> : null}
            {visibleReadiness ? <RuntimeBoundary readiness={visibleReadiness} workspaceId={data.workspaceId} /> : null}
            <AutonomousRunPanel run={agentRun} busy={busy !== null} settlementStopped={Boolean(governance?.activePolicy?.kill_switch_enabled)} onPlan={handlePlanAgentRun} onExecute={handleExecuteAgentRun} onDownloadProof={handleDownloadAgentProof} onSeedShowcase={handleSeedAgentShowcase} proofHash={agentProofHash} />
          </PremiumWorkspaceFrame>
        ) : activeView === 'vendors' ? (
          <PremiumWorkspaceFrame active="vendors" index="03 / Payout identity" title="Vendor trust directory" description="Verify who can be paid, bind every invoice to an approved Arc wallet, and make wallet changes visible before money moves." meta={`${vendorDirectory.length} trust records`} settlementMode={data.readiness.settlement_mode} onNavigate={navigateToView} onSignOut={signOutPortal}>
            <VendorTrustPanel records={vendorDirectory} activeInvoice={run?.invoice ?? null} busy={busy !== null} onOpenEvidence={() => navigateToView('evidence')} onOnboard={handleOnboardVendor} onRotateWallet={handleRotateVendorWallet} />
          </PremiumWorkspaceFrame>
        ) : activeView === 'policies' ? (
          <PremiumWorkspaceFrame active="policies" index="04 / Governance" title="Payment policies" description="Define the authority an agent can exercise, version policy changes, and keep policy administration separate from payment approval." meta={governance?.activePolicy ? `${governance.activePolicy.version} active` : 'No active policy'} settlementMode={data.readiness.settlement_mode} onNavigate={navigateToView} onSignOut={signOutPortal}>
            {visibleReadiness ? <RuntimeBoundary readiness={visibleReadiness} workspaceId={data.workspaceId} /> : null}
            {governance ? <GovernancePanel governance={governance} policyHistory={policyHistory} treasurySnapshot={treasurySnapshot} busy={busy !== null} policyActivation={policyActivation} onActivatePolicy={handleActivatePolicy} onRecordTreasury={handleRecordTreasury} /> : null}
          </PremiumWorkspaceFrame>
        ) : (
          <PremiumWorkspaceFrame active="audit" index="05 / Audit proof" title="Audit & reliability" description="Search the hash-linked tenant ledger, inspect operational handoffs, and verify the multi-tenant and idempotency evidence behind the product." meta={`${auditSearch?.events.length ?? 0} recent ledger events`} settlementMode={data.readiness.settlement_mode} onNavigate={navigateToView} onSignOut={signOutPortal}>
            {auditSearch ? <AuditExplorer result={auditSearch} busy={busy !== null} onSearch={handleAuditSearch} onLoadMore={handleLoadMoreAudit} /> : null}
            <ReliabilityPanel evidence={data.reliability} />
          </PremiumWorkspaceFrame>
        )}
      </Content>
    </Theme>
  );
}

export default App;

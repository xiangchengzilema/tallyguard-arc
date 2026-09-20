import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Button,
  Content,
  Header,
  HeaderGlobalBar,
  HeaderGlobalAction,
  HeaderName,
  InlineLoading,
  InlineNotification,
  SkeletonText,
  Tag,
  Theme,
} from '@carbon/react';
import {
  ArrowRight,
  CheckmarkFilled,
  Document,
  Download,
  Launch,
  Locked,
  Money,
  PlayFilled,
  Renew,
  Rule,
  Time,
  UserMultiple,
  Wallet,
  WarningAltFilled,
} from '@carbon/icons-react';
import {
  ApiError,
  activatePolicyVersion,
  bootstrap,
  createAgentRun,
  downloadAgentRunProof,
  downloadEvidencePacket,
  executeAgentRun,
  fetchGovernanceOverview,
  fetchInvoiceAudit,
  fetchOperationsOverview,
  fetchSettlementIncidents,
  fetchVendorDirectory,
  requestApproval,
  reviewEvidenceFiles,
  resolveApproval,
  runLiveEvidenceWorkflow,
  runUploadedEvidenceWorkflow,
  runScenario,
  runDueSchedules,
  seedAutonomyShowcase,
  settleInvoice,
  settlePaymentBatch,
  simulateDecisionPolicy,
  verifyDecisionReplay,
} from './api';
import type {
  Approval,
  AgentRun,
  AgentAction,
  AuditTrail,
  BootstrapData,
  DecisionAction,
  EvidenceFileBundle,
  EvidenceFileReview,
  GovernanceOverview,
  OperationsOverview,
  Payment,
  PaymentBatch,
  PolicyActivation,
  PolicyDraft,
  PolicySimulation,
  ReliabilityEvidence,
  ReplayVerification,
  RuleDisposition,
  RunResult,
  ScheduleRun,
  Scenario,
  SettlementIncidentOverview,
  VendorTrustRecord,
} from './types';

const ACTION_TAG: Record<DecisionAction, 'green' | 'red' | 'magenta' | 'purple' | 'blue'> = {
  PAY: 'green',
  HOLD: 'magenta',
  REJECT: 'red',
  ESCALATE: 'purple',
  SCHEDULE: 'blue',
};

const ACTION_LABEL: Record<DecisionAction, string> = {
  PAY: 'Cleared to pay',
  HOLD: 'Payment held',
  REJECT: 'Payment rejected',
  ESCALATE: 'Approval required',
  SCHEDULE: 'Scheduled',
};

const shorten = (value: string, head = 8, tail = 6) =>
  value.length > head + tail + 3 ? `${value.slice(0, head)}...${value.slice(-tail)}` : value;

const formatMoney = (value: string) =>
  new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(Number(value));

const LIVE_STEPS = [
  ['01', 'Verify vendor', 'Bind a signed Arc wallet proof to a tenant vendor.'],
  ['02', 'Lock controls', 'Activate an immutable policy and treasury snapshot.'],
  ['03', 'Ingest evidence', 'Hash and upload invoice, PO, and delivery JSON.'],
  ['04', 'Evaluate', 'Compare the agent opinion with deterministic controls.'],
] as const;

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
  return (
    <div className="metrics-band" aria-label="Persistent finance operations summary">
      <Metric label="Open exposure" value={`${formatMoney(overview.open_exposure_usdc)} USDC`} detail={`${overview.invoice_count} durable invoices · ${sessionEvaluations} this session`} />
      <Metric label="Blocked value" value={`${formatMoney(overview.blocked_exposure_usdc)} USDC`} detail="Held, rejected, or approval-gated" />
      <Metric label="Due within 7 days" value={`${formatMoney(overview.due_next_7_days_usdc)} USDC`} detail={`${overview.due_next_7_days_count} invoices · ${overview.overdue_count} overdue`} />
      <Metric label="Projected liquidity" value={projected === null ? 'Awaiting treasury' : `${formatMoney(projected)} USDC`} detail={overview.minimum_reserve_usdc === null ? 'Record a treasury snapshot' : `${formatMoney(overview.minimum_reserve_usdc)} USDC minimum reserve`} />
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
        <div><strong>{report.summary.duplicate_storm_requests} → {report.summary.duplicate_storm_provider_submissions}</strong><span>retry storm convergence</span><small>{report.summary.duplicate_payment_count} duplicate payments</small></div>
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
  onSettleBatch,
  onRunSchedules,
}: {
  overview: OperationsOverview;
  busy: boolean;
  batch: PaymentBatch | null;
  scheduleRun: ScheduleRun | null;
  settlementStopped: boolean;
  onSettleBatch: (items: Array<{ invoice_id: string; decision_id: string }>) => void;
  onRunSchedules: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const payable = overview.work_queue.filter((invoice) => (
    invoice.status === 'READY' || (invoice.status === 'SUBMISSION_FAILED' && invoice.settlement_retryable)
  ) && invoice.decision_id);
  const scheduled = overview.work_queue.filter((invoice) => invoice.status === 'SCHEDULED');
  useEffect(() => {
    const available = new Set(payable.map((invoice) => invoice.id));
    setSelected((current) => new Set([...current].filter((invoiceId) => available.has(invoiceId))));
  }, [overview]);
  const selectedItems = payable
    .filter((invoice) => selected.has(invoice.id))
    .map((invoice) => ({ invoice_id: invoice.id, decision_id: invoice.decision_id! }));
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
      ) : (
        <div className="operations-table" role="table" aria-label="Open invoices sorted by due date">
          <div className="operations-table__head" role="row">
            <span role="columnheader">Select</span><span role="columnheader">Invoice</span><span role="columnheader">Vendor</span><span role="columnheader">Due</span><span role="columnheader">Exposure</span><span role="columnheader">State</span>
          </div>
          {overview.work_queue.map((invoice) => (
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
  busy,
  policyActivation,
  onActivatePolicy,
  onResolve,
}: {
  governance: GovernanceOverview;
  busy: boolean;
  policyActivation: PolicyActivation | null;
  onActivatePolicy: (draft: PolicyDraft) => void;
  onResolve: (item: GovernanceOverview['pendingApprovals'][number], approve: boolean) => void;
}) {
  const policy = governance.activePolicy;
  const capacity = governance.settlementCapacity;
  const defaultDraft = useCallback((): PolicyDraft => ({
    daily_payment_limit_usdc: policy?.daily_payment_limit_usdc ?? '5000',
    minimum_cash_reserve_usdc: policy?.minimum_cash_reserve_usdc ?? '3000',
    maximum_autonomous_payment_usdc: policy?.maximum_autonomous_payment_usdc ?? '2000',
    po_amount_tolerance_usdc: policy?.po_amount_tolerance_usdc ?? '0',
    allowed_asset: policy?.allowed_asset ?? 'USDC',
    allowed_network: policy?.allowed_network ?? 'ARC-TESTNET',
    kill_switch_enabled: policy?.kill_switch_enabled ?? false,
    schedule_payments_before_due_days: policy?.schedule_payments_before_due_days ?? null,
  }), [policy]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [draft, setDraft] = useState<PolicyDraft>(defaultDraft);
  useEffect(() => { setDraft(defaultDraft()); }, [defaultDraft]);
  const updateMoney = (field: keyof Pick<PolicyDraft, 'daily_payment_limit_usdc' | 'minimum_cash_reserve_usdc' | 'maximum_autonomous_payment_usdc' | 'po_amount_tolerance_usdc'>, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
  };
  const changedCount = policy
    ? [
        policy.daily_payment_limit_usdc !== draft.daily_payment_limit_usdc,
        policy.minimum_cash_reserve_usdc !== draft.minimum_cash_reserve_usdc,
        policy.maximum_autonomous_payment_usdc !== draft.maximum_autonomous_payment_usdc,
        policy.po_amount_tolerance_usdc !== draft.po_amount_tolerance_usdc,
        policy.kill_switch_enabled !== draft.kill_switch_enabled,
        policy.schedule_payments_before_due_days !== draft.schedule_payments_before_due_days,
      ].filter(Boolean).length
    : 1;
  const canActivate = changedCount > 0 && [
    draft.daily_payment_limit_usdc,
    draft.minimum_cash_reserve_usdc,
    draft.maximum_autonomous_payment_usdc,
    draft.po_amount_tolerance_usdc,
  ].every((value) => value.trim() !== '' && Number.isFinite(Number(value)) && Number(value) >= 0);
  return (
    <section className="governance-panel" aria-label="Policy governance and approval inbox">
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
            <div><span>Autonomy cap</span><strong>{formatMoney(policy.maximum_autonomous_payment_usdc)} USDC</strong><small>Above this requires a separate approver</small></div>
            <div><span>Daily limit</span><strong>{formatMoney(policy.daily_payment_limit_usdc)} USDC</strong><small>{formatMoney(policy.minimum_cash_reserve_usdc)} USDC reserve floor</small></div>
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
              <div><span>Reserve floor</span><strong>{formatMoney(capacity.minimum_cash_reserve_usdc)}</strong></div>
            </div>
            <small>Snapshot #{capacity.treasury_snapshot_sequence} · {capacity.snapshot_age_seconds}s old. Competing workers reserve inside one database transaction before any provider call.</small>
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
              <label><span>Autonomy cap (USDC)</span><input type="number" min="0" step="0.01" value={draft.maximum_autonomous_payment_usdc} onChange={(event) => updateMoney('maximum_autonomous_payment_usdc', event.target.value)} /></label>
              <label><span>Daily limit (USDC)</span><input type="number" min="0" step="0.01" value={draft.daily_payment_limit_usdc} onChange={(event) => updateMoney('daily_payment_limit_usdc', event.target.value)} /></label>
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
      </div>
      <div className="approval-inbox">
        <div className="governance-panel__head">
          <div><span className="eyebrow">Segregated approval</span><h2>Exception inbox</h2></div>
          <Tag type={governance.pendingApprovals.length > 0 ? 'purple' : 'cool-gray'}>{governance.pendingApprovals.length} pending</Tag>
        </div>
        {governance.pendingApprovals.length === 0 ? (
          <div className="approval-inbox__empty"><CheckmarkFilled size={18} /><span>No policy exceptions await a second role.</span></div>
        ) : (
          <div className="approval-inbox__list">
            {governance.pendingApprovals.map((item) => (
              <article key={item.approval.id}>
                <div><strong>{item.invoice.invoice_number}</strong><small>{item.invoice.vendor_id} · due {item.invoice.due_date}</small></div>
                <div><strong>{formatMoney(item.invoice.amount)} {item.invoice.currency}</strong><code>{item.decision.reason_codes.join(' · ')}</code></div>
                <div className="approval-inbox__actions">
                  <Button size="sm" kind="danger--tertiary" disabled={busy} onClick={() => onResolve(item, false)}>Reject</Button>
                  <Button size="sm" disabled={busy} onClick={() => onResolve(item, true)}>Approve exception</Button>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function VendorTrustPanel({
  records,
  activeInvoice,
}: {
  records: VendorTrustRecord[];
  activeInvoice: RunResult['invoice'] | null;
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
        <div className="vendor-trust__empty"><Wallet size={20} /><span>Run a scenario or review uploaded evidence to create a verified payout identity.</span></div>
      )}
    </section>
  );
}

function RuntimeBoundary({ readiness }: { readiness: BootstrapData['readiness'] }) {
  const simulated = readiness.settlement_mode === 'simulation';
  return (
    <section className={simulated ? 'runtime-boundary' : 'runtime-boundary runtime-boundary--live'} aria-label="Runtime safety boundary">
      <div className="runtime-boundary__lead">
        <span className="runtime-boundary__marker" aria-hidden="true"><Locked size={18} /></span>
        <div>
          <span className="eyebrow">Runtime boundary</span>
          <strong>{simulated ? 'Judge simulation — no funds move' : 'Circle wallet execution enabled'}</strong>
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
  onEvaluate,
}: {
  busy: boolean;
  onEvaluate: (files: EvidenceFileBundle) => void;
}) {
  const [files, setFiles] = useState<Partial<EvidenceFileBundle>>({});
  const [review, setReview] = useState<EvidenceFileReview | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);

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
    reviewEvidenceFiles(bundle)
      .then((result) => { if (!cancelled) setReview(result); })
      .catch((reason: unknown) => {
        if (!cancelled) setReviewError(reason instanceof Error ? reason.message : 'Could not review these files.');
      })
      .finally(() => { if (!cancelled) setReviewing(false); });
    return () => { cancelled = true; };
  }, [files.delivery, files.invoice, files.purchaseOrder]);

  const updateFile = (key: keyof EvidenceFileBundle, file?: File) => {
    setFiles((current) => ({ ...current, [key]: file }));
  };
  const completeBundle = files.invoice && files.purchaseOrder && files.delivery
    ? { invoice: files.invoice, purchaseOrder: files.purchaseOrder, delivery: files.delivery }
    : null;

  return (
    <section className="live-workbench">
      <div className="live-workbench__intro">
        <div className="empty-workbench__icon"><Document size={32} /></div>
        <div>
          <span className="eyebrow">Bring your own evidence</span>
          <h2>Review before the agent decides.</h2>
          <p>Select three structured JSON documents. They stay in this workflow, are hashed before storage, and cannot be replaced after evaluation begins.</p>
        </div>
      </div>
      <div className="upload-grid">
        {([
          ['invoice', 'Invoice JSON', 'invoice_id · vendor_id · amount · wallet'],
          ['purchaseOrder', 'Purchase order JSON', 'purchase_order_id · vendor_id · authorized_amount'],
          ['delivery', 'Delivery JSON', 'delivery_id · purchase_order_id · delivered_value'],
        ] as const).map(([key, label, hint]) => (
          <label className={files[key] ? 'upload-slot upload-slot--ready' : 'upload-slot'} key={key}>
            <span>{files[key] ? <CheckmarkFilled size={18} /> : <Document size={18} />}</span>
            <strong>{label}</strong>
            <small>{files[key]?.name ?? hint}</small>
            <input
              aria-label={`Upload ${label}`}
              accept="application/json,.json"
              type="file"
              onChange={(event) => updateFile(key, event.target.files?.[0])}
            />
          </label>
        ))}
      </div>
      {reviewing ? <InlineLoading description="Reading and validating local evidence" status="active" /> : null}
      {reviewError ? (
        <InlineNotification kind="error" title="Evidence review stopped" subtitle={reviewError} lowContrast hideCloseButton />
      ) : null}
      {review ? (
        <div className="evidence-review" aria-label="Extracted evidence review">
          <div className="evidence-review__head">
            <div><span className="eyebrow">Extraction review</span><h3>{review.invoiceNumber}</h3></div>
            <Tag type="teal">Schema valid</Tag>
          </div>
          <div className="evidence-review__grid">
            <div><span>Vendor</span><code>{review.vendorId}</code></div>
            <div><span>Requested</span><strong>{formatMoney(review.amount)} {review.currency}</strong></div>
            <div><span>PO authorized</span><strong>{formatMoney(review.authorizedAmount)} {review.currency}</strong></div>
            <div><span>Delivered</span><strong>{formatMoney(review.deliveredValue)} {review.currency}</strong></div>
            <div><span>Due</span><strong>{review.dueDate}</strong></div>
            <div><span>Recipient</span><code>{shorten(review.walletAddress, 10, 8)}</code></div>
          </div>
          <div className="review-action">
            <p>Confirm these extracted values before creating immutable tenant records.</p>
            <Button
              disabled={!completeBundle || busy}
              renderIcon={busy ? Renew : ArrowRight}
              onClick={() => { if (completeBundle) onEvaluate(completeBundle); }}
            >
              {busy ? 'Evaluating evidence' : 'Confirm and evaluate'}
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

function EvidencePanel({ run }: { run: RunResult }) {
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

      <div className="rule-table" role="table" aria-label="Policy rule results">
        <div className="rule-table__head" role="row">
          <span role="columnheader">Control</span>
          <span role="columnheader">Finding</span>
          <span role="columnheader">Result</span>
        </div>
        {decision.rules.map((rule) => (
          <div className="rule-table__row" role="row" key={rule.code}>
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
          <ul>{decision.remediation.map((item) => <li key={item}>{item}</li>)}</ul>
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
          <Button renderIcon={CheckmarkFilled} onClick={onApprove} disabled={busy !== null}>
            Approve as separate role
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
        <div><span className="eyebrow">{simulated ? 'Reconciled simulation' : 'Reconciled settlement'}</span><h2>{formatMoney(receipt.confirmed_amount_usdc)} USDC confirmed</h2></div>
        <Tag type={simulated ? 'cool-gray' : 'green'}>{simulated ? 'SIMULATED' : receipt.status}</Tag>
      </div>
      <div className="receipt-grid">
        <div><span>Execution provider</span><strong>{receipt.provider}</strong></div>
        <div><span>Arc network</span><strong>{receipt.network}</strong></div>
        <div><span>Block</span><strong>#{receipt.block_number.toLocaleString()}</strong></div>
        <div><span>Exactly-once guard</span><strong>{payment.reused_receipt ? 'Existing receipt reused' : 'New intent persisted'}</strong></div>
        <div className="receipt-grid__wide"><span>Transaction hash</span><code>{receipt.transaction_hash}</code></div>
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

function App() {
  const [data, setData] = useState<BootstrapData | null>(null);
  const [selectedKey, setSelectedKey] = useState('clean-payment');
  const [run, setRun] = useState<RunResult | null>(null);
  const [history, setHistory] = useState<RunResult[]>([]);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [payment, setPayment] = useState<Payment | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<'scenario' | 'live'>('scenario');
  const [auditTrail, setAuditTrail] = useState<AuditTrail | null>(null);
  const [replay, setReplay] = useState<ReplayVerification | null>(null);
  const [operations, setOperations] = useState<OperationsOverview | null>(null);
  const [incidents, setIncidents] = useState<SettlementIncidentOverview | null>(null);
  const [governance, setGovernance] = useState<GovernanceOverview | null>(null);
  const [batch, setBatch] = useState<PaymentBatch | null>(null);
  const [scheduleRun, setScheduleRun] = useState<ScheduleRun | null>(null);
  const [packetHash, setPacketHash] = useState<string | null>(null);
  const [simulation, setSimulation] = useState<PolicySimulation | null>(null);
  const [policyActivation, setPolicyActivation] = useState<PolicyActivation | null>(null);
  const [vendorDirectory, setVendorDirectory] = useState<VendorTrustRecord[]>([]);
  const [settlementRetryNeeded, setSettlementRetryNeeded] = useState(false);
  const [agentRun, setAgentRun] = useState<AgentRun | null>(null);
  const [agentProofHash, setAgentProofHash] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    bootstrap()
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setOperations(result.operations);
          setIncidents(result.incidents);
          setAgentRun(result.agentRun);
          setGovernance(result.governance);
          setVendorDirectory(result.vendorDirectory);
          setSelectedKey(result.scenarios[0]?.key ?? 'clean-payment');
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not initialize TallyGuard.');
      });
    return () => { cancelled = true; };
  }, []);

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

  const handleRun = useCallback((key: string) => {
    if (!data) return;
    void act('Evaluating immutable evidence and policy rules', async () => {
      const result = await runScenario(key, data.sessions.operator);
      setRun(result);
      setHistory((items) => [result, ...items].slice(0, 12));
      setApproval(null);
      setPayment(null);
      setSettlementRetryNeeded(false);
      setReplay(null);
      setSimulation(null);
      setAuditTrail(await fetchInvoiceAudit(result.invoice.id, data.sessions.auditor));
      setPacketHash(null);
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setVendorDirectory(await fetchVendorDirectory(data.sessions.auditor));
    });
  }, [act, data]);

  const handleRequestApproval = useCallback(() => {
    if (!data || !run) return;
    void act('Creating a role-separated approval request', async () => {
      setApproval(await requestApproval(run.decision.id, data.sessions.operator));
      setAuditTrail(await fetchInvoiceAudit(run.invoice.id, data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setVendorDirectory(await fetchVendorDirectory(data.sessions.auditor));
    });
  }, [act, data, run]);

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
      setPacketHash(null);
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setVendorDirectory(await fetchVendorDirectory(data.sessions.auditor));
    });
  }, [act, data]);

  const handleUploadedEvidence = useCallback((files: EvidenceFileBundle) => {
    if (!data) return;
    void act('Persisting and evaluating your reviewed evidence', async () => {
      const result = await runUploadedEvidenceWorkflow(
        data.sessions.admin,
        data.sessions.operator,
        files,
      );
      setRun(result);
      setHistory((items) => [result, ...items].slice(0, 12));
      setApproval(null);
      setPayment(null);
      setSettlementRetryNeeded(false);
      setReplay(null);
      setSimulation(null);
      setAuditTrail(await fetchInvoiceAudit(result.invoice.id, data.sessions.auditor));
      setPacketHash(null);
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setVendorDirectory(await fetchVendorDirectory(data.sessions.auditor));
    });
  }, [act, data]);

  const handleApprove = useCallback(() => {
    if (!data || !approval) return;
    void act('Verifying and signing the approval record', async () => {
      setApproval(await resolveApproval(approval, data.sessions.approver));
      if (run) setAuditTrail(await fetchInvoiceAudit(run.invoice.id, data.sessions.auditor));
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
    });
  }, [act, approval, data, run]);

  const handleResolveInboxApproval = useCallback((item: GovernanceOverview['pendingApprovals'][number], approve: boolean) => {
    if (!data) return;
    void act(approve ? 'Approving the exception as a separate role' : 'Rejecting the policy exception', async () => {
      const resolved = await resolveApproval(
        item.approval,
        data.sessions.approver,
        approve,
        approve
          ? 'Evidence and control exception reviewed in the finance approval inbox.'
          : 'Exception rejected in the finance approval inbox.',
      );
      if (approval?.id === resolved.id) setApproval(resolved);
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      if (run?.invoice.id === item.invoice.id) {
        setAuditTrail(await fetchInvoiceAudit(item.invoice.id, data.sessions.auditor));
      }
    });
  }, [act, approval, data, run]);

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
    });
  }, [act, data, governance]);

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
      }
    });
  }, [act, approval, data, run]);

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

  const handleSettleBatch = useCallback((items: Array<{ invoice_id: string; decision_id: string }>) => {
    if (!data || items.length === 0) return;
    void act(`Reconciling ${items.length} selected Arc payments`, async () => {
      setBatch(await settlePaymentBatch(items, data.sessions.approver));
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setIncidents(await fetchSettlementIncidents(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
    });
  }, [act, data]);

  const handlePlanAgentRun = useCallback(() => {
    if (!data) return;
    void act('Planning a bounded autonomous accounts-payable run', async () => {
      setAgentRun(await createAgentRun(data.sessions.operator));
      setAgentProofHash(null);
    });
  }, [act, data]);

  const handleSeedAgentShowcase = useCallback(() => {
    if (!data) return;
    void act('Building a mixed autonomous accounts-payable queue', async () => {
      await seedAutonomyShowcase(data.sessions.operator);
      setAgentRun(await createAgentRun(data.sessions.operator));
      setAgentProofHash(null);
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
      setVendorDirectory(await fetchVendorDirectory(data.sessions.auditor));
    });
  }, [act, data]);

  const handleExecuteAgentRun = useCallback(() => {
    if (!data || !agentRun) return;
    void act('Revalidating and executing policy-cleared agent actions', async () => {
      setAgentRun(await executeAgentRun(agentRun.id, data.sessions.approver));
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
      setIncidents(await fetchSettlementIncidents(data.sessions.auditor));
      setGovernance(await fetchGovernanceOverview(data.sessions.approver));
    });
  }, [act, agentRun, data]);

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
    });
  }, [act, data]);

  const handleDownloadPacket = useCallback(() => {
    if (!data || !run) return;
    void act('Assembling a content-addressed payment evidence packet', async () => {
      setPacketHash(await downloadEvidencePacket(run.invoice.id, data.sessions.auditor));
    });
  }, [act, data, run]);

  const selectedScenario = useMemo(
    () => data?.scenarios.find((item) => item.key === selectedKey),
    [data, selectedKey],
  );
  return (
    <Theme theme="g10">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <Header aria-label="TallyGuard">
        <HeaderName prefix="">TallyGuard</HeaderName>
        <div className="header-context">Evidence-bound accounts payable on Arc</div>
        <HeaderGlobalBar>
          <HeaderGlobalAction aria-label="Role-separated team"><UserMultiple size={20} /></HeaderGlobalAction>
        </HeaderGlobalBar>
      </Header>

      <Content id="main-content">
        <div className="context-bar">
          <div>
            <span className="eyebrow">Finance control plane / Judge workspace</span>
            <h1>Approve the evidence. Automate the payment.</h1>
          </div>
          <div className="system-state">
            <span className="live-dot" aria-hidden="true" />
            <div>
              <strong>{data?.readiness.status === 'ready' ? 'Controls online' : 'Connecting'}</strong>
              <small>{data?.readiness.network ?? 'ARC-TESTNET'} · {data?.readiness.settlement_adapter ?? 'checking adapter'} · {data?.readiness.evidence_analyst ?? 'checking analyst'}</small>
            </div>
          </div>
        </div>

        {error ? (
          <InlineNotification
            className="error-notice"
            kind="error"
            title="Action stopped"
            subtitle={error}
            lowContrast
            onCloseButtonClick={() => setError(null)}
          />
        ) : null}

        {data ? <RuntimeBoundary readiness={data.readiness} /> : null}

        {operations ? <OperationsBand overview={operations} sessionEvaluations={history.length} /> : (
          <div className="metrics-band" aria-label="Loading finance operations summary">
            <Metric label="Open exposure" value="—" detail="Loading durable invoices" />
            <Metric label="Blocked value" value="—" detail="Loading control outcomes" />
            <Metric label="Due within 7 days" value="—" detail="Loading due dates" />
            <Metric label="Projected liquidity" value="—" detail="Loading treasury state" />
          </div>
        )}

        {!data ? (
          <div className="loading-layout" aria-label="Loading judge console">
            <SkeletonText heading width="32%" /><SkeletonText paragraph lineCount={8} />
          </div>
        ) : (
          <div className="workspace-grid">
            <ScenarioRail
              scenarios={data.scenarios}
              activeKey={selectedKey}
              busy={busy !== null}
              onSelect={(key) => { setSelectedKey(key); setRun(null); setApproval(null); setPayment(null); setSettlementRetryNeeded(false); setReplay(null); setSimulation(null); setAuditTrail(null); setPacketHash(null); }}
              onRun={handleRun}
              mode={mode}
              onModeChange={(nextMode) => { setMode(nextMode); setRun(null); setApproval(null); setPayment(null); setSettlementRetryNeeded(false); setReplay(null); setSimulation(null); setAuditTrail(null); setPacketHash(null); }}
              onRunLive={handleRunLive}
            />
            <main className="workbench">
              {run ? (
                <>
                  <div className="run-meta">
                    <span><CheckmarkFilled size={16} /> Evaluation complete</span>
                    <code>{run.correlation_id}</code>
                  </div>
                  <div className="decision-grid">
                    <EvidencePanel run={run} />
                    <DecisionPanel
                      run={run}
                      approval={approval}
                      payment={payment}
                      busy={busy}
                      replay={replay}
                      simulation={simulation}
                      settlementStopped={governance?.activePolicy?.kill_switch_enabled ?? false}
                      settlementRetryNeeded={settlementRetryNeeded}
                      onRequestApproval={handleRequestApproval}
                      onApprove={handleApprove}
                      onSettle={handleSettle}
                      onVerifyReplay={handleVerifyReplay}
                      onSimulatePolicy={handleSimulatePolicy}
                    />
                  </div>
                  {payment ? <ReceiptPanel payment={payment} /> : null}
                  {auditTrail ? (
                    <AuditTimeline
                      trail={auditTrail}
                      packetHash={packetHash}
                      busy={busy !== null}
                      onDownloadPacket={handleDownloadPacket}
                    />
                  ) : null}
                </>
              ) : mode === 'live' ? (
                <LiveEvidenceWorkbench busy={busy !== null} onEvaluate={handleUploadedEvidence} />
              ) : <EmptyWorkbench scenario={selectedScenario} />}
            </main>
          </div>
        )}

        {operations ? (
          <AutonomousRunPanel
            run={agentRun}
            busy={busy !== null}
            settlementStopped={governance?.activePolicy?.kill_switch_enabled ?? false}
            onPlan={handlePlanAgentRun}
            onExecute={handleExecuteAgentRun}
            onDownloadProof={handleDownloadAgentProof}
            onSeedShowcase={handleSeedAgentShowcase}
            proofHash={agentProofHash}
          />
        ) : null}

        {operations ? (
          <OperationsQueue
            overview={operations}
            busy={busy !== null}
            batch={batch}
            scheduleRun={scheduleRun}
            settlementStopped={governance?.activePolicy?.kill_switch_enabled ?? false}
            onSettleBatch={handleSettleBatch}
            onRunSchedules={handleRunSchedules}
          />
        ) : null}

        {incidents ? <SettlementIncidentCenter overview={incidents} /> : null}

        {governance ? (
          <GovernancePanel
            governance={governance}
            busy={busy !== null}
            policyActivation={policyActivation}
            onActivatePolicy={handleActivatePolicy}
            onResolve={handleResolveInboxApproval}
          />
        ) : null}

        <VendorTrustPanel records={vendorDirectory} activeInvoice={run?.invoice ?? null} />

        {data ? <ReliabilityPanel evidence={data.reliability} /> : null}

        <footer className="product-footer">
          <div><Locked size={16} /> Tenant scoped · Versioned policy · Idempotent settlement · Independent Arc RPC proof</div>
          <span>Built for Tameion Agents Hackathon 2026</span>
        </footer>
      </Content>
    </Theme>
  );
}

export default App;

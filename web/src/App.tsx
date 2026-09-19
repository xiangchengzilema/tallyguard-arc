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
  UserMultiple,
  Wallet,
  WarningAltFilled,
} from '@carbon/icons-react';
import {
  ApiError,
  bootstrap,
  downloadEvidencePacket,
  fetchInvoiceAudit,
  fetchOperationsOverview,
  requestApproval,
  reviewEvidenceFiles,
  resolveApproval,
  runLiveEvidenceWorkflow,
  runUploadedEvidenceWorkflow,
  runScenario,
  settleInvoice,
  settlePaymentBatch,
  verifyDecisionReplay,
} from './api';
import type {
  Approval,
  AuditTrail,
  BootstrapData,
  DecisionAction,
  EvidenceFileBundle,
  EvidenceFileReview,
  OperationsOverview,
  Payment,
  PaymentBatch,
  ReliabilityEvidence,
  ReplayVerification,
  RuleDisposition,
  RunResult,
  Scenario,
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
  const { report, artifact } = evidence;
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
      </div>
      <div className="reliability-latency">
        <div><span>Overall P95</span><strong>{report.latency_ms.overall.p95.toFixed(0)} ms</strong></div>
        <div><span>Settlement P95</span><strong>{settlementLatency?.p95.toFixed(0) ?? '—'} ms</strong></div>
        <div><span>Failed workflows</span><strong>{report.summary.failed_workflows}</strong></div>
        <div><span>Artifact proof</span><code>{shorten(artifact.sha256, 12, 10)}</code></div>
      </div>
      <div className="reliability-panel__foot">
        <div><CheckmarkFilled size={16} /><span>Immutable report · {new Date(report.generated_at).toLocaleDateString()} · {shorten(report.run_id, 12, 8)}</span></div>
        <p>{report.methodology.note} {report.methodology.settlement}.</p>
      </div>
    </section>
  );
}

function OperationsQueue({
  overview,
  busy,
  batch,
  onSettleBatch,
}: {
  overview: OperationsOverview;
  busy: boolean;
  batch: PaymentBatch | null;
  onSettleBatch: (items: Array<{ invoice_id: string; decision_id: string }>) => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const payable = overview.work_queue.filter((invoice) => invoice.status === 'READY' && invoice.decision_id);
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
          {payable.length > 0 ? (
            <Button
              size="sm"
              renderIcon={Money}
              disabled={busy || selectedItems.length === 0}
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
      {overview.work_queue.length === 0 ? (
        <div className="operations-queue__empty"><CheckmarkFilled size={20} /><span>No open invoices. Run a control case or upload evidence to populate the durable queue.</span></div>
      ) : (
        <div className="operations-table" role="table" aria-label="Open invoices sorted by due date">
          <div className="operations-table__head" role="row">
            <span role="columnheader">Select</span><span role="columnheader">Invoice</span><span role="columnheader">Vendor</span><span role="columnheader">Due</span><span role="columnheader">Exposure</span><span role="columnheader">State</span>
          </div>
          {overview.work_queue.map((invoice) => (
            <div className="operations-table__row" role="row" key={invoice.id}>
              <label className="batch-select" title={invoice.status === 'READY' ? 'Select for batch settlement' : 'Only READY invoices can be settled'}>
                <input
                  aria-label={`Select ${invoice.invoice_number} for batch settlement`}
                  type="checkbox"
                  checked={selected.has(invoice.id)}
                  disabled={busy || invoice.status !== 'READY' || !invoice.decision_id}
                  onChange={() => toggle(invoice.id)}
                />
              </label>
              <div role="cell"><strong>{invoice.invoice_number}</strong><code>{shorten(invoice.id, 12, 6)}</code></div>
              <code role="cell">{shorten(invoice.vendor_id, 13, 6)}</code>
              <span role="cell">{invoice.due_date}</span>
              <strong role="cell">{formatMoney(invoice.amount)} {invoice.currency}</strong>
              <Tag type={invoice.status === 'READY' ? 'green' : invoice.status === 'HOLD' ? 'magenta' : invoice.status === 'ESCALATED' ? 'purple' : 'blue'}>{invoice.status}</Tag>
            </div>
          ))}
        </div>
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
  onRequestApproval,
  onApprove,
  onSettle,
  onVerifyReplay,
}: {
  run: RunResult;
  approval: Approval | null;
  payment: Payment | null;
  busy: string | null;
  replay: ReplayVerification | null;
  onRequestApproval: () => void;
  onApprove: () => void;
  onSettle: () => void;
  onVerifyReplay: () => void;
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
          <Button renderIcon={Money} onClick={onSettle} disabled={busy !== null}>
            Settle USDC on Arc
          </Button>
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
  const [batch, setBatch] = useState<PaymentBatch | null>(null);
  const [packetHash, setPacketHash] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    bootstrap()
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setOperations(result.operations);
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
      setReplay(null);
      setAuditTrail(await fetchInvoiceAudit(result.invoice.id, data.sessions.auditor));
      setPacketHash(null);
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
    });
  }, [act, data]);

  const handleRequestApproval = useCallback(() => {
    if (!data || !run) return;
    void act('Creating a role-separated approval request', async () => {
      setApproval(await requestApproval(run.decision.id, data.sessions.operator));
      setAuditTrail(await fetchInvoiceAudit(run.invoice.id, data.sessions.auditor));
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
      setReplay(null);
      setAuditTrail(await fetchInvoiceAudit(result.invoice.id, data.sessions.auditor));
      setPacketHash(null);
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
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
      setReplay(null);
      setAuditTrail(await fetchInvoiceAudit(result.invoice.id, data.sessions.auditor));
      setPacketHash(null);
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
    });
  }, [act, data]);

  const handleApprove = useCallback(() => {
    if (!data || !approval) return;
    void act('Verifying and signing the approval record', async () => {
      setApproval(await resolveApproval(approval, data.sessions.approver));
      if (run) setAuditTrail(await fetchInvoiceAudit(run.invoice.id, data.sessions.auditor));
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
    });
  }, [act, approval, data, run]);

  const handleSettle = useCallback(() => {
    if (!data || !run) return;
    void act('Persisting intent, settling USDC, and reconciling Arc proof', async () => {
      setPayment(await settleInvoice(run, data.sessions.approver, approval?.status === 'APPROVED' ? approval.id : undefined));
      setAuditTrail(await fetchInvoiceAudit(run.invoice.id, data.sessions.auditor));
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
    });
  }, [act, approval, data, run]);

  const handleVerifyReplay = useCallback(() => {
    if (!data || !run) return;
    void act('Recomputing the decision from its sealed input snapshot', async () => {
      setReplay(await verifyDecisionReplay(run.decision.id, data.sessions.auditor));
    });
  }, [act, data, run]);

  const handleSettleBatch = useCallback((items: Array<{ invoice_id: string; decision_id: string }>) => {
    if (!data || items.length === 0) return;
    void act(`Reconciling ${items.length} selected Arc payments`, async () => {
      setBatch(await settlePaymentBatch(items, data.sessions.approver));
      setOperations(await fetchOperationsOverview(data.sessions.auditor));
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
              onSelect={(key) => { setSelectedKey(key); setRun(null); setApproval(null); setPayment(null); setReplay(null); setAuditTrail(null); setPacketHash(null); }}
              onRun={handleRun}
              mode={mode}
              onModeChange={(nextMode) => { setMode(nextMode); setRun(null); setApproval(null); setPayment(null); setReplay(null); setAuditTrail(null); setPacketHash(null); }}
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
                      onRequestApproval={handleRequestApproval}
                      onApprove={handleApprove}
                      onSettle={handleSettle}
                      onVerifyReplay={handleVerifyReplay}
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
          <OperationsQueue
            overview={operations}
            busy={busy !== null}
            batch={batch}
            onSettleBatch={handleSettleBatch}
          />
        ) : null}

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

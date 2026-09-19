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
  requestApproval,
  resolveApproval,
  runLiveEvidenceWorkflow,
  runScenario,
  settleInvoice,
} from './api';
import type {
  Approval,
  BootstrapData,
  DecisionAction,
  Payment,
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

function LiveEmptyWorkbench() {
  return (
    <section className="empty-workbench empty-workbench--live">
      <div className="empty-workbench__icon"><Document size={32} /></div>
      <span className="eyebrow">Fresh tenant-scoped records</span>
      <h2>From raw documents to a payable decision.</h2>
      <p>Create a verified vendor, policy, treasury snapshot, invoice, purchase order, and delivery proof. TallyGuard hashes every source before the agent and policy engine review it.</p>
      <div className="flow-preview" aria-label="Live evidence flow">
        <span><Document size={16} /> 3 documents</span>
        <ArrowRight size={16} />
        <span><Rule size={16} /> 12 controls</span>
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
  onRequestApproval,
  onApprove,
  onSettle,
}: {
  run: RunResult;
  approval: Approval | null;
  payment: Payment | null;
  busy: string | null;
  onRequestApproval: () => void;
  onApprove: () => void;
  onSettle: () => void;
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

  useEffect(() => {
    let cancelled = false;
    bootstrap()
      .then((result) => {
        if (!cancelled) {
          setData(result);
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
    });
  }, [act, data]);

  const handleRequestApproval = useCallback(() => {
    if (!data || !run) return;
    void act('Creating a role-separated approval request', async () => {
      setApproval(await requestApproval(run.decision.id, data.sessions.operator));
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
    });
  }, [act, data]);

  const handleApprove = useCallback(() => {
    if (!data || !approval) return;
    void act('Verifying and signing the approval record', async () => {
      setApproval(await resolveApproval(approval, data.sessions.approver));
    });
  }, [act, approval, data]);

  const handleSettle = useCallback(() => {
    if (!data || !run) return;
    void act('Persisting intent, settling USDC, and reconciling Arc proof', async () => {
      setPayment(await settleInvoice(run, data.sessions.approver, approval?.status === 'APPROVED' ? approval.id : undefined));
    });
  }, [act, approval, data, run]);

  const selectedScenario = useMemo(
    () => data?.scenarios.find((item) => item.key === selectedKey),
    [data, selectedKey],
  );
  const protectedCount = history.filter((item) => item.decision.final_action !== 'PAY').length;
  const processedValue = history.reduce((sum, item) => sum + Number(item.invoice.amount), 0);

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
            <div><strong>{data?.readiness.status === 'ready' ? 'Controls online' : 'Connecting'}</strong><small>{data?.readiness.network ?? 'ARC-TESTNET'} · {data?.readiness.settlement_adapter ?? 'checking adapter'}</small></div>
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

        <div className="metrics-band" aria-label="Session metrics">
          <Metric label="Evaluations" value={String(history.length).padStart(2, '0')} detail="This judge session" />
          <Metric label="Value reviewed" value={`${processedValue.toLocaleString()} USDC`} detail="Evidence-bound volume" />
          <Metric label="Payments protected" value={String(protectedCount).padStart(2, '0')} detail="Held, rejected, or gated" />
          <Metric label="Control coverage" value="100%" detail="Every decision gets a reason" />
        </div>

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
              onSelect={(key) => { setSelectedKey(key); setRun(null); setApproval(null); setPayment(null); }}
              onRun={handleRun}
              mode={mode}
              onModeChange={(nextMode) => { setMode(nextMode); setRun(null); setApproval(null); setPayment(null); }}
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
                      onRequestApproval={handleRequestApproval}
                      onApprove={handleApprove}
                      onSettle={handleSettle}
                    />
                  </div>
                  {payment ? <ReceiptPanel payment={payment} /> : null}
                </>
              ) : mode === 'live' ? <LiveEmptyWorkbench /> : <EmptyWorkbench scenario={selectedScenario} />}
            </main>
          </div>
        )}

        <footer className="product-footer">
          <div><Locked size={16} /> Tenant scoped · Versioned policy · Idempotent settlement · Independent Arc RPC proof</div>
          <span>Built for Tameion Agents Hackathon 2026</span>
        </footer>
      </Content>
    </Theme>
  );
}

export default App;

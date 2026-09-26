import { useState } from 'react';
import type { ArcActivity, ArcActivityEntry, ArcActivityResponse, ActivityStatus } from './types';

type ActivityProps = {
  activity: ArcActivityResponse | null;
  error: string | null;
  onOpen: () => void;
  onBack: () => void;
  onRefresh: () => void;
};

type Filter = 'ALL' | ActivityStatus;

const STATUS: Record<ActivityStatus, { label: string; detail: string }> = {
  SCHEDULED: { label: 'Scheduled', detail: 'The request has not been submitted. No funds have moved.' },
  AWAITING_REVIEW: { label: 'Awaiting approval', detail: 'Evidence and policy checks are complete. An approver decision is pending.' },
  PAID: { label: 'Confirmed on Arc', detail: 'The exact USDC transfer was confirmed on Arc Testnet.' },
  DECLINED: { label: 'Declined', detail: 'The approver declined this request. No transfer was sent.' },
  HELD: { label: 'Evidence hold', detail: 'The evidence did not meet policy. No transfer was sent.' },
};

const ROUTE = {
  AUTOMATIC: 'Policy-cleared',
  APPROVAL: 'Independent approval',
  EVIDENCE: 'Evidence review',
} as const;

const formatDate = (value: string) => new Intl.DateTimeFormat('en-US', {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
}).format(new Date(value));

const shortHash = (value: string) => `${value.slice(0, 10)}…${value.slice(-8)}`;

function ActivityPill({ status }: { status: ActivityStatus }) {
  return <span className={`arc-activity__pill is-${status.toLowerCase()}`}>{STATUS[status].label}</span>;
}

function ActivityMetrics({ activity }: { activity: ArcActivity }) {
  return (
    <div className="arc-activity__metrics" aria-label="Arc activity metrics">
      <div><span>Processed</span><strong>{activity.processed}<small> / {activity.planned}</small></strong><em>requests in the workflow</em></div>
      <div><span>Settled</span><strong>{activity.confirmed_payments}</strong><em>onchain receipts</em></div>
      <div><span>In review</span><strong>{activity.awaiting_review}</strong><em>waiting for a decision</em></div>
      <div><span>Not paid</span><strong>{activity.declined + activity.held}</strong><em>declined or held</em></div>
    </div>
  );
}

export function ArcActivityPreview({ activity, error, onOpen }: Pick<ActivityProps, 'activity' | 'error' | 'onOpen'>) {
  const available = activity?.available ? activity : null;
  const processed = available?.entries.filter((entry) => entry.status !== 'SCHEDULED') ?? [];
  const latestPaid = processed.find((entry) => entry.status === 'PAID');
  const recent = processed.slice(0, 2);
  const previewEntries = latestPaid && !recent.some((entry) => entry.id === latestPaid.id)
    ? [...recent, latestPaid]
    : processed.slice(0, 3);
  return (
    <section className="arc-activity-preview" aria-labelledby="arc-activity-preview-title">
      <div className="arc-activity-preview__header">
        <div><span className="arc-activity__eyebrow"><i /> ARC TESTNET · VERIFIED ACTIVITY</span><h2 id="arc-activity-preview-title">Every decision leaves a trail.</h2><p>Follow each request from evidence and policy review to an independently confirmed USDC receipt.</p></div>
        <button type="button" onClick={onOpen}>Explore activity <span aria-hidden="true">↗</span></button>
      </div>
      {available ? (
        <>
          <ActivityMetrics activity={available} />
          <div className="arc-activity-preview__recent">
            {previewEntries.length > 0 ? previewEntries.map((entry) => (
              <div key={entry.invoice_number}>
                <span>{entry.invoice_number}</span><small>{ROUTE[entry.route]}</small><ActivityPill status={entry.status} />
                {entry.explorer_url ? <a href={entry.explorer_url} target="_blank" rel="noreferrer">Arc proof ↗</a> : null}
              </div>
            )) : <p>Scheduled requests will appear here as they enter the workflow.</p>}
          </div>
          <div className="arc-activity-preview__foot"><span>Confirmed principal <strong>{available.confirmed_principal_usdc} USDC</strong></span><span>Verified snapshot {formatDate(available.updated_at)}</span></div>
        </>
      ) : <div className="arc-activity-preview__unavailable">{error ?? 'Connecting to the Arc activity feed…'}</div>}
    </section>
  );
}

function ActivityDetail({ entry }: { entry: ArcActivityEntry }) {
  const arrived = entry.status !== 'SCHEDULED';
  const cleared = entry.status === 'PAID' || entry.status === 'AWAITING_REVIEW' || entry.status === 'DECLINED';
  return (
    <aside className="arc-activity__detail" aria-label={`${entry.invoice_number} details`}>
      <span className="arc-activity__eyebrow">PAYMENT WORKFLOW / {entry.id.toUpperCase()}</span>
      <h2>{entry.invoice_number}</h2>
      <ActivityPill status={entry.status} />
      <p>{STATUS[entry.status].detail}</p>
      <div className="arc-activity__amount"><span>Requested amount</span><strong>{entry.amount_usdc} <small>USDC</small></strong></div>
      <dl>
        <div><dt>Route</dt><dd>{ROUTE[entry.route]}</dd></div>
        <div><dt>Scheduled</dt><dd>{formatDate(entry.scheduled_at)}</dd></div>
        {entry.review_at ? <div><dt>Decision window</dt><dd>{formatDate(entry.review_at)}</dd></div> : null}
        <div><dt>Network</dt><dd>Arc Testnet</dd></div>
      </dl>
      <ol className="arc-activity__steps" aria-label="Workflow stages">
        <li className={arrived ? 'is-complete' : ''}><span>01</span><div><strong>Request intake</strong><small>{arrived ? 'Recorded' : 'Scheduled'}</small></div></li>
        <li className={cleared ? 'is-complete' : entry.status === 'HELD' ? 'is-stopped' : ''}><span>02</span><div><strong>Evidence & policy</strong><small>{cleared ? 'Checked' : entry.status === 'HELD' ? 'Held' : 'Waiting'}</small></div></li>
        <li className={entry.status === 'PAID' || entry.status === 'DECLINED' ? 'is-complete' : ''}><span>03</span><div><strong>Payment authority</strong><small>{entry.status === 'PAID' ? 'Authorized' : entry.status === 'DECLINED' ? 'Declined' : entry.status === 'AWAITING_REVIEW' ? 'Awaiting decision' : 'Waiting'}</small></div></li>
        <li className={entry.status === 'PAID' ? 'is-complete' : ''}><span>04</span><div><strong>Arc settlement</strong><small>{entry.status === 'PAID' ? 'Confirmed' : 'No transfer'}</small></div></li>
      </ol>
      {entry.explorer_url && entry.transaction_hash ? (
        <a className="arc-activity__proof" href={entry.explorer_url} target="_blank" rel="noreferrer"><span><strong>View on Arc Explorer</strong><small>{shortHash(entry.transaction_hash)} · block {entry.block_number}</small></span><span aria-hidden="true">↗</span></a>
      ) : <div className="arc-activity__no-transfer">No onchain transfer for this request yet.</div>}
    </aside>
  );
}

export function ArcActivityPage({ activity, error, onBack, onRefresh }: Pick<ActivityProps, 'activity' | 'error' | 'onBack' | 'onRefresh'>) {
  const [filter, setFilter] = useState<Filter>('ALL');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const available = activity?.available ? activity : null;
  const entries = available?.entries ?? [];
  const visible = filter === 'ALL' ? entries : entries.filter((entry) => entry.status === filter);
  const latest = visible.find((entry) => entry.status === 'PAID') ?? visible.find((entry) => entry.status !== 'SCHEDULED') ?? visible[0];
  const selected = visible.find((entry) => entry.invoice_number === selectedId) ?? latest;
  const filters: { value: Filter; label: string; count: number }[] = available ? [
    { value: 'ALL', label: 'All', count: available.planned },
    { value: 'PAID', label: 'Settled', count: available.confirmed_payments },
    { value: 'AWAITING_REVIEW', label: 'In review', count: available.awaiting_review },
    { value: 'DECLINED', label: 'Declined', count: available.declined },
    { value: 'HELD', label: 'Held', count: available.held },
    { value: 'SCHEDULED', label: 'Scheduled', count: available.queued },
  ] : [];
  return (
    <main className="arc-activity">
      <div className="arc-activity__top"><button type="button" onClick={onBack}>← Product overview</button><span><i /> Arc Testnet</span></div>
      <header className="arc-activity__hero"><div><span className="arc-activity__eyebrow">PROGRAMMABLE ACCOUNTS PAYABLE / NETWORK ACTIVITY</span><h1>From invoice to proof.<br /><em>In public view.</em></h1><p>See how requests move through evidence checks, payment authority, and USDC settlement. Confirmed transfers link to their exact Arc receipt.</p></div><div className="arc-activity__hero-aside"><span>CONFIRMED PRINCIPAL</span><strong>{available?.confirmed_principal_usdc ?? '—'} <small>USDC</small></strong><p>{available ? `Verified snapshot ${formatDate(available.updated_at)}` : 'Awaiting feed'}</p><button type="button" onClick={onRefresh}>Check for update ↻</button></div></header>
      {available ? (
        <>
          <ActivityMetrics activity={available} />
          <div className="arc-activity__board"><section className="arc-activity__ledger" aria-label="Activity ledger"><div className="arc-activity__ledger-heading"><div><span className="arc-activity__eyebrow">WORKFLOW LEDGER</span><h2>Requests & decisions</h2></div><span>{available.processed} processed · {available.queued} scheduled</span></div><div className="arc-activity__filters" role="group" aria-label="Filter activity">{filters.map((item) => <button key={item.value} type="button" className={filter === item.value ? 'is-active' : ''} onClick={() => setFilter(item.value)}>{item.label} <span>{item.count}</span></button>)}</div><div className="arc-activity__rows">{visible.length > 0 ? visible.map((entry) => <button type="button" key={entry.invoice_number} className={selected?.invoice_number === entry.invoice_number ? 'arc-activity__row is-selected' : 'arc-activity__row'} onClick={() => setSelectedId(entry.invoice_number)}><span className="arc-activity__row-id"><strong>{entry.invoice_number}</strong><small>{entry.id.toUpperCase()} · {ROUTE[entry.route]}</small></span><span className="arc-activity__row-time">{formatDate(entry.scheduled_at)}</span><span className="arc-activity__row-amount">{entry.amount_usdc} USDC</span><ActivityPill status={entry.status} /><span className="arc-activity__row-arrow" aria-hidden="true">↗</span></button>) : <div className="arc-activity__empty">No requests in this state yet.</div>}</div></section>{selected ? <ActivityDetail entry={selected} /> : null}</div>
        </>
      ) : <div className="arc-activity__offline"><h2>Activity feed is not available</h2><p>{error ?? 'The activity source is not connected to this deployment yet.'}</p><button type="button" onClick={onRefresh}>Try again</button></div>}
    </main>
  );
}

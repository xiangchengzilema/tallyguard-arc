import type {
  Approval,
  AuditEvent,
  AuditTrail,
  BootstrapData,
  EvidenceFileBundle,
  EvidenceFileReview,
  GovernanceOverview,
  OperationsOverview,
  PaymentBatch,
  Payment,
  PolicyActivation,
  PolicyDraft,
  PolicySimulation,
  ReliabilityEvidence,
  ReplayVerification,
  RunResult,
  ScheduleRun,
  SettlementIncidentOverview,
  VendorRecord,
  VendorTrustRecord,
  VendorWalletEvent,
} from './types';

type Role = keyof BootstrapData['sessions'];

class ApiError extends Error {
  status: number;
  code: string;

  constructor(message: string, status: number, code = 'REQUEST_FAILED') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const isFormData = init.body instanceof FormData;
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init.body && !isFormData ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload?.error;
    throw new ApiError(detail?.message ?? 'TallyGuard could not complete the request.', response.status, detail?.code);
  }
  return payload as T;
}

async function createSession(role: Role): Promise<[Role, string]> {
  const payload = await request<{ access_token: string }>('/api/demo/session', {
    method: 'POST',
    body: JSON.stringify({ role }),
  });
  return [role, payload.access_token];
}

export async function bootstrap(): Promise<BootstrapData> {
  const sessionRoles: Role[] = ['admin', 'operator', 'approver', 'auditor'];
  const [catalog, readiness, sessionEntries] = await Promise.all([
    request<{ items: BootstrapData['scenarios'] }>('/api/demo/scenarios'),
    request<BootstrapData['readiness']>('/api/readiness'),
    Promise.all(sessionRoles.map(createSession)),
  ]);
  const sessions = Object.fromEntries(sessionEntries) as BootstrapData['sessions'];
  const [operations, incidents, reliability, governance, vendorDirectory] = await Promise.all([
    fetchOperationsOverview(sessions.auditor),
    fetchSettlementIncidents(sessions.auditor),
    fetchReliabilityReport(sessions.auditor),
    fetchGovernanceOverview(sessions.approver),
    fetchVendorDirectory(sessions.auditor),
  ]);
  return {
    scenarios: catalog.items,
    readiness,
    sessions,
    operations,
    incidents,
    reliability,
    governance,
    vendorDirectory,
  };
}

export async function fetchVendorDirectory(auditorToken: string): Promise<VendorTrustRecord[]> {
  const vendorPayload = await request<{ items: VendorRecord[] }>(
    '/api/vendors',
    { method: 'GET' },
    auditorToken,
  );
  return Promise.all(vendorPayload.items.map(async (vendor) => {
    const history = await request<{ items: VendorWalletEvent[] }>(
      `/api/vendors/${encodeURIComponent(vendor.id)}/wallet-history`,
      { method: 'GET' },
      auditorToken,
    );
    return { vendor, walletHistory: history.items };
  }));
}

export async function fetchGovernanceOverview(approverToken: string): Promise<GovernanceOverview> {
  const payload = await request<{
    active_policy: GovernanceOverview['activePolicy'];
    settlement_capacity: GovernanceOverview['settlementCapacity'];
    pending_approvals: GovernanceOverview['pendingApprovals'];
  }>('/api/governance/overview', { method: 'GET' }, approverToken);
  return {
    activePolicy: payload.active_policy,
    settlementCapacity: payload.settlement_capacity,
    pendingApprovals: payload.pending_approvals,
  };
}

export async function activatePolicyVersion(
  currentPolicy: GovernanceOverview['activePolicy'],
  draft: PolicyDraft,
  adminToken: string,
): Promise<PolicyActivation> {
  const version = `ops-${Date.now().toString(36)}-${crypto.randomUUID().replaceAll('-', '').slice(0, 6)}`;
  const payload = await request<{ policy: PolicyActivation['policy'] }>(
    '/api/policies',
    { method: 'POST', body: JSON.stringify({ version, ...draft }) },
    adminToken,
  );
  if (!currentPolicy) {
    return { policy: payload.policy, previousVersion: null, changes: [] };
  }
  const diff = await request<{ changes: PolicyActivation['changes'] }>(
    `/api/policies/diff?from=${encodeURIComponent(currentPolicy.version)}&to=${encodeURIComponent(version)}`,
    { method: 'GET' },
    adminToken,
  );
  return {
    policy: payload.policy,
    previousVersion: currentPolicy.version,
    changes: diff.changes,
  };
}

export async function fetchReliabilityReport(auditorToken: string): Promise<ReliabilityEvidence> {
  return request<ReliabilityEvidence>(
    '/api/reliability/report',
    { method: 'GET' },
    auditorToken,
  );
}

export async function fetchOperationsOverview(auditorToken: string): Promise<OperationsOverview> {
  const payload = await request<{ overview: OperationsOverview }>(
    '/api/operations/overview?queue_limit=12',
    { method: 'GET' },
    auditorToken,
  );
  return payload.overview;
}

export async function fetchSettlementIncidents(auditorToken: string): Promise<SettlementIncidentOverview> {
  return request<SettlementIncidentOverview>(
    '/api/operations/settlement-incidents?limit=100',
    { method: 'GET' },
    auditorToken,
  );
}

const encodeDocument = (value: Record<string, string>): ArrayBuffer => {
  const encoded = new TextEncoder().encode(JSON.stringify(value));
  return encoded.buffer.slice(
    encoded.byteOffset,
    encoded.byteOffset + encoded.byteLength,
  ) as ArrayBuffer;
};

const sha256 = async (content: ArrayBuffer) => {
  const buffer = await crypto.subtle.digest('SHA-256', content);
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, '0')).join('');
};

type JsonEvidence = Record<string, string>;

interface ParsedEvidenceBundle {
  invoice: JsonEvidence;
  purchaseOrder: JsonEvidence;
  delivery: JsonEvidence;
  bytes: {
    invoice: ArrayBuffer;
    purchaseOrder: ArrayBuffer;
    delivery: ArrayBuffer;
  };
}

const requiredField = (record: JsonEvidence, field: string, documentName: string) => {
  const value = record[field]?.trim();
  if (!value) throw new Error(`${documentName} is missing required field "${field}".`);
  return value;
};

const parseJsonEvidence = async (file: File, documentName: string): Promise<[JsonEvidence, ArrayBuffer]> => {
  if (!file.name.toLowerCase().endsWith('.json')) {
    throw new Error(`${documentName} must be a JSON file for the browser review workflow.`);
  }
  const bytes = await file.arrayBuffer();
  let decoded: unknown;
  try {
    decoded = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new Error(`${documentName} is not valid UTF-8 JSON.`);
  }
  if (!decoded || typeof decoded !== 'object' || Array.isArray(decoded)) {
    throw new Error(`${documentName} must contain one top-level JSON object.`);
  }
  const normalized: JsonEvidence = {};
  for (const [key, value] of Object.entries(decoded)) {
    if (['string', 'number', 'boolean'].includes(typeof value)) normalized[key] = String(value);
  }
  return [normalized, bytes];
};

const parseEvidenceBundle = async (files: EvidenceFileBundle): Promise<ParsedEvidenceBundle> => {
  const [[invoice, invoiceBytes], [purchaseOrder, purchaseOrderBytes], [delivery, deliveryBytes]] = await Promise.all([
    parseJsonEvidence(files.invoice, 'Invoice'),
    parseJsonEvidence(files.purchaseOrder, 'Purchase order'),
    parseJsonEvidence(files.delivery, 'Delivery evidence'),
  ]);
  const invoiceId = requiredField(invoice, 'invoice_id', 'Invoice');
  const vendorId = requiredField(invoice, 'vendor_id', 'Invoice');
  const purchaseOrderId = requiredField(purchaseOrder, 'purchase_order_id', 'Purchase order');
  if (requiredField(purchaseOrder, 'vendor_id', 'Purchase order') !== vendorId) {
    throw new Error('Invoice and purchase order vendor_id values do not match.');
  }
  if (requiredField(delivery, 'purchase_order_id', 'Delivery evidence') !== purchaseOrderId) {
    throw new Error('Purchase order and delivery purchase_order_id values do not match.');
  }
  const currency = requiredField(invoice, 'currency', 'Invoice').toUpperCase();
  if (currency !== 'USDC' || requiredField(purchaseOrder, 'currency', 'Purchase order').toUpperCase() !== 'USDC') {
    throw new Error('TallyGuard currently settles uploaded evidence in USDC only.');
  }
  const wallet = requiredField(invoice, 'payment_wallet_address', 'Invoice');
  if (!/^0x[0-9a-fA-F]{40}$/.test(wallet)) throw new Error('Invoice payment_wallet_address must be a valid EVM address.');
  for (const [value, label] of [
    [requiredField(invoice, 'amount', 'Invoice'), 'Invoice amount'],
    [requiredField(purchaseOrder, 'authorized_amount', 'Purchase order'), 'Authorized amount'],
    [requiredField(delivery, 'delivered_value', 'Delivery evidence'), 'Delivered value'],
  ]) {
    if (!Number.isFinite(Number(value)) || Number(value) <= 0) throw new Error(`${label} must be greater than zero.`);
  }
  requiredField(invoice, 'invoice_number', 'Invoice');
  requiredField(invoice, 'due_date', 'Invoice');
  requiredField(purchaseOrder, 'po_number', 'Purchase order');
  requiredField(delivery, 'delivery_id', 'Delivery evidence');
  if (!invoiceId.trim()) throw new Error('Invoice ID must not be empty.');
  return {
    invoice,
    purchaseOrder,
    delivery,
    bytes: { invoice: invoiceBytes, purchaseOrder: purchaseOrderBytes, delivery: deliveryBytes },
  };
};

export async function reviewEvidenceFiles(files: EvidenceFileBundle): Promise<EvidenceFileReview> {
  const parsed = await parseEvidenceBundle(files);
  return {
    invoiceNumber: parsed.invoice.invoice_number,
    vendorId: parsed.invoice.vendor_id,
    amount: parsed.invoice.amount,
    currency: parsed.invoice.currency,
    dueDate: parsed.invoice.due_date,
    purchaseOrderNumber: parsed.purchaseOrder.po_number,
    authorizedAmount: parsed.purchaseOrder.authorized_amount,
    deliveredValue: parsed.delivery.delivered_value,
    walletAddress: parsed.invoice.payment_wallet_address,
  };
}

export async function runUploadedEvidenceWorkflow(
  adminToken: string,
  operatorToken: string,
  files: EvidenceFileBundle,
): Promise<RunResult> {
  const parsed = await parseEvidenceBundle(files);
  const suffix = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
  const invoiceId = parsed.invoice.invoice_id;
  const vendorId = parsed.invoice.vendor_id;
  const wallet = parsed.invoice.payment_wallet_address;
  const invoiceHash = await sha256(parsed.bytes.invoice);
  await Promise.all([
    request('/api/policies', {
      method: 'POST',
      body: JSON.stringify({
        version: `upload-${suffix}`,
        daily_payment_limit_usdc: parsed.invoice.daily_payment_limit_usdc ?? '8500',
        minimum_cash_reserve_usdc: parsed.invoice.minimum_cash_reserve_usdc ?? '2500',
        maximum_autonomous_payment_usdc: parsed.invoice.maximum_autonomous_payment_usdc ?? '2000',
        po_amount_tolerance_usdc: parsed.invoice.po_amount_tolerance_usdc ?? '5',
        allowed_asset: 'USDC',
        allowed_network: 'ARC-TESTNET',
        kill_switch_enabled: false,
      }),
    }, adminToken),
    request('/api/treasury/snapshots', {
      method: 'POST',
      body: JSON.stringify({
        available_usdc: parsed.invoice.treasury_available_usdc ?? '18437.29',
        spent_today_usdc: parsed.invoice.treasury_spent_today_usdc ?? '913.48',
        source_reference: `uploaded-evidence-${suffix}`,
      }),
    }, operatorToken),
    request('/api/vendors', {
      method: 'POST',
      body: JSON.stringify({
        id: vendorId,
        legal_name: parsed.invoice.vendor_legal_name ?? vendorId,
        approved_wallet_address: wallet,
        autopay_limit: parsed.invoice.vendor_autopay_limit_usdc ?? '2000',
        risk_tier: parsed.invoice.vendor_risk_tier ?? 'low',
        verification_method: 'SIGNED_CHALLENGE',
        verification_reference: parsed.invoice.vendor_verification_reference ?? `uploaded-wallet-proof-${suffix}`,
      }),
    }, operatorToken),
  ]);
  await request('/api/invoices', {
    method: 'POST',
    body: JSON.stringify({
      id: invoiceId,
      vendor_id: vendorId,
      invoice_number: parsed.invoice.invoice_number,
      currency: 'USDC',
      amount: parsed.invoice.amount,
      due_date: parsed.invoice.due_date,
      payment_wallet_address: wallet,
      source_document_hash: invoiceHash,
    }),
  }, operatorToken);
  const uploads: Array<[string, File, ArrayBuffer]> = [
    ['INVOICE', files.invoice, parsed.bytes.invoice],
    ['PURCHASE_ORDER', files.purchaseOrder, parsed.bytes.purchaseOrder],
    ['DELIVERY', files.delivery, parsed.bytes.delivery],
  ];
  await Promise.all(uploads.map(([evidenceType, file, bytes]) => {
    const form = new FormData();
    form.append('evidence_type', evidenceType);
    form.append('file', new Blob([bytes], { type: 'application/json' }), file.name);
    return request(`/api/invoices/${encodeURIComponent(invoiceId)}/evidence`, { method: 'POST', body: form }, operatorToken);
  }));
  return request<RunResult>(`/api/invoices/${encodeURIComponent(invoiceId)}/evaluate`, { method: 'POST' }, operatorToken);
}

export async function runLiveEvidenceWorkflow(
  adminToken: string,
  operatorToken: string,
): Promise<RunResult> {
  const suffix = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
  const vendorId = `vendor-live-${suffix}`;
  const invoiceId = `invoice-live-${suffix}`;
  const purchaseOrderId = `po-live-${suffix}`;
  const wallet = '0x2222222222222222222222222222222222222222';
  const invoiceDocument = encodeDocument({
    invoice_id: invoiceId,
    vendor_id: vendorId,
    invoice_number: `TG-${suffix.toUpperCase()}`,
    currency: 'USDC',
    amount: '1274.63',
    due_date: '2026-10-08',
    payment_wallet_address: wallet,
  });
  const invoiceHash = await sha256(invoiceDocument);

  await Promise.all([
    request('/api/policies', {
      method: 'POST',
      body: JSON.stringify({
        version: `judge-${suffix}`,
        daily_payment_limit_usdc: '8500',
        minimum_cash_reserve_usdc: '2500',
        maximum_autonomous_payment_usdc: '2000',
        po_amount_tolerance_usdc: '5',
        allowed_asset: 'USDC',
        allowed_network: 'ARC-TESTNET',
        kill_switch_enabled: false,
      }),
    }, adminToken),
    request('/api/treasury/snapshots', {
      method: 'POST',
      body: JSON.stringify({
        available_usdc: '18437.29',
        spent_today_usdc: '913.48',
        source_reference: `judge-wallet-snapshot-${suffix}`,
      }),
    }, operatorToken),
    request('/api/vendors', {
      method: 'POST',
      body: JSON.stringify({
        id: vendorId,
        legal_name: 'Northline Compute Cooperative',
        approved_wallet_address: wallet,
        autopay_limit: '2000',
        risk_tier: 'low',
        verification_method: 'SIGNED_CHALLENGE',
        verification_reference: `signed-wallet-proof-${suffix}`,
      }),
    }, operatorToken),
  ]);

  await request('/api/invoices', {
    method: 'POST',
    body: JSON.stringify({
      id: invoiceId,
      vendor_id: vendorId,
      invoice_number: `TG-${suffix.toUpperCase()}`,
      currency: 'USDC',
      amount: '1274.63',
      due_date: '2026-10-08',
      payment_wallet_address: wallet,
      source_document_hash: invoiceHash,
    }),
  }, operatorToken);

  const documents: Array<[string, string, ArrayBuffer]> = [
    ['INVOICE', 'northline-invoice.json', invoiceDocument],
    ['PURCHASE_ORDER', 'northline-purchase-order.json', encodeDocument({
      purchase_order_id: purchaseOrderId,
      vendor_id: vendorId,
      po_number: `PO-${suffix.toUpperCase()}`,
      currency: 'USDC',
      authorized_amount: '1274.63',
    })],
    ['DELIVERY', 'northline-delivery.json', encodeDocument({
      delivery_id: `delivery-${suffix}`,
      purchase_order_id: purchaseOrderId,
      delivered_value: '1274.63',
    })],
  ];
  await Promise.all(documents.map(([evidenceType, filename, content]) => {
    const form = new FormData();
    form.append('evidence_type', evidenceType);
    form.append('file', new Blob([content], { type: 'application/json' }), filename);
    return request(`/api/invoices/${encodeURIComponent(invoiceId)}/evidence`, {
      method: 'POST',
      body: form,
    }, operatorToken);
  }));

  return request<RunResult>(
    `/api/invoices/${encodeURIComponent(invoiceId)}/evaluate`,
    { method: 'POST' },
    operatorToken,
  );
}

export async function runScenario(key: string, operatorToken: string): Promise<RunResult> {
  return request<RunResult>(`/api/demo/scenarios/${encodeURIComponent(key)}/run`, { method: 'POST' }, operatorToken);
}

export async function requestApproval(decisionId: string, operatorToken: string): Promise<Approval> {
  const payload = await request<{ approval: Approval }>(
    `/api/decisions/${encodeURIComponent(decisionId)}/request-approval`,
    { method: 'POST' },
    operatorToken,
  );
  return payload.approval;
}

export async function resolveApproval(
  approval: Approval,
  approverToken: string,
  approve = true,
  note = 'Contract, delivery evidence, and treasury controls verified.',
): Promise<Approval> {
  const payload = await request<{ approval: Approval }>(
    `/api/approvals/${encodeURIComponent(approval.id)}/resolve`,
    {
      method: 'POST',
      body: JSON.stringify({
        approve,
        note,
        expected_version: approval.version,
      }),
    },
    approverToken,
  );
  return payload.approval;
}

export async function settleInvoice(
  run: RunResult,
  approverToken: string,
  approvalReference?: string,
): Promise<Payment> {
  const payload = await request<{ payment: Payment }>(
    `/api/invoices/${encodeURIComponent(run.invoice.id)}/settle`,
    {
      method: 'POST',
      body: JSON.stringify({
        decision_id: run.decision.id,
        ...(approvalReference ? { approval_reference: approvalReference } : {}),
      }),
    },
    approverToken,
  );
  return payload.payment;
}

export async function settlePaymentBatch(
  items: Array<{ invoice_id: string; decision_id: string }>,
  approverToken: string,
): Promise<PaymentBatch> {
  const payload = await request<{ batch: PaymentBatch }>(
    '/api/payment-batches/settle',
    { method: 'POST', body: JSON.stringify({ items }) },
    approverToken,
  );
  return payload.batch;
}

export async function runDueSchedules(approverToken: string): Promise<ScheduleRun> {
  const payload = await request<{ schedule_run: ScheduleRun }>(
    '/api/schedules/run',
    { method: 'POST', body: JSON.stringify({}) },
    approverToken,
  );
  return payload.schedule_run;
}

export async function fetchInvoiceAudit(invoiceId: string, auditorToken: string): Promise<AuditTrail> {
  const payload = await request<{ chain_valid: boolean; items: AuditEvent[] }>(
    '/api/audit/events',
    { method: 'GET' },
    auditorToken,
  );
  const events = payload.items.filter((event) => (
    event.aggregate_id === invoiceId || event.payload.invoice_id === invoiceId
  ));
  return { chainValid: payload.chain_valid, events };
}

export async function verifyDecisionReplay(
  decisionId: string,
  auditorToken: string,
): Promise<ReplayVerification> {
  const payload = await request<{ verification: ReplayVerification }>(
    `/api/decisions/${encodeURIComponent(decisionId)}/replay`,
    { method: 'GET' },
    auditorToken,
  );
  return payload.verification;
}

export async function simulateDecisionPolicy(
  decisionId: string,
  changes: Record<string, string | number | boolean | null>,
  adminToken: string,
): Promise<PolicySimulation> {
  const payload = await request<{ simulation: PolicySimulation }>(
    `/api/decisions/${encodeURIComponent(decisionId)}/policy-simulation`,
    { method: 'POST', body: JSON.stringify(changes) },
    adminToken,
  );
  return payload.simulation;
}

export async function downloadEvidencePacket(
  invoiceId: string,
  auditorToken: string,
): Promise<string> {
  const response = await fetch(
    `/api/invoices/${encodeURIComponent(invoiceId)}/evidence-packet`,
    { headers: { Authorization: `Bearer ${auditorToken}` } },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = payload?.error;
    throw new ApiError(
      detail?.message ?? 'TallyGuard could not assemble the evidence packet.',
      response.status,
      detail?.code,
    );
  }
  const packetHash = response.headers.get('X-TallyGuard-Packet-SHA256') ?? '';
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? `tallyguard-${invoiceId}-evidence-packet.json`;
  const objectUrl = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
  return packetHash;
}

export { ApiError };

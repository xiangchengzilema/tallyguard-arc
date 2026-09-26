import type {
  ActivePolicy,
  ArcActivityResponse,
  Approval,
  AgentRun,
  AuditEvent,
  AuditSearchRequest,
  AuditSearchResult,
  AuditTrail,
  BootstrapContext,
  BootstrapData,
  EvidenceDocument,
  EvidenceExtractionMethod,
  EvidenceFieldPreview,
  EvidenceFileBundle,
  EvidenceFieldOverrides,
  EvidenceFileReview,
  GovernanceOverview,
  Invoice,
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
  SettlementBatchItem,
  SettlementIncidentOverview,
  TreasurySnapshotRecord,
  VendorOnboardingDraft,
  VendorRecord,
  VendorTrustRecord,
  VendorWalletEvent,
  VendorWalletRotationDraft,
} from './types';

type Role = keyof BootstrapData['sessions'];

const EXPECTED_LIVE_ROLES: Record<Role, string> = {
  admin: 'ADMIN',
  operator: 'FINANCE_OPERATOR',
  approver: 'APPROVER',
  auditor: 'AUDITOR',
};

const LIVE_ROLE_LABELS: Record<Role, string> = {
  admin: 'Policy administrator',
  operator: 'Finance operator',
  approver: 'Payment approver',
  auditor: 'Audit reviewer',
};

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

export class OperatorAccessRequired extends Error {
  context: BootstrapContext;

  constructor(context: BootstrapContext) {
    super('Role-separated operator sessions are required for live mode.');
    this.name = 'OperatorAccessRequired';
    this.context = context;
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

export function fetchArcActivity(): Promise<ArcActivityResponse> {
  return request<ArcActivityResponse>('/api/public/arc-activity', { cache: 'no-store' });
}

async function createDemoWorkspace(): Promise<{
  workspace_id: string;
  sessions: BootstrapData['sessions'];
}> {
  return request('/api/demo/workspace', { method: 'POST' });
}

export async function fetchBootstrapContext(): Promise<BootstrapContext> {
  const [catalog, readiness] = await Promise.all([
    request<{ items: BootstrapData['scenarios'] }>('/api/demo/scenarios'),
    request<BootstrapData['readiness']>('/api/readiness'),
  ]);
  return { scenarios: catalog.items, readiness };
}

async function hydrateBootstrap(
  context: BootstrapContext,
  sessions: BootstrapData['sessions'],
  workspaceId: string,
): Promise<BootstrapData> {
  const { scenarios, readiness } = context;
  const [operations, incidents, agentRun, reliability, governance, policyHistory, vendorDirectory, auditSearch] = await Promise.all([
    fetchOperationsOverview(sessions.auditor),
    fetchSettlementIncidents(sessions.auditor),
    fetchLatestAgentRun(sessions.auditor),
    fetchReliabilityReport(sessions.auditor),
    fetchGovernanceOverview(sessions.approver),
    fetchPolicyHistory(sessions.auditor),
    fetchVendorDirectory(sessions.auditor),
    fetchAuditEvents(sessions.auditor, { limit: 12 }),
  ]);
  return {
    workspaceId,
    scenarios,
    readiness,
    sessions,
    operations,
    incidents,
    agentRun,
    reliability,
    governance,
    policyHistory,
    vendorDirectory,
    auditSearch,
  };
}

export async function bootstrapWithSessions(
  sessions: BootstrapData['sessions'],
  context?: BootstrapContext,
): Promise<BootstrapData> {
  const resolvedContext = context ?? await fetchBootstrapContext();
  let organizationId: string | null = null;
  for (const role of Object.keys(EXPECTED_LIVE_ROLES) as Role[]) {
    let payload: { principal: { organization_id: string; roles: string[] } };
    try {
      payload = await request('/api/auth/session', { method: 'GET' }, sessions[role]);
    } catch (error) {
      if (error instanceof ApiError) {
        throw new ApiError(
          `${LIVE_ROLE_LABELS[role]} session was rejected. Generate a fresh operator bundle and paste the matching token.`,
          error.status,
          error.code,
        );
      }
      throw error;
    }
    if (!payload.principal.roles.includes(EXPECTED_LIVE_ROLES[role])) {
      throw new ApiError(
        `${LIVE_ROLE_LABELS[role]} session does not carry the required ${EXPECTED_LIVE_ROLES[role]} role.`,
        403,
        'ROLE_SESSION_MISMATCH',
      );
    }
    organizationId ??= payload.principal.organization_id;
    if (payload.principal.organization_id !== organizationId) {
      throw new ApiError(
        `${LIVE_ROLE_LABELS[role]} session belongs to a different organization.`,
        403,
        'TENANT_SESSION_MISMATCH',
      );
    }
  }
  if (!organizationId) {
    throw new ApiError('The role bundle did not resolve to an organization.', 403, 'TENANT_SESSION_MISMATCH');
  }
  return hydrateBootstrap(resolvedContext, sessions, organizationId);
}

export async function revokeOperatorSessions(
  sessions: BootstrapData['sessions'],
): Promise<void> {
  const outcomes = await Promise.allSettled(
    Object.values(sessions).map((token) => request<{ status: string }>(
      '/api/auth/session',
      { method: 'DELETE' },
      token,
    )),
  );
  const failed = outcomes.filter((outcome) => outcome.status === 'rejected');
  if (failed.length > 0) {
    throw new ApiError(
      `${failed.length} role session${failed.length === 1 ? '' : 's'} could not be revoked; let the short expiry elapse before reusing this operator bundle.`,
      503,
      'SESSION_REVOCATION_INCOMPLETE',
    );
  }
}

export async function bootstrap(): Promise<BootstrapData> {
  const context = await fetchBootstrapContext();
  if (!context.readiness.demo_sessions_enabled) {
    throw new OperatorAccessRequired(context);
  }
  const workspace = await createDemoWorkspace();
  return hydrateBootstrap(context, workspace.sessions, workspace.workspace_id);
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

export async function fetchPolicyHistory(readerToken: string): Promise<ActivePolicy[]> {
  const payload = await request<{ items: ActivePolicy[] }>(
    '/api/policies',
    { method: 'GET' },
    readerToken,
  );
  return payload.items;
}

export async function recordTreasurySnapshot(
  availableUsdc: string,
  spentTodayUsdc: string,
  sourceReference: string,
  operatorToken: string,
): Promise<TreasurySnapshotRecord> {
  const payload = await request<{ treasury: TreasurySnapshotRecord }>(
    '/api/treasury/snapshots',
    {
      method: 'POST',
      body: JSON.stringify({
        available_usdc: availableUsdc,
        spent_today_usdc: spentTodayUsdc,
        source_reference: sourceReference,
      }),
    },
    operatorToken,
  );
  return payload.treasury;
}

export async function refreshLiveTreasurySnapshot(
  operatorToken: string,
): Promise<TreasurySnapshotRecord> {
  const payload = await request<{
    treasury: TreasurySnapshotRecord;
    verification: {
      wallet_fingerprint: string;
      wallet_address_redacted: string;
      wallet_state: string;
      network: string;
      chain_id: number;
      latest_block: number;
      usdc_contract: string;
      source: string;
    };
  }>(
    '/api/treasury/snapshots/refresh',
    { method: 'POST' },
    operatorToken,
  );
  return payload.treasury;
}

export async function onboardVendor(
  draft: VendorOnboardingDraft,
  operatorToken: string,
): Promise<VendorRecord> {
  const payload = await request<{ vendor: VendorRecord }>(
    '/api/vendors',
    { method: 'POST', body: JSON.stringify(draft) },
    operatorToken,
  );
  return payload.vendor;
}

export async function rotateVendorWallet(
  vendor: VendorRecord,
  draft: VendorWalletRotationDraft,
  operatorToken: string,
): Promise<VendorRecord> {
  const payload = await request<{ vendor: VendorRecord }>(
    `/api/vendors/${encodeURIComponent(vendor.id)}/wallet`,
    {
      method: 'PATCH',
      body: JSON.stringify({
        expected_current_wallet: vendor.approved_wallet_address,
        ...draft,
      }),
    },
    operatorToken,
  );
  return payload.vendor;
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

export async function fetchInvoiceRun(
  invoice: Invoice,
  decisionId: string,
  readerToken: string,
): Promise<RunResult> {
  const payload = await request<{
    decision: RunResult['decision'];
    correlation_id: string;
  }>(
    `/api/decisions/${encodeURIComponent(decisionId)}`,
    { method: 'GET' },
    readerToken,
  );
  return {
    invoice,
    decision: payload.decision,
    correlation_id: payload.correlation_id,
  };
}

export async function fetchConfirmedPayment(
  paymentIntentId: string,
  readerToken: string,
): Promise<Payment> {
  const payload = await request<{ payment: Payment }>(
    `/api/payments/${encodeURIComponent(paymentIntentId)}`,
    { method: 'GET' },
    readerToken,
  );
  return payload.payment;
}

export async function fetchSettlementIncidents(auditorToken: string): Promise<SettlementIncidentOverview> {
  return request<SettlementIncidentOverview>(
    '/api/operations/settlement-incidents?limit=100',
    { method: 'GET' },
    auditorToken,
  );
}

export async function fetchLatestAgentRun(auditorToken: string): Promise<AgentRun | null> {
  const payload = await request<{ agent_run: AgentRun | null }>(
    '/api/agent-runs/latest',
    { method: 'GET' },
    auditorToken,
  );
  return payload.agent_run;
}

export async function createAgentRun(operatorToken: string): Promise<AgentRun> {
  const payload = await request<{ agent_run: AgentRun }>(
    '/api/agent-runs',
    { method: 'POST', body: JSON.stringify({ max_items: 25 }) },
    operatorToken,
  );
  return payload.agent_run;
}

export async function seedAutonomyShowcase(operatorToken: string): Promise<void> {
  await request<{ showcase: { id: string } }>(
    '/api/demo/autonomy-showcase',
    { method: 'POST', body: JSON.stringify({}) },
    operatorToken,
  );
}

export async function executeAgentRun(runId: string, approverToken: string): Promise<AgentRun> {
  const payload = await request<{ agent_run: AgentRun }>(
    `/api/agent-runs/${encodeURIComponent(runId)}/execute`,
    { method: 'POST', body: JSON.stringify({}) },
    approverToken,
  );
  return payload.agent_run;
}

export async function downloadAgentRunProof(
  runId: string,
  auditorToken: string,
): Promise<string> {
  const response = await fetch(
    `/api/agent-runs/${encodeURIComponent(runId)}/proof-packet`,
    { headers: { Authorization: `Bearer ${auditorToken}` } },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = payload?.error;
    throw new ApiError(
      detail?.message ?? 'TallyGuard could not assemble the agent proof packet.',
      response.status,
      detail?.code,
    );
  }
  const packetHash = response.headers.get('X-TallyGuard-Packet-SHA256') ?? '';
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? `tallyguard-${runId}-proof-packet.json`;
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

export async function downloadAccountingLedger(
  auditorToken: string,
): Promise<{ hash: string; rows: number }> {
  const response = await fetch('/api/accounting/ledger.csv', {
    headers: { Authorization: `Bearer ${auditorToken}` },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = payload?.error;
    throw new ApiError(
      detail?.message ?? 'TallyGuard could not export the accounting ledger.',
      response.status,
      detail?.code,
    );
  }
  const hash = response.headers.get('X-TallyGuard-Ledger-SHA256') ?? '';
  const rows = Number(response.headers.get('X-TallyGuard-Ledger-Rows') ?? '0');
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? 'tallyguard-ledger.csv';
  const objectUrl = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
  return { hash, rows };
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
  documents: Record<keyof EvidenceFileBundle, ParsedEvidenceDocument>;
}

interface ParsedEvidenceDocument {
  record: JsonEvidence;
  fields: EvidenceFieldPreview[];
  bytes: ArrayBuffer;
  mimeType: 'application/json' | 'application/pdf';
  contentSha256: string;
  extractionMethods: EvidenceExtractionMethod[];
  fieldCount: number;
  minimumConfidence: string;
  pageNumbers: number[];
}

const requiredField = (record: JsonEvidence, field: string, documentName: string) => {
  const value = record[field]?.trim();
  if (!value) throw new Error(`${documentName} is missing required field "${field}".`);
  return value;
};

const evidenceMimeType = (file: File, documentName: string): ParsedEvidenceDocument['mimeType'] => {
  const filename = file.name.toLowerCase();
  const mimeType = file.type.toLowerCase();
  if (mimeType === 'application/json' || filename.endsWith('.json')) return 'application/json';
  if (mimeType === 'application/pdf' || filename.endsWith('.pdf')) return 'application/pdf';
  throw new Error(`${documentName} must be JSON or a labelled text-layer PDF.`);
};

const parseJsonEvidence = async (file: File, documentName: string): Promise<ParsedEvidenceDocument> => {
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
  return {
    record: normalized,
    fields: Object.entries(normalized).map(([name, value]) => ({
      name,
      raw_value: value,
      normalized_value: value,
      confidence: '1',
      method: 'JSON',
      source: {
        document_id: `json-preview-${file.name}`,
        page_number: null,
        bounding_box: null,
        json_pointer: `/${name.replaceAll('~', '~0').replaceAll('/', '~1')}`,
      },
    })),
    bytes,
    mimeType: 'application/json',
    contentSha256: await sha256(bytes),
    extractionMethods: ['JSON'],
    fieldCount: Object.keys(normalized).length,
    minimumConfidence: '1',
    pageNumbers: [],
  };
};

interface EvidencePreviewResponse {
  preview: {
    content_sha256: string;
    fields: EvidenceFieldPreview[];
  };
  persisted: false;
}

const parseEvidenceFile = async (
  file: File,
  documentName: string,
  evidenceType: 'INVOICE' | 'PURCHASE_ORDER' | 'DELIVERY',
  operatorToken: string,
): Promise<ParsedEvidenceDocument> => {
  const mimeType = evidenceMimeType(file, documentName);
  if (mimeType === 'application/json') return parseJsonEvidence(file, documentName);

  const bytes = await file.arrayBuffer();
  const form = new FormData();
  form.append('evidence_type', evidenceType);
  form.append('file', new Blob([bytes], { type: mimeType }), file.name);
  const payload = await request<EvidencePreviewResponse>(
    '/api/evidence/extract',
    { method: 'POST', body: form },
    operatorToken,
  );
  const record = Object.fromEntries(
    payload.preview.fields.map((field) => [field.name, field.normalized_value]),
  );
  return {
    record,
    fields: payload.preview.fields,
    bytes,
    mimeType,
    contentSha256: payload.preview.content_sha256,
    extractionMethods: [...new Set(payload.preview.fields.map((field) => field.method))],
    fieldCount: payload.preview.fields.length,
    minimumConfidence: payload.preview.fields.reduce(
      (minimum, field) => Math.min(minimum, Number(field.confidence)),
      1,
    ).toFixed(2),
    pageNumbers: [...new Set(payload.preview.fields.flatMap((field) => (
      field.source.page_number === null ? [] : [field.source.page_number]
    )))].sort((left, right) => left - right),
  };
};

const applyEvidenceOverrides = (
  document: ParsedEvidenceDocument,
  overrides: Record<string, string> | undefined,
): ParsedEvidenceDocument => {
  if (!overrides || Object.keys(overrides).length === 0) return document;
  const record = { ...document.record, ...overrides };
  const originals = new Map(document.fields.map((field) => [field.name, field]));
  const fields = Object.entries(record).map(([name, value]) => {
    const original = originals.get(name);
    const manuallyChanged = overrides[name] !== undefined && overrides[name] !== original?.normalized_value;
    if (original) {
      return {
        ...original,
        normalized_value: value,
        confidence: manuallyChanged ? '1' : original.confidence,
        method: manuallyChanged ? 'MANUAL' as const : original.method,
      };
    }
    return {
      name,
      raw_value: '',
      normalized_value: value,
      confidence: '1',
      method: 'MANUAL' as const,
      source: {
        document_id: `manual-${name}`,
        page_number: null,
        bounding_box: null,
        json_pointer: null,
      },
    };
  });
  return {
    ...document,
    record,
    fields,
    extractionMethods: [...new Set(fields.map((field) => field.method))],
    fieldCount: fields.length,
    minimumConfidence: fields.reduce(
      (minimum, field) => Math.min(minimum, Number(field.confidence)),
      1,
    ).toFixed(2),
  };
};

const parseEvidenceBundle = async (
  files: EvidenceFileBundle,
  operatorToken: string,
  overrides: EvidenceFieldOverrides = {},
): Promise<ParsedEvidenceBundle> => {
  const [parsedInvoice, parsedPurchaseOrder, parsedDelivery] = await Promise.all([
    parseEvidenceFile(files.invoice, 'Invoice', 'INVOICE', operatorToken),
    parseEvidenceFile(files.purchaseOrder, 'Purchase order', 'PURCHASE_ORDER', operatorToken),
    parseEvidenceFile(files.delivery, 'Delivery evidence', 'DELIVERY', operatorToken),
  ]);
  const invoiceDocument = applyEvidenceOverrides(parsedInvoice, overrides.invoice);
  const purchaseOrderDocument = applyEvidenceOverrides(parsedPurchaseOrder, overrides.purchaseOrder);
  const deliveryDocument = applyEvidenceOverrides(parsedDelivery, overrides.delivery);
  const invoice = invoiceDocument.record;
  const purchaseOrder = purchaseOrderDocument.record;
  const delivery = deliveryDocument.record;
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
    documents: {
      invoice: invoiceDocument,
      purchaseOrder: purchaseOrderDocument,
      delivery: deliveryDocument,
    },
  };
};

export async function reviewEvidenceFiles(
  files: EvidenceFileBundle,
  operatorToken: string,
  overrides: EvidenceFieldOverrides = {},
): Promise<EvidenceFileReview> {
  const parsed = await parseEvidenceBundle(files, operatorToken, overrides);
  return {
    invoiceId: parsed.invoice.invoice_id,
    invoiceNumber: parsed.invoice.invoice_number,
    vendorId: parsed.invoice.vendor_id,
    amount: parsed.invoice.amount,
    currency: parsed.invoice.currency,
    dueDate: parsed.invoice.due_date,
    purchaseOrderNumber: parsed.purchaseOrder.po_number,
    purchaseOrderId: parsed.purchaseOrder.purchase_order_id,
    authorizedAmount: parsed.purchaseOrder.authorized_amount,
    deliveryId: parsed.delivery.delivery_id,
    deliveredValue: parsed.delivery.delivered_value,
    walletAddress: parsed.invoice.payment_wallet_address,
    extractionMethods: [...new Set(Object.values(parsed.documents).flatMap((item) => item.extractionMethods))],
    documents: ([
      ['INVOICE', files.invoice, parsed.documents.invoice],
      ['PURCHASE_ORDER', files.purchaseOrder, parsed.documents.purchaseOrder],
      ['DELIVERY', files.delivery, parsed.documents.delivery],
    ] as const).map(([evidenceType, file, document]) => ({
      evidenceType,
      filename: file.name,
      mimeType: document.mimeType,
      contentHash: document.contentSha256,
      extractionMethods: document.extractionMethods,
      fieldCount: document.fieldCount,
      minimumConfidence: document.minimumConfidence,
      pageNumbers: document.pageNumbers,
    })),
  };
}

export async function loadSamplePdfEvidence(): Promise<EvidenceFileBundle> {
  const instanceId = crypto.randomUUID();
  const sampleFiles = [
    ['invoice', 'invoice.pdf'],
    ['purchaseOrder', 'purchase-order.pdf'],
    ['delivery', 'delivery.pdf'],
  ] as const;
  const loaded = await Promise.all(sampleFiles.map(async ([, filename]) => {
    const response = await fetch(`/samples/evidence/${filename}`);
    if (!response.ok) {
      throw new ApiError(`Could not load the ${filename} judge sample.`, response.status);
    }
    const bytes = await response.arrayBuffer();
    const instanceMarker = new TextEncoder().encode(
      `\n% TallyGuard reusable sample instance ${instanceId}-${filename}\n`,
    );
    return new File([bytes, instanceMarker], filename, { type: 'application/pdf' });
  }));
  return {
    invoice: loaded[0],
    purchaseOrder: loaded[1],
    delivery: loaded[2],
  };
}

export async function runUploadedEvidenceWorkflow(
  adminToken: string,
  operatorToken: string,
  files: EvidenceFileBundle,
  overrides: EvidenceFieldOverrides = {},
  settlementMode: BootstrapData['readiness']['settlement_mode'] = 'simulation',
): Promise<RunResult> {
  const parsed = await parseEvidenceBundle(files, operatorToken, overrides);
  const suffix = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
  const invoiceId = parsed.invoice.invoice_id;
  const vendorId = parsed.invoice.vendor_id;
  const wallet = parsed.invoice.payment_wallet_address;
  const invoiceHash = parsed.documents.invoice.contentSha256;
  const invoiceDirectory = await request<{ items: Invoice[] }>(
    '/api/invoices?limit=50',
    { method: 'GET' },
    operatorToken,
  );
  const existingInvoice = invoiceDirectory.items.find((item) => item.id === invoiceId);
  if (existingInvoice) {
    if (existingInvoice.source_document_hash !== invoiceHash) {
      throw new ApiError(
        `Invoice ID ${invoiceId} already belongs to different source evidence. Correct the invoice ID or submit it as a new version.`,
        409,
        'INVOICE_ID_CONFLICT',
      );
    }
    const overviewPayload = await request<{ overview: OperationsOverview }>(
      '/api/operations/overview?queue_limit=50&recent_limit=50',
      { method: 'GET' },
      operatorToken,
    );
    const existingOperation = overviewPayload.overview.recent_requests.find((item) => item.id === invoiceId);
    if (existingOperation?.decision_id) {
      return fetchInvoiceRun(existingInvoice, existingOperation.decision_id, operatorToken);
    }
    return request<RunResult>(
      `/api/invoices/${encodeURIComponent(invoiceId)}/evaluate?auto_settle=true`,
      { method: 'POST' },
      operatorToken,
    );
  }

  const [vendorDirectory, policyDirectory, treasurySnapshot] = await Promise.all([
    request<{ items: VendorRecord[] }>(
      '/api/vendors',
      { method: 'GET' },
      operatorToken,
    ),
    request<{ items: ActivePolicy[] }>(
      '/api/policies',
      { method: 'GET' },
      operatorToken,
    ),
    request<{ treasury: TreasurySnapshotRecord | null }>(
      '/api/treasury/summary?optional=true',
      { method: 'GET' },
      operatorToken,
    ).then((payload) => payload.treasury),
  ]);
  const existingVendor = vendorDirectory.items.find((item) => item.id === vendorId);
  if (existingVendor && existingVendor.approved_wallet_address.toLowerCase() !== wallet.toLowerCase()) {
    throw new ApiError(
      `Vendor ${vendorId} already has a different verified payout wallet. Finance must verify a wallet change before this request can continue.`,
      409,
      'VENDOR_WALLET_CONFLICT',
    );
  }

  const setupRequests: Promise<unknown>[] = [];
  // A requester may bootstrap an empty demo workspace, but must never replace
  // finance-controlled policy or treasury state while submitting an invoice.
  if (policyDirectory.items.length === 0) {
    const liveDefaults = settlementMode === 'circle-live';
    setupRequests.push(request('/api/policies', {
      method: 'POST',
      body: JSON.stringify({
        version: `upload-${suffix}`,
        daily_payment_limit_usdc: parsed.invoice.daily_payment_limit_usdc ?? (liveDefaults ? '0.10' : '5000'),
        daily_autonomous_payment_limit_usdc: parsed.invoice.daily_autonomous_payment_limit_usdc ?? (liveDefaults ? '0.01' : '1000'),
        autonomous_payments_enabled: false,
        minimum_cash_reserve_usdc: parsed.invoice.minimum_cash_reserve_usdc ?? (liveDefaults ? '0' : '2500'),
        maximum_autonomous_payment_usdc: parsed.invoice.maximum_autonomous_payment_usdc ?? (liveDefaults ? '0.01' : '300'),
        po_amount_tolerance_usdc: parsed.invoice.po_amount_tolerance_usdc ?? '5',
        allowed_asset: 'USDC',
        allowed_network: 'ARC-TESTNET',
        kill_switch_enabled: false,
      }),
    }, adminToken));
  }
  if (settlementMode === 'circle-live') {
    setupRequests.push(refreshLiveTreasurySnapshot(operatorToken));
  } else if (!treasurySnapshot) {
    setupRequests.push(request('/api/treasury/snapshots', {
      method: 'POST',
      body: JSON.stringify({
        available_usdc: parsed.invoice.treasury_available_usdc ?? '18437.29',
        spent_today_usdc: parsed.invoice.treasury_spent_today_usdc ?? '0',
        source_reference: `uploaded-evidence-${suffix}`,
      }),
    }, operatorToken));
  }
  if (!existingVendor) {
    setupRequests.push(request('/api/vendors', {
      method: 'POST',
      body: JSON.stringify({
        id: vendorId,
        legal_name: parsed.invoice.vendor_legal_name ?? vendorId,
        approved_wallet_address: wallet,
        autopay_limit: parsed.invoice.vendor_autopay_limit_usdc ?? (settlementMode === 'circle-live' ? '0.10' : '300'),
        risk_tier: parsed.invoice.vendor_risk_tier ?? 'low',
        verification_method: 'SIGNED_CHALLENGE',
        verification_reference: parsed.invoice.vendor_verification_reference ?? `uploaded-wallet-proof-${suffix}`,
      }),
    }, operatorToken));
  }
  await Promise.all(setupRequests);
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
  const uploads: Array<[string, File, ParsedEvidenceDocument]> = [
    ['INVOICE', files.invoice, parsed.documents.invoice],
    ['PURCHASE_ORDER', files.purchaseOrder, parsed.documents.purchaseOrder],
    ['DELIVERY', files.delivery, parsed.documents.delivery],
  ];
  await Promise.all(uploads.map(([evidenceType, file, document]) => {
    const form = new FormData();
    form.append('evidence_type', evidenceType);
    form.append('file', new Blob([document.bytes], { type: document.mimeType }), file.name);
    form.append('fields', JSON.stringify(document.fields));
    return request(`/api/invoices/${encodeURIComponent(invoiceId)}/evidence`, { method: 'POST', body: form }, operatorToken);
  }));
  return request<RunResult>(`/api/invoices/${encodeURIComponent(invoiceId)}/evaluate?auto_settle=true`, { method: 'POST' }, operatorToken);
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

export async function fetchDecisionApproval(decisionId: string, readerToken: string): Promise<Approval | null> {
  const payload = await request<{ approval: Approval | null }>(
    `/api/decisions/${encodeURIComponent(decisionId)}/approval`,
    { method: 'GET' },
    readerToken,
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
  items: SettlementBatchItem[],
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

export async function fetchInvoiceEvidence(
  invoiceId: string,
  readerToken: string,
): Promise<EvidenceDocument[]> {
  const payload = await request<{ items: EvidenceDocument[] }>(
    `/api/invoices/${encodeURIComponent(invoiceId)}/evidence`,
    { method: 'GET' },
    readerToken,
  );
  return payload.items;
}

export async function downloadEvidenceDocument(
  evidenceDocument: EvidenceDocument,
  readerToken: string,
): Promise<void> {
  const response = await fetch(
    `/api/evidence/${encodeURIComponent(evidenceDocument.id)}/content`,
    { headers: { Authorization: `Bearer ${readerToken}` } },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = payload?.error;
    throw new ApiError(
      detail?.message ?? 'TallyGuard could not download the source evidence.',
      response.status,
      detail?.code,
    );
  }
  const objectUrl = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = evidenceDocument.filename;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
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

export async function fetchAuditEvents(
  auditorToken: string,
  filters: AuditSearchRequest = {},
): Promise<AuditSearchResult> {
  const parameters = new URLSearchParams();
  const values: Array<[string, string | number | undefined]> = [
    ['q', filters.query],
    ['event_type', filters.eventType],
    ['aggregate_type', filters.aggregateType],
    ['aggregate_id', filters.aggregateId],
    ['created_after', filters.createdAfter],
    ['created_before', filters.createdBefore],
    ['before_sequence', filters.beforeSequence],
    ['limit', filters.limit ?? 12],
  ];
  for (const [key, value] of values) {
    if (value !== undefined && String(value).trim() !== '') {
      parameters.set(key, String(value));
    }
  }
  const payload = await request<{
    chain_valid: boolean;
    items: AuditEvent[];
    filters: AuditSearchResult['filters'];
    page: AuditSearchResult['page'];
  }>(`/api/audit/events?${parameters.toString()}`, { method: 'GET' }, auditorToken);
  return {
    chainValid: payload.chain_valid,
    events: payload.items,
    filters: payload.filters,
    page: payload.page,
  };
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

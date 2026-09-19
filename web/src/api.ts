import type { Approval, BootstrapData, Payment, RunResult } from './types';

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
  return {
    scenarios: catalog.items,
    readiness,
    sessions: Object.fromEntries(sessionEntries) as BootstrapData['sessions'],
  };
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

export async function resolveApproval(approval: Approval, approverToken: string): Promise<Approval> {
  const payload = await request<{ approval: Approval }>(
    `/api/approvals/${encodeURIComponent(approval.id)}/resolve`,
    {
      method: 'POST',
      body: JSON.stringify({
        approve: true,
        note: 'Contract, delivery evidence, and treasury controls verified.',
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

export { ApiError };

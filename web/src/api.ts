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
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
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
  const sessionRoles: Role[] = ['operator', 'approver', 'auditor'];
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


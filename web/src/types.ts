export type DecisionAction = 'PAY' | 'HOLD' | 'REJECT' | 'ESCALATE' | 'SCHEDULE';

export interface Scenario {
  key: string;
  title: string;
  description: string;
  expected_action: DecisionAction;
}

export interface Invoice {
  id: string;
  organization_id: string;
  vendor_id: string;
  invoice_number: string;
  currency: string;
  amount: string;
  due_date: string;
  payment_wallet_address: string;
  source_document_hash: string;
  status: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export type RuleDisposition = 'PASS' | 'HOLD' | 'REJECT' | 'ESCALATE' | 'SCHEDULE';

export interface RuleResult {
  code: string;
  disposition: RuleDisposition;
  message: string;
  remediation: string | null;
}

export interface AgentRecommendation {
  action: DecisionAction;
  summary: string;
  reason_codes: string[];
  confidence: string;
  evidence_refs: string[];
}

export interface Decision {
  id: string;
  organization_id: string;
  invoice_id: string;
  evidence_manifest_hash: string;
  policy_version: string;
  policy_content_hash: string;
  agent_recommendation: AgentRecommendation | null;
  agent_disagreed: boolean;
  final_action: DecisionAction;
  reason_codes: string[];
  remediation: string[];
  rules: RuleResult[];
  created_at: string;
}

export interface RunResult {
  scenario?: Pick<Scenario, 'key' | 'title'>;
  invoice: Invoice;
  decision: Decision;
  correlation_id: string;
}

export interface EvidenceFileBundle {
  invoice: File;
  purchaseOrder: File;
  delivery: File;
}

export interface EvidenceFileReview {
  invoiceNumber: string;
  vendorId: string;
  amount: string;
  currency: string;
  dueDate: string;
  purchaseOrderNumber: string;
  authorizedAmount: string;
  deliveredValue: string;
  walletAddress: string;
}

export interface AuditEvent {
  sequence: number;
  aggregate_type: string;
  aggregate_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  previous_hash: string;
  event_hash: string;
  created_at: string;
}

export interface AuditTrail {
  chainValid: boolean;
  events: AuditEvent[];
}

export interface Approval {
  id: string;
  decision_id: string;
  invoice_id: string;
  status: string;
  version: number;
  requested_by_user_id?: string;
  requested_at?: string;
  resolved_by_user_id?: string;
  resolution_note?: string;
  authorized_action?: DecisionAction;
}

export interface Payment {
  intent: {
    id: string;
    organization_id: string;
    invoice_id: string;
    decision_id: string;
    recipient: string;
    amount_usdc: string;
    network: string;
    approval_reference: string | null;
  };
  receipt: {
    provider: string;
    provider_reference: string;
    transaction_hash: string;
    block_number: number;
    confirmed_recipient: string;
    confirmed_amount_usdc: string;
    network: string;
    status: string;
    confirmed_at: string;
    explorer_url: string;
  };
  invoice: Invoice;
  reused_receipt: boolean;
}

export interface BootstrapData {
  scenarios: Scenario[];
  sessions: Record<'admin' | 'operator' | 'approver' | 'auditor', string>;
  readiness: {
    status: string;
    database: string;
    network: string;
    settlement_adapter: string;
  };
}

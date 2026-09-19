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
  replayable: boolean;
  replay_input_hash: string | null;
  agent_recommendation: AgentRecommendation | null;
  agent_disagreed: boolean;
  final_action: DecisionAction;
  reason_codes: string[];
  remediation: string[];
  rules: RuleResult[];
  created_at: string;
}

export interface ReplayCheck {
  code: string;
  passed: boolean;
  expected: string;
  actual: string;
}

export interface ReplayVerification {
  verified: boolean;
  original_decision_id: string;
  replayed_decision_id: string;
  input_snapshot_hash: string;
  action: DecisionAction;
  reason_codes: string[];
  checks: ReplayCheck[];
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

export interface OperationsOverview {
  organization_id: string;
  as_of: string;
  invoice_count: number;
  status_counts: Record<string, number>;
  open_exposure_usdc: string;
  blocked_exposure_usdc: string;
  due_next_7_days_usdc: string;
  due_next_7_days_count: number;
  overdue_usdc: string;
  overdue_count: number;
  reconciled_usdc: string;
  treasury_available_usdc: string | null;
  minimum_reserve_usdc: string | null;
  projected_after_open_usdc: string | null;
  work_queue: OperationsInvoice[];
}

export interface OperationsInvoice extends Invoice {
  decision_id: string | null;
  decision_action: DecisionAction | null;
}

export interface PaymentBatchResult {
  invoice_id: string;
  status: 'SETTLED' | 'FAILED';
  payment?: Payment;
  error?: { code: string; message: string };
}

export interface PaymentBatch {
  requested: number;
  succeeded: number;
  failed: number;
  results: PaymentBatchResult[];
}

export interface BootstrapData {
  scenarios: Scenario[];
  sessions: Record<'admin' | 'operator' | 'approver' | 'auditor', string>;
  operations: OperationsOverview;
  readiness: {
    status: string;
    database: string;
    network: string;
    settlement_adapter: string;
    settlement_mode: 'simulation' | 'circle-live';
    funds_movement: 'disabled' | 'enabled';
    arc_rpc_verification: 'simulated' | 'independent-live';
    mainnet_enabled: boolean;
    demo_sessions_enabled: boolean;
    evidence_analyst: string;
  };
}

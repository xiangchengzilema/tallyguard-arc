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
  scheduled_for: string | null;
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

export interface PolicySimulation {
  persisted: false;
  source_decision_id: string;
  source_replay_input_hash: string;
  original_action: DecisionAction;
  simulated_action: DecisionAction;
  changed_fields: Array<{ field: string; before: string | number | boolean | null; after: string | number | boolean | null }>;
  reason_codes: string[];
  remediation: string[];
  rules: RuleResult[];
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

export type EvidenceExtractionMethod = 'JSON' | 'PDF_TEXT' | 'OCR' | 'MANUAL' | 'SEEDED';

export interface EvidenceFieldPreview {
  name: string;
  raw_value: string;
  normalized_value: string;
  confidence: string;
  method: EvidenceExtractionMethod;
  source: {
    document_id: string;
    page_number: number | null;
    bounding_box: [number, number, number, number] | null;
    json_pointer: string | null;
  };
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
  extractionMethods: EvidenceExtractionMethod[];
  documents: Array<{
    evidenceType: 'INVOICE' | 'PURCHASE_ORDER' | 'DELIVERY';
    filename: string;
    mimeType: 'application/json' | 'application/pdf';
    contentHash: string;
    extractionMethods: EvidenceExtractionMethod[];
    fieldCount: number;
    minimumConfidence: string;
    pageNumbers: number[];
  }>;
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

export interface AuditSearchFilters {
  event_type: string | null;
  aggregate_type: string | null;
  aggregate_id: string | null;
  query: string | null;
  created_after: string | null;
  created_before: string | null;
  before_sequence: number | null;
}

export interface AuditSearchResult {
  chainValid: boolean;
  events: AuditEvent[];
  filters: AuditSearchFilters;
  page: {
    limit: number;
    has_more: boolean;
    next_before_sequence: number | null;
  };
}

export interface AuditSearchRequest {
  query?: string;
  eventType?: string;
  aggregateType?: string;
  aggregateId?: string;
  createdAfter?: string;
  createdBefore?: string;
  beforeSequence?: number;
  limit?: number;
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

export interface ActivePolicy {
  version: string;
  organization_id: string;
  daily_payment_limit_usdc: string;
  minimum_cash_reserve_usdc: string;
  maximum_autonomous_payment_usdc: string;
  po_amount_tolerance_usdc: string;
  allowed_asset: string;
  allowed_network: string;
  kill_switch_enabled: boolean;
  schedule_payments_before_due_days: number | null;
  content_hash: string;
  activated_by_user_id: string;
  activated_at: string;
}

export interface ApprovalInboxItem {
  approval: Approval;
  invoice: Invoice;
  decision: Decision;
}

export interface GovernanceOverview {
  activePolicy: ActivePolicy | null;
  settlementCapacity: SettlementCapacity | null;
  pendingApprovals: ApprovalInboxItem[];
}

export interface VendorRecord {
  id: string;
  organization_id: string;
  legal_name: string;
  approved_wallet_address: string;
  autopay_limit: string;
  risk_tier: string;
  active: boolean;
}

export interface VendorWalletEvent {
  organization_id: string;
  vendor_id: string;
  event_type: 'VERIFIED' | 'REPLACED';
  wallet_address: string;
  previous_wallet_address: string | null;
  verification_method: string;
  verification_reference: string;
  verified_by_user_id: string;
  verified_at: string;
}

export interface VendorTrustRecord {
  vendor: VendorRecord;
  walletHistory: VendorWalletEvent[];
}

export interface SettlementCapacity {
  organization_id: string;
  active_policy_version: string;
  active_policy_hash: string;
  kill_switch_enabled: boolean;
  treasury_snapshot_sequence: number;
  treasury_snapshot_recorded_at: string;
  snapshot_age_seconds: number;
  snapshot_fresh: boolean;
  snapshot_available_usdc: string;
  snapshot_spent_today_usdc: string;
  committed_since_snapshot_usdc: string;
  effective_available_usdc: string;
  daily_payment_limit_usdc: string;
  daily_remaining_usdc: string;
  minimum_cash_reserve_usdc: string;
  maximum_new_payment_usdc: string;
}

export interface PolicyDraft {
  daily_payment_limit_usdc: string;
  minimum_cash_reserve_usdc: string;
  maximum_autonomous_payment_usdc: string;
  po_amount_tolerance_usdc: string;
  allowed_asset: string;
  allowed_network: string;
  kill_switch_enabled: boolean;
  schedule_payments_before_due_days: number | null;
}

export interface PolicyFieldChange {
  field: string;
  before: string | number | boolean | null;
  after: string | number | boolean | null;
}

export interface PolicyActivation {
  policy: ActivePolicy;
  previousVersion: string | null;
  changes: PolicyFieldChange[];
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
  scheduled_for: string | null;
  settlement_retryable: boolean;
}

export interface SettlementAttempt {
  sequence: number;
  organization_id: string;
  payment_intent_id: string;
  invoice_id: string;
  provider: string;
  outcome: 'FAILED_RETRYABLE' | 'FAILED_LOCKED' | 'RECONCILIATION_MISMATCH' | 'CONFIRMED';
  retryable: boolean;
  error_code: string | null;
  error_message: string | null;
  correlation_id: string;
  created_at: string;
}

export interface SettlementIncident {
  payment_intent_id: string;
  idempotency_fingerprint: string;
  invoice: Invoice;
  provider: string;
  state: 'OPEN_RETRYABLE' | 'LOCKED' | 'RESOLVED';
  retryable: boolean;
  latest_attempt: SettlementAttempt;
  failed_attempt: SettlementAttempt;
  attempt_count: number;
}

export interface SettlementIncidentOverview {
  summary: {
    total: number;
    open_retryable: number;
    locked: number;
    resolved: number;
  };
  items: SettlementIncident[];
}

export type AgentAction =
  | 'SETTLE'
  | 'SETTLE_APPROVED'
  | 'RETRY_SETTLEMENT'
  | 'RELEASE_SCHEDULE'
  | 'WAIT_SCHEDULE'
  | 'REQUIRE_APPROVAL'
  | 'REMEDIATE'
  | 'INVESTIGATE'
  | 'COLLECT_EVIDENCE';

export interface AgentRunItem {
  invoice_id: string;
  invoice_number: string;
  amount_usdc: string;
  due_date: string;
  invoice_status: string;
  invoice_version: number;
  decision_id: string | null;
  decision_action: DecisionAction | null;
  action: AgentAction;
  reason_code: string;
  explanation: string;
  executable: boolean;
  approval_reference: string | null;
  approval_status: string | null;
}

export interface AgentRunResult {
  invoice_id: string;
  action: AgentAction;
  status: 'SETTLED' | 'SKIPPED' | 'STALE' | 'FAILED' | 'ROUTED' | 'REVALIDATED';
  reason_code?: string;
  reason_codes?: string[];
  payment?: Payment;
  error?: { code: string; message: string };
}

export interface AgentRun {
  id: string;
  organization_id: string;
  status: 'PLANNED' | 'EXECUTING' | 'EXECUTED' | 'PARTIAL';
  as_of: string;
  state_hash: string;
  plan_hash: string;
  summary: {
    scanned: number;
    executable: number;
    requires_attention: number;
    actions: Partial<Record<AgentAction, number>>;
  };
  items: AgentRunItem[];
  created_by_user_id: string;
  created_at: string;
  executed_by_user_id: string | null;
  executed_at: string | null;
  results: AgentRunResult[];
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

export interface ScheduleRunResult {
  invoice_id: string;
  status: 'WAITING' | 'SETTLED' | 'REVALIDATED' | 'FAILED';
  scheduled_for?: string;
  evaluated_on?: string;
  source_decision_id?: string;
  release_decision?: Decision;
  invoice?: Invoice;
  payment?: Payment;
  error?: { code: string; message: string };
}

export interface ScheduleRun {
  evaluated_on: string;
  scanned: number;
  waiting: number;
  settled: number;
  revalidated: number;
  failed: number;
  results: ScheduleRunResult[];
}

export interface ReliabilityReport {
  schema_version: string;
  run_id: string;
  generated_at: string;
  configuration: {
    concurrency: number;
    duplicate_storm: number;
    invoices: number;
    organizations: number;
    slow_provider_delay_ms: number;
    treasury_contention: number;
    timeout_seconds: number;
  };
  methodology: {
    classification: string;
    identity: string;
    note: string;
    settlement: string;
    slow_provider_injection: string;
    transport: string;
    treasury_contention: string;
  };
  summary: {
    cross_tenant_attempts: number;
    cross_tenant_attempts_denied: number;
    duplicate_payment_count: number;
    duplicate_storm_delayed_provider_attempts: number;
    duplicate_storm_provider_submissions: number;
    duplicate_storm_requests: number;
    duplicate_storm_unique_transaction_hashes: number;
    failed_workflows: number;
    http_requests: number;
    successful_workflows: number;
    slow_provider_idempotency_preserved: boolean;
    treasury_atomic_limit_preserved: boolean;
    treasury_contention_denied: number;
    treasury_contention_expected_successes: number;
    treasury_contention_provider_submissions: number;
    treasury_contention_requests: number;
    treasury_contention_successes: number;
    treasury_contention_unique_transaction_hashes: number;
    workflow_error_rate: number;
    workflow_throughput_per_second: number;
  };
  latency_ms: {
    overall: { p50: number; p95: number; p99: number };
    by_request: Record<string, { p50: number; p95: number; p99: number }>;
  };
}

export interface ReliabilityEvidence {
  report: ReliabilityReport;
  artifact: {
    filename: string;
    sha256: string;
    immutable: boolean;
  };
  agent_report: {
    schema_version: string;
    run_id: string;
    generated_at: string;
    configuration: {
      organizations: number;
      concurrency: number;
      duplicate_execution_storm: number;
      timeout_seconds: number;
    };
    methodology: {
      classification: string;
      identity: string;
      note: string;
      settlement: string;
      transport: string;
      queue: string;
      duplicate_execution: string;
    };
    summary: {
      approval_routes: number;
      cross_tenant_attempts: number;
      cross_tenant_attempts_denied: number;
      duplicate_execution_claim_winners: number;
      duplicate_execution_provider_submissions: number;
      duplicate_execution_requests: number;
      failed_agent_workflows: number;
      mixed_queue_items: number;
      orchestration_single_execution_preserved: boolean;
      policy_authorized_settlements: number;
      protected_non_settlements: number;
      successful_agent_workflows: number;
      verified_proof_packets: number;
    };
    latency_ms: {
      overall: { p50: number; p95: number; p99: number };
      by_request: Record<string, { p50: number; p95: number; p99: number }>;
    };
  };
  agent_artifact: {
    filename: string;
    sha256: string;
    immutable: boolean;
  };
}

export interface BootstrapData {
  workspaceId: string;
  scenarios: Scenario[];
  sessions: Record<'admin' | 'operator' | 'approver' | 'auditor', string>;
  operations: OperationsOverview;
  incidents: SettlementIncidentOverview;
  agentRun: AgentRun | null;
  reliability: ReliabilityEvidence;
  governance: GovernanceOverview;
  vendorDirectory: VendorTrustRecord[];
  auditSearch: AuditSearchResult;
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

export interface BootstrapContext {
  scenarios: Scenario[];
  readiness: BootstrapData['readiness'];
}

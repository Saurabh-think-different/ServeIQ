export interface HistoryEvent { timestamp: string; actor: string; note: string }
export interface Incident {
  ticket_id: string; reported_store_id: string | null; store_id: string | null;
  reported_asset_id: string | null; asset_id: string | null; opened_at: string;
  priority: string | null; category: string | null; subcategory: string | null;
  status: string; subject: string; description: string; system_version: string | null;
  history: HistoryEvent[];
}
export interface IncidentCreate {
  description: string; subject?: string; ticket_id?: string; store_id?: string;
  asset_id?: string; priority?: string; category?: string; system_version?: string;
}
export interface IncidentResult { incident: Incident; warnings: string[] }
export interface IncidentPage { incidents: Incident[]; total: number; limit: number; offset: number }
export interface IncidentContext {
  incident: { ticket_id: string; reported_store_id: string | null; reported_asset_id: string | null;
    opened_at: string; priority: string | null; category: string | null; subcategory: string | null;
    status: string; subject: string; description: string; system_version: string | null; escalation_flag: boolean };
  store: { store_id: string; store_name: string; city: string; neighborhood: string; store_type: string } | null;
  asset: { asset_id: string; store_id: string; asset_type: string; make: string; model: string;
    system_version: string | null; serial_number: string | null; install_date: string | null;
    last_service_date: string | null; warranty_expiry: string | null } | null;
  sla: { priority: string; category: string; first_response_minutes: number; resolution_target_minutes: number;
    escalation_to_l2_minutes: number } | null;
  history: { event_id: number; source_event_index: number; event_at: string; actor: string; note: string }[];
  related: { reported_related_ticket_id: string; related_ticket_id: string | null;
    persisted_related_ticket_id: string | null; relation_type: string }[];
  clock: { now: string | null; opened_at: string | null; elapsed_since_open_minutes: number | null;
    status_kind: 'open' | 'closed' | 'unknown'; open_target_deadlines: {
      first_response_at: string; resolution_at: string; escalation_to_l2_at: string } | null;
    first_response_fulfillment: 'unknown'; fulfillment_note: string };
  missing: string[]; warnings: string[];
}
export interface SearchRequest { query: string; limit?: number; category?: string; model?: string; system_version?: string }
export interface Passage {
  content: string; source_path: string; section: string; similarity_score: number;
  category: string | null; model: string | null; system_version: string | null;
  document_type: string | null; applicability: 'context_match' | 'unknown'; applicability_notes: string[];
}
export interface SearchResponse { passages: Passage[]; evidence_gap: boolean; warnings: string[] }
export type IngestionTarget = 'structured' | 'knowledge' | 'all';
export interface IngestionReport {
  stage: 'structured' | 'knowledge'; status: 'succeeded' | 'failed' | 'blocked';
  inserted: Record<string, number>; updated: Record<string, number>; skipped: Record<string, number>;
  deleted: Record<string, number>; warnings: string[]; errors: string[]; path_mappings: Record<string, number>;
}
export interface PipelineReport { status: 'succeeded' | 'partial' | 'failed'; stages: IngestionReport[] }
export interface RunQuestion { question_id: string; text: string }
export interface ReviewedStep { step_id: string; instruction: string; evidence_ids: string[]; operator_role: string; applicability_notes: string[] }
export interface EvidenceSnapshot extends Omit<Passage, 'applicability'> {
  evidence_id: string; content_sha256: string; applicability: 'context_match' | 'unknown';
}
export interface RunResult {
  representation?: never;
  run_id: string; ticket_id: string; status: 'running' | 'completed' | 'failed';
  started_at: string; completed_at: string | null;
  routing_outcome: 'l1_self_service' | 'l1_guided' | 'l2_escalation' | 'non_it' | 'needs_clarification' | null;
  decision_mechanism: 'deterministic' | 'generation' | null;
  summary_for_requester: string | null; summary_for_ticket: string | null;
  questions: RunQuestion[]; steps: ReviewedStep[]; evidence: EvidenceSnapshot[]; warnings: string[];
  error_code: string | null; error_message: string | null; model: string | null; prompt_version: string | null;
}
export interface LegacyRunResult {
  representation: 'legacy_v1'; run_id: string; ticket_id: string; status: string;
  started_at: string; completed_at: string | null; routing_outcome: string | null;
  decision_summary: string | null; model: string | null; prompt_version: string | null;
  evidence_json: string; response_json: string | null;
}
export type RunReadResult = RunResult | LegacyRunResult;
export interface RunPage { runs: RunReadResult[]; total: number; limit: number; offset: number }
export type ActionDecision = 'approve' | 'reject';
export interface ActionProposal {
  action_id: string; ticket_id: string; run_id: string;
  action_type: 'simulate_post_comment' | 'simulate_escalation';
  preview: { version: 1; comment: string } | { version: 1; summary: string; reason: string };
  proposal_hash: string; context_fingerprint: string; status: 'pending' | 'rejected' | 'completed';
  created_at: string; decided_at: string | null; decided_by: string | null; result_summary: string | null;
}
export interface ActionProposalList { run_id: string; actions: ActionProposal[] }
export interface ActionDecisionResult {
  action_id: string; decision: ActionDecision; status: 'rejected' | 'completed';
  decided_at: string; decided_by: string; result_summary: string;
}
export interface AuditEvent {
  representation?: never;
  audit_event_id: number; ticket_id: string; run_id: string | null; action_id: string | null;
  actor_type: 'system' | 'agent' | 'human'; actor_id: string | null;
  event_type: 'run_started' | 'run_completed' | 'run_failed' | 'proposal_created' | 'proposal_approved' | 'proposal_rejected' | 'simulation_completed';
  occurred_at: string; approval_status: string | null;
  payload: { version: 1; summary: string; action_id: string | null; error_code: string | null };
}
export interface LegacyAuditEvent {
  representation: 'legacy_v1'; audit_event_id: number; ticket_id: string; run_id: string | null;
  actor_type: string; actor_id: string | null; event_type: string; occurred_at: string;
  approval_status: string | null; payload: unknown;
}
export interface AuditPage { events: (AuditEvent | LegacyAuditEvent)[]; total: number; limit: number; offset: number }
export interface ApiClient {
  startRun(ticketId: string, idempotencyKey: string): Promise<RunResult>;
  listRuns(ticketId: string, options?: { limit?: number; offset?: number }): Promise<RunPage>;
  getRun(runId: string): Promise<RunReadResult>;
  listActions(runId: string): Promise<ActionProposalList>;
  decideAction(actionId: string, decision: ActionDecision): Promise<ActionDecisionResult>;
  listAudit(ticketId: string, options?: { limit?: number; offset?: number }): Promise<AuditPage>;
  listIncidents(options?: { limit?: number; offset?: number; status?: string; category?: string }): Promise<IncidentPage>;
  createIncident(input: IncidentCreate): Promise<IncidentResult>;
  getIncident(ticketId: string): Promise<IncidentResult>;
  getContext(ticketId: string): Promise<IncidentContext>;
  searchKnowledge(input: SearchRequest): Promise<SearchResponse>;
  ingest(target: IngestionTarget): Promise<PipelineReport>;
}

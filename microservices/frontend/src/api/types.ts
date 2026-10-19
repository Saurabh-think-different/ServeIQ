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
export interface ApiClient {
  listIncidents(options?: { limit?: number; offset?: number; status?: string; category?: string }): Promise<IncidentPage>;
  createIncident(input: IncidentCreate): Promise<IncidentResult>;
  getIncident(ticketId: string): Promise<IncidentResult>;
  getContext(ticketId: string): Promise<IncidentContext>;
  searchKnowledge(input: SearchRequest): Promise<SearchResponse>;
  ingest(target: IngestionTarget): Promise<PipelineReport>;
}

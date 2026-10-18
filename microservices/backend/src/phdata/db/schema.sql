CREATE TABLE IF NOT EXISTS stores (
    store_id TEXT PRIMARY KEY,
    store_name TEXT NOT NULL,
    city TEXT NOT NULL,
    neighborhood TEXT NOT NULL,
    store_type TEXT NOT NULL CHECK (store_type IN ('corporate', 'franchise')),
    region_manager TEXT,
    support_phone TEXT
);

CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL REFERENCES stores(store_id),
    asset_type TEXT NOT NULL,
    make TEXT NOT NULL,
    model TEXT NOT NULL,
    system_version TEXT,
    serial_number TEXT,
    install_date TEXT,
    last_service_date TEXT,
    warranty_expiry TEXT
);

CREATE TABLE IF NOT EXISTS sla_targets (
    priority TEXT NOT NULL CHECK (priority IN ('P1', 'P2', 'P3', 'P4')),
    category TEXT NOT NULL,
    first_response_minutes INTEGER NOT NULL CHECK (first_response_minutes >= 0),
    resolution_target_minutes INTEGER NOT NULL CHECK (resolution_target_minutes >= 0),
    escalation_to_l2_minutes INTEGER NOT NULL CHECK (escalation_to_l2_minutes >= 0),
    PRIMARY KEY (priority, category)
);

CREATE TABLE IF NOT EXISTS incidents (
    ticket_id TEXT PRIMARY KEY,
    reported_store_id TEXT NOT NULL,
    store_id TEXT REFERENCES stores(store_id),
    reported_asset_id TEXT,
    asset_id TEXT REFERENCES assets(asset_id),
    submitted_by TEXT,
    contact_phone TEXT,
    opened_at TEXT NOT NULL,
    priority TEXT CHECK (priority IS NULL OR priority IN ('P1', 'P2', 'P3', 'P4')),
    category TEXT,
    subcategory TEXT,
    status TEXT NOT NULL,
    subject TEXT NOT NULL,
    description TEXT NOT NULL,
    system_version TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    escalation_flag INTEGER NOT NULL DEFAULT 0 CHECK (escalation_flag IN (0, 1)),
    resolution_notes TEXT,
    source_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (asset_id IS NULL OR store_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS incident_events (
    event_id INTEGER PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES incidents(ticket_id) ON DELETE CASCADE,
    source_event_index INTEGER NOT NULL,
    event_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT NOT NULL,
    UNIQUE (ticket_id, source_event_index)
);

CREATE TABLE IF NOT EXISTS incident_relations (
    ticket_id TEXT NOT NULL REFERENCES incidents(ticket_id) ON DELETE CASCADE,
    reported_related_ticket_id TEXT NOT NULL,
    related_ticket_id TEXT REFERENCES incidents(ticket_id),
    relation_type TEXT NOT NULL DEFAULT 'related',
    PRIMARY KEY (ticket_id, reported_related_ticket_id)
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES incidents(ticket_id),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    model TEXT,
    prompt_version TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    routing_outcome TEXT CHECK (
        routing_outcome IS NULL OR routing_outcome IN (
            'l1_self_service', 'l1_guided', 'l2_escalation', 'non_it',
            'needs_clarification'
        )
    ),
    decision_summary TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    response_json TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    audit_event_id INTEGER PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES incidents(ticket_id),
    run_id TEXT REFERENCES agent_runs(run_id),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('system', 'agent', 'human')),
    actor_id TEXT,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    approval_status TEXT CHECK (
        approval_status IS NULL OR approval_status IN (
            'not_required', 'pending', 'approved', 'rejected', 'completed', 'failed'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_ticket_started
    ON agent_runs(ticket_id, started_at);
CREATE INDEX IF NOT EXISTS idx_audit_events_ticket_time
    ON audit_events(ticket_id, occurred_at);

CREATE TRIGGER IF NOT EXISTS incidents_asset_store_insert
BEFORE INSERT ON incidents
WHEN NEW.asset_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM assets
    WHERE asset_id = NEW.asset_id AND store_id = NEW.store_id
)
BEGIN
    SELECT RAISE(ABORT, 'verified asset must belong to the resolved incident store');
END;

CREATE TRIGGER IF NOT EXISTS incidents_asset_store_update
BEFORE UPDATE OF asset_id, store_id ON incidents
WHEN NEW.asset_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM assets
    WHERE asset_id = NEW.asset_id AND store_id = NEW.store_id
)
BEGIN
    SELECT RAISE(ABORT, 'verified asset must belong to the resolved incident store');
END;
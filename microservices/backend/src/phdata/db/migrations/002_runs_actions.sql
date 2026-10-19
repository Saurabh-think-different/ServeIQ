ALTER TABLE agent_runs ADD COLUMN idempotency_key TEXT;
ALTER TABLE agent_runs ADD COLUMN request_fingerprint TEXT;
ALTER TABLE agent_runs ADD COLUMN context_fingerprint TEXT;
ALTER TABLE agent_runs ADD COLUMN context_snapshot_json TEXT;
ALTER TABLE agent_runs ADD COLUMN error_code TEXT;

CREATE UNIQUE INDEX idx_agent_runs_ticket_idempotency
    ON agent_runs(ticket_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE action_proposals (
    action_id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES incidents(ticket_id),
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id),
    action_type TEXT NOT NULL CHECK (
        action_type IN ('simulate_post_comment', 'simulate_escalation')
    ),
    payload_json TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    context_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'rejected', 'completed')),
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    result_json TEXT,
    CHECK (
        (status = 'pending' AND decided_at IS NULL AND decided_by IS NULL AND result_json IS NULL)
        OR (status IN ('rejected', 'completed') AND decided_at IS NOT NULL AND decided_by IS NOT NULL AND result_json IS NOT NULL)
    )
);

CREATE INDEX idx_action_proposals_run_created
    ON action_proposals(run_id, created_at, action_id);

CREATE TRIGGER audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit events are append-only');
END;

CREATE TRIGGER audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit events are append-only');
END;

CREATE TRIGGER action_proposals_immutable_content
BEFORE UPDATE OF action_id, ticket_id, run_id, action_type, payload_json,
                 proposal_hash, context_fingerprint, created_at
ON action_proposals
BEGIN
    SELECT RAISE(ABORT, 'action proposal content is immutable');
END;

CREATE TRIGGER action_proposals_no_delete
BEFORE DELETE ON action_proposals
BEGIN
    SELECT RAISE(ABORT, 'action proposals are immutable');
END;

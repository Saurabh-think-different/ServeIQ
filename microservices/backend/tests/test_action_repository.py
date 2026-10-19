from datetime import datetime, timezone

import pytest
import sqlite3
from pydantic import ValidationError

from phdata.db.database import open_database
from phdata.models.actions import ActionProposal, AuditPayload, PostCommentPreview
from phdata.repositories.actions import (
    append_audit_event,
    create_proposal,
    get_proposal,
    list_audit_events,
    list_proposals_for_run,
    proposal_hash_for,
)
from phdata.repositories.runs import insert_run

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def proposal(action_id="ACTION-1", *, proposal_hash=None):
    candidate = ActionProposal(
        action_id=action_id,
        ticket_id="INC-1",
        run_id="RUN-1",
        action_type="simulate_post_comment",
        preview=PostCommentPreview(comment="Confirm the printer power light is on."),
        proposal_hash=proposal_hash or "0" * 64,
        context_fingerprint="b" * 64,
        status="pending",
        created_at=NOW,
    )
    return candidate.model_copy(update={"proposal_hash": proposal_hash_for(candidate)}) if proposal_hash is None else candidate


def setup_database():
    connection = open_database(":memory:")
    connection.execute(
        "INSERT INTO stores (store_id, store_name, city, neighborhood, store_type) "
        "VALUES ('S1', 'Store', 'City', 'Area', 'corporate')",
    )
    connection.execute(
        """INSERT INTO incidents (
            ticket_id, reported_store_id, store_id, opened_at, status, subject, description
        ) VALUES ('INC-1', 'S1', 'S1', ?, 'Open', 'Printer', 'Offline')""",
        (NOW.isoformat(),),
    )
    insert_run(
        connection,
        run_id="RUN-1",
        ticket_id="INC-1",
        started_at=NOW.isoformat(),
        idempotency_key="key",
        request_fingerprint="a" * 64,
    )
    return connection


def test_proposal_preview_is_persisted_and_immutable():
    connection = setup_database()
    original = proposal()
    create_proposal(connection, original)
    assert get_proposal(connection, original.action_id) == original
    assert list_proposals_for_run(connection, "RUN-1") == [original]

    with pytest.raises(sqlite3.IntegrityError, match="content is immutable"):
        connection.execute("UPDATE action_proposals SET payload_json='{}' WHERE action_id='ACTION-1'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("DELETE FROM action_proposals WHERE action_id='ACTION-1'")
    connection.close()


def test_proposal_hash_must_match_normalized_preview():
    connection = setup_database()
    invalid = proposal(proposal_hash="c" * 64)
    with pytest.raises(ValueError, match="does not match"):
        create_proposal(connection, invalid)
    connection.close()


def test_audit_events_round_trip_in_stable_order():
    connection = setup_database()
    action = proposal()
    create_proposal(connection, action)
    appended = append_audit_event(
        connection,
        ticket_id="INC-1",
        run_id="RUN-1",
        actor_type="agent",
        actor_id="support_workflow",
        event_type="proposal_created",
        occurred_at=NOW.isoformat(),
        approval_status="pending",
        payload=AuditPayload(summary="Created local support proposal", action_id=action.action_id),
    )
    page = list_audit_events(connection, ticket_id="INC-1")
    assert appended.audit_event_id == 1
    assert page.total == 1
    assert page.events[0].action_id == action.action_id
    assert page.events[0].payload.summary == "Created local support proposal"
    assert list_audit_events(connection, ticket_id="INC-1", limit=1, offset=1).events == []
    connection.close()


def test_audit_payload_rejects_unstructured_extra_data():
    with pytest.raises(ValidationError):
        AuditPayload.model_validate({"version": 1, "summary": "safe", "prompt": "secret"})

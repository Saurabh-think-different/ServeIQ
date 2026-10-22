from datetime import datetime, timezone

import pytest
import sqlite3

from phdata.db.database import open_database
from phdata.models.runs import RunResult
from phdata.repositories.runs import (
    RunStateConflict,
    finalize_run,
    find_run_by_idempotency_key,
    get_run,
    get_run_by_idempotency_key,
    insert_run,
    list_runs,
)

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
REQUEST_FINGERPRINT = "a" * 64
CONTEXT_FINGERPRINT = "b" * 64


def add_incident(connection, ticket_id):
    if connection.execute("SELECT 1 FROM stores WHERE store_id='S1'").fetchone() is None:
        connection.execute(
            "INSERT INTO stores (store_id, store_name, city, neighborhood, store_type) "
            "VALUES ('S1', 'Store', 'City', 'Area', 'corporate')",
        )
    connection.execute(
        """INSERT INTO incidents (
            ticket_id, reported_store_id, store_id, opened_at, status, subject, description
        ) VALUES (?, 'S1', 'S1', ?, 'Open', 'Printer', 'Offline')""",
        (ticket_id, NOW.isoformat()),
    )


def test_idempotency_is_per_incident_and_run_survives_reopen():
    connection = open_database(":memory:")
    add_incident(connection, "INC-1")
    add_incident(connection, "INC-2")
    first = insert_run(
        connection,
        run_id="RUN-1",
        ticket_id="INC-1",
        started_at=NOW.isoformat(),
        idempotency_key="request-1",
        request_fingerprint=REQUEST_FINGERPRINT,
    )
    assert get_run(connection, "RUN-1") == first
    assert get_run_by_idempotency_key(
        connection, ticket_id="INC-1", idempotency_key="request-1",
    ) == first
    assert find_run_by_idempotency_key(
        connection, ticket_id="INC-1", idempotency_key="request-1",
    ) == (first, REQUEST_FINGERPRINT)

    other_ticket = insert_run(
        connection,
        run_id="RUN-2",
        ticket_id="INC-2",
        started_at=NOW.isoformat(),
        idempotency_key="request-1",
        request_fingerprint=REQUEST_FINGERPRINT,
    )
    assert other_ticket.ticket_id == "INC-2"
    with pytest.raises(sqlite3.IntegrityError):
        insert_run(
            connection,
            run_id="RUN-3",
            ticket_id="INC-1",
            started_at=NOW.isoformat(),
            idempotency_key="request-1",
            request_fingerprint=REQUEST_FINGERPRINT,
        )
    connection.close()


def test_final_run_snapshot_and_replay_are_persisted():
    connection = open_database(":memory:")
    add_incident(connection, "INC-1")
    running = insert_run(
        connection,
        run_id="RUN-1",
        ticket_id="INC-1",
        started_at=NOW.isoformat(),
        idempotency_key="key",
        request_fingerprint=REQUEST_FINGERPRINT,
    )
    finished_at = datetime(2026, 10, 20, 0, 1, tzinfo=timezone.utc)
    completed = RunResult(
        run_id=running.run_id,
        ticket_id=running.ticket_id,
        status="completed",
        started_at=running.started_at,
        completed_at=finished_at,
        routing_outcome="needs_clarification",
        decision_mechanism="deterministic",
        summary_for_ticket="Ask which printer is affected.",
    )
    finalize_run(
        connection,
        completed,
        context_fingerprint=CONTEXT_FINGERPRINT,
        context_snapshot_json='{"version":1,"missing":["asset_id"]}',
    )
    connection.commit()
    assert get_run(connection, "RUN-1") == completed
    page = list_runs(connection, ticket_id="INC-1", limit=20, offset=0)
    assert page.total == 1 and page.runs == [completed]
    assert get_run_by_idempotency_key(connection, ticket_id="INC-1", idempotency_key="key") == completed
    with pytest.raises(RunStateConflict):
        finalize_run(
            connection,
            completed,
            context_fingerprint=CONTEXT_FINGERPRINT,
            context_snapshot_json='{"version":1}',
        )
    connection.close()


def test_failed_result_is_persisted_without_raw_provider_error():
    connection = open_database(":memory:")
    add_incident(connection, "INC-1")
    running = insert_run(
        connection,
        run_id="RUN-1",
        ticket_id="INC-1",
        started_at=NOW.isoformat(),
        idempotency_key="key",
        request_fingerprint=REQUEST_FINGERPRINT,
    )
    failed = RunResult(
        run_id=running.run_id,
        ticket_id=running.ticket_id,
        status="failed",
        started_at=running.started_at,
        completed_at=NOW,
        error_code="provider_unavailable",
        error_message="Support analysis is temporarily unavailable.",
    )
    finalize_run(
        connection,
        failed,
        context_fingerprint=CONTEXT_FINGERPRINT,
        context_snapshot_json='{"version":1}',
    )
    connection.commit()
    stored = get_run(connection, "RUN-1")
    assert stored is not None and stored.error_code == "provider_unavailable"
    assert "traceback" not in stored.error_message.lower()
    connection.close()


def test_run_repository_leaves_transaction_control_to_caller():
    connection = open_database(":memory:")
    add_incident(connection, "INC-1")
    connection.commit()
    insert_run(
        connection,
        run_id="RUN-1",
        ticket_id="INC-1",
        started_at=NOW.isoformat(),
        idempotency_key="key",
        request_fingerprint=REQUEST_FINGERPRINT,
    )
    assert connection.in_transaction
    connection.rollback()
    assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
    connection.close()


def test_invalid_snapshot_and_pagination_are_rejected():
    connection = open_database(":memory:")
    add_incident(connection, "INC-1")
    running = insert_run(
        connection,
        run_id="RUN-1",
        ticket_id="INC-1",
        started_at=NOW.isoformat(),
        idempotency_key="key",
        request_fingerprint=REQUEST_FINGERPRINT,
    )
    completed = RunResult(
        run_id=running.run_id,
        ticket_id=running.ticket_id,
        status="completed",
        started_at=NOW,
        completed_at=NOW,
        routing_outcome="l2_escalation",
        decision_mechanism="deterministic",
    )
    with pytest.raises(ValueError, match="version-1 object"):
        finalize_run(
            connection, completed, context_fingerprint=CONTEXT_FINGERPRINT,
            context_snapshot_json='{"version":2}',
        )
    with pytest.raises(ValueError, match="limit"):
        list_runs(connection, ticket_id="INC-1", limit=0)
    connection.close()

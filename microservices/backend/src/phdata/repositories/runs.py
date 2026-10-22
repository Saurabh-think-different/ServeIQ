"""Concrete SQLite persistence for idempotent support-run records."""

import json
import re
import sqlite3
from typing import Any

from phdata.models.legacy import LegacyRunResult
from phdata.models.runs import RunPage, RunReadResult, RunResult

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RunStateConflict(Exception):
    """A run has already left the state expected by a repository operation."""


def insert_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    ticket_id: str,
    started_at: str,
    idempotency_key: str,
    request_fingerprint: str,
) -> RunResult:
    """Insert a running row; the caller owns and commits the transaction."""
    if not idempotency_key.strip() or len(idempotency_key) > 128:
        raise ValueError("idempotency_key must contain 1 to 128 nonblank characters")
    if not _SHA256.fullmatch(request_fingerprint):
        raise ValueError("request_fingerprint must be a lowercase SHA-256 hash")

    result = RunResult(
        run_id=run_id,
        ticket_id=ticket_id,
        status="running",
        started_at=started_at,
    )
    connection.execute(
        """INSERT INTO agent_runs (
            run_id, ticket_id, started_at, status, idempotency_key,
            request_fingerprint, response_json
        ) VALUES (?, ?, ?, 'running', ?, ?, ?)""",
        (run_id, ticket_id, started_at, idempotency_key, request_fingerprint, result.model_dump_json()),
    )
    return result


def get_run(connection: sqlite3.Connection, run_id: str) -> RunReadResult | None:
    """Read untracked v1 rows as history; v2 snapshots still require validation."""
    row = connection.execute(
        "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,),
    ).fetchone()
    if row is None:
        return None
    if all(row[column] is None for column in (
        "idempotency_key", "request_fingerprint", "context_fingerprint", "context_snapshot_json", "error_code",
    )):
        return LegacyRunResult(**{
            field: row[field] for field in LegacyRunResult.model_fields if field != "representation"
        })
    if row["response_json"] is None:
        raise ValueError("stored run does not contain a typed response snapshot")
    return RunResult.model_validate_json(row["response_json"])


def find_run_by_idempotency_key(
    connection: sqlite3.Connection,
    *,
    ticket_id: str,
    idempotency_key: str,
) -> tuple[RunResult, str] | None:
    row = connection.execute(
        "SELECT run_id, request_fingerprint FROM agent_runs "
        "WHERE ticket_id = ? AND idempotency_key = ?",
        (ticket_id, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    result = get_run(connection, row["run_id"])
    if result is None:
        return None
    if not isinstance(result, RunResult):
        raise ValueError("legacy runs cannot be replayed as typed results")
    fingerprint = row["request_fingerprint"]
    if not isinstance(fingerprint, str) or not _SHA256.fullmatch(fingerprint):
        raise ValueError("stored request fingerprint is invalid")
    return result, fingerprint


def get_run_by_idempotency_key(
    connection: sqlite3.Connection,
    *,
    ticket_id: str,
    idempotency_key: str,
) -> RunResult | None:
    record = find_run_by_idempotency_key(
        connection, ticket_id=ticket_id, idempotency_key=idempotency_key,
    )
    return record[0] if record is not None else None


def finalize_run(
    connection: sqlite3.Connection,
    result: RunResult,
    *,
    context_fingerprint: str,
    context_snapshot_json: str,
) -> None:
    """Persist a typed terminal result without committing the caller's transaction."""
    if not isinstance(result, RunResult) or result.status == "running" or result.completed_at is None:
        raise ValueError("finalize_run requires a completed or failed typed result")
    if not _SHA256.fullmatch(context_fingerprint):
        raise ValueError("context_fingerprint must be a lowercase SHA-256 hash")
    snapshot: Any = json.loads(context_snapshot_json)
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        raise ValueError("context snapshot must be a version-1 object")

    cursor = connection.execute(
        """UPDATE agent_runs SET
            completed_at = ?, model = ?, prompt_version = ?, status = ?,
            routing_outcome = ?, decision_summary = ?, evidence_json = ?,
            response_json = ?, context_fingerprint = ?, context_snapshot_json = ?,
            error_code = ?
        WHERE run_id = ? AND status = 'running'""",
        (
            result.completed_at.isoformat(),
            result.model,
            result.prompt_version,
            result.status,
            result.routing_outcome,
            result.summary_for_ticket,
            json.dumps([item.model_dump(mode="json") for item in result.evidence], separators=(",", ":")),
            result.model_dump_json(),
            context_fingerprint,
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
            result.error_code,
            result.run_id,
        ),
    )
    if cursor.rowcount != 1:
        raise RunStateConflict("run is missing or is no longer running")


def list_runs(
    connection: sqlite3.Connection,
    *,
    ticket_id: str,
    limit: int = 20,
    offset: int = 0,
) -> RunPage:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1 to 100 and offset must not be negative")
    total = connection.execute(
        "SELECT COUNT(*) FROM agent_runs WHERE ticket_id = ?", (ticket_id,),
    ).fetchone()[0]
    rows = connection.execute(
        "SELECT run_id FROM agent_runs WHERE ticket_id = ? "
        "ORDER BY started_at DESC, run_id ASC LIMIT ? OFFSET ?",
        (ticket_id, limit, offset),
    ).fetchall()
    return RunPage(
        runs=[get_run(connection, row["run_id"]) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )

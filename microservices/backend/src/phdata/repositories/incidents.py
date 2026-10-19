"""Parameterized incident persistence on an already-open SQLite connection."""

import sqlite3
from uuid import uuid4

from phdata.models.incidents import (
    Incident, IncidentCreate, IncidentHistory, IncidentPage, IncidentResult,
)

_COLUMNS = """ticket_id, NULLIF(reported_store_id, '') AS reported_store_id,
    store_id, reported_asset_id, asset_id, opened_at, priority, category,
    subcategory, status, subject, description, system_version"""


class DuplicateIncident(Exception):
    """A ticket ID is already owned by an incident."""


def _incident(connection: sqlite3.Connection, row: sqlite3.Row) -> Incident:
    history = [
        IncidentHistory(timestamp=event["event_at"], actor=event["actor"], note=event["note"])
        for event in connection.execute(
            "SELECT event_at, actor, note FROM incident_events WHERE ticket_id = ? "
            "ORDER BY source_event_index, event_id", (row["ticket_id"],),
        )
    ]
    return Incident(**dict(row), history=history)


def _warnings(incident: Incident) -> list[str]:
    warnings = []
    if incident.reported_store_id is None:
        warnings.append("store_id was not supplied; stored without a resolved store")
    elif incident.store_id is None:
        warnings.append("store_id was not found; reported ID preserved without a resolved store")
    if incident.reported_asset_id is None:
        warnings.append("asset_id was not supplied; stored without a resolved asset")
    elif incident.asset_id is None:
        warnings.append("asset_id could not be verified for the resolved store; reported ID preserved")
    return warnings


def get_incident(connection: sqlite3.Connection, ticket_id: str) -> IncidentResult | None:
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM incidents WHERE ticket_id = ?", (ticket_id,),
    ).fetchone()
    if row is None:
        return None
    incident = _incident(connection, row)
    return IncidentResult(incident=incident, warnings=_warnings(incident))


def create_incident(connection: sqlite3.Connection, intake: IncidentCreate) -> IncidentResult:
    ticket_id = intake.ticket_id or f"INC-{uuid4()}"
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute(
            "SELECT 1 FROM incidents WHERE ticket_id = ?", (ticket_id,),
        ).fetchone():
            raise DuplicateIncident
        store = connection.execute(
            "SELECT store_id FROM stores WHERE store_id = ?", (intake.store_id,),
        ).fetchone()
        store_id = store["store_id"] if store is not None else None
        asset = connection.execute(
            "SELECT asset_id FROM assets WHERE asset_id = ? AND store_id = ?",
            (intake.asset_id, store_id),
        ).fetchone()
        asset_id = asset["asset_id"] if asset is not None else None
        subject = intake.subject or " ".join(intake.description.split())[:100]
        connection.execute(
            """INSERT INTO incidents (
                ticket_id, reported_store_id, store_id, reported_asset_id, asset_id,
                opened_at, priority, category, subcategory, status, subject,
                description, system_version, source_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Open', ?, ?, ?, NULL)""",
            (ticket_id, intake.store_id or "", store_id, intake.asset_id, asset_id,
             intake.opened_at, intake.priority, intake.category, intake.subcategory,
             subject, intake.description, intake.system_version),
        )
        connection.executemany(
            "INSERT INTO incident_events "
            "(ticket_id, source_event_index, event_at, actor, note) VALUES (?, ?, ?, ?, ?)",
            [(ticket_id, index, event.timestamp, event.actor, event.note)
             for index, event in enumerate(intake.history)],
        )
        result = get_incident(connection, ticket_id)
        assert result is not None
        if intake.status is not None and intake.status != "Open":
            result.warnings.append("Submitted status was not applied; new incidents start Open")
    return result


def list_incidents(
    connection: sqlite3.Connection, *, limit: int, offset: int,
    status: str | None = None, category: str | None = None,
) -> IncidentPage:
    where = " WHERE (? IS NULL OR status = ?) AND (? IS NULL OR category = ?)"
    values = (status, status, category, category)
    total = connection.execute("SELECT COUNT(*) FROM incidents" + where, values).fetchone()[0]
    rows = connection.execute(
        f"SELECT {_COLUMNS} FROM incidents" + where
        + " ORDER BY julianday(opened_at) DESC, ticket_id ASC LIMIT ? OFFSET ?",
        (*values, limit, offset),
    ).fetchall()
    return IncidentPage(
        incidents=[_incident(connection, row) for row in rows], total=total, limit=limit, offset=offset,
    )

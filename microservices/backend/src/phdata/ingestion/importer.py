"""Transactional SQLite ingestion for the ServeWell source dataset."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

TICKET_DIRECTORY_MAPPINGS = {"raw": "train"}


class DatasetImportError(ValueError):
    """Input or database error with actionable source context."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass
class ImportReport:
    inserted: Counter[str] = field(default_factory=Counter)
    updated: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)
    deleted: Counter[str] = field(default_factory=Counter)
    path_mappings: Counter[str] = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)


def _error(context: str, message: str) -> DatasetImportError:
    return DatasetImportError([f"{context}: {message}"])


def _required_text(record: dict[str, Any], field_name: str, context: str) -> str:
    value = record.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise _error(context, f"required field {field_name!r} is missing or empty")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _timestamp(value: Any, context: str, field_name: str) -> str:
    text = _required_text({field_name: value}, field_name, context)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise _error(context, f"{field_name} must be an ISO-8601 timestamp: {text!r}") from error
    if parsed.utcoffset() is None:
        raise _error(context, f"{field_name} must include a UTC offset")
    return text


def _boolean(value: Any, context: str, field_name: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return 1
        if normalized in {"false", "0"}:
            return 0
    raise _error(context, f"{field_name} must be a boolean")


def _integer(value: Any, context: str, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise _error(context, f"{field_name} must be an integer") from error


def _read_csv(path: Path, required_columns: set[str]) -> list[dict[str, str | None]]:
    if not path.is_file():
        raise _error(str(path), "required CSV file does not exist")
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames is None:
                raise _error(str(path), "CSV has no header row")
            missing = required_columns.difference(reader.fieldnames)
            if missing:
                raise _error(str(path), f"missing columns: {', '.join(sorted(missing))}")
            return list(reader)
    except OSError as error:
        raise _error(str(path), f"could not read CSV: {error}") from error


def _assert_unique(rows: list[dict[str, Any]], key: str, source: Path) -> None:
    seen: set[str] = set()
    for line_number, row in enumerate(rows, start=2):
        value = _required_text(row, key, f"{source}:{line_number}")
        if value in seen:
            raise _error(f"{source}:{line_number}", f"duplicate {key} {value!r}")
        seen.add(value)


def _read_ticket(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise _error(str(path), "ticket JSON file does not exist")
    try:
        with path.open("r", encoding="utf-8") as source:
            record = json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise _error(str(path), f"could not read ticket JSON: {error}") from error
    if not isinstance(record, dict):
        raise _error(str(path), "ticket JSON must contain an object")
    _validate_ticket(record, str(path))
    return record


def _validate_ticket(record: dict[str, Any], context: str) -> None:
    for field_name in ("ticket_id", "store_id", "status", "subject", "description"):
        _required_text(record, field_name, context)
    _timestamp(record.get("date_opened"), context, "date_opened")

    for list_field in ("tags", "ticket_history", "related_tickets"):
        value = record.get(list_field, [])
        if value is None:
            value = []
        if not isinstance(value, list):
            raise _error(context, f"{list_field} must be a JSON array")

    history = record.get("ticket_history") or []
    for event_index, event in enumerate(history):
        event_context = f"{context}: ticket_history[{event_index}]"
        if not isinstance(event, dict):
            raise _error(event_context, "history item must be an object")
        _timestamp(event.get("timestamp"), event_context, "timestamp")
        _required_text(event, "actor", event_context)
        _required_text(event, "note", event_context)

    for related_id in record.get("related_tickets") or []:
        if not isinstance(related_id, str) or not related_id.strip():
            raise _error(context, "related_tickets entries must be non-empty ticket IDs")

    tags = record.get("tags") or []
    if any(not isinstance(tag, str) for tag in tags):
        raise _error(context, "tags entries must be strings")
    _boolean(record.get("escalation_flag", False), context, "escalation_flag")


def _resolve_manifest_ticket(
    tickets_root: Path,
    filename: str,
    manifest_path: Path,
    line_number: int,
    report: ImportReport,
) -> Path:
    normalized = filename.replace("\\", "/")
    relative_path = PurePosixPath(normalized)
    context = f"{manifest_path}:{line_number}"
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise _error(context, f"unsafe ticket filename {filename!r}")

    exact_path = tickets_root.joinpath(*relative_path.parts)
    if exact_path.is_file():
        return exact_path

    if relative_path.parts and relative_path.parts[0] in TICKET_DIRECTORY_MAPPINGS:
        source_prefix = relative_path.parts[0]
        mapped_prefix = TICKET_DIRECTORY_MAPPINGS[source_prefix]
        mapped_path = tickets_root.joinpath(mapped_prefix, *relative_path.parts[1:])
        if mapped_path.is_file():
            report.path_mappings[f"tickets/{source_prefix} -> tickets/{mapped_prefix}"] += 1
            return mapped_path

    raise _error(context, f"ticket file {filename!r} is missing under {tickets_root}")


def _upsert(
    connection: sqlite3.Connection,
    table: str,
    key_columns: tuple[str, ...],
    values: dict[str, Any],
    report: ImportReport,
) -> None:
    columns = tuple(values)
    where = " AND ".join(f"{column} = ?" for column in key_columns)
    key_values = tuple(values[column] for column in key_columns)
    selected = ", ".join(columns)
    existing = connection.execute(
        f"SELECT {selected} FROM {table} WHERE {where}", key_values
    ).fetchone()

    try:
        if existing is None:
            placeholders = ", ".join("?" for _ in columns)
            connection.execute(
                f"INSERT INTO {table} ({selected}) VALUES ({placeholders})",
                tuple(values[column] for column in columns),
            )
            report.inserted[table] += 1
        elif all(existing[column] == values[column] for column in columns):
            report.skipped[table] += 1
        else:
            update_columns = [column for column in columns if column not in key_columns]
            assignments = [f"{column} = ?" for column in update_columns]
            if table == "incidents":
                assignments.append("updated_at = CURRENT_TIMESTAMP")
            connection.execute(
                f"UPDATE {table} SET {', '.join(assignments)} WHERE {where}",
                tuple(values[column] for column in update_columns) + key_values,
            )
            report.updated[table] += 1
    except sqlite3.IntegrityError as error:
        key_description = ", ".join(f"{key}={values[key]!r}" for key in key_columns)
        raise _error(f"{table} record ({key_description})", str(error)) from error


def _reference_maps(
    connection: sqlite3.Connection,
) -> tuple[set[str], dict[str, str]]:
    store_ids = {row[0] for row in connection.execute("SELECT store_id FROM stores")}
    asset_stores = {
        row["asset_id"]: row["store_id"]
        for row in connection.execute("SELECT asset_id, store_id FROM assets")
    }
    return store_ids, asset_stores


def _ticket_rows(
    record: dict[str, Any],
    context: str,
    source_path: str,
    store_ids: set[str],
    asset_stores: dict[str, str],
    known_ticket_ids: set[str],
    report: ImportReport,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ticket_id = _required_text(record, "ticket_id", context)
    reported_store_id = _required_text(record, "store_id", context)
    store_id = reported_store_id if reported_store_id in store_ids else None
    if store_id is None:
        report.warnings.append(
            f"{ticket_id}: reported store {reported_store_id!r} was not found; stored unresolved"
        )

    reported_asset_id = _optional_text(record.get("asset_id"))
    asset_id = None
    if reported_asset_id is not None:
        if store_id is not None and asset_stores.get(reported_asset_id) == store_id:
            asset_id = reported_asset_id
        else:
            report.warnings.append(
                f"{ticket_id}: reported asset {reported_asset_id!r} was not verified for its store"
            )

    tags = record.get("tags") or []
    incident = {
        "ticket_id": ticket_id,
        "reported_store_id": reported_store_id,
        "store_id": store_id,
        "reported_asset_id": reported_asset_id,
        "asset_id": asset_id,
        "submitted_by": _optional_text(record.get("submitted_by")),
        "contact_phone": _optional_text(record.get("contact_phone")),
        "opened_at": _timestamp(record.get("date_opened"), context, "date_opened"),
        "priority": _optional_text(record.get("priority")),
        "category": _optional_text(record.get("category")),
        "subcategory": _optional_text(record.get("subcategory")),
        "status": _required_text(record, "status", context),
        "subject": _required_text(record, "subject", context),
        "description": _required_text(record, "description", context),
        "system_version": _optional_text(record.get("system_version")),
        "tags_json": json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
        "escalation_flag": _boolean(record.get("escalation_flag", False), context, "escalation_flag"),
        "resolution_notes": _optional_text(record.get("resolution_notes")),
        "source_path": source_path,
    }

    events = [
        {
            "ticket_id": ticket_id,
            "source_event_index": event_index,
            "event_at": _timestamp(event.get("timestamp"), f"{context}: history[{event_index}]", "timestamp"),
            "actor": _required_text(event, "actor", f"{context}: history[{event_index}]"),
            "note": _required_text(event, "note", f"{context}: history[{event_index}]"),
        }
        for event_index, event in enumerate(record.get("ticket_history") or [])
    ]

    relations: list[dict[str, Any]] = []
    for related_id in record.get("related_tickets") or []:
        resolved_related_id = related_id if related_id in known_ticket_ids else None
        if resolved_related_id is None:
            report.warnings.append(
                f"{ticket_id}: related ticket {related_id!r} was not found; stored unresolved"
            )
        relations.append({
            "ticket_id": ticket_id,
            "reported_related_ticket_id": related_id,
            "related_ticket_id": resolved_related_id,
            "relation_type": "related",
        })
    return incident, events, relations


def _upsert_ticket_bundle(
    connection: sqlite3.Connection,
    ticket_records: list[tuple[dict[str, Any], str, str]],
    report: ImportReport,
) -> None:
    store_ids, asset_stores = _reference_maps(connection)
    known_ticket_ids = {
        row[0] for row in connection.execute("SELECT ticket_id FROM incidents")
    }
    known_ticket_ids.update(
        _required_text(record, "ticket_id", context)
        for record, context, _source_path in ticket_records
    )

    bundles = [
        _ticket_rows(record, context, source_path, store_ids, asset_stores, known_ticket_ids, report)
        for record, context, source_path in ticket_records
    ]
    for incident, _, _ in bundles:
        _upsert(connection, "incidents", ("ticket_id",), incident, report)
    for incident, events, relations in bundles:
        ticket_id = incident["ticket_id"]
        report.deleted["incident_events"] += connection.execute(
            "DELETE FROM incident_events WHERE ticket_id = ? AND source_event_index >= ?",
            (ticket_id, len(events)),
        ).rowcount
        current_relations = {relation["reported_related_ticket_id"] for relation in relations}
        stale_relations = [
            (ticket_id, row[0])
            for row in connection.execute(
                "SELECT reported_related_ticket_id FROM incident_relations WHERE ticket_id = ?",
                (ticket_id,),
            )
            if row[0] not in current_relations
        ]
        if stale_relations:
            report.deleted["incident_relations"] += connection.executemany(
                "DELETE FROM incident_relations WHERE ticket_id = ? AND reported_related_ticket_id = ?",
                stale_relations,
            ).rowcount
        for event in events:
            _upsert(
                connection,
                "incident_events",
                ("ticket_id", "source_event_index"),
                event,
                report,
            )
    for _, _, relations in bundles:
        for relation in relations:
            _upsert(
                connection,
                "incident_relations",
                ("ticket_id", "reported_related_ticket_id"),
                relation,
                report,
            )


def ingest_dataset(data_dir: str | Path, connection: sqlite3.Connection) -> ImportReport:
    """Import reference CSVs and indexed ticket JSON in one transaction."""
    data_root = Path(data_dir)
    report = ImportReport()
    stores_path = data_root / "data" / "stores.csv"
    assets_path = data_root / "data" / "assets.csv"
    sla_path = data_root / "data" / "sla_matrix.csv"
    manifest_path = data_root / "tickets" / "train_index.csv"

    store_rows = _read_csv(stores_path, {
        "store_id", "store_name", "city", "neighborhood", "store_type",
    })
    asset_rows = _read_csv(assets_path, {
        "asset_id", "store_id", "asset_type", "make", "model",
    })
    sla_rows = _read_csv(sla_path, {
        "priority", "category", "first_response_minutes",
        "resolution_target_minutes", "escalation_to_l2_minutes",
    })
    index_rows = _read_csv(manifest_path, {"ticket_id", "filename"})
    for rows, key, source in (
        (store_rows, "store_id", stores_path),
        (asset_rows, "asset_id", assets_path),
        (index_rows, "ticket_id", manifest_path),
    ):
        _assert_unique(rows, key, source)

    known_store_ids = {
        _required_text(row, "store_id", str(stores_path)) for row in store_rows
    }
    stores = []
    for row in store_rows:
        context = f"{stores_path}: store {_required_text(row, 'store_id', str(stores_path))}"
        store_type = _required_text(row, "store_type", context)
        if store_type not in {"corporate", "franchise"}:
            raise _error(context, f"unsupported store_type {store_type!r}")
        stores.append({
            "store_id": _required_text(row, "store_id", context),
            "store_name": _required_text(row, "store_name", context),
            "city": _required_text(row, "city", context),
            "neighborhood": _required_text(row, "neighborhood", context),
            "store_type": store_type,
            "region_manager": _optional_text(row.get("region_manager")),
            "support_phone": _optional_text(row.get("support_phone")),
        })

    assets = []
    for row in asset_rows:
        context = f"{assets_path}: asset {_required_text(row, 'asset_id', str(assets_path))}"
        store_id = _required_text(row, "store_id", context)
        if store_id not in known_store_ids:
            raise _error(context, f"store_id {store_id!r} is not present in stores.csv")
        assets.append({
            "asset_id": _required_text(row, "asset_id", context),
            "store_id": store_id,
            "asset_type": _required_text(row, "asset_type", context),
            "make": _required_text(row, "make", context),
            "model": _required_text(row, "model", context),
            "system_version": _optional_text(row.get("system_version")),
            "serial_number": _optional_text(row.get("serial_number")),
            "install_date": _optional_text(row.get("install_date")),
            "last_service_date": _optional_text(row.get("last_service_date")),
            "warranty_expiry": _optional_text(row.get("warranty_expiry")),
        })

    sla_targets = []
    for line_number, row in enumerate(sla_rows, start=2):
        context = f"{sla_path}:{line_number}"
        priority = _required_text(row, "priority", context)
        if priority not in {"P1", "P2", "P3", "P4"}:
            raise _error(context, f"unsupported priority {priority!r}")
        sla_targets.append({
            "priority": priority,
            "category": _required_text(row, "category", context),
            "first_response_minutes": _integer(row.get("first_response_minutes"), context, "first_response_minutes"),
            "resolution_target_minutes": _integer(row.get("resolution_target_minutes"), context, "resolution_target_minutes"),
            "escalation_to_l2_minutes": _integer(row.get("escalation_to_l2_minutes"), context, "escalation_to_l2_minutes"),
        })

    ticket_records: list[tuple[dict[str, Any], str, str]] = []
    tickets_root = data_root / "tickets"
    for line_number, index_row in enumerate(index_rows, start=2):
        context = f"{manifest_path}:{line_number}"
        expected_id = _required_text(index_row, "ticket_id", context)
        filename = _required_text(index_row, "filename", context)
        ticket_path = _resolve_manifest_ticket(
            tickets_root, filename, manifest_path, line_number, report
        )
        record = _read_ticket(ticket_path)
        actual_id = _required_text(record, "ticket_id", str(ticket_path))
        if actual_id != expected_id:
            raise _error(context, f"manifest ticket_id {expected_id!r} does not match JSON {actual_id!r}")
        source_path = ticket_path.relative_to(data_root).as_posix()
        ticket_records.append((record, str(ticket_path), source_path))

    connection.row_factory = sqlite3.Row
    try:
        with connection:
            for store in stores:
                _upsert(connection, "stores", ("store_id",), store, report)
            for asset in assets:
                _upsert(connection, "assets", ("asset_id",), asset, report)
            for target in sla_targets:
                _upsert(connection, "sla_targets", ("priority", "category"), target, report)
            _upsert_ticket_bundle(connection, ticket_records, report)
    except DatasetImportError:
        raise
    except sqlite3.Error as error:
        raise DatasetImportError([f"SQLite transaction rolled back: {error}"]) from error
    return report


def ingest_ticket_file(ticket_path: str | Path, connection: sqlite3.Connection) -> ImportReport:
    """Import one fresh ticket JSON after reference data has been loaded."""
    source = Path(ticket_path)
    record = _read_ticket(source)
    report = ImportReport()
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            _upsert_ticket_bundle(
                connection,
                [(record, str(source), source.name)],
                report,
            )
    except DatasetImportError:
        raise
    except sqlite3.Error as error:
        raise DatasetImportError([f"SQLite transaction rolled back: {error}"]) from error
    return report
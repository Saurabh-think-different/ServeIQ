"""Transactional pandas/Pydantic ingestion of reference CSVs and manifest tickets."""

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, TypeVar
import warnings

import pandas as pd
from pydantic import BaseModel, ValidationError

from phdata.config import Settings
from phdata.db.database import open_database
from phdata.ingestion.interfaces import IngestionError, IngestionReport
from phdata.ingestion.schemas import Asset, ManifestEntry, SLATarget, Store, Ticket

Model = TypeVar("Model", bound=BaseModel)


def _error(context: str, message: str) -> IngestionError:
    return IngestionError([f"{context}: {message}"])


def _validate(model: type[Model], value: Any, context: str, *, json_input: bool = False) -> Model:
    try:
        return model.model_validate_json(value) if json_input else model.model_validate(value)
    except ValidationError as error:
        raise IngestionError([
            f"{context}: {'.'.join(map(str, item['loc'])) or '$'}: {item['type']}"
            for item in error.errors(include_input=False, include_context=False, include_url=False)
        ]) from None


def _read_csv(path: Path, model: type[Model], keys: tuple[str, ...]) -> list[Model]:
    try:
        # Disable implicit indexes and reject pandas' warning before it can discard extra fields.
        # Underfilled trailing columns remain blank; Pydantic checks required values below.
        with warnings.catch_warnings():
            warnings.simplefilter("error", pd.errors.ParserWarning)
            frame = pd.read_csv(
                path, dtype=str, keep_default_na=False, encoding="utf-8-sig", index_col=False,
            )
    except (OSError, UnicodeError, ValueError, pd.errors.ParserError, pd.errors.ParserWarning):
        raise _error(str(path), "could not read required CSV (unreadable or malformed)") from None
    required = {name for name, field in model.model_fields.items() if field.is_required()}
    missing = required - set(frame.columns)
    if missing:
        raise _error(str(path), f"missing columns: {', '.join(sorted(missing))}")
    records = []
    seen = set()
    for row, data in enumerate(frame.to_dict(orient="records"), start=2):
        record = _validate(model, data, f"{path}:{row}")
        key = tuple(getattr(record, name) for name in keys)
        if key in seen:
            raise _error(f"{path}:{row}", f"duplicate key ({', '.join(keys)})")
        seen.add(key)
        records.append(record)
    return records


def _read_ticket(path: Path) -> Ticket:
    try:
        payload = path.read_bytes()
    except OSError:
        raise _error(str(path), "could not read ticket JSON") from None
    return _validate(Ticket, payload, str(path), json_input=True)


def _resolve_ticket(root: Path, entry: ManifestEntry, context: str, report: IngestionReport) -> Path:
    relative = Path(entry.filename)
    candidates = [root / relative]
    # Source manifests use raw/, but the supplied files live in train/. Exact wins.
    if relative.parts and relative.parts[0] == "raw":
        candidates.append(root / "train" / Path(*relative.parts[1:]))
    for index, path in enumerate(candidates):
        if not path.resolve().is_relative_to(root.resolve()):
            raise _error(context, "filename: ticket path escapes tickets directory")
        if path.is_file():
            if index:
                _count(report.path_mappings, "tickets/raw -> tickets/train")
            return path
    raise _error(context, f"filename: ticket file is missing ({entry.filename})")


def _count(counts: dict[str, int], key: str, amount: int = 1) -> None:
    counts[key] = counts.get(key, 0) + amount


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    # A savepoint also preserves any transaction already owned by the caller.
    connection.execute("SAVEPOINT structured_ingestion")
    try:
        yield
        connection.execute("RELEASE SAVEPOINT structured_ingestion")
    except Exception as error:
        connection.execute("ROLLBACK TO SAVEPOINT structured_ingestion")
        connection.execute("RELEASE SAVEPOINT structured_ingestion")
        if isinstance(error, sqlite3.Error):
            raise _error("SQLite", "structured ingestion transaction rolled back") from None
        raise


def _upsert(connection: sqlite3.Connection, table: str, keys: tuple[str, ...],
            values: dict[str, Any], report: IngestionReport) -> None:
    columns = tuple(values)
    where = " AND ".join(f"{key} = ?" for key in keys)
    key_values = tuple(values[key] for key in keys)
    existing = connection.execute(
        f"SELECT {', '.join(columns)} FROM {table} WHERE {where}", key_values,
    ).fetchone()
    if existing is not None and tuple(existing) == tuple(values.values()):
        _count(report.skipped, table)
        return
    assignments = ", ".join(f"{column} = excluded.{column}" for column in columns if column not in keys)
    if table == "incidents":
        assignments += ", updated_at = CURRENT_TIMESTAMP"
    connection.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
        f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {assignments}", tuple(values.values()),
    )
    _count(report.inserted if existing is None else report.updated, table)


def _write_tickets(connection: sqlite3.Connection, records: list[tuple[Ticket, str]],
                   report: IngestionReport) -> None:
    stores = {row[0] for row in connection.execute("SELECT store_id FROM stores")}
    assets = dict(connection.execute("SELECT asset_id, store_id FROM assets"))
    known = {row[0] for row in connection.execute("SELECT ticket_id FROM incidents")}
    known.update(ticket.ticket_id for ticket, _ in records)
    for ticket, source in records:
        store = ticket.store_id if ticket.store_id in stores else None
        asset = ticket.asset_id if store is not None and assets.get(ticket.asset_id) == store else None
        if store is None:
            report.warnings.append(f"{source}: store_id: reported store was not found; stored unresolved")
        if ticket.asset_id is not None and asset is None:
            report.warnings.append(f"{source}: asset_id: reported asset was not verified for its store")
        values = ticket.model_dump(exclude={"date_opened", "tags", "ticket_history", "related_tickets"})
        values.update(reported_store_id=ticket.store_id, store_id=store,
                      reported_asset_id=ticket.asset_id, asset_id=asset, opened_at=ticket.date_opened,
                      tags_json=json.dumps(ticket.tags, ensure_ascii=False, separators=(",", ":")),
                      escalation_flag=int(ticket.escalation_flag), source_path=source)
        _upsert(connection, "incidents", ("ticket_id",), values, report)
    # All incidents exist before resolving forward/cyclic relation foreign keys.
    for ticket, source in records:
        _count(report.deleted, "incident_events", connection.execute(
            "DELETE FROM incident_events WHERE ticket_id = ? AND source_event_index >= ?",
            (ticket.ticket_id, len(ticket.ticket_history)),
        ).rowcount)
        stale = [(ticket.ticket_id, row[0]) for row in connection.execute(
            "SELECT reported_related_ticket_id FROM incident_relations WHERE ticket_id = ?",
            (ticket.ticket_id,),
        ) if row[0] not in set(ticket.related_tickets)]
        if stale:
            _count(report.deleted, "incident_relations", connection.executemany(
                "DELETE FROM incident_relations WHERE ticket_id = ? AND reported_related_ticket_id = ?", stale,
            ).rowcount)
        for index, event in enumerate(ticket.ticket_history):
            _upsert(connection, "incident_events", ("ticket_id", "source_event_index"), {
                "ticket_id": ticket.ticket_id, "source_event_index": index,
                "event_at": event.timestamp, "actor": event.actor, "note": event.note,
            }, report)
        for related in ticket.related_tickets:
            resolved = related if related in known else None
            if resolved is None:
                report.warnings.append(f"{source}: related_tickets: related ticket was not found; stored unresolved")
            _upsert(connection, "incident_relations", ("ticket_id", "reported_related_ticket_id"), {
                "ticket_id": ticket.ticket_id, "reported_related_ticket_id": related,
                "related_ticket_id": resolved, "relation_type": "related",
            }, report)


def ingest_dataset(data_dir: str | Path, connection: sqlite3.Connection) -> IngestionReport:
    """Validate then atomically import references and indexed tickets; never read labels."""
    root = Path(data_dir)
    report = IngestionReport(stage="structured")
    stores = _read_csv(root / "data/stores.csv", Store, ("store_id",))
    assets = _read_csv(root / "data/assets.csv", Asset, ("asset_id",))
    targets = _read_csv(root / "data/sla_matrix.csv", SLATarget, ("priority", "category"))
    manifest = root / "tickets/train_index.csv"
    entries = _read_csv(manifest, ManifestEntry, ("ticket_id",))
    store_ids = {store.store_id for store in stores}
    for row, asset in enumerate(assets, start=2):
        if asset.store_id not in store_ids:
            raise _error(f"{root / 'data/assets.csv'}:{row}", "store_id: not present in stores.csv")
    records = []
    for row, entry in enumerate(entries, start=2):
        context = f"{manifest}:{row}"
        path = _resolve_ticket(root / "tickets", entry, context, report)
        ticket = _read_ticket(path)
        if ticket.ticket_id != entry.ticket_id:
            raise _error(context, "ticket_id: manifest does not match ticket JSON")
        records.append((ticket, path.relative_to(root).as_posix()))
    with _transaction(connection):
        for table, keys, rows in (
            ("stores", ("store_id",), stores), ("assets", ("asset_id",), assets),
            ("sla_targets", ("priority", "category"), targets),
        ):
            for record in rows:
                _upsert(connection, table, keys, record.model_dump(), report)
        _write_tickets(connection, records, report)
    return report


def ingest_ticket_file(path: str | Path, connection: sqlite3.Connection) -> IngestionReport:
    """Import one fresh ticket using the database's already loaded reference data."""
    source = Path(path)
    ticket = _read_ticket(source)
    report = IngestionReport(stage="structured")
    with _transaction(connection):
        _write_tickets(connection, [(ticket, source.name)], report)
    return report


class StructuredIngestion:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self) -> IngestionReport:
        try:
            self.settings.database_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(open_database(self.settings.database_path)) as connection:
                return ingest_dataset(self.settings.data_dir, connection)
        except (OSError, sqlite3.Error):
            raise _error("SQLite", "could not open or initialize configured database") from None

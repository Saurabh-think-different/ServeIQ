"""Validated, transactional imports of operational CSVs and historical tickets.

Imports update source records, never replace existing conversations or credentials.
Each public entry point commits its complete import, or rolls back on any error.
"""

import csv
import hashlib
import io
import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import Incident, IncidentStatus, Message, MessageRole, User
from src.models.structured import SLA, Asset, HistoricalTicket, RegionManager, Store, SystemVersion

_HEADERS = {
    "stores": (
        "store_id",
        "store_name",
        "city",
        "neighborhood",
        "store_type",
        "region_manager",
        "support_phone",
    ),
    "assets": (
        "asset_id",
        "store_id",
        "asset_type",
        "make",
        "model",
        "system_version",
        "serial_number",
        "install_date",
        "last_service_date",
        "warranty_expiry",
    ),
    "sla_matrix": (
        "priority",
        "category",
        "first_response_minutes",
        "resolution_target_minutes",
        "escalation_to_l2_minutes",
    ),
    "system_versions": (
        "system",
        "version",
        "release_date",
        "known_bugs",
        "patch_available",
        "notes",
    ),
}
_MODELS = {"stores": Store, "assets": Asset, "sla_matrix": SLA, "system_versions": SystemVersion}
_KEYS = {
    "stores": ("store_id",),
    "assets": ("asset_id",),
    "sla_matrix": ("priority", "category"),
    "system_versions": ("system", "version"),
}
_ALIASES = {
    "sla": "sla_matrix",
    "slas": "sla_matrix",
    "version-matrix": "system_versions",
    "version_matrix": "system_versions",
}
_DATES = {"install_date", "last_service_date", "warranty_expiry", "release_date"}
_METRICS = {
    "first_response_minutes",
    "resolution_target_minutes",
    "escalation_to_l2_minutes",
}
_STATUSES = {
    "open": IncidentStatus.open,
    "new": IncidentStatus.open,
    "pending": IncidentStatus.waiting_for_user,
    "on hold": IncidentStatus.waiting_for_user,
    "waiting for user": IncidentStatus.waiting_for_user,
    "waiting_for_user": IncidentStatus.waiting_for_user,
    "in progress": IncidentStatus.running,
    "in_progress": IncidentStatus.running,
    "running": IncidentStatus.running,
    "resolved": IncidentStatus.resolved,
    "closed": IncidentStatus.resolved,
    "escalated": IncidentStatus.escalated,
    "failed": IncidentStatus.failed,
}


def _counts() -> dict[str, Any]:
    return {
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "managers": {"inserted": 0, "unchanged": 0},
    }


def _text(content: bytes, label: str) -> str:
    if not isinstance(content, bytes):
        raise ValueError(f"{label}: content must be bytes")  # noqa: TRY004 - public validation uses HTTP 422.
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label}: expected UTF-8 text at byte {exc.start}") from exc
    if "\x00" in text:
        raise ValueError(f"{label}: NUL characters are not allowed")
    return text


def _table_name(table: str) -> str:
    if not isinstance(table, str):
        raise ValueError("table must be a string")  # noqa: TRY004 - public validation uses HTTP 422.
    name = table.strip().lower().removesuffix(".csv")
    name = _ALIASES.get(name, name)
    if name not in _HEADERS:
        raise ValueError(f"Unknown table {table!r}; expected {', '.join(_HEADERS)}")
    return name


def _parse_csv(table: str, content: bytes) -> list[dict[str, Any]]:
    reader = csv.reader(io.StringIO(_text(content, table), newline=""), strict=True)
    try:
        headers = next(reader, None)
        expected = _HEADERS[table]
        if headers is None or len(headers) != len(set(headers)) or set(headers) != set(expected):
            raise ValueError(f"{table}: expected exactly these headers: {', '.join(expected)}")
        rows: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for values in reader:
            line = reader.line_num
            if len(values) != len(headers):
                raise ValueError(
                    f"{table} row {line}: expected {len(headers)} columns, got {len(values)}"
                )
            row: dict[str, Any] = dict(
                zip(headers, (value.strip() for value in values), strict=True)
            )
            for field, value in row.items():
                if not value and not (
                    table == "system_versions" and field in {"known_bugs", "notes"}
                ):
                    raise ValueError(f"{table} row {line}: {field} must not be empty")
                column = _MODELS[table].__table__.columns[field]
                limit = getattr(column.type, "length", None)
                if limit and len(value) > limit:
                    raise ValueError(f"{table} row {line}: {field} exceeds {limit} characters")
                if field in _DATES:
                    try:
                        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                            raise ValueError
                        row[field] = date.fromisoformat(value)
                    except ValueError as exc:
                        raise ValueError(
                            f"{table} row {line}: {field} must be an ISO date (YYYY-MM-DD)"
                        ) from exc
                elif field in _METRICS:
                    digits = value.lstrip("0") or "0"
                    if (
                        not re.fullmatch(r"[0-9]+", value)
                        or len(digits) > 10
                        or int(digits) > 2_147_483_647
                    ):
                        raise ValueError(
                            f"{table} row {line}: {field} must be a nonnegative 32-bit integer"
                        )
                    row[field] = int(digits)
            key = tuple(row[field] for field in _KEYS[table])
            if key in seen:
                raise ValueError(f"{table} row {line}: duplicate ID {key!r}")
            seen.add(key)
            if table == "stores":
                _email_base(row["region_manager"], f"{table} row {line}")
            rows.append(row)
        return rows
    except csv.Error as exc:
        raise ValueError(f"{table} row {reader.line_num}: malformed CSV: {exc}") from exc


def _email_base(name: str, label: str) -> str:
    parts = name.lower().split()
    first = re.sub(r"[^a-z0-9]", "", parts[0]) if parts else ""
    last = re.sub(r"[^a-z0-9]", "", parts[-1]) if parts else ""
    if len(parts) < 2 or not first or not last:
        raise ValueError(f"{label}: region_manager needs an alphanumeric first name and surname")
    return first[:3] + last[-3:]


def _opened_at(value: str, label: str) -> datetime:
    try:
        opened = datetime.fromisoformat(value)
        if opened.tzinfo is None or "T" not in value:
            raise ValueError
        return opened.astimezone(UTC)
    except ValueError as exc:
        raise ValueError(f"{label}: date_opened must be an ISO timestamp with timezone") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"tickets: duplicate JSON key {key!r}")
        obj[key] = value
    return obj


def _invalid_constant(value: str) -> Any:
    raise ValueError(f"tickets: invalid JSON number {value}")


def _parse_tickets(content: bytes, label: str = "tickets") -> list[dict[str, Any]]:
    try:
        data = json.loads(
            _text(content, label),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label}: malformed JSON: {exc}") from exc
    tickets = data if isinstance(data, list) else [data]
    seen: set[str] = set()
    for index, ticket in enumerate(tickets, 1):
        row = f"{label} row {index}"
        if not isinstance(ticket, dict):
            raise ValueError(f"{row}: expected a ticket object")  # noqa: TRY004 - public validation uses HTTP 422.
        for field, limit in (
            ("ticket_id", 100),
            ("store_id", 100),
            ("subject", 200),
            ("description", None),
            ("date_opened", None),
            ("status", None),
        ):
            value = ticket.get(field)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(
                    f"{row}: {field} must be a nonempty string without surrounding whitespace"
                )
            if limit and len(value) > limit:
                raise ValueError(f"{row}: {field} exceeds {limit} characters")
        if ticket["ticket_id"] in seen:
            raise ValueError(f"{row}: duplicate ticket_id {ticket['ticket_id']!r}")
        seen.add(ticket["ticket_id"])
        _opened_at(ticket["date_opened"], row)
        if ticket["status"].lower() not in _STATUSES:
            raise ValueError(f"{row}: unsupported status {ticket['status']!r}")
        asset_id = ticket.get("asset_id")
        if asset_id is not None and (
            not isinstance(asset_id, str)
            or not asset_id.strip()
            or asset_id != asset_id.strip()
            or len(asset_id) > 100
        ):
            raise ValueError(
                f"{row}: asset_id must be null or a nonempty string of at most 100 characters"
            )
        if ticket.get("resolution_notes") is not None and not isinstance(
            ticket["resolution_notes"], str
        ):
            raise ValueError(f"{row}: resolution_notes must be a string or null")
        for field in ("tags", "related_tickets"):
            if field in ticket and (
                not isinstance(ticket[field], list)
                or any(not isinstance(item, str) for item in ticket[field])
            ):
                raise ValueError(f"{row}: {field} must be an array of strings")
        if "escalation_flag" in ticket and not isinstance(ticket["escalation_flag"], bool):
            raise ValueError(f"{row}: escalation_flag must be a boolean")
        history = ticket.get("ticket_history", [])
        if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
            raise ValueError(f"{row}: ticket_history must be an array of objects")
        try:
            _source_hash(ticket)
        except (ValueError, RecursionError, UnicodeEncodeError) as exc:
            raise ValueError(f"{row}: ticket contains invalid JSON values: {exc}") from exc
    return tickets


def _validate_references(
    db: Session,
    csv_rows: dict[str, list[dict[str, Any]]],
    tickets: list[dict[str, Any]],
) -> None:
    stores = {store.store_id: store for store in db.scalars(select(Store))}
    incoming_stores = {row["store_id"] for row in csv_rows.get("stores", [])}
    store_ids = set(stores) | incoming_stores
    user_ids = set(db.scalars(select(User.id)))
    manager_names = {row["region_manager"] for row in csv_rows.get("stores", [])}
    for profile in db.scalars(select(RegionManager)):
        if profile.name in manager_names and profile.user_id not in user_ids:
            raise ValueError(f"Manager {profile.name!r} has no linked user")
    for index, asset in enumerate(csv_rows.get("assets", []), 2):
        if asset["store_id"] not in store_ids:
            raise ValueError(f"assets row {index}: unknown store_id {asset['store_id']!r}")
    for index, ticket in enumerate(tickets, 1):
        if ticket["store_id"] not in store_ids:
            raise ValueError(f"tickets row {index}: unknown store_id {ticket['store_id']!r}")
        if (
            ticket["store_id"] not in incoming_stores
            and stores[ticket["store_id"]].manager_user_id not in user_ids
        ):
            raise ValueError(f"tickets row {index}: store has no valid manager")
        existing = db.get(HistoricalTicket, ticket["ticket_id"])
        if existing is not None and db.get(Incident, existing.incident_id) is None:
            raise ValueError(
                f"tickets row {index}: missing linked incident for {ticket['ticket_id']!r}"
            )
        if existing is None:
            if db.get(Incident, _import_id(ticket["ticket_id"], "incident")) is not None:
                raise ValueError(f"tickets row {index}: generated incident ID is already in use")
            for role in ("user", "resolution"):
                if db.get(Message, _import_id(ticket["ticket_id"], role)) is not None:
                    raise ValueError(f"tickets row {index}: generated message ID is already in use")


def _ensure_managers(db: Session, names: set[str]) -> tuple[dict[str, str], dict[str, int]]:
    profiles = {profile.name: profile for profile in db.scalars(select(RegionManager))}
    used_emails = {email.lower() for email in db.scalars(select(User.email))}
    mapping: dict[str, str] = {}
    counts = {"inserted": 0, "unchanged": 0}
    for name in sorted(names):
        profile = profiles.get(name)
        if profile is not None:
            if db.get(User, profile.user_id) is None:
                raise ValueError(f"Manager {name!r} has no linked user")
            mapping[name] = profile.user_id
            counts["unchanged"] += 1
            continue
        from auth import hash_password

        base = _email_base(name, "stores")
        email = f"{base}@sw.com"
        suffix = 2
        while email in used_emails:
            email = f"{base}{suffix}@sw.com"
            suffix += 1
        user = User(email=email, password_hash=hash_password("operator"))
        db.add(user)
        db.flush()
        db.add(RegionManager(user_id=user.id, name=name))
        used_emails.add(email)
        mapping[name] = user.id
        counts["inserted"] += 1
    db.flush()
    return mapping, counts


def _sync_historical_owners(db: Session, store: Store) -> None:
    # Only provenance-linked incidents belong to this importer.
    incidents = db.scalars(
        select(Incident)
        .join(HistoricalTicket, HistoricalTicket.incident_id == Incident.id)
        .where(HistoricalTicket.store_id == store.store_id)
    )
    for incident in incidents:
        if incident.user_id != store.manager_user_id:
            incident.user_id = store.manager_user_id


def _apply_csv(db: Session, table: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _counts()
    managers: dict[str, str] = {}
    if table == "stores":
        managers, counts["managers"] = _ensure_managers(db, {row["region_manager"] for row in rows})
    model = _MODELS[table]
    for row in rows:
        values = dict(row)
        if table == "stores":
            values["manager_user_id"] = managers[row["region_manager"]]
        key = tuple(row[field] for field in _KEYS[table])
        record = db.get(model, key[0] if len(key) == 1 else key)
        if record is None:
            record = model(**values)
            db.add(record)
            counts["inserted"] += 1
        else:
            changed = any(getattr(record, field) != value for field, value in values.items())
            if changed:
                for field, value in values.items():
                    setattr(record, field, value)
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1
        if isinstance(record, Store):
            _sync_historical_owners(db, record)
    db.flush()
    return counts


def _import_id(ticket_id: str, kind: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"servewell:historical-ticket:{ticket_id}:{kind}"))


def _source_hash(ticket: dict[str, Any]) -> str:
    canonical = json.dumps(
        ticket, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _apply_tickets(db: Session, tickets: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _counts()
    for ticket in tickets:
        store = db.get(Store, ticket["store_id"])
        if store is None or db.get(User, store.manager_user_id) is None:
            raise ValueError(f"Ticket {ticket['ticket_id']!r}: store has no valid manager")
        source_hash = _source_hash(ticket)
        historical = db.get(HistoricalTicket, ticket["ticket_id"])
        if historical is not None:
            incident = db.get(Incident, historical.incident_id)
            if incident is None:
                raise ValueError(f"Ticket {ticket['ticket_id']!r}: missing linked incident")
            changed = (
                historical.source_hash != source_hash
                or historical.raw != ticket
                or historical.store_id != store.store_id
                or historical.asset_id != ticket.get("asset_id")
                or incident.user_id != store.manager_user_id
            )
            if changed:
                historical.store_id = store.store_id
                historical.asset_id = ticket.get("asset_id")
                historical.raw = ticket
                historical.source_hash = source_hash
                incident.user_id = store.manager_user_id
            counts["updated" if changed else "unchanged"] += 1
            continue
        opened = _opened_at(ticket["date_opened"], f"Ticket {ticket['ticket_id']!r}")
        incident = Incident(
            id=_import_id(ticket["ticket_id"], "incident"),
            user_id=store.manager_user_id,
            title=ticket["subject"],
            description=ticket["description"],
            status=_STATUSES[ticket["status"].lower()],
            created_at=opened,
        )
        db.add(incident)
        db.flush()
        db.add(
            Message(
                id=_import_id(ticket["ticket_id"], "user"),
                incident_id=incident.id,
                role=MessageRole.user,
                content=ticket["description"],
                created_at=opened,
            )
        )
        if ticket.get("resolution_notes"):
            db.add(
                Message(
                    id=_import_id(ticket["ticket_id"], "resolution"),
                    incident_id=incident.id,
                    role=MessageRole.assistant,
                    content=ticket["resolution_notes"],
                    created_at=opened + timedelta(microseconds=1),
                )
            )
        db.add(
            HistoricalTicket(
                ticket_id=ticket["ticket_id"],
                incident_id=incident.id,
                store_id=store.store_id,
                asset_id=ticket.get("asset_id"),
                raw=ticket,
                source_hash=source_hash,
            )
        )
        counts["inserted"] += 1
    db.flush()
    return counts


def ingest_csv(db: Session, table: str, content: bytes) -> dict[str, Any]:
    """Upsert one supported CSV; report source-row and manager-profile counts."""
    try:
        name = _table_name(table)
        rows = _parse_csv(name, content)
        _validate_references(db, {name: rows}, [])
        result = _apply_csv(db, name, rows)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def ingest_tickets(db: Session, content: bytes) -> dict[str, Any]:
    """Import a single ticket object or array without replacing live conversations."""
    try:
        tickets = _parse_tickets(content)
        _validate_references(db, {}, tickets)
        result = _apply_tickets(db, tickets)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def ingest_structured_dataset(db: Session, root: Path) -> dict[str, Any]:
    """Atomically load data/, tickets/train/, and the optional version-matrix CSV."""
    try:
        root = Path(root)
        csv_rows = {
            table: _parse_csv(table, (root / "data" / f"{table}.csv").read_bytes())
            for table in ("stores", "assets", "sla_matrix")
        }
        version_matrix = root / "kb" / "system-specs" / "version-matrix.csv"
        if version_matrix.is_file():
            csv_rows["system_versions"] = _parse_csv("system_versions", version_matrix.read_bytes())
        ticket_dir = root / "tickets" / "train"
        if not ticket_dir.is_dir():
            raise ValueError(f"Missing ticket directory: {ticket_dir}")
        tickets: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in sorted(ticket_dir.glob("*.json")):
            for ticket in _parse_tickets(path.read_bytes(), path.name):
                if ticket["ticket_id"] in seen:
                    raise ValueError(
                        f"{path.name}: duplicate ticket_id {ticket['ticket_id']!r} across files"
                    )
                seen.add(ticket["ticket_id"])
                tickets.append(ticket)
        _validate_references(db, csv_rows, tickets)
        tables = {table: _apply_csv(db, table, rows) for table, rows in csv_rows.items()}
        tables["tickets"] = _apply_tickets(db, tickets)
        result = _counts()
        for counts in tables.values():
            for key in ("inserted", "updated", "unchanged"):
                result[key] += counts[key]
            for key in ("inserted", "unchanged"):
                result["managers"][key] += counts["managers"][key]
        result["tables"] = tables
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


__all__ = ["ingest_csv", "ingest_structured_dataset", "ingest_tickets"]

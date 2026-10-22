"""Read-only support data. Callers must bind authenticated user/incident IDs.

Never expose these owner arguments as model-supplied tool parameters. Responses
use explicit public field allowlists, not serialized ORM rows or source payloads.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from src.models import Incident
from src.models.structured import SLA, Asset, HistoricalTicket, Store, SystemVersion
from src.services import store

_NOT_FOUND = {"status": "not_found", "error": "Resource not found."}


def _text(value: Any, limit: int = 1000) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _valid_ids(*values: str) -> bool:
    return all(isinstance(value, str) and len(value) <= 100 for value in values)


def _literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _owned_store(db: Session, user_id: str, store_id: str) -> Store | None:
    return db.scalar(
        select(Store).where(Store.store_id == store_id, Store.manager_user_id == user_id)
    )


def _owned_asset(db: Session, user_id: str, asset_id: str) -> Asset | None:
    return db.scalar(
        select(Asset)
        .join(Store, Store.store_id == Asset.store_id)
        .where(Asset.asset_id == asset_id, Store.manager_user_id == user_id)
    )


def _store_summary(item: Store) -> dict[str, Any]:
    return {
        "store_id": item.store_id,
        "store_name": item.store_name,
        "city": item.city,
        "neighborhood": item.neighborhood,
        "store_type": item.store_type,
    }


def _asset_summary(item: Asset) -> dict[str, Any]:
    return {
        "asset_id": item.asset_id,
        "store_id": item.store_id,
        "asset_type": item.asset_type,
        "make": item.make,
        "model": item.model,
        "system_version": item.system_version,
        "install_date": _iso(item.install_date),
        "last_service_date": _iso(item.last_service_date),
        "warranty_expiry": _iso(item.warranty_expiry),
    }


def _history_statement(
    user_id: str,
) -> Select[tuple[Incident, HistoricalTicket, Store, str | None]]:
    # The asset join suppresses stale or cross-store IDs in imported payloads.
    return (
        select(Incident, HistoricalTicket, Store, Asset.asset_id)
        .join(HistoricalTicket, HistoricalTicket.incident_id == Incident.id)
        .join(Store, Store.store_id == HistoricalTicket.store_id)
        .outerjoin(
            Asset,
            and_(
                Asset.asset_id == HistoricalTicket.asset_id,
                Asset.store_id == Store.store_id,
            ),
        )
        .where(Incident.user_id == user_id, Store.manager_user_id == user_id)
    )


def _history_summary(
    incident: Incident, ticket: HistoricalTicket, location: Store, asset_id: str | None
) -> dict[str, Any]:
    raw = ticket.raw if isinstance(ticket.raw, dict) else {}
    return {
        "incident_id": incident.id,
        "ticket_id": ticket.ticket_id,
        "store_id": location.store_id,
        "asset_id": asset_id,
        "title": _text(incident.title, 200),
        "description": _text(incident.description, 2000),
        "status": _text(raw.get("status"), 100),
        "date_opened": _text(raw.get("date_opened"), 100),
        "date_closed": _text(raw.get("date_closed"), 100),
        "resolution_notes": _text(raw.get("resolution_notes"), 1000),
    }


def get_store_context(
    user_id: str, incident_id: str, store_id: str = "", asset_id: str = ""
) -> dict:
    """Resolve an owned incident's location without guessing among assigned stores."""
    with store.SessionLocal() as db:
        if (
            not _valid_ids(user_id, incident_id, store_id, asset_id)
            or not user_id
            or not incident_id
        ):
            return dict(_NOT_FOUND)
        incident = db.scalar(
            select(Incident).where(Incident.id == incident_id, Incident.user_id == user_id)
        )
        if incident is None:
            return dict(_NOT_FOUND)
        linked = db.scalar(
            select(HistoricalTicket).where(HistoricalTicket.incident_id == incident.id)
        )
        supplied_store = _owned_store(db, user_id, store_id) if store_id else None
        supplied_asset = _owned_asset(db, user_id, asset_id) if asset_id else None
        if (store_id and supplied_store is None) or (asset_id and supplied_asset is None):
            return dict(_NOT_FOUND)
        if supplied_asset and store_id and supplied_asset.store_id != store_id:
            return dict(_NOT_FOUND)

        inferred = False
        selected_asset = supplied_asset
        location = supplied_store
        if linked is not None:
            location = _owned_store(db, user_id, linked.store_id)
            if location is None or (store_id and store_id != location.store_id):
                return dict(_NOT_FOUND)
            if linked.asset_id:
                selected_asset = _owned_asset(db, user_id, linked.asset_id)
                if selected_asset is None or selected_asset.store_id != location.store_id:
                    return dict(_NOT_FOUND)
                if asset_id and asset_id != selected_asset.asset_id:
                    return dict(_NOT_FOUND)
            if selected_asset and selected_asset.store_id != location.store_id:
                return dict(_NOT_FOUND)
        elif selected_asset is not None:
            location = _owned_store(db, user_id, selected_asset.store_id)
        elif location is None:
            assigned = db.scalars(
                select(Store)
                .where(Store.manager_user_id == user_id)
                .order_by(Store.store_id)
                .limit(2)
            ).all()
            if len(assigned) == 1:
                location = assigned[0]
                inferred = True

        assets = []
        recent = []
        assets_truncated = False
        if location is not None:
            rows = db.scalars(
                select(Asset)
                .join(Store, Store.store_id == Asset.store_id)
                .where(Store.manager_user_id == user_id, Store.store_id == location.store_id)
                .order_by(Asset.asset_id)
                .limit(21)
            ).all()
            assets = [_asset_summary(item) for item in rows[:20]]
            assets_truncated = len(rows) > 20
            recent_rows = db.execute(
                _history_statement(user_id)
                .where(Store.store_id == location.store_id, Incident.id != incident.id)
                .order_by(Incident.created_at.desc(), Incident.id)
                .limit(5)
            ).all()
            recent = [_history_summary(*row) for row in recent_rows]
        missing = []
        if location is None:
            missing.append("store_id")
        if selected_asset is None:
            missing.append("asset_id")
        clarification = None
        if location is None:
            clarification = "Provide an assigned store ID or an asset ID belonging to it."
        elif selected_asset is None:
            clarification = (
                "Identify the affected asset if this incident concerns a specific asset."
            )
        return {
            "status": "ok" if location is not None else "clarification_required",
            "incident": {
                "incident_id": incident.id,
                "title": _text(incident.title, 200),
                "description": _text(incident.description, 4000),
            },
            "store": _store_summary(location) if location is not None else None,
            "asset": _asset_summary(selected_asset) if selected_asset is not None else None,
            "assets": assets,
            "assets_truncated": assets_truncated,
            "inferred": inferred,
            "linkage": {
                "ticket_id": linked.ticket_id,
                "incident_id": linked.incident_id,
                "store_id": linked.store_id,
                "asset_id": linked.asset_id,
            }
            if linked is not None
            else None,
            "recent_history": recent,
            "missing_info": missing,
            "clarification_required": clarification is not None,
            "clarification": clarification,
        }


def search_ticket_history(
    user_id: str, query: str = "", store_id: str = "", asset_id: str = "", limit: int = 5
) -> dict:
    """Search an owner's imported history using a bounded literal title/description match."""
    with store.SessionLocal() as db:
        if not _valid_ids(user_id, store_id, asset_id) or not user_id:
            return dict(_NOT_FOUND)
        if not isinstance(query, str) or len(query) > 200:
            return {"status": "invalid_input", "error": "query must be at most 200 characters."}
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return {"status": "invalid_input", "error": "limit must be a positive integer."}
        limit = min(limit, 10)
        location = _owned_store(db, user_id, store_id) if store_id else None
        asset = _owned_asset(db, user_id, asset_id) if asset_id else None
        if (store_id and location is None) or (asset_id and asset is None):
            return dict(_NOT_FOUND)
        if asset and store_id and asset.store_id != store_id:
            return dict(_NOT_FOUND)
        statement = _history_statement(user_id)
        if store_id:
            statement = statement.where(Store.store_id == store_id)
        if asset is not None:
            statement = statement.where(Asset.asset_id == asset.asset_id)
        if query.strip():
            pattern = "%" + _literal(query.strip()) + "%"
            statement = statement.where(
                or_(
                    Incident.title.ilike(pattern, escape="\\"),
                    Incident.description.ilike(pattern, escape="\\"),
                )
            )
        rows = db.execute(
            statement.order_by(Incident.created_at.desc(), Incident.id).limit(limit)
        ).all()
        tickets = [_history_summary(*row) for row in rows]
        return {"status": "ok", "tickets": tickets, "count": len(tickets), "limit": limit}


def lookup_sla(priority: str, category: str) -> dict:
    """Look up only recorded priority/category combinations, never infer targets."""
    with store.SessionLocal() as db:
        if not isinstance(priority, str) or priority.strip().upper() not in {
            "P1",
            "P2",
            "P3",
            "P4",
        }:
            return {
                "status": "invalid_input",
                "error": "priority must be P1, P2, P3, or P4.",
                "available_priorities": ["P1", "P2", "P3", "P4"],
            }
        if not isinstance(category, str) or len(category) > 200:
            return {"status": "invalid_input", "error": "category must be at most 200 characters."}
        normalized = priority.strip().upper()
        rows = db.scalars(
            select(SLA).where(func.upper(SLA.priority) == normalized).order_by(SLA.category)
        ).all()
        matches = [item for item in rows if item.category.casefold() == category.strip().casefold()]
        if not category.strip() or len(matches) != 1:
            return {
                "status": "clarification_required" if rows else "not_found",
                "priority": normalized,
                "available_categories": sorted({item.category for item in rows}),
                "clarification_required": True,
                "clarification": "Provide an exact available category for this priority.",
            }
        item = matches[0]
        return {
            "status": "ok",
            "priority": item.priority,
            "category": item.category,
            "first_response_minutes": item.first_response_minutes,
            "resolution_target_minutes": item.resolution_target_minutes,
            "escalation_to_l2_minutes": item.escalation_to_l2_minutes,
        }


def lookup_system_version(system: str, version: str = "") -> dict:
    """Prefer an exact system; use a literal substring only if it identifies one system."""
    with store.SessionLocal() as db:
        if not isinstance(system, str) or not system.strip() or len(system) > 300:
            return {
                "status": "invalid_input",
                "error": "Provide a system of at most 300 characters.",
            }
        if not isinstance(version, str) or len(version) > 100:
            return {"status": "invalid_input", "error": "version must be at most 100 characters."}
        name = system.strip().lower()
        names = db.scalars(
            select(SystemVersion.system)
            .where(func.lower(SystemVersion.system) == name)
            .distinct()
            .order_by(SystemVersion.system)
            .limit(11)
        ).all()
        match_type = "exact"
        if not names:
            names = db.scalars(
                select(SystemVersion.system)
                .where(
                    SystemVersion.system.ilike("%" + _literal(system.strip()) + "%", escape="\\")
                )
                .distinct()
                .order_by(SystemVersion.system)
                .limit(11)
            ).all()
            match_type = "literal_substring"
        if not names:
            return {"status": "not_found", "versions": [], "clarification_required": False}
        if len(names) != 1:
            return {
                "status": "clarification_required",
                "systems": list(names[:10]),
                "truncated": len(names) > 10,
                "versions": [],
                "clarification_required": True,
                "clarification": "Specify an exact system name; multiple systems match.",
            }
        statement = select(SystemVersion).where(SystemVersion.system == names[0])
        if version.strip():
            statement = statement.where(
                func.lower(SystemVersion.version) == version.strip().lower()
            )
        rows = db.scalars(
            statement.order_by(SystemVersion.release_date.desc(), SystemVersion.version).limit(11)
        ).all()
        versions = [
            {
                "system": item.system,
                "version": item.version,
                "release_date": _iso(item.release_date),
                "known_bugs": _text(item.known_bugs, 2000),
                "patch_available": _text(item.patch_available, 200),
                "notes": _text(item.notes, 2000),
            }
            for item in rows[:10]
        ]
        ambiguous = len(rows) > 1
        return {
            "status": "clarification_required" if ambiguous else "ok" if rows else "not_found",
            "system": names[0],
            "match_type": match_type,
            "versions": versions,
            "truncated": len(rows) > 10,
            "clarification_required": ambiguous or not rows,
            "clarification": (
                "Specify an exact version; multiple versions match."
                if ambiguous
                else "No recorded version matches; verify the version."
                if not rows
                else None
            ),
        }


__all__ = ["get_store_context", "lookup_sla", "lookup_system_version", "search_ticket_history"]

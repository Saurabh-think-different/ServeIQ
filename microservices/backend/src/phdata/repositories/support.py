"""Read-only, exact-ID support lookups and conservative incident context assembly."""

from datetime import datetime, timedelta, timezone
import sqlite3
from typing import TypeVar

from pydantic import BaseModel

from phdata.models.context import (
    AssetContext, ClockContext, HistoryEvent, IncidentContext, IncidentFacts,
    RelatedReference, SLATarget, StoreContext, TargetDeadlines,
)

Model = TypeVar("Model", bound=BaseModel)


def _aware(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            parsed.astimezone(timezone.utc)
            return parsed
    except (ValueError, OverflowError):
        pass
    return None


def _present(value: str | None) -> str | None:
    # Blank intake identifiers mean missing, but nonblank identifiers stay exact.
    return value if value is not None and value.strip() else None


class SupportRepository:
    """Caller owns the connection/transaction; reads never modify persisted state.

    References are resolved again against their exact reported IDs at read time.
    Persisted foreign keys are not trusted as substitutes for reported identifiers.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def _many(self, model: type[Model], sql: str,
              parameters: tuple[str, ...]) -> list[Model]:
        cursor = self.connection.execute(sql, parameters)
        columns = [item[0] for item in cursor.description]
        return [model.model_validate(dict(zip(columns, row))) for row in cursor]

    def _one(self, model: type[Model], sql: str,
             parameters: tuple[str, ...]) -> Model | None:
        rows = self._many(model, sql, parameters)
        return rows[0] if rows else None

    def get_store(self, store_id: str) -> StoreContext | None:
        return self._one(StoreContext, """
            SELECT store_id, store_name, city, neighborhood, store_type
            FROM stores WHERE store_id = ?
        """, (store_id,))

    def get_asset(self, asset_id: str) -> AssetContext | None:
        return self._one(AssetContext, """
            SELECT asset_id, store_id, asset_type, make, model, system_version,
                   serial_number, install_date, last_service_date, warranty_expiry
            FROM assets WHERE asset_id = ?
        """, (asset_id,))

    def get_sla(self, priority: str, category: str) -> SLATarget | None:
        return self._one(SLATarget, """
            SELECT priority, category, first_response_minutes,
                   resolution_target_minutes, escalation_to_l2_minutes
            FROM sla_targets WHERE priority = ? AND category = ?
        """, (priority, category))

    def get_history(self, ticket_id: str) -> list[HistoryEvent]:
        """Chronological instants, source-index ties, invalid timestamps last."""
        events = self._many(HistoryEvent, """
            SELECT event_id, source_event_index, event_at, actor, note
            FROM incident_events WHERE ticket_id = ?
            ORDER BY source_event_index, event_id
        """, (ticket_id,))
        def order(event: HistoryEvent) -> tuple[bool, datetime, int, int]:
            timestamp = _aware(event.event_at)
            return (
                timestamp is None,
                timestamp.astimezone(timezone.utc) if timestamp else datetime.max.replace(tzinfo=timezone.utc),
                event.source_event_index, event.event_id,
            )
        return sorted(events, key=order)

    def get_related(self, ticket_id: str) -> list[RelatedReference]:
        """Preserve unresolved references; resolve later arrivals without reimport."""
        return self._many(RelatedReference, """
            SELECT r.reported_related_ticket_id, i.ticket_id AS related_ticket_id,
                   r.related_ticket_id AS persisted_related_ticket_id, r.relation_type
            FROM incident_relations AS r
            LEFT JOIN incidents AS i ON i.ticket_id = r.reported_related_ticket_id
            WHERE r.ticket_id = ?
            ORDER BY r.reported_related_ticket_id
        """, (ticket_id,))

    def get_context(self, ticket_id: str, *, now: datetime | None = None) -> IncidentContext | None:
        incident = self._one(IncidentFacts, """
            SELECT ticket_id, reported_store_id, reported_asset_id, opened_at,
                   priority, category, subcategory, status, subject, description,
                   system_version, escalation_flag
            FROM incidents WHERE ticket_id = ?
        """, (ticket_id,))
        if incident is None:
            return None
        incident.reported_store_id = _present(incident.reported_store_id)
        incident.reported_asset_id = _present(incident.reported_asset_id)
        missing: list[str] = []
        warnings: list[str] = []
        store = self.get_store(incident.reported_store_id) if incident.reported_store_id else None
        if store is None:
            missing.append("store")
            warnings.append("Reported store ID is missing or has no exact reference match.")
        asset = self.get_asset(incident.reported_asset_id) if incident.reported_asset_id else None
        if asset is not None and (store is None or asset.store_id != store.store_id):
            asset = None
            warnings.append("Reported asset cannot be verified for the exact resolved store.")
        if asset is None:
            missing.append("asset")
            warnings.append("Asset is missing, unresolved, or belongs to a different store; no substitute used.")
        for name in ("priority", "category"):
            if not _present(getattr(incident, name)):
                missing.append(name)
        sla = (
            self.get_sla(incident.priority, incident.category)
            if incident.priority is not None and incident.category is not None else None
        )
        if sla is None:
            missing.append("sla")
            warnings.append("No exact priority/category SLA target is available; no default inferred.")
        history = self.get_history(ticket_id)
        if not history:
            missing.append("history")
        for event in history:
            if _aware(event.event_at) is None:
                warnings.append(f"History event {event.source_event_index} has a naive or malformed timestamp.")
        related = self.get_related(ticket_id)
        for reference in related:
            if reference.related_ticket_id is None:
                warnings.append(f"Related ticket {reference.reported_related_ticket_id} is unresolved.")
            if reference.persisted_related_ticket_id != reference.related_ticket_id:
                warnings.append(
                    f"Related ticket {reference.reported_related_ticket_id}: read-time exact resolution "
                    "differs from persisted reference state."
                )
        clock = self._clock(incident, sla, now, warnings)
        if clock.opened_at is None:
            missing.append("valid_opened_at")
        if clock.now is None:
            missing.append("valid_now")
        return IncidentContext(
            incident=incident, store=store, asset=asset, sla=sla, history=history,
            related=related, clock=clock, missing=missing, warnings=warnings,
        )

    @staticmethod
    def _clock(incident: IncidentFacts, sla: SLATarget | None,
               now: datetime | None, warnings: list[str]) -> ClockContext:
        current = now if now is not None else datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            current = None
            warnings.append("Clock now is naive; elapsed time is unavailable (no timezone assumed).")
        opened = _aware(incident.opened_at)
        if opened is None:
            warnings.append("Incident opened_at is naive or malformed; clock calculations are unavailable.")
        status = incident.status.casefold().strip()
        kind = (
            "closed" if status in {"closed", "resolved", "cancelled", "canceled"}
            else "open" if status in {"open", "new", "in progress", "pending", "on hold", "reopened"}
            else "unknown"
        )
        if kind == "unknown":
            warnings.append("Incident status is not recognized; no open-target deadlines inferred.")
        elapsed = None
        valid_start = opened is not None
        if current is not None and opened is not None:
            elapsed = (current.astimezone(timezone.utc) - opened.astimezone(timezone.utc)).total_seconds() / 60
            if elapsed < 0:
                elapsed = None
                valid_start = False
                warnings.append("Incident opened_at is in the future; elapsed time and open deadlines withheld.")
        deadlines = None
        if valid_start and opened is not None and sla is not None and kind == "open":
            try:
                deadlines = TargetDeadlines(
                    first_response_at=opened + timedelta(minutes=sla.first_response_minutes),
                    resolution_at=opened + timedelta(minutes=sla.resolution_target_minutes),
                    escalation_to_l2_at=opened + timedelta(minutes=sla.escalation_to_l2_minutes),
                )
            except OverflowError:
                warnings.append("SLA target deadlines exceed the supported datetime range; withheld.")
        return ClockContext(
            now=current, opened_at=opened, elapsed_since_open_minutes=elapsed,
            status_kind=kind, open_target_deadlines=deadlines,
        )

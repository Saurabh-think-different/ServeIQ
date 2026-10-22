"""Normalized operational data and provenance for imported historical incidents."""

from datetime import date
from typing import Any

from sqlalchemy import JSON, Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base


class RegionManager(Base):
    __tablename__ = "region_managers"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)


class Store(Base):
    __tablename__ = "stores"

    store_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    manager_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    store_name: Mapped[str] = mapped_column(String(300))
    city: Mapped[str] = mapped_column(String(200))
    neighborhood: Mapped[str] = mapped_column(String(200))
    store_type: Mapped[str] = mapped_column(String(100))
    region_manager: Mapped[str] = mapped_column(String(200))
    support_phone: Mapped[str] = mapped_column(String(100))


class Asset(Base):
    __tablename__ = "assets"

    asset_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.store_id"), index=True)
    asset_type: Mapped[str] = mapped_column(String(200))
    make: Mapped[str] = mapped_column(String(200))
    model: Mapped[str] = mapped_column(String(300))
    system_version: Mapped[str] = mapped_column(String(300))
    serial_number: Mapped[str] = mapped_column(String(200))
    install_date: Mapped[date] = mapped_column(Date)
    last_service_date: Mapped[date] = mapped_column(Date)
    warranty_expiry: Mapped[date] = mapped_column(Date)


class SLA(Base):
    __tablename__ = "sla_matrix"

    priority: Mapped[str] = mapped_column(String(20), primary_key=True)
    category: Mapped[str] = mapped_column(String(200), primary_key=True)
    first_response_minutes: Mapped[int] = mapped_column(Integer)
    resolution_target_minutes: Mapped[int] = mapped_column(Integer)
    escalation_to_l2_minutes: Mapped[int] = mapped_column(Integer)


class SystemVersion(Base):
    __tablename__ = "system_versions"

    system: Mapped[str] = mapped_column(String(300), primary_key=True)
    version: Mapped[str] = mapped_column(String(100), primary_key=True)
    release_date: Mapped[date] = mapped_column(Date)
    known_bugs: Mapped[str] = mapped_column(Text)
    patch_available: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text)


class HistoricalTicket(Base):
    __tablename__ = "historical_tickets"

    ticket_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), unique=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.store_id"), index=True)
    asset_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_hash: Mapped[str] = mapped_column(String(64))


__all__ = ["SLA", "Asset", "HistoricalTicket", "RegionManager", "Store", "SystemVersion"]

import csv
import json

import pytest

from phdata.db.database import open_database
from phdata.ingestion.cli import main
from phdata.ingestion.importer import DatasetImportError, ingest_dataset, ingest_ticket_file


def make_dataset(data_root, *, include_ticket=True):
    data_dir = data_root / "data"
    tickets_dir = data_root / "tickets"
    (tickets_dir / "train").mkdir(parents=True)
    data_dir.mkdir()

    with (data_dir / "stores.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["store_id", "store_name", "city", "neighborhood", "store_type", "region_manager", "support_phone"],
        )
        writer.writeheader()
        writer.writerow({
            "store_id": "SW-0001",
            "store_name": "ServeWell - Banjara Hills",
            "city": "Hyderabad",
            "neighborhood": "Banjara Hills",
            "store_type": "corporate",
            "region_manager": "Manager",
            "support_phone": "+91-00000-00000",
        })

    with (data_dir / "assets.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["asset_id", "store_id", "asset_type", "make", "model", "system_version", "serial_number", "install_date", "last_service_date", "warranty_expiry"],
        )
        writer.writeheader()
        writer.writerow({
            "asset_id": "PRN-0001-P1",
            "store_id": "SW-0001",
            "asset_type": "Receipt Printer",
            "make": "EpsonTM-T88VI",
            "model": "EpsonTM-T88VI",
            "system_version": "v1.12",
            "serial_number": "EP000001",
            "install_date": "2024-01-01",
            "last_service_date": "2025-01-01",
            "warranty_expiry": "2027-01-01",
        })

    with (data_dir / "sla_matrix.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["priority", "category", "first_response_minutes", "resolution_target_minutes", "escalation_to_l2_minutes"],
        )
        writer.writeheader()
        writer.writerow({
            "priority": "P2",
            "category": "Printers",
            "first_response_minutes": 30,
            "resolution_target_minutes": 240,
            "escalation_to_l2_minutes": 90,
        })

    filename = "raw/INC-00001.json"
    with (tickets_dir / "train_index.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["ticket_id", "filename"])
        writer.writeheader()
        if include_ticket:
            writer.writerow({"ticket_id": "INC-00001", "filename": filename})

    if include_ticket:
        ticket = {
            "ticket_id": "INC-00001",
            "store_id": "SW-0001",
            "date_opened": "2026-01-01T10:00:00+05:30",
            "priority": "P2",
            "category": "Printers",
            "subcategory": "Printer Offline",
            "status": "Open",
            "subject": "Printer offline",
            "description": "Receipt printer stopped printing.",
            "asset_id": "PRN-0001-P1",
            "system_version": "EpsonTM-T88VI v1.12",
            "tags": ["printer", "offline"],
            "escalation_flag": False,
            "related_tickets": [],
            "ticket_history": [{
                "timestamp": "2026-01-01T10:05:00+05:30",
                "actor": "L1 Agent",
                "note": "Ticket acknowledged.",
            }],
            "resolution_notes": None,
        }
        with (tickets_dir / "train" / "INC-00001.json").open("w", encoding="utf-8") as file:
            json.dump(ticket, file)


def test_dataset_import_maps_manifest_paths_and_is_idempotent(tmp_path):
    data_root = tmp_path / "client_data"
    make_dataset(data_root)
    connection = open_database(":memory:")
    try:
        first = ingest_dataset(data_root, connection)
        assert first.inserted["stores"] == 1
        assert first.inserted["assets"] == 1
        assert first.inserted["sla_targets"] == 1
        assert first.inserted["incidents"] == 1
        assert first.inserted["incident_events"] == 1
        assert first.path_mappings["tickets/raw -> tickets/train"] == 1

        second = ingest_dataset(data_root, connection)
        assert sum(second.inserted.values()) == 0
        assert sum(second.updated.values()) == 0
        assert second.skipped["incidents"] == 1
        assert second.skipped["incident_events"] == 1
        assert connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM incident_events").fetchone()[0] == 1
    finally:
        connection.close()


def test_missing_manifest_file_fails_before_writing_rows(tmp_path):
    data_root = tmp_path / "client_data"
    make_dataset(data_root, include_ticket=False)
    with (data_root / "tickets" / "train_index.csv").open("a", encoding="utf-8") as file:
        file.write("INC-MISSING,raw/INC-MISSING.json\n")
    connection = open_database(":memory:")
    try:
        with pytest.raises(DatasetImportError, match="INC-MISSING"):
            ingest_dataset(data_root, connection)
        assert connection.execute("SELECT COUNT(*) FROM stores").fetchone()[0] == 0
    finally:
        connection.close()


def test_fresh_ticket_import_uses_loaded_reference_data(tmp_path):
    data_root = tmp_path / "client_data"
    make_dataset(data_root)
    connection = open_database(":memory:")
    try:
        ingest_dataset(data_root, connection)
        fresh_ticket = tmp_path / "fresh-ticket.json"
        payload = json.loads((data_root / "tickets" / "train" / "INC-00001.json").read_text())
        payload["ticket_id"] = "INC-NEW"
        payload["subject"] = "Fresh incident"
        fresh_ticket.write_text(json.dumps(payload), encoding="utf-8")

        report = ingest_ticket_file(fresh_ticket, connection)
        row = connection.execute(
            "SELECT ticket_id, store_id, asset_id FROM incidents WHERE ticket_id = ?",
            ("INC-NEW",),
        ).fetchone()
        assert report.inserted["incidents"] == 1
        assert row["store_id"] == "SW-0001"
        assert row["asset_id"] == "PRN-0001-P1"
    finally:
        connection.close()


@pytest.mark.parametrize("retained_events", [0, 1])
def test_reimport_removes_stale_source_children(tmp_path, retained_events):
    data_root = tmp_path / "dataset"
    make_dataset(data_root)
    ticket_path = data_root / "tickets" / "train" / "INC-00001.json"
    payload = json.loads(ticket_path.read_text(encoding="utf-8"))
    payload["ticket_history"].append({
        "timestamp": "2026-01-01T10:10:00+05:30",
        "actor": "L1 Agent",
        "note": "Second source note.",
    })
    payload["related_tickets"] = ["INC-UNRESOLVED"]
    ticket_path.write_text(json.dumps(payload), encoding="utf-8")
    connection = open_database(":memory:")
    try:
        ingest_dataset(data_root, connection)
        payload["ticket_history"] = payload["ticket_history"][:retained_events]
        payload["related_tickets"] = []
        ticket_path.write_text(json.dumps(payload), encoding="utf-8")

        report = ingest_ticket_file(ticket_path, connection)

        assert connection.execute("SELECT COUNT(*) FROM incident_events").fetchone()[0] == retained_events
        assert connection.execute("SELECT COUNT(*) FROM incident_relations").fetchone()[0] == 0
        assert report.deleted["incident_events"] == 2 - retained_events
        assert report.deleted["incident_relations"] == 1
        repeated = ingest_ticket_file(ticket_path, connection)
        assert sum(repeated.deleted.values()) == 0
        assert sum(repeated.inserted.values()) == 0
        assert sum(repeated.updated.values()) == 0
    finally:
        connection.close()


def test_cli_uses_environment_paths_and_is_repeatable(monkeypatch, tmp_path, capsys):
    data_root = tmp_path / "dataset"
    database_path = tmp_path / "state" / "servewell.db"
    make_dataset(data_root)
    monkeypatch.setenv("DATA_DIR", str(data_root))
    monkeypatch.setenv("DATABASE_PATH", str(database_path))

    assert main(["ingest"]) == 0
    assert "failed: 0" in capsys.readouterr().out
    assert main(["ingest"]) == 0
    output = capsys.readouterr().out
    assert "inserted: 0" in output
    assert "updated: 0" in output
    connection = open_database(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1
    finally:
        connection.close()


def test_cli_flags_override_environment_paths(monkeypatch, tmp_path):
    data_root = tmp_path / "dataset"
    database_path = tmp_path / "explicit.db"
    make_dataset(data_root)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "missing-dataset"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "unused.db"))

    assert main([
        "ingest", "--data-dir", str(data_root), "--database-path", str(database_path)
    ]) == 0

    assert database_path.is_file()
    assert not (tmp_path / "unused.db").exists()
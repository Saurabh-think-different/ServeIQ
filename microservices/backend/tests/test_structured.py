import csv
import json

import pytest

from phdata.db.database import open_database
from phdata.ingestion.interfaces import IngestionError
from phdata.ingestion.structured import StructuredIngestion, ingest_dataset, ingest_ticket_file

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
        with pytest.raises(IngestionError, match="INC-MISSING"):
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


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "dataset"
    make_dataset(root)
    return root


@pytest.fixture
def connection():
    db = open_database(":memory:")
    yield db
    db.close()


def change_ticket(root, **changes):
    path = root / "tickets/train/INC-00001.json"
    payload = json.loads(path.read_text())
    payload.update(changes)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_stage_uses_settings_and_persists(dataset, tmp_path):
    from phdata.config import get_settings
    from dataclasses import replace

    path = tmp_path / "state/structured.db"
    settings = replace(get_settings(), data_dir=dataset, database_path=path)
    stage = StructuredIngestion(settings)
    assert stage.run().stage == "structured"
    assert stage.run().skipped["incidents"] == 1
    db = open_database(path)
    try:
        assert db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1
    finally:
        db.close()


@pytest.mark.parametrize("changes,field", [
    ({"date_opened": "secret-not-a-date"}, "date_opened"),
    ({"date_opened": "2026-01-01T00:00:00"}, "date_opened"),
    ({"date_opened": "1700000000"}, "date_opened"),
    ({"ticket_history": [{"timestamp": "secret-date", "actor": "a", "note": "n"}]}, "ticket_history.0.timestamp"),
    ({"ticket_history": ["secret-object"]}, "ticket_history.0"),
    ({"related_tickets": [None]}, "related_tickets.0"),
    ({"related_tickets": ["same", "same"]}, "related_tickets"),
    ({"tags": [42]}, "tags.0"),
    ({"priority": "secret-priority"}, "priority"),
    ({"subject": None}, "subject"),
    ({"escalation_flag": "secret-boolean"}, "escalation_flag"),
])
def test_invalid_ticket_is_contextual_and_sanitized(dataset, connection, changes, field):
    change_ticket(dataset, **changes)
    with pytest.raises(IngestionError) as caught:
        ingest_dataset(dataset, connection)
    assert "INC-00001.json" in str(caught.value)
    assert field in str(caught.value)
    assert "secret" not in str(caught.value)
    assert connection.execute("SELECT COUNT(*) FROM stores").fetchone()[0] == 0


def test_nulls_unknown_text_and_original_offsets(dataset, connection):
    change_ticket(dataset, tags=None, ticket_history=None, related_tickets=None,
                  resolution_notes="", contact_phone=None, priority=None,
                  category="Unknown Category", status="Awaiting Vendor", date_opened="2026-01-01T00:00:00Z")
    ingest_dataset(dataset, connection)
    row = connection.execute("SELECT * FROM incidents").fetchone()
    assert row["opened_at"] == "2026-01-01T00:00:00Z"
    assert row["category"] == "Unknown Category"
    assert row["status"] == "Awaiting Vendor"
    assert row["tags_json"] == "[]"
    assert row["resolution_notes"] is None
    assert row["priority"] is None


def test_csv_preserves_leading_zero_ids_and_literal_na(dataset, connection):
    stores = dataset / "data/stores.csv"
    stores.write_text(stores.read_text().replace("SW-0001", "0001").replace("Manager", "NA"))
    assets = dataset / "data/assets.csv"
    assets.write_text(assets.read_text().replace("SW-0001", "0001").replace("PRN-0001-P1", "0002"))
    change_ticket(dataset, store_id="0001", asset_id="0002")
    ingest_dataset(dataset, connection)
    row = connection.execute("SELECT store_id, region_manager FROM stores").fetchone()
    assert tuple(row) == ("0001", "NA")
    assert connection.execute("SELECT asset_id FROM incidents").fetchone()[0] == "0002"


@pytest.mark.parametrize("file", ["stores.csv", "assets.csv", "sla_matrix.csv"])
def test_duplicate_csv_keys_rejected(dataset, connection, file):
    path = dataset / "data" / file
    path.write_text(path.read_text() + path.read_text().splitlines()[1] + "\n")
    with pytest.raises(IngestionError, match=r":3: duplicate key"):
        ingest_dataset(dataset, connection)
    assert connection.execute("SELECT COUNT(*) FROM stores").fetchone()[0] == 0


@pytest.mark.parametrize("filename", ["../private.json", "/private.json", "C:\\private.json"])
def test_manifest_rejects_unsafe_paths(dataset, connection, filename):
    path = dataset / "tickets/train_index.csv"
    path.write_text("ticket_id,filename\nINC-00001," + filename + "\n")
    with pytest.raises(IngestionError, match="filename"):
        ingest_dataset(dataset, connection)


def test_missing_manifest_csv_is_atomic(dataset, connection):
    (dataset / "tickets/train_index.csv").unlink()
    with pytest.raises(IngestionError, match="train_index.csv"):
        ingest_dataset(dataset, connection)
    assert connection.execute("SELECT COUNT(*) FROM stores").fetchone()[0] == 0


def test_duplicate_manifest_and_mismatched_ticket_ids(dataset, connection):
    manifest = dataset / "tickets/train_index.csv"
    text = manifest.read_text()
    manifest.write_text(text + text.splitlines()[1] + "\n")
    with pytest.raises(IngestionError, match="duplicate key"):
        ingest_dataset(dataset, connection)
    manifest.write_text(text)
    change_ticket(dataset, ticket_id="different")
    with pytest.raises(IngestionError, match="manifest does not match"):
        ingest_dataset(dataset, connection)


@pytest.mark.parametrize("store,asset,resolved_store,resolved_asset,warnings", [
    ("SW-0001", "PRN-0001-P1", "SW-0001", "PRN-0001-P1", 0),
    ("SW-0001", "prn-0001-p1", "SW-0001", None, 1),
    ("sw-0001", "PRN-0001-P1", None, None, 2),
    ("UNKNOWN", "UNKNOWN", None, None, 2),
])
def test_exact_reference_resolution(dataset, connection, store, asset, resolved_store, resolved_asset, warnings):
    change_ticket(dataset, store_id=store, asset_id=asset)
    report = ingest_dataset(dataset, connection)
    row = connection.execute("SELECT reported_store_id, reported_asset_id, store_id, asset_id FROM incidents").fetchone()
    assert tuple(row) == (store, asset, resolved_store, resolved_asset)
    assert len(report.warnings) == warnings


def test_asset_from_different_store_not_resolved(dataset, connection):
    path = dataset / "data/stores.csv"
    path.write_text(path.read_text() + path.read_text().splitlines()[1].replace("SW-0001", "SW-0002") + "\n")
    change_ticket(dataset, store_id="SW-0002")
    report = ingest_dataset(dataset, connection)
    assert len(report.warnings) == 1
    assert connection.execute("SELECT asset_id FROM incidents").fetchone()[0] is None


def test_two_pass_forward_and_cyclic_relations(dataset, connection):
    first = change_ticket(dataset, related_tickets=["INC-00002", "MISSING"])
    payload = json.loads(first.read_text())
    payload.update(ticket_id="INC-00002", related_tickets=["INC-00001"])
    (first.parent / "INC-00002.json").write_text(json.dumps(payload))
    manifest = dataset / "tickets/train_index.csv"
    manifest.write_text(manifest.read_text() + "INC-00002,raw/INC-00002.json\n")
    report = ingest_dataset(dataset, connection)
    assert len(report.warnings) == 1
    assert [tuple(row) for row in connection.execute(
        "SELECT reported_related_ticket_id, related_ticket_id FROM incident_relations ORDER BY ticket_id, reported_related_ticket_id"
    )] == [("INC-00002", "INC-00002"), ("MISSING", None), ("INC-00001", "INC-00001")]


def test_database_failure_rolls_back_updates_and_children(dataset, connection):
    ingest_dataset(dataset, connection)
    connection.execute("""CREATE TRIGGER fail_relations BEFORE INSERT ON incident_relations
                          BEGIN SELECT RAISE(ABORT, 'secret database detail'); END""")
    path = change_ticket(dataset, subject="Changed", ticket_history=[], related_tickets=["INC-00001"])
    with pytest.raises(IngestionError, match="transaction rolled back") as caught:
        ingest_ticket_file(path, connection)
    assert "secret" not in str(caught.value)
    assert connection.execute("SELECT subject FROM incidents").fetchone()[0] == "Printer offline"
    assert connection.execute("SELECT COUNT(*) FROM incident_events").fetchone()[0] == 1


def test_malformed_json_sanitized(dataset, connection):
    path = dataset / "tickets/train/INC-00001.json"
    path.write_text('{"secret-sensitive-content":')
    with pytest.raises(IngestionError) as caught:
        ingest_dataset(dataset, connection)
    assert "json_invalid" in str(caught.value)
    assert "secret-sensitive-content" not in str(caught.value)


def test_exact_manifest_path_precedes_mapping(dataset, connection):
    path = dataset / "tickets/raw"
    path.mkdir()
    (path / "INC-00001.json").write_text((dataset / "tickets/train/INC-00001.json").read_text())
    assert ingest_dataset(dataset, connection).path_mappings == {}


def test_empty_csv_missing_columns_and_invalid_sla(dataset, connection):
    path = dataset / "data/sla_matrix.csv"
    text = path.read_text()
    path.write_text("")
    with pytest.raises(IngestionError, match="required CSV"):
        ingest_dataset(dataset, connection)
    path.write_text("priority,category\nP2,Printers\n")
    with pytest.raises(IngestionError, match="missing columns"):
        ingest_dataset(dataset, connection)
    path.write_text(text.replace(",30,", ",-1,"))
    with pytest.raises(IngestionError, match="first_response_minutes"):
        ingest_dataset(dataset, connection)


def test_original_offset_and_history_retained(dataset, connection):
    ingest_dataset(dataset, connection)
    assert connection.execute("SELECT opened_at FROM incidents").fetchone()[0] == "2026-01-01T10:00:00+05:30"
    assert connection.execute("SELECT event_at FROM incident_events").fetchone()[0] == "2026-01-01T10:05:00+05:30"


def test_database_failure_rolls_back_entire_dataset(dataset, connection):
    connection.execute("""CREATE TRIGGER fail_incidents BEFORE INSERT ON incidents
                          BEGIN SELECT RAISE(ABORT, 'secret database detail'); END""")
    with pytest.raises(IngestionError, match="transaction rolled back"):
        ingest_dataset(dataset, connection)
    for table in ("stores", "assets", "sla_targets", "incidents"):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_library_does_not_commit_callers_transaction(dataset, connection):
    connection.execute("BEGIN")
    ingest_dataset(dataset, connection)
    assert connection.in_transaction
    connection.rollback()
    assert connection.execute("SELECT COUNT(*) FROM stores").fetchone()[0] == 0


def test_labels_are_not_ingested(dataset, connection):
    (dataset / "tickets/labels.json").write_text("invalid json never opened")
    change_ticket(dataset, labels={"decision": "must not appear"})
    ingest_dataset(dataset, connection)
    row = connection.execute("SELECT * FROM incidents").fetchone()
    assert "must not appear" not in str(tuple(row))


@pytest.mark.parametrize("valid_first", [False, True])
def test_manifest_extra_field_cannot_shift_ticket_identity(dataset, connection, valid_first):
    original = dataset / "tickets/train/INC-00001.json"
    other = json.loads(original.read_text())
    other["ticket_id"] = "INC-OTHER"
    (original.parent / "INC-OTHER.json").write_text(json.dumps(other))
    manifest = dataset / "tickets/train_index.csv"
    manifest.write_text(
        "ticket_id,filename\n"
        + ("INC-00001,train/INC-00001.json\n" if valid_first else "")
        + "INC-ORIGINAL,INC-OTHER,train/INC-OTHER.json\n"
    )
    with pytest.raises(IngestionError) as caught:
        ingest_dataset(dataset, connection)
    assert "train_index.csv" in str(caught.value)
    assert "INC-ORIGINAL" not in str(caught.value)
    for table in ("stores", "assets", "sla_targets", "incidents"):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("optional_columns", [False, True])
def test_reference_csv_extra_field_rejected(dataset, connection, optional_columns):
    path = dataset / "data/stores.csv"
    header = "store_id,store_name,city,neighborhood,store_type"
    row = "SW-0001,Store,City,Neighborhood,corporate"
    if optional_columns:
        header += ",region_manager,support_phone"
        row += ",Manager,Phone"
    path.write_text(header + "\n" + row + ",secret-extra-field\n")
    with pytest.raises(IngestionError) as caught:
        ingest_dataset(dataset, connection)
    assert "stores.csv" in str(caught.value)
    assert "secret-extra-field" not in str(caught.value)
    assert connection.execute("SELECT COUNT(*) FROM stores").fetchone()[0] == 0


def test_underfilled_required_csv_field_rejected(dataset, connection):
    path = dataset / "tickets/train_index.csv"
    path.write_text("ticket_id,filename\nINC-00001\n")
    with pytest.raises(IngestionError, match=r"train_index.csv:2: filename"):
        ingest_dataset(dataset, connection)
    assert connection.execute("SELECT COUNT(*) FROM stores").fetchone()[0] == 0


@pytest.mark.parametrize("suffix", ["", ",", ",,"])
def test_underfilled_optional_csv_fields_are_blank_not_width_validated(dataset, connection, suffix):
    path = dataset / "data/stores.csv"
    path.write_text(
        "store_id,store_name,city,neighborhood,store_type,region_manager,support_phone\n"
        "SW-0001,Store,City,Neighborhood,corporate" + suffix + "\n"
    )
    ingest_dataset(dataset, connection)
    assert tuple(connection.execute("SELECT region_manager, support_phone FROM stores").fetchone()) == (None, None)

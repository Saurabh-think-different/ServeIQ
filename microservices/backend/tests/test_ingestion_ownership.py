from pathlib import Path

import pytest

from phdata.db.database import open_database
from phdata.ingestion.interfaces import IngestionError
from phdata.ingestion.structured import ingest_dataset


def test_seed_import_cannot_overwrite_runtime_incident(tmp_path):
    data_dir = Path(__file__).resolve().parents[3] / "client_data"
    connection = open_database(tmp_path / "ownership.db")
    try:
        ingest_dataset(data_dir, connection)
        ticket_id = connection.execute("SELECT ticket_id FROM incidents ORDER BY ticket_id LIMIT 1").fetchone()[0]
        with connection:
            connection.execute(
                "UPDATE incidents SET source_path = NULL, subject = ? WHERE ticket_id = ?",
                ("Runtime operator report", ticket_id),
            )
        before_events = connection.execute("SELECT COUNT(*) FROM incident_events").fetchone()[0]

        with pytest.raises(IngestionError, match="seed overwrite refused"):
            ingest_dataset(data_dir, connection)

        incident = connection.execute(
            "SELECT subject, source_path FROM incidents WHERE ticket_id = ?", (ticket_id,),
        ).fetchone()
        assert incident["subject"] == "Runtime operator report"
        assert incident["source_path"] is None
        assert connection.execute("SELECT COUNT(*) FROM incident_events").fetchone()[0] == before_events
    finally:
        connection.close()

"""Command-line interface for loading ServeWell ticket data."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from phdata.config import get_settings
from phdata.db.database import open_database
from phdata.ingestion.importer import DatasetImportError, ImportReport, ingest_dataset, ingest_ticket_file


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database-path", type=Path, default=get_settings().database_path)


def _print_report(report: ImportReport) -> None:
    for action in ("inserted", "updated", "skipped", "deleted"):
        counts = getattr(report, action)
        total = sum(counts.values())
        details = ", ".join(f"{table}={count}" for table, count in sorted(counts.items()))
        print(f"{action}: {total}" + (f" ({details})" if details else ""))
    print("failed: 0")
    for mapping, count in sorted(report.path_mappings.items()):
        print(f"path mapping used: {mapping} ({count} files)")
    for warning in report.warnings:
        print(f"warning: {warning}")


def main(argv: Sequence[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="servewell-data")
    commands = parser.add_subparsers(dest="command", required=True)

    dataset_command = commands.add_parser("ingest", help="Import reference data and indexed tickets")
    dataset_command.add_argument(
        "--data-dir", type=Path, default=settings.data_dir
    )
    _add_database_argument(dataset_command)

    ticket_command = commands.add_parser("ticket", help="Import one fresh ticket JSON")
    ticket_command.add_argument("ticket_file", type=Path)
    _add_database_argument(ticket_command)

    arguments = parser.parse_args(argv)
    arguments.database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = open_database(arguments.database_path)
    try:
        if arguments.command == "ingest":
            report = ingest_dataset(arguments.data_dir, connection)
        else:
            report = ingest_ticket_file(arguments.ticket_file, connection)
    except DatasetImportError as error:
        print(f"Import failed ({len(error.errors)} failed record(s)); transaction rolled back.", file=sys.stderr)
        for message in error.errors:
            print(f"- {message}", file=sys.stderr)
        return 1
    finally:
        connection.close()

    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
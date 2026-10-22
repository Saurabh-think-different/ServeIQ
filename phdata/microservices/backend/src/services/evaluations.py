"""Bounded background evaluation jobs with isolated, held-out SQLite fixtures."""

import asyncio
import fcntl
import hashlib
import json
import logging
import shutil
import sqlite3
from contextlib import closing, contextmanager
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import delete, select, update

from prompts.system import PROMPT_VERSION, build_system_prompt
from src.models import Event, Incident, Message, utc_now
from src.models.escalation import L2Escalation
from src.models.evaluation import EvaluationCaseResult, EvaluationRun
from src.models.knowledge import KnowledgeDocument
from src.models.structured import HistoricalTicket
from src.services import store
from src.services.eval_execution import execute_case
from src.services.eval_metrics import evaluate_case
from src.services.llm import validate_model
from src.services.tools import ToolName

logger = logging.getLogger(__name__)
ACTIVE_STATUSES = {"queued", "running"}


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def storage_root() -> Path:
    url = store.engine.url
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise HTTPException(503, "Evaluations require persistent SQLite storage")
    root = Path(url.database).resolve().parent / "evaluations"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


@contextmanager
def _job_lock():
    with (storage_root() / ".runner.lock").open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise HTTPException(409, "Another evaluation run is active") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def recover_interrupted_runs() -> None:
    try:
        with _job_lock():
            with store.operational_session() as db:
                ids = list(
                    db.scalars(
                        select(EvaluationRun.id).where(EvaluationRun.status.in_(ACTIVE_STATUSES))
                    )
                )
            for run_id in ids:
                _finish_run(run_id, "interrupted", "Backend restarted before evaluation completed")
                directory = storage_root() / run_id
                if directory.is_dir():
                    shutil.rmtree(directory)
    except HTTPException as error:
        if error.status_code != 409:
            raise


def queue_run(cases: list[dict], *, suite: str, model: str | None, settings) -> str:
    if not cases or len(cases) > settings.evaluation_max_cases:
        raise HTTPException(422, "Select between one and EVALUATION_MAX_CASES cases")
    if len({case["id"] for case in cases}) != len(cases):
        raise HTTPException(422, "Evaluation case IDs must be unique")
    model = validate_model(settings, model)
    if not settings.openai_api_key:
        raise HTTPException(503, "OPENAI_API_KEY is required to run live evaluations")
    with _job_lock(), store.operational_session() as db:
        if db.scalar(
            select(EvaluationRun.id).where(EvaluationRun.status.in_(ACTIVE_STATUSES)).limit(1)
        ):
            raise HTTPException(409, "Another evaluation is queued or running")
        run = EvaluationRun(suite=suite, model=model, case_count=len(cases))
        db.add(run)
        db.flush()
        for ordinal, case in enumerate(cases):
            db.add(
                EvaluationCaseResult(
                    run_id=run.id,
                    case_id=case["id"],
                    title=case["title"],
                    ordinal=ordinal,
                    specification=case,
                )
            )
        db.commit()
        return run.id


def _kb_fingerprint(db) -> str:
    rows = db.execute(
        select(
            KnowledgeDocument.id,
            KnowledgeDocument.source_hash,
            KnowledgeDocument.active_generation,
            KnowledgeDocument.embedding_model,
            KnowledgeDocument.embedding_dimensions,
        ).order_by(KnowledgeDocument.id)
    ).all()
    return _digest([tuple(row) for row in rows])


def _fixture_fingerprint(db) -> str:
    names = ("stores", "assets", "sla_matrix", "system_versions", "historical_tickets")
    rows = {}
    for name in names:
        table = store.Base.metadata.tables[name]
        rows[name] = [
            dict(row)
            for row in db.execute(select(table).order_by(*table.primary_key.columns)).mappings()
        ]
    return _digest(rows)


def _snapshot(base: Path, cases: list[dict]) -> dict:
    source = Path(store.engine.url.database).resolve()
    with (
        closing(sqlite3.connect(f"file:{quote(str(source))}?mode=ro", uri=True)) as connection,
        closing(sqlite3.connect(base)) as destination,
    ):
        connection.backup(destination)
    heldout = {ticket for case in cases for ticket in case.get("source_ticket_ids", [])}
    with store.use_database("sqlite:///" + str(base)), store.SessionLocal() as db:
        historical = list(db.scalars(select(HistoricalTicket)))
        descriptions = {
            str(row.raw.get("description", "")).strip().casefold()
            for row in historical
            if row.ticket_id in heldout
        } - {""}
        changed = True
        while changed:
            before = len(heldout)
            for row in historical:
                if (
                    row.ticket_id in heldout
                    or str(row.raw.get("description", "")).strip().casefold() in descriptions
                ):
                    heldout.add(row.ticket_id)
                    heldout.update(
                        item for item in row.raw.get("related_tickets", []) if isinstance(item, str)
                    )
            changed = len(heldout) != before
        ids = [row.incident_id for row in historical if row.ticket_id in heldout]
        if ids:
            message_ids = select(Message.id).where(Message.incident_id.in_(ids))
            db.execute(delete(Event).where(Event.message_id.in_(message_ids)))
            db.execute(delete(L2Escalation).where(L2Escalation.incident_id.in_(ids)))
            db.execute(delete(HistoricalTicket).where(HistoricalTicket.incident_id.in_(ids)))
            db.execute(delete(Message).where(Message.incident_id.in_(ids)))
            db.execute(delete(Incident).where(Incident.id.in_(ids)))
        db.commit()
    with closing(sqlite3.connect(base)) as connection:
        # Expected answers and evaluation traces must not enter an agent's fixture.
        connection.execute("DROP TABLE IF EXISTS evaluation_case_results")
        connection.execute("DROP TABLE IF EXISTS evaluation_runs")
        connection.execute("UPDATE users SET password_hash='evaluation-login-disabled'")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {"excluded_ticket_ids": sorted(heldout), "excluded_incidents": len(ids)}


def _summary(db, run_id: str) -> dict:
    rows = list(
        db.scalars(select(EvaluationCaseResult).where(EvaluationCaseResult.run_id == run_id))
    )
    counts = {
        name: sum(row.status == name for row in rows)
        for name in ("passed", "failed", "error", "cancelled", "pending", "running")
    }
    completed = counts["passed"] + counts["failed"] + counts["error"]
    finished = [row for row in rows if row.status in {"passed", "failed", "error"}]
    metric_names = {name for row in finished for name in row.assessment.get("metrics", {})}
    metrics = {}
    for name in sorted(metric_names):
        values = [row.assessment.get("metrics", {}).get(name, {}) for row in finished]
        evaluated = [item for item in values if item.get("status") in {"passed", "failed"}]
        scores = [
            item["value"]
            for item in evaluated
            if isinstance(item.get("value"), (int, float)) and not isinstance(item["value"], bool)
        ]
        metrics[name] = {
            "evaluated_cases": len(evaluated),
            "passed_cases": sum(item["status"] == "passed" for item in evaluated),
            "mean_score": sum(scores) / len(scores) if scores else None,
            "scored_cases": len(scores),
        }
    native = [row.assessment.get("native_evaluator", {}).get("score") for row in finished]
    native = [score for score in native if isinstance(score, (int, float))]
    false_resolutions = sum(
        len(row.assessment.get("metrics", {}).get("no_false_resolution", {}).get("value") or [])
        for row in finished
    )
    run = db.get(EvaluationRun, run_id)
    duration = sum(row.duration_ms for row in rows)
    return {
        "counts": counts,
        "completed": completed,
        "overall_passed": run.status == "completed" and counts["passed"] == len(rows),
        "snapshot_valid": True
        if run.status == "completed"
        else False
        if run.status == "invalidated"
        else None,
        "pass_rate": counts["passed"] / completed if completed else None,
        "metrics": metrics,
        "native_trajectory_mean": sum(native) / len(native) if native else None,
        "false_resolutions": false_resolutions,
        "duration_ms": duration,
        "mean_case_duration_ms": duration / completed if completed else None,
        "token_usage": None,
        "estimated_cost": None,
        "usage_note": "Token/cost telemetry is not yet exposed by the SSE adapter; not estimated.",
    }


def _persist_case(
    run_id: str, case_id: str, actual: dict, assessment: dict, elapsed: float
) -> None:
    invariants = actual.get("invariants", {})
    invariant_failures = [
        name for name, item in invariants.items() if not item.get("passed", False)
    ]
    if invariant_failures:
        assessment["passed"] = False
        assessment.setdefault("failures", []).extend(
            "Backend invariant: " + name for name in invariant_failures
        )
    error = actual.get("error")
    passed = bool(assessment.get("passed")) and not error
    with store.operational_session() as db:
        row = db.scalar(
            select(EvaluationCaseResult).where(
                EvaluationCaseResult.run_id == run_id, EvaluationCaseResult.case_id == case_id
            )
        )
        expected_reason = row.specification.get("metadata", {}).get("expected_escalation_reason")
        if expected_reason:
            reasons = [
                call.get("response", {}).get("reason_code")
                for turn in actual.get("turns", [])
                for call in turn.get("tool_calls", [])
                if call.get("name") == "escalate_to_l2"
                and call.get("response", {}).get("status") == "escalated"
            ]
            actual_reason = reasons[-1] if reasons else None
            reason_matches = actual_reason == expected_reason
            actual.setdefault("invariants", {})["expected_escalation_reason"] = {
                "passed": reason_matches,
                "details": {"expected": expected_reason, "actual": actual_reason},
            }
            if not reason_matches:
                passed = False
                assessment["passed"] = False
                assessment.setdefault("failures", []).append(
                    "Backend invariant: expected_escalation_reason"
                )
        row.status = "error" if error else "passed" if passed else "failed"
        row.actual, row.assessment = actual, assessment
        row.duration_ms = round(elapsed * 1000)
        row.error = error
        run = db.get(EvaluationRun, run_id)
        db.flush()
        summary = _summary(db, run_id)
        run.summary = summary
        run.completed_count = summary["completed"]
        run.passed_count = summary["counts"]["passed"]
        run.failed_count = summary["counts"]["failed"]
        run.error_count = summary["counts"]["error"]
        db.commit()


async def run_evaluation(run_id: str, settings) -> None:
    try:
        with _job_lock():
            await _run_locked(run_id, settings)
    except asyncio.CancelledError:
        _finish_run(run_id, "interrupted", "Evaluation worker was interrupted")
        raise
    except HTTPException:
        _finish_run(run_id, "error", "Evaluation runner is unavailable or already active")
    except Exception as error:  # noqa: BLE001 - never persist provider bodies or credentials
        logger.warning(
            "Evaluation run failed", extra={"run_id": run_id, "error_type": type(error).__name__}
        )
        _finish_run(run_id, "error", f"{type(error).__name__}: evaluation run failed")
    finally:
        directory = storage_root() / run_id
        if directory.is_dir():
            await asyncio.to_thread(shutil.rmtree, directory)


async def _run_locked(run_id: str, settings) -> None:
    with store.operational_session() as db:
        run = db.get(EvaluationRun, run_id)
        if run is None or run.status != "queued":
            return
        rows = list(
            db.scalars(
                select(EvaluationCaseResult)
                .where(EvaluationCaseResult.run_id == run_id)
                .order_by(EvaluationCaseResult.ordinal)
            )
        )
        specs = [row.specification for row in rows]
        model = run.model
        run.status, run.started_at = "running", utc_now()
        source_hash, kb_hash = _fixture_fingerprint(db), _kb_fingerprint(db)
        prompt = build_system_prompt([name.value for name in ToolName])
        run.configuration = {
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "fixture_sha256": source_hash,
            "kb_generation_sha256": kb_hash,
            "case_set_sha256": _digest(specs),
            "tools": [name.value for name in ToolName],
            "source_sha256": {
                name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in (
                    "tools.py",
                    "support_data.py",
                    "llm.py",
                    "eval_execution.py",
                    "eval_metrics.py",
                )
            },
            "packages": {
                name: version(name) for name in ("google-adk", "litellm", "openai", "qdrant-client")
            },
            "max_agent_steps": settings.max_agent_steps,
            "kb_min_score": settings.kb_min_score,
            "embedding_model": settings.embedding_model,
            "embedding_dimensions": settings.embedding_dimensions,
            "qdrant_collection": settings.qdrant_collection,
            "isolation": "per-case SQLite; read-only shared KB vectors",
            "case_timeout_seconds": settings.evaluation_case_timeout_seconds,
            "live_provider_calls": True,
            "llm_judge": False,
        }
        db.commit()
    directory = storage_root() / run_id
    directory.mkdir(mode=0o700, exist_ok=True)
    base = directory / "base.db"
    exclusions = await asyncio.to_thread(_snapshot, base, specs)
    with store.operational_session() as db:
        run = db.get(EvaluationRun, run_id)
        run.configuration = {**run.configuration, "holdout": exclusions}
        db.commit()
    try:
        async with asyncio.timeout(settings.evaluation_run_timeout_seconds):
            for index, case in enumerate(specs):
                with store.operational_session() as db:
                    run = db.get(EvaluationRun, run_id)
                    if run.cancel_requested:
                        _finish_run(run_id, "cancelled", None)
                        return
                    row = db.scalar(
                        select(EvaluationCaseResult).where(
                            EvaluationCaseResult.run_id == run_id,
                            EvaluationCaseResult.case_id == case["id"],
                        )
                    )
                    row.status = "running"
                    db.commit()
                sandbox = directory / f"case-{index}.db"
                await asyncio.to_thread(shutil.copyfile, base, sandbox)
                started = perf_counter()
                try:
                    actual = await asyncio.wait_for(
                        execute_case(case, "sqlite:///" + str(sandbox), settings, model),
                        timeout=settings.evaluation_case_timeout_seconds,
                    )
                    assessment = await evaluate_case(case, actual.get("turns", []))
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001 - sanitize every execution/adapter error
                    actual = {
                        "turns": [],
                        "invariants": {},
                        "error": f"{type(error).__name__}: case execution failed",
                    }
                    assessment = {"passed": False, "metrics": {}, "failures": [actual["error"]]}
                _persist_case(run_id, case["id"], actual, assessment, perf_counter() - started)
    except TimeoutError:
        _finish_run(run_id, "error", "Evaluation run exceeded its time budget")
        return
    with store.operational_session() as db:
        changed = _fixture_fingerprint(db) != source_hash or _kb_fingerprint(db) != kb_hash
    _finish_run(
        run_id,
        "invalidated" if changed else "completed",
        "Source data changed during evaluation; rerun against a stable snapshot"
        if changed
        else None,
    )


def _finish_run(run_id: str, status: str, error: str | None) -> None:
    with store.operational_session() as db:
        run = db.get(EvaluationRun, run_id)
        if run is None:
            return
        db.execute(
            update(EvaluationCaseResult)
            .where(
                EvaluationCaseResult.run_id == run_id,
                EvaluationCaseResult.status.in_({"pending", "running"}),
            )
            .values(status="cancelled" if status == "cancelled" else "error", error=error)
        )
        run.status, run.error, run.finished_at = status, error, utc_now()
        db.flush()
        summary = _summary(db, run_id)
        run.summary = summary
        run.completed_count = summary["completed"]
        run.passed_count = summary["counts"]["passed"]
        run.failed_count = summary["counts"]["failed"]
        run.error_count = summary["counts"]["error"]
        db.commit()

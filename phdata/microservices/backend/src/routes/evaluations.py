"""Admin-only evaluation jobs; live provider calls are explicit and bounded."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from config import Settings, get_settings
from src.models.evaluation import EvaluationCaseResult, EvaluationRun
from src.routes.ingestion import require_ingestion_key
from src.services import store
from src.services.eval_cases import build_curated_cases, build_training_cases
from src.services.evaluations import queue_run, run_evaluation
from src.services.llm import configured_models
from src.services.tools import ToolName

router = APIRouter(
    prefix="/evaluations", tags=["evaluations"], dependencies=[Depends(require_ingestion_key)]
)
Config = Annotated[Settings, Depends(get_settings)]
Status = Literal["waiting_for_user", "resolved", "escalated"]


class CaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    title: str = Field(min_length=1, max_length=200)
    actor_email: str = Field(default="", max_length=320)
    actor_name: str = Field(default="", max_length=200)
    turns: list[str] = Field(min_length=1, max_length=5)
    expected_statuses: list[Status] = Field(min_length=1, max_length=5)
    expected_tools: list[ToolName] = Field(default_factory=list, max_length=8)
    forbidden_tools: list[ToolName] = Field(default_factory=list, max_length=8)
    expected_kb_docs: list[str] = Field(default_factory=list, max_length=30)
    source_ticket_ids: list[Annotated[str, Field(pattern=r"^INC-[0-9]{5}$")]] = Field(
        default_factory=list, max_length=100
    )
    metadata: dict = Field(default_factory=dict)

    @field_validator("turns")
    @classmethod
    def validate_turns(cls, values):
        if any(not value.strip() or len(value) > 50000 for value in values):
            raise ValueError("Turns must be nonblank text of at most 50000 characters")
        return [value.strip() for value in values]

    @field_validator("expected_kb_docs")
    @classmethod
    def validate_sources(cls, values):
        if any(
            ".." in value.split("/")
            or value.startswith("/")
            or "\\" in value
            or not value.endswith(".md")
            for value in values
        ):
            raise ValueError("KB expectations must be relative Markdown paths")
        return values

    @model_validator(mode="after")
    def validate_case(self):
        if not self.actor_name and not self.actor_email:
            raise ValueError("Specify an existing manager name or email")
        if len(self.turns) != len(self.expected_statuses):
            raise ValueError("Each turn requires one expected status")
        if any(status != "waiting_for_user" for status in self.expected_statuses[:-1]):
            raise ValueError("A scenario cannot continue after resolution or escalation")
        if set(self.expected_tools) & set(self.forbidden_tools):
            raise ValueError("A tool cannot be both expected and forbidden")
        if self.metadata.get("fault") not in {None, "kb_unavailable", "no_kb_match"}:
            raise ValueError("Unsupported fault injection")
        if len(str(self.metadata)) > 20000:
            raise ValueError("Case metadata is too large")
        return self


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suite: Literal["curated", "training", "custom"] = "curated"
    model: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=3, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10000)
    case_ids: list[str] | None = Field(default=None, max_length=100)
    cases: list[CaseSpec] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_suite(self):
        if self.suite == "custom" and not self.cases:
            raise ValueError("Custom suites require cases")
        if self.suite != "custom" and self.cases:
            raise ValueError("Cases can only be supplied for a custom suite")
        return self


def _catalog(suite: str, settings: Settings, limit: int, offset: int) -> list[dict]:
    try:
        if suite == "curated":
            return build_curated_cases(settings.dataset_root)[offset : offset + limit]
        return build_training_cases(settings.dataset_root, limit=limit, offset=offset)
    except (OSError, ValueError) as error:
        raise HTTPException(
            503, "Evaluation dataset is unavailable or invalid; check DATASET_ROOT and ingestion"
        ) from error


@router.get("/config")
def evaluation_config(settings: Config):
    default, allowed = configured_models(settings)
    return {
        "default_model": default,
        "allowed_models": allowed,
        "max_cases": settings.evaluation_max_cases,
        "case_timeout_seconds": settings.evaluation_case_timeout_seconds,
    }


@router.get("/cases")
def cases(
    settings: Config,
    suite: Literal["curated", "training"] = "curated",
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    return {"suite": suite, "cases": _catalog(suite, settings, limit, offset)}


@router.post("/runs", status_code=202)
def start_run(body: RunCreate, background: BackgroundTasks, settings: Config):
    if body.suite == "custom":
        selected = [case.model_dump(mode="json") for case in body.cases]
    elif body.case_ids is not None:
        if body.suite != "curated":
            raise HTTPException(
                422, "Select training cases using offset/limit, or submit custom cases"
            )
        available = {case["id"]: case for case in _catalog("curated", settings, 100, 0)}
        if not body.case_ids or any(case_id not in available for case_id in body.case_ids):
            raise HTTPException(422, "One or more requested case IDs are unknown")
        selected = [available[case_id] for case_id in body.case_ids]
    else:
        selected = _catalog(body.suite, settings, body.limit, body.offset)
    try:
        selected = [CaseSpec.model_validate(case).model_dump(mode="json") for case in selected]
    except ValueError as error:
        raise HTTPException(422, "Evaluation case specification is invalid") from error
    run_id = queue_run(selected, suite=body.suite, model=body.model, settings=settings)
    background.add_task(run_evaluation, run_id, settings)
    return {
        "id": run_id,
        "status": "queued",
        "case_count": len(selected),
        "live_provider_calls": True,
        "results_url": f"/evaluations/runs/{run_id}",
    }


def _run_dict(row: EvaluationRun) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


@router.get("/runs")
def list_runs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    with store.operational_session() as db:
        rows = db.scalars(
            select(EvaluationRun)
            .order_by(EvaluationRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return [_run_dict(row) for row in rows]


@router.get("/runs/{run_id}")
def get_run(run_id: UUID):
    with store.operational_session() as db:
        row = db.get(EvaluationRun, str(run_id))
        if row is None:
            raise HTTPException(404, "Evaluation run not found")
        return _run_dict(row)


@router.get("/runs/{run_id}/cases")
def case_results(run_id: UUID, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
    with store.operational_session() as db:
        if db.get(EvaluationRun, str(run_id)) is None:
            raise HTTPException(404, "Evaluation run not found")
        rows = db.scalars(
            select(EvaluationCaseResult)
            .where(EvaluationCaseResult.run_id == str(run_id))
            .order_by(EvaluationCaseResult.case_id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            {
                "case_id": row.case_id,
                "title": row.title,
                "status": row.status,
                "duration_ms": row.duration_ms,
                "error": row.error,
                "assessment": row.assessment,
            }
            for row in rows
        ]


@router.get("/runs/{run_id}/cases/{case_id}")
def case_result(run_id: UUID, case_id: str):
    with store.operational_session() as db:
        row = db.scalar(
            select(EvaluationCaseResult).where(
                EvaluationCaseResult.run_id == str(run_id), EvaluationCaseResult.case_id == case_id
            )
        )
        if row is None:
            raise HTTPException(404, "Evaluation case result not found")
        return {column.name: getattr(row, column.name) for column in row.__table__.columns}


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: UUID):
    with store.operational_session() as db:
        row = db.get(EvaluationRun, str(run_id))
        if row is None:
            raise HTTPException(404, "Evaluation run not found")
        if row.status not in {"queued", "running"}:
            raise HTTPException(409, "Evaluation run is already complete")
        row.cancel_requested = True
        db.commit()
        return {
            "id": row.id,
            "cancel_requested": True,
            "note": "Stops after the current case; completed results are retained",
        }

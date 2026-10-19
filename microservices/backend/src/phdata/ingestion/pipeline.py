"""Synchronous orchestration; stage reports are not a cross-store transaction."""

from collections.abc import Mapping
import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict

from phdata.ingestion.interfaces import IngestionError, IngestionReport, IngestionStage, StageName

logger = logging.getLogger(__name__)


class IngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["structured", "knowledge", "all"]


class PipelineReport(BaseModel):
    status: Literal["succeeded", "partial", "failed"]
    stages: list[IngestionReport]


def run_pipeline(
    target: Literal["structured", "knowledge", "all"],
    stages: Mapping[StageName, IngestionStage],
) -> PipelineReport:
    names: tuple[StageName, ...] = ("structured", "knowledge") if target == "all" else (target,)
    reports = []
    for name in names:
        try:
            report = stages[name].run()
        except IngestionError as error:
            report = IngestionReport(stage=name, status="failed", errors=error.errors)
        except Exception as error:
            logger.warning("Ingestion stage %s failed (%s)", name, type(error).__name__)
            report = IngestionReport(
                stage=name,
                status="failed",
                errors=["Stage failed; check data availability, configuration, and dependency connectivity."],
            )
        reports.append(report)
    successes = sum(report.status == "succeeded" for report in reports)
    status = "succeeded" if successes == len(reports) else "partial" if successes else "failed"
    return PipelineReport(status=status, stages=reports)

"""Persisted support-run contracts with bounded, explicit output fields."""

from hashlib import sha256
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from phdata.models.legacy import LegacyRunResult


def evidence_id_for(source_path: str, section: str, content_sha256: str) -> str:
    """Create a stable run-local ID from immutable source identity and content hash."""
    identity = f"{source_path}\0{section}\0{content_sha256}"
    return sha256(identity.encode("utf-8")).hexdigest()


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RunStatus = Literal["running", "completed", "failed"]
RoutingOutcome = Literal[
    "l1_self_service",
    "l1_guided",
    "l2_escalation",
    "non_it",
    "needs_clarification",
]


class RunModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunRequest(RunModel):
    """An intentionally empty request; clients cannot supply prompts or policy."""


class EvidenceSnapshot(RunModel):
    evidence_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    source_path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    section: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    content: Annotated[str, StringConstraints(min_length=1, max_length=8000)]
    content_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    category: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    system_version: str | None = Field(default=None, max_length=200)
    document_type: str | None = Field(default=None, max_length=100)
    applicability: Literal["context_match", "unknown"]
    applicability_notes: list[Annotated[str, StringConstraints(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=20)
    similarity_score: float = Field(allow_inf_nan=False)

    @field_validator("source_path")
    @classmethod
    def source_path_is_relative(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("source_path must be a relative source path")
        return value

    @model_validator(mode="after")
    def validate_content_identity(self) -> "EvidenceSnapshot":
        if sha256(self.content.encode("utf-8")).hexdigest() != self.content_sha256:
            raise ValueError("content_sha256 must match the exact evidence content")
        if self.evidence_id != evidence_id_for(self.source_path, self.section, self.content_sha256):
            raise ValueError("evidence_id must match the immutable source identity")
        return self


class ReviewedStep(RunModel):
    step_id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
    instruction: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    evidence_ids: list[Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]] = Field(min_length=1, max_length=10)
    operator_role: Literal["store_operator"] = "store_operator"
    applicability_notes: list[Annotated[str, StringConstraints(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=10)


class RunQuestion(RunModel):
    question_id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class RunResult(RunModel):
    run_id: NonBlank
    ticket_id: NonBlank
    status: RunStatus
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    routing_outcome: RoutingOutcome | None = None
    decision_mechanism: Literal["deterministic", "generation"] | None = None
    summary_for_requester: Annotated[str, StringConstraints(max_length=2000)] | None = None
    summary_for_ticket: Annotated[str, StringConstraints(max_length=4000)] | None = None
    questions: list[RunQuestion] = Field(default_factory=list, max_length=10)
    steps: list[ReviewedStep] = Field(default_factory=list, max_length=10)
    evidence: list[EvidenceSnapshot] = Field(default_factory=list, max_length=20)
    warnings: list[Annotated[str, StringConstraints(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=20)
    error_code: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")] | None = None
    error_message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    prompt_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "RunResult":
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        if self.status == "running" and self.completed_at is not None:
            raise ValueError("running results cannot have completed_at")
        if self.status == "failed" and (not self.error_code or self.completed_at is None):
            raise ValueError("failed results require an error_code and completed_at")
        if self.status == "completed" and (
            self.completed_at is None or self.routing_outcome is None or self.decision_mechanism is None
        ):
            raise ValueError("completed results require completed_at, routing_outcome, and decision_mechanism")
        if self.status != "failed" and (self.error_code or self.error_message):
            raise ValueError("only failed results may include error details")
        if self.routing_outcome == "l1_guided" and not self.steps:
            raise ValueError("guided results require at least one reviewed step")
        evidence_ids = {item.evidence_id for item in self.evidence}
        if any(not set(step.evidence_ids) <= evidence_ids for step in self.steps):
            raise ValueError("step citations must reference evidence in this run")
        return self


RunReadResult = RunResult | LegacyRunResult


class RunPage(RunModel):
    runs: list[RunReadResult] = Field(max_length=100)
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)

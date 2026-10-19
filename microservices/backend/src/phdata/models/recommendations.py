"""Allowlisted provider input/output contracts for recommendation selection."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class RecommendationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
EvidenceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class RecommendationCandidateStep(RecommendationModel):
    step_id: Identifier
    instruction: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=10)


class RecommendationEvidence(RecommendationModel):
    evidence_id: EvidenceId
    source_path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    section: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    content: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    applicability: Literal["context_match", "unknown"]


class RecommendationInput(RecommendationModel):
    category: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    symptom: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    verified_model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    verified_system_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    candidate_steps: list[RecommendationCandidateStep] = Field(min_length=1, max_length=10)
    evidence: list[RecommendationEvidence] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def candidates_reference_supplied_evidence(self) -> "RecommendationInput":
        evidence_ids = {item.evidence_id for item in self.evidence}
        if any(not set(step.evidence_ids) <= evidence_ids for step in self.candidate_steps):
            raise ValueError("candidate step references evidence not supplied in this request")
        if len({step.step_id for step in self.candidate_steps}) != len(self.candidate_steps):
            raise ValueError("candidate step IDs must be unique")
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("evidence IDs must be unique")
        return self


class SelectedRecommendationStep(RecommendationModel):
    step_id: Identifier
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=10)


class RecommendationOutput(RecommendationModel):
    outcome: Literal["l1_guided", "l2_escalation"]
    selected_steps: list[SelectedRecommendationStep] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_selection_shape(self) -> "RecommendationOutput":
        step_ids = [step.step_id for step in self.selected_steps]
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("selected step IDs must be unique")
        if self.outcome == "l1_guided" and not self.selected_steps:
            raise ValueError("guided output requires at least one cited candidate step")
        if self.outcome == "l2_escalation" and self.selected_steps:
            raise ValueError("escalation output cannot select guidance steps")
        return self

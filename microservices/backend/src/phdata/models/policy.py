"""Typed declarations for content reviewed for narrowly scoped operator guidance."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


PolicyId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ReviewedPolicyStep(PolicyModel):
    step_id: PolicyId
    policy_id: PolicyId
    policy_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    category: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    symptom_scope: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    operator_role: Literal["store_operator"]
    source_path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    section: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    approved_content_sha256: Sha256
    disposition: Literal["guidance", "escalation"]
    instruction: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    allowed_models: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]] = Field(default_factory=list, max_length=20)
    allowed_system_versions: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]] = Field(default_factory=list, max_length=20)

    @field_validator("source_path")
    @classmethod
    def source_path_is_relative(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("source_path must be a relative source path")
        return value


class ReviewedPolicy(PolicyModel):
    policy_id: PolicyId
    policy_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    category: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    symptom_scope: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    steps: list[ReviewedPolicyStep] = Field(min_length=1, max_length=50)

    @field_validator("steps")
    @classmethod
    def steps_match_policy(cls, steps: list[ReviewedPolicyStep]) -> list[ReviewedPolicyStep]:
        step_ids = [step.step_id for step in steps]
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("step IDs must be unique within a policy")
        return steps

    @field_validator("steps")
    @classmethod
    def policy_scope_is_consistent(cls, steps: list[ReviewedPolicyStep], info):
        values = info.data
        for step in steps:
            if step.policy_id != values.get("policy_id"):
                raise ValueError("step policy_id must match its policy")
            if step.policy_version != values.get("policy_version"):
                raise ValueError("step policy_version must match its policy")
            if step.category != values.get("category"):
                raise ValueError("step category must match its policy")
            if step.symptom_scope != values.get("symptom_scope"):
                raise ValueError("step symptom_scope must match its policy")
        return steps

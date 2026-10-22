"""Small helpers that enforce exact source identity for reviewed policy steps."""

from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import Field

from phdata.models.context import IncidentContext
from phdata.models.policy import PolicyModel, ReviewedPolicy, ReviewedPolicyStep


# The pilot cannot authorize operational changes, even through a reviewed file.
PILOT_OBSERVATIONS = frozenset({
    "Confirm the printer power indicator is illuminated.",
    "Observe the printer status display without changing settings.",
})
MAX_POLICY_BYTES = 262144


class PolicyConfigurationError(Exception):
    """A configured policy file could not be safely loaded."""


class ReviewedPolicyFile(PolicyModel):
    schema_version: Literal[1]
    policies: list[ReviewedPolicy] = Field(min_length=1, max_length=20)


def load_reviewed_policies(path: Path | None) -> tuple[ReviewedPolicy, ...]:
    """No built-in approval: only an explicit, strictly validated local file opts in."""
    if path is None:
        return ()
    try:
        with path.open("rb") as source:
            content = source.read(MAX_POLICY_BYTES + 1)
        if len(content) > MAX_POLICY_BYTES:
            raise ValueError("policy file exceeds size limit")
        bundle = ReviewedPolicyFile.model_validate_json(content)
        identities = [(policy.policy_id, policy.policy_version) for policy in bundle.policies]
        scopes = [(policy.category.casefold(), policy.symptom_scope.casefold()) for policy in bundle.policies]
        if len(set(identities)) != len(identities) or len(set(scopes)) != len(scopes):
            raise ValueError("duplicate or overlapping policies")
        for policy in bundle.policies:
            if policy.review is None or not policy_is_observation_only(policy):
                raise ValueError("policy requires explicit review and observation-only steps")
        return tuple(bundle.policies)
    except (OSError, ValueError):
        raise PolicyConfigurationError("Reviewed support policy configuration is invalid.") from None


def policy_is_observation_only(policy: ReviewedPolicy) -> bool:
    return all(
        step.disposition == "escalation"
        or (step.instruction in PILOT_OBSERVATIONS and step.applicability == "exact")
        for step in policy.steps
    )


def policy_applies_to_report(policy: ReviewedPolicy, context: IncidentContext, *, now: datetime) -> bool:
    review = policy.review
    if review is None or not review.reviewed_at <= now < review.expires_at:
        return False
    if not policy_is_observation_only(policy):
        return False
    incident = context.incident
    # Free text is not classified by a model or category keywords: unseen wording fails closed.
    if not any(
        incident.subject == report.subject and incident.description == report.description
        for report in review.approved_reports
    ):
        return False
    if context.store is None or context.asset is None or incident.escalation_flag:
        return False
    if context.history or context.related:
        return False
    if incident.system_version and incident.system_version != context.asset.system_version:
        return False
    return True


def policy_provenance(policy: ReviewedPolicy) -> str:
    digest = sha256(policy.model_dump_json().encode("utf-8")).hexdigest()
    return f"Reviewed policy {policy.policy_id}@{policy.policy_version}; declaration SHA256 {digest}."


def reviewed_source_matches(
    step: ReviewedPolicyStep,
    *,
    source_path: str,
    section: str,
    content: str,
) -> bool:
    """Return true only when the reviewed path, section, and exact content are unchanged."""
    return (
        step.source_path == source_path
        and step.section == section
        and sha256(content.encode("utf-8")).hexdigest() == step.approved_content_sha256
    )

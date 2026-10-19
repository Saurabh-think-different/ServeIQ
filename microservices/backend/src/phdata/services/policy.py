"""Small helpers that enforce exact source identity for reviewed policy steps."""

from hashlib import sha256

from phdata.models.policy import ReviewedPolicyStep


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

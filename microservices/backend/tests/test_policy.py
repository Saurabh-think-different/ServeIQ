from hashlib import sha256

import pytest
from pydantic import ValidationError

from phdata.models.policy import ReviewedPolicy
from phdata.services.policy import reviewed_source_matches

SOURCE = "Confirm the printer is powered on."
SOURCE_PATH = "kb/runbooks/printer-offline.md"
SECTION = "Check printer connectivity"


def synthetic_policy():
    return ReviewedPolicy.model_validate({
        "policy_id": "test-printer-policy",
        "policy_version": "test-v1",
        "category": "Printers",
        "symptom_scope": "Printer Offline or Not Detected",
        "steps": [{
            "step_id": "printer.power",
            "policy_id": "test-printer-policy",
            "policy_version": "test-v1",
            "category": "Printers",
            "symptom_scope": "Printer Offline or Not Detected",
            "operator_role": "store_operator",
            "source_path": SOURCE_PATH,
            "section": SECTION,
            "approved_content_sha256": sha256(SOURCE.encode()).hexdigest(),
            "disposition": "guidance",
            "instruction": "Confirm the printer is powered on.",
        }],
    })


def test_policy_step_requires_exact_reviewed_source_identity():
    step = synthetic_policy().steps[0]
    assert reviewed_source_matches(step, source_path=SOURCE_PATH, section=SECTION, content=SOURCE)
    assert not reviewed_source_matches(step, source_path=SOURCE_PATH, section=SECTION, content=SOURCE + " Check status lights.")
    assert not reviewed_source_matches(step, source_path=SOURCE_PATH, section="Other section", content=SOURCE)
    assert not reviewed_source_matches(step, source_path="kb/other.md", section=SECTION, content=SOURCE)


def test_policy_rejects_duplicate_or_mismatched_steps():
    policy = synthetic_policy().model_dump()
    policy["steps"].append(policy["steps"][0].copy())
    with pytest.raises(ValidationError, match="unique"):
        ReviewedPolicy.model_validate(policy)

    policy = synthetic_policy().model_dump()
    policy["steps"][0]["policy_version"] = "unreviewed-v2"
    with pytest.raises(ValidationError, match="policy_version must match"):
        ReviewedPolicy.model_validate(policy)


def test_policy_rejects_unbounded_or_unsafe_source_paths():
    policy = synthetic_policy().model_dump()
    policy["steps"][0]["source_path"] = "../unreviewed.md"
    with pytest.raises(ValidationError, match="relative source path"):
        ReviewedPolicy.model_validate(policy)

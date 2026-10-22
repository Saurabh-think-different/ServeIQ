"""Explicitly opted-in provider checks using invented text only; two calls total."""

from dataclasses import replace
from hashlib import sha256
import os

from openai import OpenAI
import pytest

from phdata.config import get_settings
from phdata.models.recommendations import RecommendationInput
from phdata.services.recommendations import OpenAIRecommendationAdapter, RecommendationError


pytestmark = pytest.mark.skipif(
    os.getenv("SERVEIQ_LIVE_OPENAI") != "1",
    reason="Set SERVEIQ_LIVE_OPENAI=1 explicitly to authorize two synthetic provider calls.",
)


def live_settings():
    settings = replace(get_settings(), generation_timeout_seconds=60)
    if not settings.openai_api_key or not settings.generation_model:
        pytest.fail("Configure OPENAI_API_KEY and GENERATION_MODEL for live tests.", pytrace=False)
    return settings


def test_live_synthetic_embedding():
    settings = live_settings()
    with OpenAI(api_key=settings.openai_api_key, max_retries=0, timeout=60) as client:
        try:
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=["Invented synthetic example: a demonstration printer displays an offline indicator."],
            )
        except Exception as error:
            pytest.fail(f"Live embedding failed ({type(error).__name__}); provider details omitted.", pytrace=False)
    assert len(response.data) == 1
    assert len(response.data[0].embedding) == 1536
    assert response.usage.total_tokens > 0


def test_live_production_recommendation_adapter():
    settings = live_settings()
    content = (
        "Synthetic demonstration only: observe the synthetic printer status indicator "
        "without changing settings."
    )
    evidence_id = sha256(content.encode()).hexdigest()
    request = RecommendationInput(
        category="Synthetic printer",
        symptom="Synthetic offline indicator",
        candidate_steps=[{
            "step_id": "synthetic.observe",
            "instruction": "Observe the synthetic printer status indicator without changing settings.",
            "evidence_ids": [evidence_id],
        }],
        evidence=[{
            "evidence_id": evidence_id,
            "source_path": "synthetic-only/observation.txt",
            "section": "Synthetic example",
            "content": content,
            "applicability": "context_match",
        }],
    )
    try:
        result = OpenAIRecommendationAdapter(settings).recommend(request)
    except RecommendationError as error:
        pytest.fail(f"Live recommendation failed ({error.error_code}).", pytrace=False)
    assert result.outcome in {"l1_guided", "l2_escalation"}
    for step in result.selected_steps:
        assert step.step_id == "synthetic.observe"
        assert step.evidence_ids == [evidence_id]

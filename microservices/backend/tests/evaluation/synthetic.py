"""Execute real workflow/persistence against synthetic-only inputs and bounded fakes."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from phdata.config import Settings
from phdata.db.database import open_database
from phdata.models.policy import ReviewedPolicy
from phdata.models.retrieval import Passage, SearchResponse
from phdata.services.recommendations import RecommendationError
from phdata.services.retrieval import RetrievalError
from phdata.services.workflow import SupportWorkflow

from evaluation.pilot import EvaluationExpectation, EvaluationObservation

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
SUBJECT = "Synthetic printer offline"
DESCRIPTION = "Synthetic receipt printer is not detected."
CONTENT = "Synthetic reference: confirm the printer power indicator is illuminated."
SOURCE = "kb/synthetic/printer.md"
SECTION = "Synthetic observation"


def synthetic_settings(directory: Path) -> Settings:
    return Settings(
        host="127.0.0.1", port=8000, data_dir=directory,
        database_path=directory / "state.db", qdrant_url="http://127.0.0.1:6333",
    )


def synthetic_policy() -> ReviewedPolicy:
    return ReviewedPolicy.model_validate({
        "policy_id": "synthetic-observation", "policy_version": "test-v1",
        "category": "Printers", "symptom_scope": "Offline",
        "review": {
            "reviewed_by": "synthetic-test-reviewer",
            "approval_reference": "SYNTHETIC-ONLY-NOT-CLIENT-APPROVAL",
            "reviewed_at": (NOW - timedelta(days=1)).isoformat(),
            "expires_at": (NOW + timedelta(days=30)).isoformat(),
            "scope": "non_disruptive_observation_only",
            "conflict_check": "no_unresolved_conflicts_for_selected_steps",
            "applicability_rationale": "Synthetic fixture only; no client knowledge is approved.",
            "approved_reports": [{"subject": SUBJECT, "description": DESCRIPTION}],
        },
        "steps": [{
            "step_id": "synthetic.power-indicator", "policy_id": "synthetic-observation",
            "policy_version": "test-v1", "category": "Printers", "symptom_scope": "Offline",
            "operator_role": "store_operator", "source_path": SOURCE, "section": SECTION,
            "approved_content_sha256": sha256(CONTENT.encode()).hexdigest(),
            "disposition": "guidance", "instruction": "Confirm the printer power indicator is illuminated.",
            "applicability": "exact", "allowed_models": ["SYNTHETIC-PRINTER"],
            "allowed_system_versions": ["synthetic-1"],
        }],
    })


def seed_synthetic(path: Path, *, description=DESCRIPTION, category="Printers", model="SYNTHETIC-PRINTER") -> None:
    connection = open_database(path)
    try:
        connection.execute(
            "INSERT INTO stores (store_id, store_name, city, neighborhood, store_type) "
            "VALUES ('SYN-S1', 'Synthetic Store', 'Synthetic City', 'Synthetic Area', 'corporate')",
        )
        connection.execute(
            "INSERT INTO assets (asset_id, store_id, asset_type, make, model, system_version) "
            "VALUES ('SYN-A1', 'SYN-S1', 'Receipt Printer', 'Synthetic', ?, 'synthetic-1')", (model,),
        )
        connection.execute(
            "INSERT INTO incidents (ticket_id, reported_store_id, store_id, reported_asset_id, asset_id, "
            "opened_at, category, subcategory, status, subject, description, system_version) "
            "VALUES ('SYN-INC', 'SYN-S1', 'SYN-S1', 'SYN-A1', 'SYN-A1', ?, ?, 'Offline', 'Open', ?, ?, 'synthetic-1')",
            (NOW.isoformat(), category, SUBJECT, description),
        )
        connection.commit()
    finally:
        connection.close()


class SyntheticRetrieval:
    def __init__(self, *, error=None, content=CONTENT):
        self.calls = []
        self.error = error
        self.content = content

    def search(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return SearchResponse(passages=[Passage(
            content=self.content, source_path=SOURCE, section=SECTION, similarity_score=0.9,
            category="Printers", model="SYNTHETIC-PRINTER", system_version="synthetic-1",
            document_type="synthetic", applicability="context_match", applicability_notes=[],
        )], evidence_gap=False, warnings=[])


class SyntheticFailedRecommender:
    model = "gpt-6-luna"
    prompt_version = "synthetic-failure-v1"

    def recommend(self, request):
        raise RecommendationError("generation_failed", "Support generation could not complete safely.")


def execute_synthetic_pilot(directory: Path) -> list[EvaluationObservation]:
    """No providers, environment keys, dataset files, labels, or authored run outputs."""
    cases = [
        ("guided", "l1_guided", None),
        ("unclear", "needs_clarification", None),
        ("out_of_scope", "l2_escalation", None),
        ("contradictory", "l2_escalation", None),
        ("ambiguous", "l2_escalation", None),
        ("incompatible_model", "l2_escalation", None),
        ("conflicting_policies", "l2_escalation", None),
        ("expired_review", "l2_escalation", None),
        ("unreviewed", "l2_escalation", None),
        ("privileged", "l2_escalation", None),
        ("changed_source", "l2_escalation", None),
        ("retrieval_failure", None, "knowledge_unavailable"),
        ("generation_failure", None, "generation_failed"),
    ]
    observations = []
    for case_id, expected, error_code in cases:
        case_directory = directory / case_id
        case_directory.mkdir(parents=True)
        settings = synthetic_settings(case_directory)
        seed_synthetic(
            settings.database_path,
            category=None if case_id == "unclear" else ("POS" if case_id == "out_of_scope" else "Printers"),
            description=("Printer works; WiFi is down." if case_id == "contradictory" else (
                "Something sometimes fails." if case_id == "ambiguous" else DESCRIPTION
            )),
            model="OTHER-SYNTHETIC-MODEL" if case_id == "incompatible_model" else "SYNTHETIC-PRINTER",
        )
        policy_data = synthetic_policy().model_dump(mode="json")
        if case_id == "expired_review":
            policy_data["review"]["expires_at"] = NOW.isoformat()
        if case_id == "unreviewed":
            policy_data["review"] = None
        if case_id == "privileged":
            policy_data["steps"][0]["instruction"] = "Restart the print spooler as administrator."
        policies = [ReviewedPolicy.model_validate(policy_data)]
        if case_id == "conflicting_policies":
            policies.append(policies[0])
        retrieval = SyntheticRetrieval(
            error=RetrievalError(503, "Synthetic private provider exception") if case_id == "retrieval_failure" else None,
            content=CONTENT + " Changed." if case_id == "changed_source" else CONTENT,
        )
        service = SupportWorkflow(
            settings, policies=policies, retrieval=retrieval,
            recommender=SyntheticFailedRecommender() if case_id == "generation_failure" else None,
            clock=lambda: NOW,
        )
        run, _ = service.start_run("SYN-INC", idempotency_key=case_id)
        assert service.get_run(run.run_id) == run
        observations.append(EvaluationObservation(
            expectation=EvaluationExpectation(
                case_id=case_id, pilot_eligible=case_id != "out_of_scope",
                expected_routing=expected, expected_error=error_code,
            ),
            run=run,
        ))
    return observations

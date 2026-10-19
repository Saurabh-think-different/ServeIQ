"""Bounded support-run orchestration with exact, reviewed evidence only."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from hashlib import sha256
from typing import Callable, Literal, Protocol, Sequence

from pydantic import ValidationError

from phdata.config import Settings
from phdata.db.database import open_database
from phdata.models.actions import AuditPayload
from phdata.models.context import IncidentContext
from phdata.models.policy import ReviewedPolicy, ReviewedPolicyStep
from phdata.models.recommendations import (
    RecommendationCandidateStep,
    RecommendationEvidence,
    RecommendationInput,
    RecommendationOutput,
)
from phdata.models.retrieval import SearchRequest, SearchResponse
from phdata.models.runs import (
    EvidenceSnapshot,
    RunQuestion,
    RunResult,
    ReviewedStep,
    evidence_id_for,
)
from phdata.repositories.actions import append_audit_event
from phdata.repositories.incidents import get_incident
from phdata.repositories.runs import (
    finalize_run,
    find_run_by_idempotency_key,
    get_run,
    insert_run,
    list_runs,
)
from phdata.repositories.support import SupportRepository
from phdata.services.policy import reviewed_source_matches
from phdata.services.recommendations import RecommendationError
from phdata.services.retrieval import KnowledgeRetrieval, RetrievalError


class RetrievalPort(Protocol):
    def search(self, request: SearchRequest) -> SearchResponse: ...


class RecommenderPort(Protocol):
    model: str | None
    prompt_version: str

    def recommend(self, request: RecommendationInput) -> RecommendationOutput: ...


class IncidentNotFound(Exception):
    """The requested incident does not exist."""


class IdempotencyConflict(Exception):
    """An idempotency key was reused for a different normalized request."""


class SupportWorkflow:
    """Synchronous workflow; provider calls run outside SQLite write transactions."""

    def __init__(
        self,
        settings: Settings,
        *,
        retrieval: RetrievalPort | None = None,
        recommender: RecommenderPort | None = None,
        policies: Sequence[ReviewedPolicy] = (),
        connection_factory: Callable[[], sqlite3.Connection] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.retrieval = retrieval or KnowledgeRetrieval(settings)
        self.recommender = recommender
        self.policies = tuple(policies)
        self.connection_factory = connection_factory or self._open_database
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _open_database(self) -> sqlite3.Connection:
        self.settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        return open_database(self.settings.database_path)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("workflow clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def get_run(self, run_id: str) -> RunResult | None:
        connection = self.connection_factory()
        try:
            return get_run(connection, run_id)
        finally:
            connection.close()

    def list_runs(self, ticket_id: str, *, limit: int, offset: int):
        connection = self.connection_factory()
        try:
            return list_runs(connection, ticket_id=ticket_id, limit=limit, offset=offset)
        finally:
            connection.close()

    def start_run(self, ticket_id: str, *, idempotency_key: str) -> tuple[RunResult, bool]:
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise ValueError("Idempotency-Key must contain 1 to 128 nonblank characters")
        request_fingerprint = sha256(b"{}").hexdigest()
        started_at = self._now()

        connection = self.connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if get_incident(connection, ticket_id) is None:
                connection.rollback()
                raise IncidentNotFound
            replay = find_run_by_idempotency_key(
                connection, ticket_id=ticket_id, idempotency_key=idempotency_key,
            )
            if replay is not None:
                connection.commit()
                existing, fingerprint = replay
                if fingerprint != request_fingerprint:
                    raise IdempotencyConflict
                return existing, True

            run_identity = "\0".join((ticket_id, idempotency_key, started_at.isoformat()))
            run_id = f"RUN-{sha256(run_identity.encode()).hexdigest()[:32]}"
            result = insert_run(
                connection,
                run_id=run_id,
                ticket_id=ticket_id,
                started_at=started_at.isoformat(),
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            append_audit_event(
                connection,
                ticket_id=ticket_id,
                run_id=run_id,
                actor_type="agent",
                actor_id="support_workflow",
                event_type="run_started",
                occurred_at=started_at.isoformat(),
                approval_status="not_required",
                payload=AuditPayload(summary="Support run started"),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

        return self._process(run=result)

    def _process(self, *, run: RunResult) -> tuple[RunResult, bool]:
        snapshot: dict[str, object] = {"version": 1}
        fingerprint = context_fingerprint(snapshot)
        try:
            connection = self.connection_factory()
            try:
                context = SupportRepository(connection).get_context(run.ticket_id, now=self._now())
            finally:
                connection.close()
            if context is None:
                raise IncidentNotFound
            snapshot = build_context_snapshot(context)
            fingerprint = context_fingerprint(snapshot)
            result = self._recommend(run, context)
        except IncidentNotFound:
            result = self._failure(run, "context_unavailable", "Incident context is unavailable.")
        except RetrievalError as error:
            code, message = (
                ("knowledge_conflict", "Knowledge evidence is not compatible with the configured index.")
                if error.status_code == 409
                else ("knowledge_unavailable", "Knowledge search is temporarily unavailable.")
            )
            result = self._failure(run, code, message)
        except RecommendationError as error:
            result = self._failure(run, error.error_code, error.safe_message)
        except Exception:
            result = self._failure(run, "workflow_failed", "Support analysis could not be completed safely.")
        return self._finalize(run, result, snapshot, fingerprint), False

    def _recommend(self, run: RunResult, context: IncidentContext) -> RunResult:
        incident = context.incident
        if not incident.category or not incident.subcategory:
            questions = []
            if not incident.category:
                questions.append(RunQuestion(
                    question_id="confirm_category",
                    text="Please confirm the affected device category.",
                ))
            if not incident.subcategory:
                questions.append(RunQuestion(
                    question_id="confirm_symptom",
                    text="Please describe the specific device symptom.",
                ))
            return self._completed(
                run,
                "needs_clarification",
                requester="Please provide the missing incident details before support guidance is selected.",
                ticket="Clarification is needed before selecting reviewed support guidance.",
                questions=questions,
                warnings=context.warnings,
            )

        matching = [
            policy for policy in self.policies
            if _same(incident.category, policy.category)
            and _same(incident.subcategory, policy.symptom_scope)
        ]
        if len(matching) != 1:
            reason = (
                "No reviewed support policy is configured for this pilot."
                if not self.policies
                else "No unique reviewed policy matches the exact reported category and symptom."
            )
            return self._completed(
                run,
                "l2_escalation",
                requester="No reviewed applicable instructions are available. Please contact human support.",
                ticket="Human support review is required; no troubleshooting steps were selected.",
                warnings=[*context.warnings, reason],
            )

        policy = matching[0]
        candidates, questions = _applicable_steps(policy.steps, context)
        if questions:
            return self._completed(
                run,
                "needs_clarification",
                requester="Please provide the missing device details before selecting applicable guidance.",
                ticket="A reviewed step requires device details that are not verified in the incident context.",
                questions=questions,
                warnings=context.warnings,
            )
        if not candidates:
            return self._completed(
                run,
                "l2_escalation",
                requester="No reviewed applicable instructions are available. Please contact human support.",
                ticket="No reviewed step applies to the exact verified device context.",
                warnings=[*context.warnings, "No reviewed policy step matches the verified model/version."],
            )
        if any(step.disposition == "escalation" for step in candidates):
            return self._completed(
                run,
                "l2_escalation",
                requester="The reviewed policy requires human support. No device changes were made.",
                ticket="A matching reviewed policy step requires human support.",
                warnings=context.warnings,
            )

        search = self.retrieval.search(_search_request(policy, context))
        evidence_by_id: dict[str, EvidenceSnapshot] = {}
        steps: list[ReviewedStep] = []
        warnings = list(context.warnings) + list(search.warnings)
        for step in candidates:
            evidence_ids = []
            for passage in search.passages:
                if not _passage_applies(passage, step, context):
                    continue
                content_hash = sha256(passage.content.encode("utf-8")).hexdigest()
                evidence_id = evidence_id_for(passage.source_path, passage.section, content_hash)
                if len(passage.content) > 8000:
                    warnings.append("A reviewed source passage exceeded the evidence snapshot limit and was omitted.")
                    continue
                evidence_by_id[evidence_id] = EvidenceSnapshot(
                    evidence_id=evidence_id,
                    source_path=passage.source_path,
                    section=passage.section,
                    content=passage.content,
                    content_sha256=content_hash,
                    category=passage.category,
                    model=passage.model,
                    system_version=passage.system_version,
                    document_type=passage.document_type,
                    applicability=passage.applicability,
                    applicability_notes=passage.applicability_notes,
                    similarity_score=passage.similarity_score,
                )
                evidence_ids.append(evidence_id)
            if evidence_ids:
                steps.append(ReviewedStep(
                    step_id=step.step_id,
                    instruction=step.instruction,
                    evidence_ids=list(dict.fromkeys(evidence_ids))[:10],
                    operator_role=step.operator_role,
                ))
        if not steps:
            return self._completed(
                run,
                "l2_escalation",
                requester="No reviewed source matches the approved guidance. Please contact human support.",
                ticket="Retrieved material did not match an unchanged, reviewed source section; no steps were selected.",
                evidence=[],
                warnings=[*warnings, "Evidence gap: no exact reviewed source hash matched."],
            )
        evidence = list(evidence_by_id.values())[:10]
        if self.recommender is not None:
            return self._recommend_from_model(run, context, policy, steps[:10], evidence, warnings)
        return self._completed(
            run,
            "l1_guided",
            requester="Follow only the reviewed checks listed below. No device changes were made.",
            ticket="Deterministic selection of exact, reviewed guidance; no action was executed.",
            steps=steps[:10],
            evidence=evidence,
            warnings=warnings[:20],
        )

    def _recommend_from_model(
        self,
        run: RunResult,
        context: IncidentContext,
        policy: ReviewedPolicy,
        candidates: list[ReviewedStep],
        evidence: list[EvidenceSnapshot],
        warnings: list[str],
    ) -> RunResult:
        model = self.recommender.model
        prompt_version = self.recommender.prompt_version
        if not model or not prompt_version:
            raise RecommendationError("generation_not_configured", "Support generation is not configured.")
        if any(len(item.content) > 4000 for item in evidence):
            raise RecommendationError("generation_input_too_large", "Approved source text exceeds the generation input limit.")
        request = RecommendationInput(
            category=policy.category,
            symptom=policy.symptom_scope,
            verified_model=context.asset.model if context.asset else None,
            verified_system_version=context.asset.system_version if context.asset else None,
            candidate_steps=[
                RecommendationCandidateStep(
                    step_id=step.step_id,
                    instruction=step.instruction,
                    evidence_ids=step.evidence_ids,
                )
                for step in candidates
            ],
            evidence=[
                RecommendationEvidence(
                    evidence_id=item.evidence_id,
                    source_path=item.source_path,
                    section=item.section,
                    content=item.content,
                    applicability=item.applicability,
                )
                for item in evidence
            ],
        )
        try:
            selection = RecommendationOutput.model_validate(self.recommender.recommend(request))
        except ValidationError:
            raise RecommendationError("invalid_model_output", "Support generation returned an invalid selection.") from None
        candidate_by_id = {step.step_id: step for step in candidates}
        evidence_by_id = {item.evidence_id: item for item in evidence}
        selected_steps = []
        selected_evidence_ids = set()
        for selected in selection.selected_steps:
            candidate = candidate_by_id.get(selected.step_id)
            if candidate is None or not set(selected.evidence_ids) <= set(candidate.evidence_ids):
                raise RecommendationError("invalid_model_output", "Support generation selected an unknown step or citation.")
            selected_steps.append(ReviewedStep(
                step_id=candidate.step_id,
                instruction=candidate.instruction,
                evidence_ids=selected.evidence_ids,
                operator_role="store_operator",
            ))
            selected_evidence_ids.update(selected.evidence_ids)
        if not selected_evidence_ids <= evidence_by_id.keys():
            raise RecommendationError("invalid_model_output", "Support generation cited evidence outside this run.")

        if selection.outcome == "l2_escalation":
            return self._completed(
                run,
                "l2_escalation",
                requester="The reviewed policy selected human support. No device changes were made.",
                ticket="Generation selected the supported escalation outcome; no action was executed.",
                warnings=warnings[:20],
                decision_mechanism="generation",
                model=model,
                prompt_version=prompt_version,
            )
        if not selected_steps or not selected_evidence_ids:
            raise RecommendationError("invalid_model_output", "Guided support output requires a cited reviewed step.")
        return self._completed(
            run,
            "l1_guided",
            requester="Follow only the reviewed checks listed below. No device changes were made.",
            ticket="Generation selected exact reviewed steps; no action was executed.",
            steps=selected_steps,
            evidence=[item for item in evidence if item.evidence_id in selected_evidence_ids],
            warnings=warnings[:20],
            decision_mechanism="generation",
            model=model,
            prompt_version=prompt_version,
        )

    def _completed(
        self,
        run: RunResult,
        outcome: str,
        *,
        requester: str,
        ticket: str,
        questions: list[RunQuestion] | None = None,
        steps: list[ReviewedStep] | None = None,
        evidence: list[EvidenceSnapshot] | None = None,
        warnings: list[str] | None = None,
        decision_mechanism: Literal["deterministic", "generation"] = "deterministic",
        model: str | None = None,
        prompt_version: str | None = None,
    ) -> RunResult:
        return RunResult(
            run_id=run.run_id,
            ticket_id=run.ticket_id,
            status="completed",
            started_at=run.started_at,
            completed_at=self._now(),
            routing_outcome=outcome,
            decision_mechanism=decision_mechanism,
            summary_for_requester=requester,
            summary_for_ticket=ticket,
            questions=(questions or [])[:10],
            steps=(steps or [])[:10],
            evidence=(evidence or [])[:20],
            warnings=(warnings or [])[:20],
            model=model,
            prompt_version=prompt_version,
        )

    def _failure(self, run: RunResult, code: str, message: str) -> RunResult:
        return RunResult(
            run_id=run.run_id,
            ticket_id=run.ticket_id,
            status="failed",
            started_at=run.started_at,
            completed_at=self._now(),
            error_code=code,
            error_message=message,
        )

    def _finalize(
        self,
        run: RunResult,
        result: RunResult,
        snapshot: dict[str, object],
        fingerprint: str,
    ) -> RunResult:
        connection = self.connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current_context = SupportRepository(connection).get_context(run.ticket_id, now=self._now())
            if current_context is None or context_fingerprint(build_context_snapshot(current_context)) != fingerprint:
                result = self._failure(
                    run,
                    "context_changed",
                    "Incident context changed during analysis. Start a new run to review current facts.",
                )
            finalize_run(
                connection,
                result,
                context_fingerprint=fingerprint,
                context_snapshot_json=json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
            )
            event_type = "run_completed" if result.status == "completed" else "run_failed"
            event_summary = (
                f"Run completed with outcome {result.routing_outcome}."
                if result.status == "completed"
                else f"Run failed with code {result.error_code}."
            )
            append_audit_event(
                connection,
                ticket_id=run.ticket_id,
                run_id=run.run_id,
                actor_type="agent",
                actor_id="support_workflow",
                event_type=event_type,
                occurred_at=self._now().isoformat(),
                approval_status="not_required" if result.status == "completed" else "failed",
                payload=AuditPayload(summary=event_summary, error_code=result.error_code),
            )
            if result.status == "completed":
                from phdata.services.actions import create_outcome_proposal

                create_outcome_proposal(
                    connection,
                    result,
                    context_fingerprint_value=fingerprint,
                    created_at=self._now(),
                )
            connection.commit()
            return result
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()


def _same(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()


def _applicable_steps(
    steps: Sequence[ReviewedPolicyStep],
    context: IncidentContext,
) -> tuple[list[ReviewedPolicyStep], list[RunQuestion]]:
    candidates = []
    questions: dict[str, RunQuestion] = {}
    asset = context.asset
    for step in steps:
        if step.disposition == "escalation":
            candidates.append(step)
            continue
        if step.applicability == "explicit_generic":
            candidates.append(step)
            continue
        if step.allowed_models and asset is None:
            questions["confirm_printer_model"] = RunQuestion(
                question_id="confirm_printer_model",
                text="Please confirm the printer make and model from its label.",
            )
            continue
        if step.allowed_system_versions and (asset is None or not asset.system_version):
            questions["confirm_system_version"] = RunQuestion(
                question_id="confirm_system_version",
                text="Please confirm the printer system or firmware version from its settings.",
            )
            continue
        if asset is None:
            continue
        if step.allowed_models and asset.model not in step.allowed_models:
            continue
        if step.allowed_system_versions and asset.system_version not in step.allowed_system_versions:
            continue
        candidates.append(step)
    return candidates, list(questions.values())


def _search_request(policy: ReviewedPolicy, context: IncidentContext) -> SearchRequest:
    asset = context.asset
    return SearchRequest(
        query=f"{policy.category} {policy.symptom_scope}",
        limit=10,
        category=policy.category,
        model=asset.model if asset is not None else None,
        system_version=asset.system_version if asset is not None else None,
    )


def _passage_applies(passage, step: ReviewedPolicyStep, context: IncidentContext) -> bool:
    if not reviewed_source_matches(
        step,
        source_path=passage.source_path,
        section=passage.section,
        content=passage.content,
    ):
        return False
    asset = context.asset
    if passage.model and (asset is None or passage.model != asset.model):
        return False
    if passage.system_version and (asset is None or passage.system_version != asset.system_version):
        return False
    if passage.applicability == "unknown" and step.applicability != "explicit_generic":
        return False
    return True


def build_context_snapshot(context: IncidentContext) -> dict[str, object]:
    incident = context.incident.model_dump(mode="json", exclude={"escalation_flag"})
    asset = context.asset.model_dump(mode="json", exclude={"serial_number"}) if context.asset else None
    return {
        "version": 1,
        "incident": incident,
        "store": context.store.model_dump(mode="json") if context.store else None,
        "asset": asset,
        "sla": context.sla.model_dump(mode="json") if context.sla else None,
        "history": [event.model_dump(mode="json") for event in context.history],
        "related": [reference.model_dump(mode="json") for reference in context.related],
        "missing": [value for value in context.missing if value != "valid_now"],
    }


def context_fingerprint(snapshot: dict[str, object]) -> str:
    normalized = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(normalized.encode("utf-8")).hexdigest()


def recover_interrupted_runs(
    connection: sqlite3.Connection,
    *,
    now: datetime,
) -> int:
    """Fail orphaned running rows once; callers own the transaction and audit events."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("recovery timestamp must be timezone-aware")
    rows = connection.execute(
        "SELECT run_id, ticket_id, started_at FROM agent_runs WHERE status = 'running' "
        "ORDER BY started_at, run_id",
    ).fetchall()
    for row in rows:
        started_at = datetime.fromisoformat(row["started_at"])
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            raise ValueError("orphaned run has a naive start timestamp")
        completed_at = max(now.astimezone(timezone.utc), started_at.astimezone(timezone.utc))
        result = RunResult(
            run_id=row["run_id"],
            ticket_id=row["ticket_id"],
            status="failed",
            started_at=started_at,
            completed_at=completed_at,
            error_code="interrupted",
            error_message="The previous run was interrupted before it could finish. Start a new run.",
        )
        connection.execute(
            "UPDATE agent_runs SET completed_at=?, status='failed', error_code=?, response_json=? "
            "WHERE run_id=? AND status='running'",
            (result.completed_at.isoformat(), result.error_code, result.model_dump_json(), row["run_id"]),
        )
        append_audit_event(
            connection,
            ticket_id=row["ticket_id"],
            run_id=row["run_id"],
            actor_type="system",
            actor_id="startup_recovery",
            event_type="run_failed",
            occurred_at=result.completed_at.isoformat(),
            approval_status="failed",
            payload=AuditPayload(summary="Interrupted run was marked failed", error_code="interrupted"),
        )
    return len(rows)

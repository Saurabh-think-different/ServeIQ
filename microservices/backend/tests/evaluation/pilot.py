"""Small deterministic evaluator for synthetic printer-pilot observations."""

from dataclasses import dataclass
from typing import Literal

from phdata.models.actions import ActionDecisionResult
from phdata.models.runs import RoutingOutcome, RunResult


ActionDecision = Literal["approve", "reject"]


@dataclass(frozen=True)
class EvaluationExpectation:
    case_id: str
    pilot_eligible: bool
    expected_routing: RoutingOutcome | None
    expected_action: ActionDecision | None = None
    expected_replay: bool | None = None


@dataclass(frozen=True)
class EvaluationObservation:
    expectation: EvaluationExpectation
    run: RunResult
    action: ActionDecisionResult | None = None
    replayed: bool | None = None


@dataclass(frozen=True)
class EvaluationReport:
    case_count: int
    eligible_case_count: int
    labeled_case_count: int
    routing_matches: int
    escalation_misses: tuple[str, ...]
    uncited_guided_cases: tuple[str, ...]
    action_mismatches: tuple[str, ...]
    replay_mismatches: tuple[str, ...]

    @property
    def routing_agreement(self) -> float | None:
        return self.routing_matches / self.labeled_case_count if self.labeled_case_count else None


def evaluate(observations: list[EvaluationObservation]) -> EvaluationReport:
    labeled = [item for item in observations if item.expectation.expected_routing is not None]
    matches = 0
    escalation_misses = []
    uncited_guidance = []
    action_mismatches = []
    replay_mismatches = []

    for item in observations:
        expected = item.expectation.expected_routing
        actual = item.run.routing_outcome if item.run.status == "completed" else None
        if expected is not None and expected == actual:
            matches += 1
        if expected == "l2_escalation" and actual != "l2_escalation":
            escalation_misses.append(item.expectation.case_id)
        if actual == "l1_guided":
            evidence_ids = {evidence.evidence_id for evidence in item.run.evidence}
            if not item.run.steps or any(
                not step.evidence_ids or not set(step.evidence_ids) <= evidence_ids
                for step in item.run.steps
            ):
                uncited_guidance.append(item.expectation.case_id)
        if item.expectation.expected_action is not None and (
            item.action is None or item.action.decision != item.expectation.expected_action
        ):
            action_mismatches.append(item.expectation.case_id)
        if item.expectation.expected_replay is not None and item.replayed != item.expectation.expected_replay:
            replay_mismatches.append(item.expectation.case_id)

    return EvaluationReport(
        case_count=len(observations),
        eligible_case_count=sum(item.expectation.pilot_eligible for item in observations),
        labeled_case_count=len(labeled),
        routing_matches=matches,
        escalation_misses=tuple(escalation_misses),
        uncited_guided_cases=tuple(uncited_guidance),
        action_mismatches=tuple(action_mismatches),
        replay_mismatches=tuple(replay_mismatches),
    )

"""Constructed offline trajectories exercise real native ADK and custom gates."""

import asyncio
from copy import deepcopy

import pytest

from src.services.eval_metrics import evaluate_case


@pytest.fixture
def recorded_case() -> tuple[dict, list[dict]]:
    citation = "kb/device-guide.md#L10-L12"
    guidance = "Check the device power indicator."
    question = "What does the indicator show?"
    message = f"{guidance}\n\n{question}\n\nSources: {citation}"
    terminal_args = {"guidance": guidance, "question": question, "citations": [citation]}
    case = {
        "id": "constructed-device-guidance",
        "title": "Grounded device guidance",
        "turns": ["The device is not responding."],
        "expected_statuses": ["waiting_for_user"],
        "expected_tools": ["get_store_context", "search_knowledge_base", "user_input"],
        "expected_kb_docs": ["device-guide.md#L1-L2"],
        "forbidden_tools": ["delete_ticket"],
        "source_ticket_ids": [],
        "metadata": {"expected_turn_modes": ["guidance"]},
    }
    turn = {
        "user_input": case["turns"][0],
        "final_status": "waiting_for_user",
        "final_message": message,
        "tool_calls": [
            {
                "name": "get_store_context",
                "args": {},
                "response": {"status": "ok", "data": {"store_id": "constructed-store"}},
            },
            {
                "name": "search_knowledge_base",
                "args": {"query": "device not responding"},
                "response": {
                    "status": "ok",
                    "data": {
                        "matches": [
                            {
                                "source": "kb/device-guide.md",
                                "citation": citation,
                                "content": guidance,
                            }
                        ]
                    },
                },
            },
            {
                "name": "lookup_system_version",
                "args": {"system": "constructed-device"},
                "response": {"status": "ok", "data": {"version": "1.0"}},
            },
            {
                "name": "search_ticket_history",
                "args": {"query": "device"},
                "response": {"status": "ok", "data": {"tickets": []}},
            },
            {
                "name": "user_input",
                "args": deepcopy(terminal_args),
                "response": {
                    "tool": "user_input",
                    "status": "waiting_for_user",
                    "message": message,
                    **terminal_args,
                },
            },
        ],
        "citations": [citation],
        "retrieved_sources": ["kb/device-guide.md"],
        "retrieved_citations": [citation],
        "duration_ms": 1.0,
        "error": None,
        "token_usage": {},
    }
    return case, [turn]


def test_native_additional_optional_tools_pass(recorded_case):
    case, turns = recorded_case

    result = asyncio.run(evaluate_case(case, turns))

    assert result["passed"] is True, result["failures"]
    native = result["native_evaluator"]
    assert native["evaluator"] == "google.adk.evaluation.trajectory_evaluator.TrajectoryEvaluator"
    assert native["criterion"] == {
        "metric_name": "tool_trajectory_avg_score",
        "threshold": 1.0,
        "match_type": "ANY_ORDER",
        "ignore_args": True,
    }
    assert native["score"] == 1.0
    assert native["status"] == "passed"
    assert native["per_scope_scores"] == [1.0, 1.0]
    assert result["adapter"]["final_responses"] == [
        turns[0]["tool_calls"][-1]["response"]["message"]
    ]
    assert result["adapter"]["final_response_sources"] == ["terminal_tool_response"]
    assert result["adapter"]["normalized_kb_sources"] == ["device-guide.md"]
    assert result["metrics"]["kb_source_recall"]["value"] == 1.0
    for metric in result["metrics"].values():
        assert {"value", "expected", "status", "notes"} <= metric.keys()


@pytest.mark.parametrize(
    ("final_status", "terminal_status"),
    [("resolved", "waiting_for_user"), ("waiting_for_user", "resolved")],
)
def test_incorrect_terminal_status_fails(recorded_case, final_status, terminal_status):
    case, turns = recorded_case
    turns[0]["final_status"] = final_status
    turns[0]["tool_calls"][-1]["response"]["status"] = terminal_status

    result = asyncio.run(evaluate_case(case, turns))

    assert result["passed"] is False
    assert result["metrics"]["terminal_consistency"]["status"] == "failed"
    if final_status == "resolved":
        assert result["metrics"]["routing_accuracy"]["status"] == "failed"
        assert result["metrics"]["no_false_resolution"]["status"] == "failed"
    assert result["failures"]


def test_fabricated_citation_fails_even_with_matching_document_and_runner_summary(recorded_case):
    case, turns = recorded_case
    fabricated = "kb/device-guide.md#L90-L99"
    turns[0]["citations"] = [fabricated]
    turns[0]["retrieved_citations"].append(fabricated)
    terminal = turns[0]["tool_calls"][-1]
    terminal["args"]["citations"] = [fabricated]
    terminal["response"]["citations"] = [fabricated]

    result = asyncio.run(evaluate_case(case, turns))

    assert result["passed"] is False
    assert result["metrics"]["kb_source_recall"]["status"] == "passed"
    citations = result["metrics"]["citation_validity"]
    assert citations["status"] == "failed"
    assert {"turn": 0, "citation": fabricated} in citations["value"]
    assert (
        result["adapter"]["retrieval_evidence_contract"]["runner_summaries_used_as_proof"] is False
    )


def test_missing_expected_kb_source_fails_despite_runner_summary(recorded_case):
    case, turns = recorded_case
    case["expected_kb_docs"] = ["kb/missing-guide.md#L1-L5"]
    turns[0]["retrieved_sources"].append("kb/missing-guide.md")
    turns[0]["retrieved_citations"].append("kb/missing-guide.md#L1-L5")

    result = asyncio.run(evaluate_case(case, turns))

    assert result["passed"] is False
    recall = result["metrics"]["kb_source_recall"]
    assert recall["status"] == "failed"
    assert recall["value"] == 0.0
    assert recall["expected"] == ["missing-guide.md"]
    assert recall["missing"] == ["missing-guide.md"]


@pytest.mark.parametrize("empty_case", [False, True])
def test_empty_turns_never_pass(recorded_case, empty_case):
    case, _ = recorded_case
    if empty_case:
        case["turns"] = []
        case["expected_statuses"] = []
        case["metadata"] = {}

    result = asyncio.run(evaluate_case(case, []))

    assert result["passed"] is False
    assert result["metrics"]["input_integrity"]["status"] == "failed"
    assert result["native_evaluator"]["score"] is None
    assert result["failures"]


def test_empty_recorded_turn_never_passes(recorded_case):
    case, _ = recorded_case

    result = asyncio.run(evaluate_case(case, [{}]))

    assert result["passed"] is False
    assert result["metrics"]["input_integrity"]["status"] == "failed"
    assert result["failures"]


def test_runner_errors_never_pass_even_with_successful_final_turn(recorded_case):
    case, turns = recorded_case
    turns[0]["error"] = "Constructed runner failure"

    result = asyncio.run(evaluate_case(case, turns))

    assert result["passed"] is False
    assert result["native_evaluator"]["score"] == 1.0
    assert result["metrics"]["execution_errors"]["status"] == "failed"
    assert result["metrics"]["execution_errors"]["counts"]["runner_errors"] == 1
    assert result["metrics"]["execution_errors"]["value"]
    assert result["failures"]


def test_optional_tool_error_is_informational_after_valid_terminal(recorded_case):
    case, turns = recorded_case
    turns[0]["tool_calls"][2]["response"] = {"error": "Constructed lookup failure"}

    result = asyncio.run(evaluate_case(case, turns))

    assert result["passed"] is True, result["failures"]
    assert result["metrics"]["execution_errors"]["status"] == "passed"
    recovered = result["metrics"]["recovered_tool_errors"]
    assert recovered["status"] == "informational"
    assert recovered["counts"]["tool_errors"] == 1
    assert recovered["counts"]["recovered_tool_errors"] == 1
    assert recovered["counts"]["unhandled_tool_errors"] == 0
    assert recovered["value"][0]["response"] == {"error": "Constructed lookup failure"}


def test_corrected_argument_retry_passes_without_hiding_raw_error(recorded_case):
    case, turns = recorded_case
    failed = {
        "name": "lookup_system_version",
        "call_id": "invalid-argument-attempt",
        "args": {"system": ""},
        "response": {"error": "Provide a nonempty system"},
    }
    turns[0]["tool_calls"].insert(2, failed)

    result = asyncio.run(evaluate_case(case, turns))

    assert result["passed"] is True, result["failures"]
    assert result["native_evaluator"]["score"] == 1.0
    assert result["metrics"]["terminal_exactly_once"]["value"] == [1]
    recovered = result["metrics"]["recovered_tool_errors"]
    assert recovered["status"] == "informational"
    assert recovered["counts"]["tool_errors"] == 1
    trace = recovered["value"][0]
    assert trace["classification"] == "recovered"
    assert trace["recovery"] == "successful_retry"
    assert trace["call_id"] == "invalid-argument-attempt"
    assert trace["args"] == failed["args"]
    assert trace["response"] == failed["response"]


@pytest.fixture
def kb_outage_case(recorded_case) -> tuple[dict, list[dict]]:
    case, turns = recorded_case
    case["expected_statuses"] = ["escalated"]
    case["expected_tools"] = ["get_store_context", "search_knowledge_base", "escalate_to_l2"]
    case["expected_kb_docs"] = []
    case["metadata"] = {"fault": "kb_unavailable", "expected_turn_modes": ["escalation"]}
    turn = turns[0]
    turn["tool_calls"][1]["response"] = {
        "tool": "search_knowledge_base",
        "error": "Knowledge data is temporarily unavailable",
    }
    terminal_args = {
        "reason_code": "insufficient_evidence",
        "reason": "Knowledge data is temporarily unavailable",
        "summary": "Missing evidence prevents safe troubleshooting",
        "attempted_steps": [],
        "citations": [],
    }
    message = "Missing evidence prevents safe troubleshooting. Simulated L2 handoff."
    turn["tool_calls"] = [
        *turn["tool_calls"][:2],
        {
            "name": "escalate_to_l2",
            "args": deepcopy(terminal_args),
            "response": {"status": "escalated", "message": message, **terminal_args},
        },
    ]
    turn.update(
        final_status="escalated",
        final_message=message,
        citations=[],
        retrieved_sources=[],
        retrieved_citations=[],
    )
    return case, turns


def test_expected_kb_outage_allows_insufficient_evidence_escalation(kb_outage_case):
    case, turns = kb_outage_case

    result = asyncio.run(evaluate_case(case, turns))

    assert result["passed"] is True, result["failures"]
    assert result["metrics"]["kb_source_recall"]["status"] == "not_applicable"
    assert result["metrics"]["execution_errors"]["status"] == "passed"
    recovered = result["metrics"]["recovered_tool_errors"]
    assert recovered["status"] == "informational"
    assert recovered["counts"]["expected_tool_errors"] == 1
    assert recovered["value"][0]["classification"] == "expected"
    assert "kb_unavailable" in recovered["value"][0]["expected_reason"]
    assert recovered["value"][0]["response"] == turns[0]["tool_calls"][1]["response"]


def test_expected_kb_outage_does_not_waive_required_document_recall(kb_outage_case):
    case, turns = kb_outage_case
    case["expected_kb_docs"] = ["required-runbook.md"]

    result = asyncio.run(evaluate_case(case, turns))

    assert result["passed"] is False
    assert result["metrics"]["execution_errors"]["status"] == "passed"
    assert result["metrics"]["recovered_tool_errors"]["counts"]["expected_tool_errors"] == 1
    assert result["metrics"]["kb_source_recall"]["status"] == "failed"
    assert result["metrics"]["kb_source_recall"]["missing"] == ["required-runbook.md"]


@pytest.mark.parametrize("failure", ["runner_error", "terminal_error", "missing_terminal"])
def test_expected_fault_never_waives_failed_execution_or_terminal(kb_outage_case, failure):
    case, turns = kb_outage_case
    if failure == "runner_error":
        turns[0]["error"] = "Provider stream failed"
    elif failure == "terminal_error":
        turns[0]["tool_calls"][-1]["response"] = {"error": "Handoff failed"}
    else:
        turns[0]["tool_calls"].pop()

    result = asyncio.run(evaluate_case(case, turns))

    assert result["passed"] is False
    errors = result["metrics"]["execution_errors"]
    assert errors["status"] == "failed"
    assert errors["counts"]["unhandled_tool_errors"] >= 1
    assert result["metrics"]["recovered_tool_errors"]["value"] == []
    assert result["failures"]


def test_terminal_argument_retry_still_fails_once_only_policy(recorded_case):
    case, turns = recorded_case
    failed_terminal = {
        "name": "user_input",
        "args": {"question": ""},
        "response": {"error": "Ask a nonempty question"},
    }
    turns[0]["tool_calls"].insert(-1, failed_terminal)

    result = asyncio.run(evaluate_case(case, turns))

    assert result["passed"] is False
    assert result["metrics"]["terminal_exactly_once"]["value"] == [2]
    assert result["metrics"]["terminal_exactly_once"]["status"] == "failed"
    assert result["metrics"]["execution_errors"]["status"] == "failed"
    assert result["metrics"]["recovered_tool_errors"]["value"] == []


def test_denied_foreign_store_lookup_is_expected_and_remains_visible(recorded_case):
    case, turns = recorded_case
    case["metadata"]["forbidden_store_ids"] = ["foreign-store"]
    denial = {
        "name": "get_store_context",
        "args": {"store_id": "foreign-store"},
        "response": {"error": "Store is not assigned to this manager"},
    }
    turns[0]["tool_calls"].insert(0, denial)

    result = asyncio.run(evaluate_case(case, turns))

    assert result["passed"] is True, result["failures"]
    recovered = result["metrics"]["recovered_tool_errors"]
    assert recovered["counts"]["expected_tool_errors"] == 1
    assert recovered["value"][0]["response"] == denial["response"]
    assert "forbidden_store_ids" in recovered["value"][0]["expected_reason"]

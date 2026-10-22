"""Offline evaluation of recorded turns; no agent, judge, settings or database imports.

``evaluate_case`` accepts the runner's case/turn dictionaries and returns JSON-safe
metrics. Every metric has ``value``, ``expected``, ``status`` and ``notes``;
``not_applicable`` has value None; ``informational`` retains recovered tool errors.
Neither status fails a case. Runner/provider errors and unsuccessful terminal
attempts remain hard failures. Nonterminal errors preceding a structurally valid
terminal are reported, not silently dropped; other evidence/routing gates still apply.
Expected tools are a case-wide set, statuses are positional per turn. ADK uses
ANY_ORDER with ignored arguments; deterministic checks enforce partial ordering.

Optional fixture metadata: ``expected_turn_modes`` (one guidance/clarification/
resolution/escalation or null per turn), ``confirmed_resolution_turns`` (zero-based
indices), and ``policy_overrides.allow_resolution_without_confirmation`` (requires
``policy_override_note``). Overrides cannot waive routing, evidence or errors.
Expected resolved statuses are the confirmation oracle when explicit confirmation
indices are absent; this evaluator does not infer confirmation from user wording.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_TERMINALS = {
    "user_input": "waiting_for_user",
    "complete_task": "resolved",
    "escalate_to_l2": "escalated",
}
_STATUS_TO_TOOL = {status: tool for tool, status in _TERMINALS.items()}
_RETRIEVALS = {
    "get_store_context",
    "search_ticket_history",
    "lookup_sla",
    "lookup_system_version",
    "search_knowledge_base",
}
_METRIC_NAME = "tool_trajectory_avg_score"


def _metric(value: Any, expected: Any, passed: bool | None, notes: str, **details: Any) -> dict:
    return {
        "value": value,
        "expected": expected,
        "status": "not_applicable" if passed is None else "passed" if passed else "failed",
        "notes": notes,
        **details,
    }


def _tool_failed(response: dict) -> bool:
    return response.get("error") is not None or response.get("status") in ("error", "failed")


def _expected_tool_error(call: dict, metadata: dict, terminal: dict) -> str | None:
    if (
        metadata.get("fault") == "kb_unavailable"
        and call["name"] == "search_knowledge_base"
        and terminal["name"] == "escalate_to_l2"
        and terminal["response"].get("reason_code", terminal["args"].get("reason_code"))
        == "insufficient_evidence"
    ):
        return "metadata.fault=kb_unavailable; insufficient_evidence escalation"
    forbidden = metadata.get("forbidden_store_ids", [])
    store_id = call["args"].get("store_id")
    asset_id = call["args"].get("asset_id")
    if (
        isinstance(forbidden, list)
        and forbidden
        and call["name"] in _RETRIEVALS - {"search_knowledge_base"}
        and (
            (isinstance(store_id, str) and store_id in forbidden)
            or (asset_id and asset_id == metadata.get("supplied_unowned_asset_id"))
        )
        and not call["response"].get("data")
        and terminal["name"] in ("user_input", "escalate_to_l2")
    ):
        return "metadata.forbidden_store_ids; denied lookup with no returned data"
    return None


def _document(value: str) -> str:
    """Normalize only document identity; citation validity retains exact anchors."""
    value = re.sub(r"#L\d+(?:-(?:L)?\d+)?$", "", value.strip(), flags=re.IGNORECASE)
    value = value.replace("\\", "/").removeprefix("./")
    while value.startswith("kb/"):
        value = value[3:]
    return value


def _evidence(value: Any) -> tuple[set[str], set[str]]:
    """Read explicit evidence fields, never arbitrary response text or tool args."""
    citations: set[str] = set()
    sources: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "citation" and isinstance(item, str):
                citations.add(item)
            elif key == "citations" and isinstance(item, list):
                citations.update(c for c in item if isinstance(c, str))
            elif key == "source" and isinstance(item, str):
                sources.add(_document(item))
            else:
                child_citations, child_sources = _evidence(item)
                citations.update(child_citations)
                sources.update(child_sources)
    elif isinstance(value, list):
        for item in value:
            child_citations, child_sources = _evidence(item)
            citations.update(child_citations)
            sources.update(child_sources)
    sources.update(_document(citation) for citation in citations)
    return citations, sources


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _validate(case: dict, turns: list[dict]) -> list[str]:
    errors = []
    if not isinstance(case, dict) or not isinstance(turns, list):
        return ["case must be a dict and turns must be a list"]
    if not isinstance(case.get("id"), str) or not case["id"].strip():
        errors.append("case.id must be a nonempty string")
    for name in (
        "turns",
        "expected_statuses",
        "expected_tools",
        "expected_kb_docs",
        "forbidden_tools",
        "source_ticket_ids",
    ):
        if not _string_list(case.get(name, [])):
            errors.append(f"case.{name} must be a list of strings")
    prompts = case.get("turns", [])
    statuses = case.get("expected_statuses", [])
    if not _string_list(prompts):
        prompts = []
    if not _string_list(statuses):
        statuses = []
    if not errors:
        if not prompts or len(prompts) != len(turns) or len(statuses) != len(prompts):
            errors.append(
                "case.turns, expected_statuses and recorded turns need equal nonzero lengths"
            )
        if any(status not in _STATUS_TO_TOOL for status in statuses):
            errors.append("expected_statuses must use waiting_for_user, resolved or escalated")
    metadata = case.get("metadata", {})
    if not isinstance(metadata, dict):
        errors.append("case.metadata must be a dict")
        metadata = {}
    modes = metadata.get("expected_turn_modes")
    if modes is not None and (
        not isinstance(modes, list)
        or len(modes) != len(prompts)
        or any(
            mode not in (None, "guidance", "clarification", "resolution", "escalation")
            for mode in modes
        )
    ):
        errors.append("metadata.expected_turn_modes needs one supported mode or null per case turn")
    confirmed = metadata.get("confirmed_resolution_turns")
    if confirmed is not None and (
        not isinstance(confirmed, list)
        or any(type(i) is not int or not 0 <= i < len(prompts) for i in confirmed)
    ):
        errors.append(
            "metadata.confirmed_resolution_turns must contain zero-based case turn indices"
        )
    overrides = metadata.get("policy_overrides", {})
    if not isinstance(overrides, dict) or any(
        key != "allow_resolution_without_confirmation" or type(value) is not bool
        for key, value in overrides.items()
    ):
        errors.append("only boolean allow_resolution_without_confirmation is a supported override")
    elif overrides and not str(metadata.get("policy_override_note", "")).strip():
        errors.append("policy_overrides require a nonempty metadata.policy_override_note")
    for i, turn in enumerate(turns):
        if not isinstance(turn, dict):
            errors.append(f"turn {i}: must be a dict")
            continue
        for key in ("user_input", "final_status", "final_message"):
            if not isinstance(turn.get(key), str):
                errors.append(f"turn {i}: {key} must be a string")
        if i < len(prompts) and turn.get("user_input") != prompts[i]:
            errors.append(f"turn {i}: user_input differs from case.turns")
        for key in ("citations", "retrieved_sources", "retrieved_citations"):
            if not _string_list(turn.get(key, [])):
                errors.append(f"turn {i}: {key} must be a list of strings")
        calls = turn.get("tool_calls")
        if not isinstance(calls, list):
            errors.append(f"turn {i}: tool_calls must be a list")
        else:
            for j, call in enumerate(calls):
                if (
                    not isinstance(call, dict)
                    or not isinstance(call.get("name"), str)
                    or not (
                        isinstance(call.get("args"), dict)
                        and isinstance(call.get("response"), dict)
                    )
                ):
                    errors.append(f"turn {i} call {j}: requires name:str, args:dict, response:dict")
    return errors


def _native(case: dict, turns: list[dict], messages: list[str]) -> dict:
    """Invoke only ADK's deterministic native trajectory evaluator, lazily imported."""
    configuration = {"threshold": 1.0, "match_type": "ANY_ORDER", "ignore_args": True}
    report: dict[str, Any] = {
        "criterion": {"metric_name": _METRIC_NAME, **configuration},
        "score": None,
        "status": "failed",
        "evaluator": "google.adk.evaluation.trajectory_evaluator.TrajectoryEvaluator",
    }
    try:
        from google.adk.evaluation.eval_case import EvalCase, IntermediateData, Invocation
        from google.adk.evaluation.eval_metrics import EvalMetric, ToolTrajectoryCriterion
        from google.adk.evaluation.trajectory_evaluator import TrajectoryEvaluator
        from google.genai.types import Content, FunctionCall, FunctionResponse, Part

        actual = []
        expected = []
        for i, turn in enumerate(turns):
            invocation_id = f"{case['id']}:{i}"
            actual.append(
                Invocation(
                    invocation_id=invocation_id,
                    user_content=Content(role="user", parts=[Part(text=turn["user_input"])]),
                    final_response=Content(role="model", parts=[Part(text=messages[i])]),
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            FunctionCall(name=c["name"], args=c["args"]) for c in turn["tool_calls"]
                        ],
                        tool_responses=[
                            FunctionResponse(name=c["name"], response=c["response"])
                            for c in turn["tool_calls"]
                        ],
                    ),
                )
            )
            expected.append(
                Invocation(
                    invocation_id=invocation_id,
                    user_content=actual[-1].user_content,
                    intermediate_data=IntermediateData(
                        tool_uses=[
                            FunctionCall(
                                name=_STATUS_TO_TOOL[case["expected_statuses"][i]],
                                args={},
                            )
                        ]
                    ),
                )
            )
        actual_case = EvalCase(eval_id=case["id"], conversation=actual)
        expected_case = EvalCase(eval_id=case["id"], conversation=expected)
        # The case's flat expected_tools is a set across the conversation, not a
        # requirement to repeat every tool (including every terminal) on each turn.
        aggregate_actual = Invocation(
            user_content=Content(role="user", parts=[Part(text="Case-wide tool presence")]),
            intermediate_data=IntermediateData(
                tool_uses=[
                    call for invocation in actual for call in invocation.intermediate_data.tool_uses
                ]
            ),
        )
        aggregate_expected = Invocation(
            user_content=aggregate_actual.user_content,
            intermediate_data=IntermediateData(
                tool_uses=[
                    FunctionCall(name=name, args={})
                    for name in sorted(set(case.get("expected_tools", [])))
                ]
            ),
        )
        criterion = ToolTrajectoryCriterion(**configuration)
        evaluator = TrajectoryEvaluator(
            eval_metric=EvalMetric(
                metric_name=_METRIC_NAME,
                criterion=criterion,
            )
        )
        result = evaluator.evaluate_invocations(
            actual_invocations=[aggregate_actual, *actual_case.conversation],
            expected_invocations=[aggregate_expected, *expected_case.conversation],
        )
        report.update(
            score=result.overall_score,
            status="passed" if result.overall_score == 1.0 else "failed",
            per_scope_scores=[item.score for item in result.per_invocation_results],
            scopes=[
                "case_tool_presence",
                *[f"turn:{i}:terminal_presence" for i in range(len(turns))],
            ],
            notes="Native ANY_ORDER subset matching; extra calls permitted; args ignored. "
            "Order, duplicate terminals, errors and evidence are separate strict gates.",
        )
    except ImportError as error:
        report.update(
            error=f"{type(error).__name__}: {error}",
            required_manifest_extra="google-adk[eval]>=2.12,<3",
            notes="ADK evaluation dependency unavailable; no custom fallback or installation attempted.",
        )
    except (AttributeError, RuntimeError, TypeError, ValueError) as error:
        report.update(
            error=f"{type(error).__name__}: {error}",
            notes="Native evaluator failed; no custom fallback was substituted.",
        )
    return report


async def evaluate_case(case: dict, turns: list[dict]) -> dict:
    """Evaluate recorded data locally; execution/import failures always fail closed."""
    metrics: dict[str, dict] = {}
    failures = _validate(case, turns)
    metrics["input_integrity"] = _metric(
        failures.copy(), [], not failures, "Nonempty aligned case/turn data is required."
    )
    if failures:
        return {
            "passed": False,
            "metrics": metrics,
            "failures": failures,
            "native_evaluator": {
                "criterion": {
                    "metric_name": _METRIC_NAME,
                    "threshold": 1.0,
                    "match_type": "ANY_ORDER",
                    "ignore_args": True,
                },
                "score": None,
                "status": "failed",
                "error": "Invalid input; native evaluation not run",
            },
            "adapter": {
                "name": "terminal_tool_as_final_text",
                "turn_count": len(turns) if isinstance(turns, list) else 0,
                "final_responses": [],
            },
        }

    metadata = case.get("metadata", {})
    expected_statuses = case["expected_statuses"]
    statuses = [turn["final_status"] for turn in turns]
    metrics["routing_accuracy"] = _metric(
        sum(a == e for a, e in zip(statuses, expected_statuses, strict=True)) / len(turns),
        1.0,
        statuses == expected_statuses,
        "Positional fixture status oracle; no wording heuristics.",
        actual_statuses=statuses,
        expected_statuses=expected_statuses,
    )
    confirmed = metadata.get("confirmed_resolution_turns")
    override = metadata.get("policy_overrides", {}).get(
        "allow_resolution_without_confirmation", False
    )
    false_resolutions = [
        i
        for i, turn in enumerate(turns)
        if (
            turn["final_status"] == "resolved"
            or any(call["name"] == "complete_task" for call in turn["tool_calls"])
        )
        and (
            expected_statuses[i] != "resolved"
            or (confirmed is not None and i not in confirmed and not override)
        )
    ]
    metrics["no_false_resolution"] = _metric(
        false_resolutions,
        [],
        not false_resolutions,
        "Expected resolved status is the fixture's user-confirmation oracle; explicit "
        "confirmed_resolution_turns, when supplied, additionally gates closure. No semantic "
        "confirmation claim is inferred from free text.",
        policy_override_note=metadata.get("policy_override_note") if override else None,
    )

    all_names = {call["name"] for turn in turns for call in turn["tool_calls"]}
    required_tools = set(case.get("expected_tools", []))
    metrics["expected_tool_presence"] = _metric(
        sorted(all_names & required_tools),
        sorted(required_tools),
        required_tools <= all_names,
        "Case-wide set presence, not an exact sequence or per-turn repetition requirement.",
        missing=sorted(required_tools - all_names),
    )
    forbidden = all_names & set(case.get("forbidden_tools", []))
    metrics["no_forbidden_tools"] = _metric(
        sorted(forbidden),
        [],
        not forbidden,
        "Any attempted forbidden call fails, including errored calls.",
    )

    messages = []
    message_sources = []
    terminal_counts = []
    terminal_issues = []
    order_issues = []
    errors = []
    recovered_tool_errors = []
    tool_error_trace = []
    runner_error_count = 0
    invalid_citations = []
    retrieved_docs: set[str] = set()
    modes = []
    mode_issues = []
    expected_modes = metadata.get("expected_turn_modes")
    for i, turn in enumerate(turns):
        calls = turn["tool_calls"]
        terminals = [(j, call) for j, call in enumerate(calls) if call["name"] in _TERMINALS]
        terminal_counts.append(len(terminals))
        terminal = terminals[0][1] if len(terminals) == 1 else None
        payload = terminal["response"] if terminal else {}
        terminal_message = payload.get("message")
        message = terminal_message if isinstance(terminal_message, str) else turn["final_message"]
        messages.append(message)
        message_sources.append(
            "terminal_tool_response" if isinstance(terminal_message, str) else "final_message"
        )
        runner_failed = turn.get("error") is not None or turn["final_status"] in {"error", "failed"}
        if runner_failed:
            errors.append({"turn": i, "error": turn.get("error") or turn["final_status"]})
            runner_error_count += 1
        valid_terminal = bool(
            terminal
            and not runner_failed
            and not _tool_failed(payload)
            and terminals[0][0] == len(calls) - 1
            and payload.get("status") == _TERMINALS[terminal["name"]]
            and turn["final_status"] == payload.get("status") == expected_statuses[i]
            and isinstance(terminal_message, str)
            and terminal_message.strip()
            and (not turn["final_message"].strip() or turn["final_message"] == terminal_message)
        )
        if not message.strip():
            terminal_issues.append(f"turn {i}: empty final text")
        if terminal:
            if payload.get("status") != _TERMINALS[terminal["name"]] or (
                turn["final_status"] != payload.get("status")
            ):
                terminal_issues.append(
                    f"turn {i}: terminal tool, response status and final_status disagree"
                )
            if not isinstance(terminal_message, str) or not terminal_message.strip():
                terminal_issues.append(f"turn {i}: terminal response lacks a nonempty message")
            elif turn["final_message"].strip() and turn["final_message"] != terminal_message:
                terminal_issues.append(
                    f"turn {i}: final_message differs from terminal response message"
                )
        if terminals:
            first_terminal = terminals[0][0]
            if any(j > first_terminal and c["name"] in _RETRIEVALS for j, c in enumerate(calls)):
                order_issues.append(f"turn {i}: retrieval after terminal")
            if first_terminal != len(calls) - 1:
                order_issues.append(f"turn {i}: calls occurred after the first terminal")

        evidence: set[str] = set()
        claims = set(turn.get("citations", []))
        for j, call in enumerate(calls):
            response = call["response"]
            if _tool_failed(response):
                trace = {
                    "turn": i,
                    "call": j,
                    "call_id": call.get("call_id"),
                    "tool": call["name"],
                    "error": response.get("error")
                    if response.get("error") is not None
                    else response.get("status"),
                    "args": call["args"],
                    "response": response,
                }
                if valid_terminal and call["name"] not in _TERMINALS and j < terminals[0][0]:
                    expected_error = _expected_tool_error(call, metadata, terminal)
                    retried = any(
                        later["name"] == call["name"] and not _tool_failed(later["response"])
                        for later in calls[j + 1 : terminals[0][0]]
                    )
                    trace.update(
                        classification="expected" if expected_error else "recovered",
                        recovery="successful_retry" if retried else "valid_terminal",
                        expected_reason=expected_error,
                    )
                    recovered_tool_errors.append(trace)
                else:
                    trace["classification"] = "unhandled"
                    errors.append(trace)
                tool_error_trace.append(trace)
            elif call["name"] == "search_knowledge_base":
                citations, sources = _evidence(response)
                retrieved_docs.update(sources)
                # A citation retrieved after the decision cannot ground that decision.
                if not terminals or j < terminals[0][0]:
                    evidence.update(citations)
            if call["name"] in _TERMINALS:
                for container in (call["args"], response):
                    cited = container.get("citations", [])
                    if not _string_list(cited):
                        invalid_citations.append(
                            {"turn": i, "reason": "malformed terminal citations"}
                        )
                    else:
                        claims.update(cited)
                if call["name"] == "user_input":
                    for key in ("guidance", "question"):
                        field = response.get(key, call["args"].get(key, ""))
                        if not isinstance(field, str):
                            terminal_issues.append(f"turn {i}: {key} must be a string")
        # Include line citations embedded in final text even if omitted from the
        # structured citations list. Exact anchor identity is deliberately retained.
        claims.update(re.findall(r"[^\s\[\]<>();,]+#L\d+(?:-(?:L)?\d+)?", message))
        for citation in sorted(claims - evidence):
            invalid_citations.append({"turn": i, "citation": citation})

        if terminal and terminal["name"] == "user_input":
            guidance = payload.get("guidance", terminal["args"].get("guidance", ""))
            question = payload.get("question", terminal["args"].get("question", ""))
            has_guidance = isinstance(guidance, str) and bool(guidance.strip())
            modes.append("guidance" if has_guidance else "clarification")
            if not isinstance(question, str) or not question.strip():
                mode_issues.append(f"turn {i}: user_input needs a nonempty structured question")
            if has_guidance and (not claims or not claims <= evidence):
                mode_issues.append(f"turn {i}: guidance needs same-turn retrieved citations")
        elif terminal:
            modes.append("resolution" if terminal["name"] == "complete_task" else "escalation")
        else:
            modes.append(None)
        if expected_modes and expected_modes[i] is not None and modes[-1] != expected_modes[i]:
            mode_issues.append(f"turn {i}: expected {expected_modes[i]}, got {modes[-1]}")

    metrics["terminal_exactly_once"] = _metric(
        terminal_counts,
        [1] * len(turns),
        all(count == 1 for count in terminal_counts),
        "Count attempts, not just successful terminal calls; one per turn.",
    )
    metrics["terminal_consistency"] = _metric(
        terminal_issues,
        [],
        not terminal_issues,
        "Terminal payload is the authoritative public final text/status.",
    )
    metrics["retrieval_before_terminal"] = _metric(
        order_issues,
        [],
        not order_issues,
        "Partial order only: any retrieval precedes the terminal; optional lookups may appear in any order.",
    )
    error_counts = {
        "runner_errors": runner_error_count,
        "tool_errors": len(tool_error_trace),
        "recovered_tool_errors": len(recovered_tool_errors),
        "expected_tool_errors": sum(
            error["classification"] == "expected" for error in recovered_tool_errors
        ),
        "unhandled_tool_errors": len(tool_error_trace) - len(recovered_tool_errors),
    }
    metrics["execution_errors"] = _metric(
        errors,
        [],
        not errors,
        "Runner/provider errors, failed terminal attempts and tool errors without a later single "
        "successful, status-aligned terminal fail. Recovered nonterminal errors are informational; "
        "routing, evidence, forbidden-tool and terminal-attempt gates remain independent.",
        counts=error_counts,
        tool_error_trace=tool_error_trace,
    )
    metrics["recovered_tool_errors"] = {
        **_metric(
            recovered_tool_errors,
            "Informational only; zero unhandled tool errors required",
            True,
            "Raw nonterminal error trace retained after a valid terminal, with retry/expected-fault "
            "classification. Metadata never waives runner errors or invalid/duplicate terminals.",
            counts=error_counts,
        ),
        "status": "informational",
    }
    expected_docs = {_document(doc) for doc in case.get("expected_kb_docs", [])}
    metrics["kb_source_recall"] = _metric(
        len(expected_docs & retrieved_docs) / len(expected_docs) if expected_docs else None,
        sorted(expected_docs),
        expected_docs <= retrieved_docs if expected_docs else None,
        "Document recall from successful KB tool responses only; kb/ prefix and #L anchors normalized. "
        "No expected documents means not_applicable, not a synthetic score of one.",
        retrieved=sorted(retrieved_docs),
        missing=sorted(expected_docs - retrieved_docs),
        threshold=1.0 if expected_docs else None,
    )
    metrics["citation_validity"] = _metric(
        invalid_citations,
        [],
        not invalid_citations,
        "Exact citation identifiers (including line anchors) must have been returned by successful "
        "KB retrieval before this turn's terminal; runner source names and earlier turns are not evidence.",
    )
    metrics["turn_behavior"] = _metric(
        modes,
        expected_modes,
        not mode_issues,
        "Guidance versus clarification uses structured guidance/question, never fixed word matching. "
        "Modes are informational unless specified; question and grounded guidance remain required.",
        issues=mode_issues,
    )
    native = _native(case, turns, messages)
    for name, metric in metrics.items():
        if metric["status"] == "failed":
            failures.append(f"{name}: {metric['notes']}")
    if native["status"] != "passed":
        failures.append(f"native_evaluator: {native.get('error', 'trajectory score below 1.0')}")
    return {
        "passed": not failures,
        "metrics": metrics,
        "failures": failures,
        "native_evaluator": native,
        "adapter": {
            "name": "terminal_tool_as_final_text",
            "turn_count": len(turns),
            "final_responses": messages,
            "final_response_sources": message_sources,
            "normalized_kb_sources": sorted(retrieved_docs),
            "retrieval_evidence_contract": {
                "authoritative": "successful search_knowledge_base response fields citation/citations/source",
                "runner_summary_fields": ["retrieved_citations", "retrieved_sources"],
                "runner_summaries_used_as_proof": False,
                "document_identity": "strip kb/ prefix and terminal #L line anchors from both sources and citations",
                "citation_identity": "exact returned citation, including prefix and line anchors; same turn only",
            },
            "native_types": [
                "EvalCase",
                "Invocation",
                "IntermediateData",
                "Content",
                "FunctionCall",
                "FunctionResponse",
            ],
            "notes": "Actual tool names/args/responses are retained. Terminal response message maps "
            "to Invocation.final_response; no text or semantic response judge is run. "
            "An additional synthetic invocation checks case-wide expected tool presence.",
        },
    }

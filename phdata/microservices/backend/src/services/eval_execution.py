"""Execute evaluation conversations through the real domain pipeline in a backup DB.

Only public SSE/tool fields are retained. No provider reasoning, credentials, JWT
claims, synthetic token counts, or operational source mutations are involved.
"""

import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from itertools import pairwise
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from src.models import Event, Incident, Message, User
from src.models.escalation import L2Escalation
from src.models.structured import RegionManager, Store
from src.services import llm, store, support_data

_ADAPTER = {
    "trajectory_source": "PreparedRun.stream public domain SSE",
    "tool_call_matching": "call_id",
    "terminal_message_source": "done.message (not provider final text)",
    "jwt_auth_tested": False,
    "usage_source": "unavailable",
}
_TERMINAL = {"resolved", "escalated"}
_PUBLIC_EVENTS = {"thought", "action", "action_input", "observation", "done", "error"}


def _status(value: Any) -> str:
    return str(getattr(value, "value", value))


def _validate_sandbox(sandbox_url: str) -> None:
    url = make_url(sandbox_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("Evaluation requires an existing isolated SQLite backup file.")
    path = Path(url.database).expanduser().resolve()
    if not path.is_file():
        raise ValueError("Evaluation SQLite backup file does not exist.")
    operational = store.engine.url
    if (
        operational.get_backend_name() == "sqlite"
        and operational.database
        and path == Path(operational.database).expanduser().resolve()
    ):
        raise ValueError("Evaluation cannot use the operational database.")
    if not callable(getattr(store, "use_database", None)):
        raise TypeError("Isolated store.use_database routing is required for evaluation.")


def _actor_id(db, case: dict) -> str:
    email = case.get("actor_email")
    name = case.get("actor_name")
    actor = None
    if email:
        if not isinstance(email, str):
            raise ValueError("actor_email must be a string.")
        actor = db.scalar(select(User.id).where(func.lower(User.email) == email.strip().lower()))
        if actor is None:
            raise ValueError("Actor email was not found in the sandbox.")
    if name:
        if not isinstance(name, str):
            raise ValueError("actor_name must be a string.")
        named = db.scalar(
            select(User.id)
            .join(RegionManager, RegionManager.user_id == User.id)
            .where(func.lower(RegionManager.name) == name.strip().lower())
        )
        if named is None or (actor is not None and named != actor):
            raise ValueError("Actor name was not found or does not match the sandbox actor email.")
        actor = named
    if actor is None:
        raise ValueError("An existing sandbox actor_email or RegionManager actor_name is required.")
    return actor


def _event_source(fault: str | None):
    async def source(*, history, model, settings, registry):
        if fault:

            async def search_knowledge_base(query: str, limit: int = 3) -> dict:
                """Retrieve grounded KB instructions and citations for the supplied query.

                Args:
                    query: Specific issue symptoms plus product/version when known.
                    limit: Maximum distinct parent sections, from one to five.
                """
                registry.kb_checked = True
                registry._active()
                if fault == "kb_unavailable":
                    return {
                        "tool": "search_knowledge_base",
                        "error": "Knowledge data is temporarily unavailable",
                    }
                return {
                    "tool": "search_knowledge_base",
                    "status": "no_match",
                    "data": {"matches": []},
                }

            registry.tools["search_knowledge_base"] = search_knowledge_base
        stream = llm.adk_events(history=history, model=model, settings=settings, registry=registry)
        async with aclosing(stream):
            async for event in stream:
                yield event

    return source


async def _frames(stream) -> AsyncIterator[dict]:
    buffer = ""
    async for chunk in stream:
        buffer += (chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk).replace(
            "\r\n", "\n"
        )
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            kind = "message"
            data = []
            for line in frame.splitlines():
                if line.startswith("event:"):
                    kind = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip(" "))
            if kind in _PUBLIC_EVENTS and data:
                payload = json.loads("\n".join(data))
                if not isinstance(payload, dict):
                    raise ValueError("Invalid public SSE payload.")
                yield {"event": kind, "data": payload}
    if buffer.strip():
        raise ValueError("Incomplete public SSE frame.")


def _capture(record: dict, frame: dict, pending: dict) -> None:
    kind, payload = frame["event"], frame["data"]
    record["events"].append(frame)
    if kind in {"done", "error"}:
        record["final_status"] = payload.get("status", "failed")
        record["final_message"] = payload.get("message", "")
        record["citations"] = [c for c in payload.get("citations", []) if isinstance(c, str)]
        for key in ("escalation_id", "dispatch_status"):
            if key in payload:
                record[key] = payload[key]
        if kind == "error":
            record["error"] = record["final_message"] or "Agent run failed."
        return
    data = payload.get("data", {})
    call_id = data.get("call_id")
    if kind == "action":
        if not call_id or call_id in pending:
            raise ValueError("Invalid public tool call sequence.")
        call = {"name": data["tool"], "args": {}, "response": None, "call_id": call_id}
        record["tool_calls"].append(call)
        pending[call_id] = call
    elif kind in {"action_input", "observation"}:
        call = pending.get(call_id)
        if call is None or call["name"] != data.get("tool"):
            raise ValueError("Unmatched public tool response or input.")
        if kind == "action_input":
            call["args"] = data.get("arguments", {})
        else:
            call["response"] = data.get("result", {})
            pending.pop(call_id)
            if call["name"] == "search_knowledge_base":
                result = call["response"]
                matches = result.get("data", {}).get("matches", [])
                for match in matches:
                    for field, target in (
                        ("source", "retrieved_sources"),
                        ("citation", "retrieved_citations"),
                    ):
                        value = match.get(field)
                        if isinstance(value, str) and value not in record[target]:
                            record[target].append(value)


def _ownership_checks(actor_id: str, incident_id: str, forbidden: list[str]) -> dict:
    checks = {}
    for store_id in forbidden:
        with store.SessionLocal() as db:
            location = db.get(Store, store_id)
            foreign = location is not None and location.manager_user_id != actor_id
        context = support_data.get_store_context(actor_id, incident_id, store_id=store_id)
        history = support_data.search_ticket_history(actor_id, store_id=store_id)
        checks[f"foreign_store:{store_id}"] = bool(
            foreign
            and context.get("status") == "not_found"
            and set(context) <= {"status", "error"}
            and history.get("status") == "not_found"
            and set(history) <= {"status", "error"}
        )
    return checks


def _invariants(db, run, record: dict, source, settings, model: str) -> dict:
    db.expire_all()
    incident = store.require_incident(db, run.incident_id, run.user_id)
    assistant = db.get(Message, run.message_id)
    events = db.scalars(
        select(Event).where(Event.message_id == run.message_id).order_by(Event.sequence)
    ).all()
    public_events = [
        frame["data"] for frame in record["events"] if frame["event"] not in {"done", "error"}
    ]
    done = any(frame["event"] == "done" for frame in record["events"])
    escalations = db.scalars(
        select(L2Escalation).where(L2Escalation.incident_id == incident.id)
    ).all()
    checks = {
        "persisted_assistant_content_matches_done": bool(
            done and assistant is not None and assistant.content == record["final_message"]
        ),
        "persisted_status_matches_done": bool(
            done and _status(incident.status) == record["final_status"]
        ),
        "l2_count": len(escalations) == (1 if record["final_status"] == "escalated" else 0),
        "chronological_event_sequence": bool(
            events
            and [event.sequence for event in events] == list(range(1, len(events) + 1))
            and all(a.created_at <= b.created_at for a, b in pairwise(events))
            and [event.id for event in events] == [event.get("id") for event in public_events]
            and [event.sequence for event in events]
            == [event.get("sequence") for event in public_events]
        ),
        "tool_calls_matched": all(call["response"] is not None for call in record["tool_calls"]),
    }
    if escalations:
        row = escalations[0]
        record["escalation"] = {
            "id": row.id,
            "reason_code": row.reason_code,
            "reason": row.reason,
            "summary": row.summary,
            "attempted_steps": row.attempted_steps,
            "citations": row.citations,
            "missing_information": row.missing_information,
            "dispatch_status": row.dispatch_status,
        }
        checks["l2_atomic_owner_message"] = (
            row.user_id == run.user_id
            and row.message_id == run.message_id
            and row.id == record.get("escalation_id")
        )
    if _status(incident.status) in _TERMINAL:
        before = [row.id for row in escalations]
        rejected = False
        try:
            retry = llm.prepare_run(db, incident, settings, model, event_source=source)
        except HTTPException as error:
            rejected = error.status_code == 409
        else:
            retry.close()
        db.expire_all()
        after = list(
            db.scalars(select(L2Escalation.id).where(L2Escalation.incident_id == incident.id)).all()
        )
        checks["completed_retry_http_409"] = rejected
        checks["completed_retry_no_additional_l2"] = sorted(before) == sorted(after)
    return checks


async def execute_case(case: dict, sandbox_url: str, settings, model: str) -> dict:
    """Run up to five turns as one owned incident; the caller controls case timeout.

    The caller supplies a backup with *all suite* held-out tickets already removed.
    Completed incidents are not reopened to consume extra scripted turns. Every
    preparation, tool lookup, persistence check, and retry stays inside use_database.
    Cancellation propagates after PreparedRun closes and marks interrupted work failed.
    Returned invariants contain passed/details objects; unavailable usage stays empty.
    """
    result = {
        "case_id": case.get("id"),
        "incident_id": None,
        "turns": [],
        "invariants": {},
        "invariant_checks": {},
        "error": None,
        "usage_available": False,
        "token_usage": {},
        "adapter_metadata": dict(_ADAPTER),
    }
    started = perf_counter()
    try:
        _validate_sandbox(sandbox_url)
        inputs = case.get("turns")
        if (
            not isinstance(inputs, list)
            or not 1 <= len(inputs) <= 5
            or any(not isinstance(text, str) or not text.strip() for text in inputs)
        ):
            raise ValueError("Case turns must contain one to five nonempty user strings.")
        title = case.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Case title must be a nonempty string.")
        metadata = case.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise TypeError("Case metadata must be an object.")
        fault = metadata.get("fault")
        if fault not in {None, "kb_unavailable", "no_kb_match"}:
            raise ValueError("Unsupported evaluation fault.")
        forbidden = metadata.get("forbidden_store_ids", [])
        if not isinstance(forbidden, list) or any(
            not isinstance(value, str) or not value or len(value) > 100 for value in forbidden
        ):
            raise ValueError("forbidden_store_ids must be a list of store IDs.")
        source = _event_source(fault)
        with store.use_database(sandbox_url), store.SessionLocal() as db:
            actor_id = _actor_id(db, case)
            incident = Incident(user_id=actor_id, title=title[:200], description=inputs[0])
            db.add(incident)
            db.commit()
            db.refresh(incident)
            result["incident_id"] = incident.id
            if "forbidden_store_ids" in metadata:
                ownership = _ownership_checks(actor_id, incident.id, forbidden)
                result["ownership_checks"] = ownership
                result["invariant_checks"]["foreign_store_lookups_return_no_data"] = all(
                    ownership.values()
                )
            for user_input in inputs:
                turn_started = perf_counter()
                record = {
                    "user_input": user_input,
                    "final_status": "failed",
                    "final_message": "",
                    "tool_calls": [],
                    "citations": [],
                    "retrieved_sources": [],
                    "retrieved_citations": [],
                    "events": [],
                    "duration_ms": 0,
                    "error": None,
                    "token_usage": {},
                    "usage_available": False,
                    "adapter_metadata": dict(_ADAPTER),
                }
                result["turns"].append(record)
                run = None
                try:
                    db.add(Message(incident_id=incident.id, role="user", content=user_input))
                    db.commit()
                    owned = store.require_incident(db, incident.id, actor_id)
                    run = llm.prepare_run(db, owned, settings, model, event_source=source)
                    record["message_id"] = run.message_id
                    pending = {}
                    async with aclosing(run.stream()) as stream:
                        async for frame in _frames(stream):
                            _capture(record, frame, pending)
                    if not any(frame["event"] in {"done", "error"} for frame in record["events"]):
                        record["error"] = "Agent stream ended without a terminal frame."
                    record["invariant_checks"] = _invariants(
                        db, run, record, source, settings, model
                    )
                    for key, passed in record["invariant_checks"].items():
                        result["invariant_checks"][key] = (
                            result["invariant_checks"].get(key, True) and passed
                        )
                except HTTPException as error:
                    db.rollback()
                    record["error"] = f"Run preparation rejected (HTTP {error.status_code})."
                except Exception:  # noqa: BLE001 - never export private provider/DB errors
                    db.rollback()
                    record["error"] = "Evaluation turn failed; private exception details omitted."
                finally:
                    if run is not None:
                        run.close()
                    record["duration_ms"] = round((perf_counter() - turn_started) * 1000, 3)
                if record["error"]:
                    result["error"] = record["error"]
                    break
                if record["final_status"] in _TERMINAL:
                    break
    except (ValueError, TypeError) as error:
        # These validation errors contain only fixed messages, never provider/DB exception text.
        result["error"] = (
            str(error)
            if str(error).startswith(
                (
                    "Evaluation ",
                    "Isolated ",
                    "Actor ",
                    "An existing ",
                    "actor_",
                    "Case ",
                    "Unsupported ",
                    "forbidden_store_ids ",
                )
            )
            else "Invalid evaluation case or sandbox configuration."
        )
    except Exception:  # noqa: BLE001 - never export private provider/DB errors
        result["error"] = "Evaluation case failed; private exception details omitted."
    result["duration_ms"] = round((perf_counter() - started) * 1000, 3)
    result["final_status"] = result["turns"][-1]["final_status"] if result["turns"] else "failed"
    for name, passed in result["invariant_checks"].items():
        details = {
            "turns": [
                {
                    "turn_index": index,
                    "message_id": turn.get("message_id"),
                    "passed": turn["invariant_checks"][name],
                }
                for index, turn in enumerate(result["turns"])
                if name in turn.get("invariant_checks", {})
            ]
        }
        if name == "foreign_store_lookups_return_no_data":
            details["store_checks"] = result.get("ownership_checks", {})
        result["invariants"][name] = {"passed": bool(passed), "details": details}
    result["invariants"]["execution_completed"] = {
        "passed": bool(result["turns"]) and result["error"] is None,
        "details": {"completed_turns": len(result["turns"]), "error": result["error"]},
    }
    result["invariants_passed"] = all(check["passed"] for check in result["invariants"].values())
    return result

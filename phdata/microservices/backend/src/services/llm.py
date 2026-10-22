"""ADK tool/status streaming. No provider text or reasoning is made public.

Run locks protect workers on one shared filesystem (not distributed hosts).
A dead worker releases its OS lock; the next request recovers its running row.
Tests can inject an EventSource into prepare_run without provider credentials.
"""

import asyncio
import fcntl
import hashlib
import inspect
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from prompts.system import build_system_prompt
from src.models import Event, Incident, Message
from src.services import store
from src.services.escalations import record_escalation
from src.services.tools import AgentContext, ToolRegistry

BACKEND = Path(__file__).resolve().parents[2]
APP_NAME = "incident_support"
EventSource = Callable[..., AsyncIterator[Any]]
logger = logging.getLogger(__name__)


def configured_models(settings) -> tuple[str, list[str]]:
    default = settings.llm_model or "gpt-6.1-sol"
    return default, list(dict.fromkeys([default, *settings.allowed_models]))


def validate_model(settings, requested: str | None) -> str:
    default, allowed = configured_models(settings)
    model = default if requested is None else requested
    if model not in allowed or not re.fullmatch(r"(?:openai/)?gpt-[A-Za-z0-9._-]+", model):
        raise HTTPException(422, "Model must be a configured OpenAI GPT model")
    return model


async def adk_events(*, history: list[dict], model: str, settings, registry: ToolRegistry):
    """Rebuild a fresh ADK session from durable public conversation each run."""
    from google.adk.agents import Agent
    from google.adk.agents.run_config import RunConfig
    from google.adk.events import Event as AdkEvent
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name=APP_NAME, user_id="incident")
    runner = None
    try:
        agent = Agent(
            name=APP_NAME,
            model=LiteLlm(
                model=model if model.startswith("openai/") else f"openai/{model}",
                api_key=settings.openai_api_key,
                timeout=60,
                num_retries=0,
            ),
            instruction=build_system_prompt(registry.tools),
            tools=list(registry.tools.values()),
        )
        for message in history[:-1]:
            role = "user" if message["role"] == "user" else "model"
            await sessions.append_event(
                session,
                AdkEvent(
                    author="user" if role == "user" else APP_NAME,
                    content=types.Content(role=role, parts=[types.Part(text=message["content"])]),
                ),
            )
        runner = Runner(agent=agent, app_name=APP_NAME, session_service=sessions)
        stream = runner.run_async(
            user_id="incident",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=history[-1]["content"])]),
            run_config=RunConfig(max_llm_calls=max(1, settings.max_agent_steps)),
        )
        async with aclosing(stream):
            async for event in stream:
                yield event
    finally:
        try:
            if runner is not None:
                await runner.close()
        finally:
            await sessions.delete_session(
                app_name=APP_NAME, user_id="incident", session_id=session.id
            )


def sse(kind: str, data: dict, event_id: str | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class PreparedRun:
    def __init__(self, incident_id, message_id, history, model, settings, source, lock, *, user_id):
        self.incident_id = incident_id
        self.user_id = user_id
        self.message_id = message_id
        self.history = history
        self.model = model
        self.settings = settings
        self.source = source
        self.lock = lock
        self.sequence = 0
        self.finished = False

    def persist(self, kind: str, data: dict) -> str:
        with store.SessionLocal() as db:
            self.sequence += 1
            row = Event(message_id=self.message_id, type=kind, data=data, sequence=self.sequence)
            db.add(row)
            db.commit()
            db.refresh(row)
            payload = {
                "id": row.id,
                "message_id": self.message_id,
                "incident_id": self.incident_id,
                "type": kind,
                "data": data,
                "sequence": row.sequence,
                "created_at": row.created_at.isoformat(),
            }
        return sse(kind, payload, row.id)

    def finish(self, status: str, content: str, registry: ToolRegistry | None = None) -> None:
        with store.SessionLocal() as db:
            incident = db.get(Incident, self.incident_id)
            message = db.get(Message, self.message_id)
            if status == "escalated":
                if registry is None or registry.escalation is None:
                    raise ValueError("An escalation must include a recorded handoff")
                snapshot = registry.conversation_snapshot()
                record_escalation(
                    db,
                    incident_id=self.incident_id,
                    user_id=self.user_id,
                    message_id=self.message_id,
                    conversation_snapshot=snapshot,
                    **registry.escalation,
                )
            incident.status = status
            message.content = content
            db.commit()
        self.finished = True

    def close(self) -> None:
        if self.lock is None:
            return
        try:
            if not self.finished:
                self.finish("failed", "Run interrupted. Submit a new message or retry.")
        finally:
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_UN)
            self.lock.close()
            self.lock = None

    async def stream(self) -> AsyncIterator[str]:
        registry = ToolRegistry(
            AgentContext(
                incident_id=self.incident_id,
                user_id=self.user_id,
                message_id=self.message_id,
                history=self.history,
                settings=self.settings,
            )
        )
        pending: dict[str, tuple[str, dict]] = {}
        calls = 0
        terminal_observed = False
        try:
            yield self.persist("thought", {"status": "Reviewing the incident."})
            async with asyncio.timeout(getattr(self.settings, "agent_timeout_seconds", 120)):
                source = self.source(
                    history=self.history,
                    model=self.model,
                    settings=self.settings,
                    registry=registry,
                )
                async with aclosing(source):
                    async for event in source:
                        if getattr(event, "error_code", None):
                            raise RuntimeError("Provider returned an error")
                        if getattr(event, "partial", False):
                            continue
                        for part in getattr(getattr(event, "content", None), "parts", None) or []:
                            if getattr(part, "thought", False):
                                continue
                            call = getattr(part, "function_call", None)
                            response = getattr(part, "function_response", None)
                            if call is not None:
                                calls += 1
                                if (
                                    calls > max(1, self.settings.max_agent_steps)
                                    or call.name not in registry.tools
                                ):
                                    raise RuntimeError("Unsupported tool or step limit exceeded")
                                key = call.id or call.name
                                if key in pending:
                                    raise RuntimeError("Duplicate tool call")
                                args = dict(call.args or {})
                                allowed_args = set(
                                    inspect.signature(registry.tools[call.name]).parameters
                                )
                                allowed_args.discard("tool_context")
                                if set(args) - allowed_args:
                                    raise RuntimeError("Unexpected tool arguments")
                                pending[key] = (call.name, args)
                                yield self.persist("action", {"tool": call.name, "call_id": key})
                                yield self.persist(
                                    "action_input",
                                    {"tool": call.name, "call_id": key, "arguments": args},
                                )
                            elif response is not None:
                                key = response.id or response.name
                                if key not in pending or pending.pop(key)[0] != response.name:
                                    raise RuntimeError("Unmatched tool response")
                                result = dict(response.response or {})
                                public = registry.public_result(result)
                                yield self.persist(
                                    "observation",
                                    {"tool": response.name, "call_id": key, "result": public},
                                )
                                if registry.terminal and result == registry.terminal:
                                    terminal_observed = True
                        if terminal_observed:
                            if pending:
                                raise RuntimeError("Multiple pending terminal calls")
                            break
            if not terminal_observed or not registry.terminal:
                raise RuntimeError("Agent did not execute a terminal tool")
            self.finish(registry.terminal["status"], registry.terminal["message"], registry)
            yield sse(
                "done",
                {
                    "incident_id": self.incident_id,
                    "message_id": self.message_id,
                    "status": registry.terminal["status"],
                    "message": registry.terminal["message"],
                    "citations": registry.terminal.get("citations", []),
                    **{
                        key: registry.terminal[key]
                        for key in ("escalation_id", "dispatch_status")
                        if key in registry.terminal
                    },
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - sanitize every provider failure at the SSE boundary
            logger.warning("Incident agent run failed", extra={"incident_id": self.incident_id})
            self.finish("failed", "Agent run failed. Retry or submit more information.")
            yield sse(
                "error",
                {
                    "incident_id": self.incident_id,
                    "message_id": self.message_id,
                    "status": "failed",
                    "message": "Agent run failed. Retry or submit more information.",
                },
            )
        finally:
            self.close()


def prepare_run(
    db, incident, settings, requested_model=None, event_source: EventSource = adk_events
) -> PreparedRun:
    """Call only after ownership authorization; errors happen before SSE headers."""
    if not settings.openai_api_key or not settings.openai_api_key.strip():
        raise HTTPException(503, "OPENAI_API_KEY is not configured")
    lock_dir = BACKEND / ".run-locks"
    lock_dir.mkdir(exist_ok=True)
    key = hashlib.sha256(incident.id.encode()).hexdigest()
    lock = (lock_dir / key).open("a")
    try:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(409, "An agent run is already active for this incident") from None
        db.refresh(incident)
        if incident.status in {"resolved", "escalated"}:
            raise HTTPException(409, "Incident is already complete")
        if incident.status == "running":
            # Exclusive OS ownership proves a previous worker no longer owns the run.
            incident.status = "failed"
            db.commit()
        messages = db.scalars(
            select(Message)
            .where(Message.incident_id == incident.id)
            .order_by(Message.created_at, Message.id)
        ).all()
        pinned_model = next(
            (
                message.model
                for message in messages
                if message.role == "assistant" and message.model
            ),
            None,
        )
        if (
            pinned_model
            and requested_model
            and (pinned_model.removeprefix("openai/") != requested_model.removeprefix("openai/"))
        ):
            raise HTTPException(
                409,
                f"This conversation is locked to {pinned_model}. Start a new incident to change models.",
            )
        model = validate_model(settings, pinned_model or requested_model)
        users = [i for i, message in enumerate(messages) if message.role == "user"]
        if not users:
            raise HTTPException(422, "Incident has no persisted user message")
        latest = users[-1]
        if incident.status == "waiting_for_user" and latest != len(messages) - 1:
            raise HTTPException(409, "A new user message is required to continue")
        history = [
            {"role": getattr(m.role, "value", m.role), "content": m.content}
            for m in messages[: latest + 1]
            if m.content
        ]
        history[0]["content"] = (
            f"Incident: {incident.title}\nDescription: {incident.description}\n\n"
            + history[0]["content"]
        )
        assistant = Message(incident_id=incident.id, role="assistant", content="", model=model)
        incident.status = "running"
        db.add(assistant)
        db.commit()
        db.refresh(assistant)
        return PreparedRun(
            incident.id,
            assistant.id,
            history,
            model,
            settings,
            event_source,
            lock,
            user_id=incident.user_id,
        )
    except BaseException:
        db.rollback()
        lock.close()
        raise

"""A small, bounded tool-calling agent loop shared by every GHAST agent.

What makes something here an agent and not a function: the model decides which tools to call,
in what order, and when it has seen enough, then commits to a decision through a required
`finish` tool. What keeps it safe to run unattended:

* Tools are read-only lookups plus `finish`. An agent never writes to the database itself; the
  caller applies a decision only after `validate` accepts it (hard bounds live there, in code).
* The loop is capped at `max_steps` tool rounds and `max_tokens` per model call.
* If there is no client, the provider errors, the model never calls `finish`, or its decision
  fails validation twice, the agent's deterministic `fallback` decides instead and the run is
  marked `mode="fallback"` with the reason. A missing LLM key degrades GHAST, it never stops it.
* Every step is returned in `AgentRun.steps` and stored by `record_run`, so a decision can be
  audited and replayed.

`client` is any object with the OpenAI-compatible `chat.completions.create` coroutine (Groq's
AsyncGroq is one). Nothing here imports a provider.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 6
DEFAULT_MAX_TOKENS = 700
FINISH = "finish"

ToolFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
Validate = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
Fallback = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: ToolFn


@dataclass
class AgentRun:
    agent: str
    task: dict[str, Any]
    mode: str = "llm"  # "llm" or "fallback"
    decision: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None  # why the fallback decided, when it did
    # Every tool result seen, by tool name (last call wins); handed to validate/fallback.
    observations: dict[str, Any] = field(default_factory=dict)


class Agent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[ToolSpec],
        decision_schema: dict[str, Any],
        validate: Validate,
        fallback: Fallback,
        client: Any | None = None,
        model: str | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        fallback_tools: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self._system = system_prompt
        self._tools = {tool.name: tool for tool in tools}
        self._decision_schema = decision_schema
        self._validate = validate
        self._fallback = fallback
        self._client = client
        self._model = model
        self._max_steps = max_steps
        self._max_tokens = max_tokens
        # Tools the fallback needs evidence from. A model would pick its own; the deterministic
        # baseline is run on exactly these, so it decides from the same evidence.
        self._fallback_tools = fallback_tools

    def _tool_schemas(self) -> list[dict[str, Any]]:
        schemas = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in self._tools.values()
        ]
        schemas.append({"type": "function", "function": {
            "name": FINISH,
            "description": "Commit to your final decision. Call this exactly once, when you have enough evidence.",
            "parameters": self._decision_schema,
        }})
        return schemas

    async def _use_fallback(self, run: AgentRun, reason: str) -> AgentRun:
        run.mode, run.reason = "fallback", reason
        for name in self._fallback_tools:
            if name in run.observations or name not in self._tools:
                continue
            try:
                run.observations[name] = await self._tools[name].fn({})
            except Exception as error:  # noqa: BLE001
                run.observations[name] = {"error": f"{type(error).__name__}: {error}"}
            run.steps.append({"type": "tool", "tool": name, "arguments": {}, "result": run.observations[name], "by": "fallback"})
        run.decision = self._validate(self._fallback(run.task, run.observations), run.observations)
        run.steps.append({"type": "fallback", "reason": reason, "decision": run.decision})
        return run

    async def run(self, task: dict[str, Any]) -> AgentRun:
        run = AgentRun(agent=self.name, task=task)
        if self._client is None or not self._model:
            return await self._use_fallback(run, "no LLM client configured")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": json.dumps(task, default=str)},
        ]
        rejected = 0
        for _ in range(self._max_steps):
            try:
                completion = await self._client.chat.completions.create(
                    model=self._model, messages=messages, tools=self._tool_schemas(),
                    tool_choice="auto", max_tokens=self._max_tokens,
                )
            except Exception as error:  # noqa: BLE001 - any provider failure must degrade, not stop
                logger.warning("agent %s: model call failed: %s", self.name, type(error).__name__)
                return await self._use_fallback(run, f"model call failed: {type(error).__name__}")
            message = completion.choices[0].message
            calls = getattr(message, "tool_calls", None) or []
            if not calls:
                messages.append({"role": "assistant", "content": message.content or ""})
                messages.append({"role": "user", "content": f"Use your tools, then call {FINISH}."})
                run.steps.append({"type": "no_tool_call", "text": message.content})
                continue
            messages.append({
                "role": "assistant", "content": message.content or "",
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls],
            })
            for call in calls:
                name = call.function.name
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = None
                if arguments is None:
                    result: dict[str, Any] = {"error": "arguments were not valid JSON"}
                elif name == FINISH:
                    try:
                        run.decision = self._validate(arguments, run.observations)
                    except ValueError as error:
                        rejected += 1
                        run.steps.append({"type": "rejected", "arguments": arguments, "error": str(error)})
                        if rejected >= 2:
                            return await self._use_fallback(run, f"decision rejected twice: {error}")
                        result = {"error": f"decision rejected: {error}. Fix it and call {FINISH} again."}
                    else:
                        run.steps.append({"type": "finish", "arguments": arguments})
                        return run
                elif name not in self._tools:
                    result = {"error": f"unknown tool {name}"}
                else:
                    try:
                        result = await self._tools[name].fn(arguments)
                    except Exception as error:  # noqa: BLE001 - a broken tool is evidence of nothing, not a crash
                        result = {"error": f"{type(error).__name__}: {error}"}
                    else:
                        run.observations[name] = result
                    run.steps.append({"type": "tool", "tool": name, "arguments": arguments, "result": result})
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, default=str)[:6000]})
        return await self._use_fallback(run, "no decision within the step limit")


RECORD_RUN_QUERY = """
INSERT INTO agent_runs (agent, mode, task, steps, decision, reason, incident_id)
VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6, $7::uuid)
"""


async def record_run(db: Any, run: AgentRun, incident_id: str | None = None) -> None:
    """Store one run for audit. Never raises: auditing must not break the thing it audits."""
    try:
        await db.execute(
            RECORD_RUN_QUERY, run.agent, run.mode, json.dumps(run.task, default=str),
            json.dumps(run.steps, default=str), json.dumps(run.decision, default=str), run.reason, incident_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("could not record agent run for %s", run.agent)

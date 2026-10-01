import json
from types import SimpleNamespace as NS

import pytest

from runtime.agent import Agent, ToolSpec, record_run


def call(name, args, id="c1"):
    return NS(id=id, function=NS(name=name, arguments=json.dumps(args) if not isinstance(args, str) else args))


def reply(*calls, content=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=list(calls)))])


class ScriptedClient:
    def __init__(self, *replies, error=None):
        self.replies, self.error, self.requests = list(replies), error, []
        self.chat = NS(completions=self)

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return self.replies.pop(0)


async def lookup(args):
    return {"value": args.get("x", 0) * 2}


def validate(decision, observations):
    if not 0 <= decision["n"] <= 10:
        raise ValueError("n must be within 0..10")
    return {"n": decision["n"]}


def fallback(task, observations):
    return {"n": 1}


def make(client, **kw):
    tool = ToolSpec("lookup", "double x", {"type": "object", "properties": {"x": {"type": "number"}}}, lookup)
    return Agent("t", "sys", [tool], {"type": "object", "properties": {"n": {"type": "number"}}},
                 validate, fallback, client=client, model="m", **kw)


@pytest.mark.asyncio
async def test_no_client_uses_the_fallback_and_says_why():
    run = await make(None).run({"q": 1})
    assert run.mode == "fallback" and run.decision == {"n": 1} and "no LLM" in run.reason


@pytest.mark.asyncio
async def test_model_calls_a_tool_then_finishes():
    client = ScriptedClient(reply(call("lookup", {"x": 4})), reply(call("finish", {"n": 8}, "c2")))
    run = await make(client).run({"q": 1})
    assert run.mode == "llm" and run.decision == {"n": 8}
    assert run.observations["lookup"] == {"value": 8}
    assert [s["type"] for s in run.steps] == ["tool", "finish"]
    tool_msg = [m for m in client.requests[1]["messages"] if m["role"] == "tool"][0]
    assert json.loads(tool_msg["content"]) == {"value": 8}


@pytest.mark.asyncio
async def test_out_of_bounds_decision_is_sent_back_once_then_accepted():
    client = ScriptedClient(reply(call("finish", {"n": 99})), reply(call("finish", {"n": 5}, "c2")))
    run = await make(client).run({})
    assert run.mode == "llm" and run.decision == {"n": 5}
    assert run.steps[0]["type"] == "rejected"


@pytest.mark.asyncio
async def test_two_rejected_decisions_fall_back():
    client = ScriptedClient(reply(call("finish", {"n": 99})), reply(call("finish", {"n": -3}, "c2")))
    run = await make(client).run({})
    assert run.mode == "fallback" and run.decision == {"n": 1} and "rejected twice" in run.reason


@pytest.mark.asyncio
async def test_provider_error_falls_back():
    run = await make(ScriptedClient(error=RuntimeError("429"))).run({})
    assert run.mode == "fallback" and "model call failed" in run.reason


@pytest.mark.asyncio
async def test_never_finishing_hits_the_step_limit_and_falls_back():
    replies = [reply(call("lookup", {"x": 1}, f"c{i}")) for i in range(3)]
    run = await make(ScriptedClient(*replies), max_steps=3).run({})
    assert run.mode == "fallback" and "step limit" in run.reason


@pytest.mark.asyncio
async def test_unknown_tool_and_bad_json_are_reported_to_the_model_not_raised():
    client = ScriptedClient(reply(call("nope", {})), reply(call("lookup", "{bad", "c2")), reply(call("finish", {"n": 2}, "c3")))
    run = await make(client).run({})
    assert run.decision == {"n": 2}
    results = [json.loads(m["content"]) for m in client.requests[2]["messages"] if m["role"] == "tool"]
    assert "unknown tool" in results[0]["error"] and "not valid JSON" in results[1]["error"]


@pytest.mark.asyncio
async def test_a_broken_tool_does_not_crash_the_run():
    async def boom(args):
        raise KeyError("x")
    tool = ToolSpec("lookup", "d", {"type": "object"}, boom)
    client = ScriptedClient(reply(call("lookup", {})), reply(call("finish", {"n": 3}, "c2")))
    agent = Agent("t", "s", [tool], {"type": "object"}, validate, fallback, client=client, model="m")
    assert (await agent.run({})).decision == {"n": 3}


@pytest.mark.asyncio
async def test_record_run_never_raises():
    class Db:
        async def execute(self, *a):
            raise RuntimeError("db down")
    await record_run(Db(), await make(None).run({}))

"""Assistant tool loop: reads run, writes are only proposed, confirmed writes are re-validated."""

from __future__ import annotations

import json
from typing import Any

import pytest

from rai_gateway.assistant import engine
from rai_gateway.assistant.catalog import BY_NAME, TOOLS, Tool

MESSAGES = [{"role": "user", "content": "hello"}]


class ScriptedLlm:
    """Returns the scripted replies in order and records every prompt it was shown."""

    def __init__(self, *replies: Any) -> None:
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.prompts: list[str] = []

    async def __call__(self, system: str, user: str) -> str:
        self.prompts.append(user)
        return self.replies.pop(0)


class FakeApi:
    def __init__(self, result: engine.ApiResult | None = None) -> None:
        self.calls: list[tuple[str, str, dict, dict | None]] = []
        self.result = result or engine.ApiResult(True, 200, {"items": [{"id": "abc"}]})

    async def __call__(self, tool: Tool, path: str, query: dict, body: dict | None):
        self.calls.append((tool.method, path, query, body))
        return self.result


def _no_describe(_tool: Tool) -> dict[str, Any] | None:
    return {"type": "object"}


async def _chat(llm: ScriptedLlm, api: FakeApi) -> engine.ChatOutcome:
    return await engine.run_chat(MESSAGES, llm=llm, call_api=api, describe=_no_describe)


def test_catalog_names_are_unique_and_paths_resolve_their_params() -> None:
    assert len({t.name for t in TOOLS}) == len(TOOLS)
    for tool in TOOLS:
        assert tool.method in {"GET", "POST", "PATCH", "PUT"}
        # A write tool without a path param may only be a collection create.
        if tool.is_write and not tool.path_params:
            assert tool.method == "POST"


@pytest.mark.asyncio
async def test_plain_question_answers_without_tools() -> None:
    api = FakeApi()
    out = await _chat(ScriptedLlm({"type": "final", "message": "Hi"}), api)
    assert out.reply == "Hi" and out.pending_action is None and api.calls == []


@pytest.mark.asyncio
async def test_read_tool_runs_and_result_reaches_the_next_turn() -> None:
    llm = ScriptedLlm(
        {"type": "call", "tool": "ai_systems_list", "args": {"query": {"q": "bot", "evil": "x"}}},
        {"type": "final", "message": "You have one system."},
    )
    api = FakeApi()
    out = await _chat(llm, api)
    assert api.calls == [("GET", "/v1/ai-systems", {"q": "bot"}, None)]  # unknown filter dropped
    assert "HTTP 200" in llm.prompts[1] and '"abc"' in llm.prompts[1]
    assert out.reply == "You have one system."
    assert out.trace == [{"tool": "ai_systems_list", "ok": True, "status": 200}]


@pytest.mark.asyncio
async def test_write_is_proposed_not_executed() -> None:
    llm = ScriptedLlm(
        {
            "type": "propose",
            "summary": "Create AI system Support Bot",
            "calls": [{"tool": "ai_systems_create", "args": {"body": {"name": "Support Bot"}}}],
        }
    )
    api = FakeApi()
    out = await _chat(llm, api)
    assert api.calls == []
    assert out.pending_action is not None
    call = out.pending_action["calls"][0]
    assert (call["method"], call["path"]) == ("POST", "/v1/ai-systems")


@pytest.mark.asyncio
async def test_model_calling_a_write_directly_becomes_a_proposal() -> None:
    llm = ScriptedLlm(
        {"type": "call", "tool": "ai_systems_update", "args": {"id": "sys-1", "body": {"name": "X"}}}
    )
    api = FakeApi()
    out = await _chat(llm, api)
    assert api.calls == []
    assert out.pending_action["calls"][0]["path"] == "/v1/ai-systems/sys-1"


@pytest.mark.asyncio
async def test_bad_args_are_fed_back_and_path_traversal_is_rejected() -> None:
    llm = ScriptedLlm(
        {"type": "call", "tool": "ai_systems_get", "args": {"id": "../../admin"}},
        {"type": "call", "tool": "ai_systems_get", "args": {"id": ".."}},
        {"type": "final", "message": "Could not."},
    )
    api = FakeApi()
    out = await _chat(llm, api)
    assert api.calls == []
    assert "rejected" in llm.prompts[1] and "rejected" in llm.prompts[2]
    assert out.reply == "Could not."


@pytest.mark.asyncio
async def test_describe_returns_the_body_schema() -> None:
    llm = ScriptedLlm(
        {"type": "describe", "tool": "ai_systems_create"},
        {"type": "final", "message": "ok"},
    )
    await _chat(llm, FakeApi())
    assert "schema of ai_systems_create" in llm.prompts[1]


@pytest.mark.asyncio
async def test_unparseable_reply_is_returned_as_text_and_loop_is_bounded() -> None:
    out = await _chat(ScriptedLlm("just text"), FakeApi())
    assert out.reply == "just text"

    looping = ScriptedLlm(*[{"type": "call", "tool": "risk_overview", "args": {}}] * engine.MAX_STEPS)
    api = FakeApi()
    out = await _chat(looping, api)
    assert len(api.calls) == engine.MAX_STEPS
    assert "could not finish" in out.reply.lower()


@pytest.mark.asyncio
async def test_run_onboarding_is_a_ui_action() -> None:
    out = await _chat(ScriptedLlm({"type": "run_onboarding"}), FakeApi())
    assert out.ui_action == "run_onboarding"


@pytest.mark.asyncio
async def test_execute_runs_writes_in_order_and_stops_on_failure() -> None:
    api = FakeApi()
    calls = [
        {"tool": "ai_systems_create", "args": {"body": {"name": "A"}}},
        {"tool": "agents_create", "args": {"id": "sys-1", "body": {"name": "B"}}},
    ]
    results = await engine.execute_calls(calls, call_api=api)
    assert [c[1] for c in api.calls] == ["/v1/ai-systems", "/v1/ai-systems/sys-1/agents"]
    assert all(r["ok"] for r in results)

    failing = FakeApi(engine.ApiResult(False, 403, {"detail": "no"}))
    results = await engine.execute_calls(calls, call_api=failing)
    assert len(results) == 1 and results[0]["status"] == 403


@pytest.mark.asyncio
async def test_execute_refuses_reads_and_unknown_tools() -> None:
    for bad in (
        [{"tool": "ai_systems_list", "args": {}}],
        [{"tool": "drop_database", "args": {}}],
        [],
    ):
        with pytest.raises(engine.ToolArgError):
            await engine.execute_calls(bad, call_api=FakeApi())


def test_parse_action_tolerates_fences_and_prose() -> None:
    assert engine.parse_action('```json\n{"type":"final","message":"a {b}"}\n```') == {
        "type": "final",
        "message": "a {b}",
    }
    assert engine.parse_action("no json here") is None
    assert BY_NAME["ai_systems_create"].is_write


def test_rule_effect_must_be_a_real_policy_effect():
    import pytest

    from rai_gateway.assistant.engine import ToolArgError, build_request

    tool = BY_NAME["content_rules_create"]
    args = {"id": "sys-1", "body": {"title": "t", "content": "c", "effect": "BLOCK"}}
    with pytest.raises(ToolArgError, match="DENY"):
        build_request(tool, args)
    args["body"]["effect"] = "DENY"
    assert build_request(tool, args)[0].endswith("/sys-1/policies/content-rules")

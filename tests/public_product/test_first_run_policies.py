"""First-run policy step: rule extraction, creation and enforcement at runtime."""
# ruff: noqa: F811 - pytest fixtures are imported, then named as parameters.

from __future__ import annotations

import io
import json
import uuid

import pytest

# Shares the empty-schema database and sign-in helpers of the first-run tests.
from test_first_run import (  # noqa: F401
    BASE,
    _apply_resources,
    _login,
    _systems_body,
    client,
    db_engine,
    headers,
)

from rai_gateway.core import ai_provider
from rai_gateway.governance.domain import GovernanceRole
from rai_gateway.models.policy_engine import ContentRule, ToolRule
from rai_gateway.models.policy_library import PolicyRuleLink, PolicySource
from rai_gateway.models.risk_intelligence import RuntimeBudget
from rai_gateway.risk_intelligence.content_evaluation import ContentEvaluationService

POLICY = """# Support policy

Agents must never reveal a customer's bank account number.
Creating an order above 500 requires approval from a business approver.
Support agents may use at most 1000 requests per day.
Every quarter the team reviews a sample of conversations.
"""

QUOTES = {
    "content": "Agents must never reveal a customer's bank account number.",
    "tool": "Creating an order above 500 requires approval from a business approver.",
    "budget": "Support agents may use at most 1000 requests per day.",
    "manual": "Every quarter the team reviews a sample of conversations.",
}


def _requirement(block: str, quote: str, kind: str, **extra) -> dict:
    return {
        "block": block,
        "quote": quote,
        "modality": "MUST",
        "topic": "support",
        "kind": kind,
        "title": f"{kind} rule",
        "effect": None,
        "applies_to": None,
        "tool_selector": None,
        "approval_role": None,
        "system_keys": [],
        "budget": None,
        **extra,
    }


def _fake_ai(monkeypatch, prompts: list[str]) -> None:
    """A model that finds the four statements, and invents one that is not there."""

    def generate(config, *, system, user, **kwargs):
        prompts.append(user)
        block = user.split("Source blocks:\n\n[")[1].split("]")[0]
        items = [
            _requirement(
                block,
                QUOTES["content"],
                "content_rule",
                effect="DENY",
                applies_to="OUTPUT",
                system_keys=["S1"],
            ),
            _requirement(
                block,
                QUOTES["tool"],
                "tool_rule",
                effect="REQUIRE_APPROVAL",
                tool_selector="create order",
                approval_role="business approver",
            ),
            _requirement(
                block,
                QUOTES["budget"],
                "runtime_budget",
                budget={
                    "metric": "REQUESTS",
                    "limit_value": 1000,
                    "currency": None,
                    "window": "DAY",
                },
            ),
            _requirement(block, QUOTES["manual"], "manual_process"),
            _requirement(block, "Agents must always smile.", "content_rule"),
        ]
        return ai_provider.GenerationResult(
            text=json.dumps({"requirements": items}), usage={}
        )

    config = ai_provider.AIConfig(
        provider="openai",
        model="test-model",
        models=("test-model",),
        api_key="test",
        base_url=None,
        gcp_project_id=None,
        location=None,
        service_account_json=None,
        timeout_seconds=5,
        temperature=0,
        max_output_tokens=1000,
        structured_mode="JSON_OBJECT",
        allow_private_network=False,
    )
    monkeypatch.setattr(ai_provider, "try_load_ai_config", lambda: (config, None))
    monkeypatch.setattr(ai_provider, "generate_json_text", generate)


CONTEXT = json.dumps(
    {
        "systems": [{"ref": "a", "name": "Support"}, {"ref": "b", "name": "Sales"}],
        "tools": [
            {"ref": "crm|Create order", "name": "Create order", "system_refs": ["a"]},
            {"ref": "crm|Read order", "name": "Read order", "system_refs": ["a", "b"]},
        ],
    }
)


def _extract(client, headers, *, text="", files=None):
    return client.post(
        f"{BASE}/extract-rules",
        data={"context": CONTEXT, "text": text},
        files=files,
        headers=headers,
    )


def test_extraction_without_ai_explains_what_to_do(client, headers, monkeypatch):
    monkeypatch.setattr(ai_provider, "try_load_ai_config", lambda: (None, None))
    response = _extract(client, headers, text=POLICY)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "AI_NOT_CONFIGURED"
    assert ".env" in detail["message"]
    assert client.get(BASE, headers=headers).json()["ai"]["configured"] is False


@pytest.mark.parametrize("source", ["text", "markdown", "docx"])
def test_extraction_drafts_rules_and_saves_nothing(
    client, db_session, headers, monkeypatch, source
):
    prompts: list[str] = []
    _fake_ai(monkeypatch, prompts)
    counts = lambda: tuple(  # noqa: E731
        db_session.query(model).count()
        for model in (ContentRule, ToolRule, RuntimeBudget, PolicySource)
    )
    before = counts()
    if source == "text":
        response = _extract(client, headers, text=POLICY)
    elif source == "markdown":
        upload = ("policy.md", POLICY.encode(), "text/markdown")
        response = _extract(client, headers, files=[("files", upload)])
    else:
        docx = pytest.importorskip("docx")
        document = docx.Document()
        for line in QUOTES.values():
            document.add_paragraph(line)
        buffer = io.BytesIO()
        document.save(buffer)
        # One paragraph per block: answer each quote from the block that holds it.
        mapping = {quote: f"B{i}" for i, quote in enumerate(QUOTES.values(), 1)}
        original = ai_provider.generate_json_text

        def generate(config, **kwargs):
            reply = json.loads(original(config, **kwargs).text)
            for item in reply["requirements"]:
                item["block"] = mapping.get(item["quote"], item["block"])
            return ai_provider.GenerationResult(text=json.dumps(reply), usage={})

        monkeypatch.setattr(ai_provider, "generate_json_text", generate)
        upload = (
            "policy.docx",
            buffer.getvalue(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        response = _extract(client, headers, files=[("files", upload)])
    assert response.status_code == 200, response.text
    body = response.json()
    assert counts() == before

    assert "Create order" in prompts[0] and "S2: Sales" in prompts[0]
    rules = {rule["family"]: rule for rule in body["rules"]}
    assert set(rules) == {"content", "tool", "budget"}
    assert rules["content"]["system_refs"] == ["a"]
    assert rules["content"]["enabled"] is True
    assert rules["content"]["content"]["content"] == QUOTES["content"]
    # Matched to the real tool, limited to the system that uses it, and off.
    assert rules["tool"]["tool"]["tool_ref"] == "crm|Create order"
    assert rules["tool"]["system_refs"] == ["a"]
    assert rules["tool"]["enabled"] is False
    assert rules["budget"]["budget"] == {
        "metric": "ACTION_COUNT",
        "limit_value": 1000.0,
        "window_seconds": 86400,
        "effect": "DENY",
    }
    assert rules["budget"]["system_refs"] == ["a", "b"]
    assert [note["quote"] for note in body["notes"]] == [QUOTES["manual"]]
    assert any("did not quote" in warning for warning in body["warnings"])
    assert body["documents"] and rules["content"]["document_ref"] == "doc1"


def test_extraction_refuses_unreadable_and_oversized_input(
    client, headers, monkeypatch
):
    _fake_ai(monkeypatch, [])
    picture = ("scan.png", b"\x89PNG\r\n", "image/png")
    bad = _extract(client, headers, files=[("files", picture)])
    assert bad.status_code == 400, bad.text
    assert bad.json()["detail"]["error"] == "UNREADABLE"

    huge = ("big.txt", ("word " * 40_000).encode(), "text/plain")
    too_big = _extract(client, headers, files=[("files", huge)])
    assert too_big.json()["detail"]["error"] == "TOO_LARGE", too_big.text

    assert _extract(client, headers).status_code == 400


def test_provider_failure_is_reported(client, headers, monkeypatch):
    _fake_ai(monkeypatch, [])

    def fail(*args, **kwargs):
        raise ai_provider.AIProviderError("auth", "rejected")

    monkeypatch.setattr(ai_provider, "generate_json_text", fail)
    response = _extract(client, headers, text=POLICY)
    assert response.status_code == 502, response.text
    assert "manually" in response.json()["detail"]["message"]


# --------------------------------------------------------------------------- apply


def _setup_system(client, headers, tag: str) -> tuple[dict, dict]:
    resources = _apply_resources(client, headers, tag)
    response = client.post(
        f"{BASE}/apply/systems",
        json=_systems_body(resources, [f"Policy {tag}"]),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return resources, response.json()["systems"][0]


def _crm(resources: dict) -> tuple[dict, dict, dict]:
    """The custom MCP server and its two tools."""
    entry = next(r for r in resources["resources"] if r["ref"] == "crm")
    server, create, read = entry["items"]
    return server, create, read


def _authorize(client, system, server, tool, parameters=None):
    response = client.post(
        "/v1/runtime/actions/authorize",
        json={
            "integration_id": server["id"],
            "tool_id": tool["id"],
            "parameters": parameters or {},
        },
        headers={"X-RAI-Agent-Key": system["credential"]["secret"]},
    )
    return response.json()


def _policies_body(system: dict, tool_id: str) -> dict:
    ids = [system["ai_system_id"]]
    return {
        "documents": [{"ref": "doc1", "title": "Support policy", "text": POLICY}],
        "rules": [
            {
                "family": "content",
                "title": "No bank accounts",
                "ai_system_ids": ids,
                "document_ref": "doc1",
                "quote": QUOTES["content"],
                "content": {
                    "content": QUOTES["content"],
                    "effect": "DENY",
                    "applies_to": "OUTPUT",
                },
            },
            {
                "family": "tool",
                "title": "Block large orders",
                "ai_system_ids": ids,
                "document_ref": "doc1",
                "quote": QUOTES["tool"],
                "tool": {
                    "tool_id": tool_id,
                    "effect": "DENY",
                    "conditions": [
                        {
                            "parameter_path": "total_value",
                            "operator": "gt",
                            "value_type": "number",
                            "value": 500,
                        }
                    ],
                },
            },
            {
                "family": "budget",
                "title": "Three actions a day",
                "ai_system_ids": ids,
                "budget": {
                    "metric": "ACTION_COUNT",
                    "limit_value": 3,
                    "window_seconds": 86400,
                },
            },
            {
                "family": "budget",
                "title": "Daily spend",
                "ai_system_ids": ids,
                "budget": {
                    "metric": "COST",
                    "limit_value": 10,
                    "window_seconds": 86400,
                },
            },
        ],
    }


def test_created_rules_are_enforced_at_runtime(client, db_session, headers):
    resources, system = _setup_system(client, headers, "enforce")
    server, create, read = _crm(resources)
    body = _policies_body(system, create["id"])
    response = client.post(f"{BASE}/apply/policies", json=body, headers=headers)
    assert response.status_code == 200, response.text
    created = response.json()["rules"]
    assert [r["status"] for r in created] == ["created"] * 4

    # A cost budget must not deny tool calls, which carry no cost.
    small = _authorize(client, system, server, create, {"total_value": 10})
    assert small["allowed"] is True, small
    large = _authorize(client, system, server, create, {"total_value": 900})
    assert large["allowed"] is False and large["effect"] == "DENY", large
    for _ in range(4):
        last = _authorize(client, system, server, read)
    assert last["allowed"] is False, last
    assert str(last["reason_code"]).startswith("BUDGET"), last

    system_id = uuid.UUID(system["ai_system_id"])
    applicable = ContentEvaluationService(db_session).get_applicable_rules(
        system_id, content_type="OUTPUT"
    )
    assert [r["title"] for r in applicable] == ["No bank accounts"]

    source = (
        db_session.query(PolicySource)
        .filter(PolicySource.title == "Support policy")
        .one()
    )
    linked = (
        db_session.query(PolicyRuleLink)
        .filter(PolicyRuleLink.policy_source_id == source.id)
        .count()
    )
    assert linked == 2

    again = client.post(f"{BASE}/apply/policies", json=body, headers=headers)
    assert again.status_code == 200, again.text
    assert [r["status"] for r in again.json()["rules"]] == ["existing"] * 4
    assert [r["rule_id"] for r in again.json()["rules"]] == [
        r["rule_id"] for r in created
    ]
    db_session.expire_all()
    assert (
        db_session.query(RuntimeBudget)
        .filter(RuntimeBudget.ai_system_id == system_id)
        .count()
        == 2
    )


def test_disabled_rule_is_stored_but_not_applied(client, headers):
    resources, system = _setup_system(client, headers, "disabled")
    server, create, _ = _crm(resources)
    rule = {
        "family": "tool",
        "title": "Block every order",
        "ai_system_ids": [system["ai_system_id"]],
        "enabled": False,
        "tool": {"tool_id": create["id"], "effect": "DENY"},
    }
    response = client.post(
        f"{BASE}/apply/policies", json={"rules": [rule]}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["rules"][0]["enabled"] is False
    assert _authorize(client, system, server, create)["allowed"] is True


def test_policies_stage_is_all_or_nothing(client, db_session, headers):
    resources, system = _setup_system(client, headers, "rollback")
    _, create, _ = _crm(resources)
    body = _policies_body(system, create["id"])
    # A tool no agent of this system declares.
    body["rules"].append(
        {
            "family": "tool",
            "title": "Unrelated tool",
            "ai_system_ids": [system["ai_system_id"]],
            "tool": {"tool_id": str(uuid.uuid4()), "effect": "DENY"},
        }
    )
    before = db_session.query(ContentRule).count()
    response = client.post(f"{BASE}/apply/policies", json=body, headers=headers)
    assert response.status_code == 400, response.text
    assert "does not use that tool" in response.json()["detail"]["message"]
    db_session.expire_all()
    assert db_session.query(ContentRule).count() == before

    duplicate = _policies_body(system, create["id"])
    duplicate["rules"].append(dict(duplicate["rules"][0]))
    clash = client.post(f"{BASE}/apply/policies", json=duplicate, headers=headers)
    assert clash.status_code == 400, clash.text


def test_rule_shape_and_permission_are_checked(client, db_session, headers):
    rule = {
        "family": "content",
        "title": "Approval without approver",
        "ai_system_ids": [str(uuid.uuid4())],
        "content": {"content": "x", "effect": "REQUIRE_APPROVAL"},
    }
    invalid = client.post(
        f"{BASE}/apply/policies", json={"rules": [rule]}, headers=headers
    )
    assert invalid.status_code in (400, 422), invalid.text

    auditor = _login(client, db_session, role=GovernanceRole.AUDITOR)
    rule["content"]["effect"] = "DENY"
    denied = client.post(
        f"{BASE}/apply/policies", json={"rules": [rule]}, headers=auditor
    )
    assert denied.status_code == 403, denied.text
    assert _extract(client, auditor, text=POLICY).status_code == 403

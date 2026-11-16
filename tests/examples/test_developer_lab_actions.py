"""Developer lab action and case-assessment tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[2] / "examples" / "developer_lab"
sys.path.insert(0, str(LAB))

import provision  # noqa: E402
from actions import ToolError, apply_action, load_state  # noqa: E402
from run_case import assess_content_case, assess_tool_case  # noqa: E402


def test_allowed_ticket_is_written(tmp_path: Path) -> None:
    result = apply_action(
        "create_support_ticket",
        {"priority": "normal", "summary": "Damaged order"},
        state_dir=tmp_path,
    )
    state = load_state(tmp_path)
    assert result["artifact_id"] == "SUP-1001"
    assert state["tickets"][0]["summary"] == "Damaged order"
    assert state["audit"][0]["tool_key"] == "create_support_ticket"


def test_budgeted_order_tool_can_create_an_order(tmp_path: Path) -> None:
    result = apply_action(
        "create_budgeted_order",
        {"amount": 1200, "currency": "USD", "account": "Acme"},
        state_dir=tmp_path,
    )
    state = load_state(tmp_path)
    assert result["order"]["amount"] == 1200
    assert state["audit"][0]["tool_key"] == "create_budgeted_order"


def test_approved_version_with_missing_dependencies_gets_a_new_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required = [("model", "model-1"), ("tool", "tool-1")]
    created_version = {"id": "version-2", "lifecycle_status": "DRAFT"}

    def fake_request_json(
        method: str, url: str, *, token: str, payload: dict | None = None
    ) -> list[dict] | dict:
        del token
        if method == "GET" and url.endswith("/agents/agent-1/versions"):
            return [{"id": "version-1", "lifecycle_status": "APPROVED"}]
        if method == "GET" and url.endswith("/agent-versions/version-1/dependencies"):
            return [{"resource_type": "model", "resource_id": "model-1"}]
        if method == "POST" and url.endswith("/agents/agent-1/versions"):
            assert payload is not None
            assert "registered dependencies" in payload["change_summary"]
            return created_version
        raise AssertionError(f"Unexpected request: {method} {url}")

    monkeypatch.setattr(provision, "request_json", fake_request_json)
    version = provision._ensure_version(
        "http://example.test",
        "token",
        "agent-1",
        required_dependencies=required,
    )
    assert version == created_version


def test_monitor_rule_provisioning_creates_mark_and_monitor_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    created_rules: list[dict] = []

    def fake_request_json(
        method: str,
        url: str,
        *,
        token: str,
        payload: dict | None = None,
    ) -> list[dict] | dict:
        del token
        calls.append((method, url, payload))
        if method == "GET":
            return {"items": created_rules}
        rule = payload or {}
        created_rules.append(rule)
        return rule

    monkeypatch.setattr(provision, "request_json", fake_request_json)
    arguments = {
        "system_id": "system-1",
        "tool": {"id": "tool-1", "name": "Create budgeted order"},
        "spec": {
            "name": "Monitor budgeted orders",
            "tool_key": "create_budgeted_order",
            "scope": "RUNTIME_IDENTITY",
            "signal_type": "UNUSUAL_TOOL_FREQUENCY",
            "window_seconds": 86400,
            "aggregation": "COUNT",
            "threshold": 1,
            "effect": "MARK_AND_MONITOR",
            "monitor_duration_seconds": 86400,
            "enabled": True,
        },
    }
    rule = provision._ensure_monitor_rule("http://example.test", "token", **arguments)
    repeated = provision._ensure_monitor_rule(
        "http://example.test", "token", **arguments
    )
    assert rule["effect"] == "MARK_AND_MONITOR"
    assert repeated == rule
    assert calls[1][2]["tool_id"] == "tool-1"
    assert calls[1][2]["ai_system_id"] == "system-1"
    assert len(created_rules) == 1


def test_integration_is_recreated_for_new_runtime_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[dict] = []

    def fake_request_json(
        method: str,
        url: str,
        *,
        token: str,
        payload: dict | None = None,
    ) -> list[dict] | dict:
        del token
        if method == "GET":
            return {
                "items": [
                    {
                        "integration_key": "sales-ai-sdk",
                        "runtime_identity_id": "old-identity",
                    }
                ]
            }
        payloads.append(payload or {})
        return {"id": "new-integration", **(payload or {})}

    monkeypatch.setattr(provision, "request_json", fake_request_json)
    integration = provision._ensure_integration(
        "http://example.test", "token", "sales-ai", "new-identity"
    )
    assert integration["integration_key"] == "sales-ai-sdk-new-identity"
    assert integration["runtime_identity_id"] == "new-identity"
    assert len(payloads) == 1


def test_finops_demo_records_estimated_metering_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans: list[dict] = []

    def fake_request_json(
        method: str,
        url: str,
        *,
        agent_key: str | None = None,
        payload: dict | None = None,
    ) -> dict:
        assert method == "POST"
        assert agent_key == "runtime-key"
        if url.endswith("/sdk/v1/sessions"):
            return {"session_id": "session-1"}
        spans.append(payload or {})
        return {"id": f"span-{len(spans)}"}

    monkeypatch.setattr(provision, "request_json", fake_request_json)
    provision._ensure_finops_demo(
        "http://example.test",
        api_key="runtime-key",
        integration_id="integration-1",
        identity_id="identity-1",
        system_key="sales-ai",
        spec={
            "provider": "developer-lab-demo",
            "model": "sample-text-v1",
            "samples": [
                {
                    "key": "model-call-1",
                    "input_tokens": 1200,
                    "output_tokens": 300,
                    "estimated_cost": 0.45,
                },
                {
                    "key": "model-call-2",
                    "input_tokens": 2500,
                    "output_tokens": 700,
                    "estimated_cost": 1.05,
                },
            ],
        },
    )
    assert len(spans) == 2
    assert all(span["cost"]["source"] == "ESTIMATED" for span in spans)
    assert all(span["tokens_are_estimated"] for span in spans)
    assert sum(span["cost"]["amount"] for span in spans) == 1.5
    assert spans[0]["external_span_id"] == "developer-lab:identity-1:model-call-1"


def test_shell_runs_a_real_process_only_for_the_fixed_command(tmp_path: Path) -> None:
    result = apply_action(
        "run_shell",
        {"command": "python -c print-governed"},
        state_dir=tmp_path,
    )
    assert load_state(tmp_path)["processes"][0]["stdout"] == "governed"
    assert result["process"]["stdout"] == "governed"
    with pytest.raises(ToolError):
        apply_action("run_shell", {"command": "rm -rf /"}, state_dir=tmp_path)


def test_read_file_stays_in_the_sandbox(tmp_path: Path) -> None:
    text = apply_action("read_file", {"path": "README.md"}, state_dir=tmp_path)
    assert "sandbox" in text["content"].lower()
    with pytest.raises(ToolError):
        apply_action(
            "read_file", {"path": "../knowledge/payroll.md"}, state_dir=tmp_path
        )


def test_retrieve_reads_the_collection_file(tmp_path: Path) -> None:
    hr = apply_action(
        "retrieve_documents",
        {"collection": "hr-policies"},
        state_dir=tmp_path,
    )
    assert "section 4.2" in hr["excerpt"]
    payroll = apply_action(
        "retrieve_documents", {"collection": "payroll"}, state_dir=tmp_path
    )
    assert "bank account" in payroll["excerpt"]


def test_case_assessment_requires_a_real_side_effect() -> None:
    ok, _ = assess_tool_case(
        expect="ALLOW",
        effect="ALLOW",
        execution_attempted=True,
        execution_status="SUCCEEDED",
        state_changed=True,
    )
    assert ok
    blocked, _ = assess_tool_case(
        expect="ALLOW",
        effect="ALLOW",
        execution_attempted=False,
        execution_status="NOT_ATTEMPTED",
        state_changed=False,
    )
    assert not blocked
    denied, _ = assess_tool_case(
        expect="DENY",
        effect="DENY",
        execution_attempted=False,
        execution_status="NOT_ATTEMPTED",
        state_changed=False,
        expected_reason_code="BUDGET_EXCEEDED",
        reason_code="BUDGET_EXCEEDED",
    )
    assert denied
    wrong_reason, _ = assess_tool_case(
        expect="DENY",
        effect="DENY",
        execution_attempted=False,
        execution_status="NOT_ATTEMPTED",
        state_changed=False,
        expected_reason_code="BUDGET_EXCEEDED",
        reason_code="POLICY_DENIED",
    )
    assert not wrong_reason
    leaked, _ = assess_tool_case(
        expect="REQUIRE_APPROVAL",
        effect="REQUIRE_APPROVAL",
        execution_attempted=False,
        execution_status="NOT_ATTEMPTED",
        state_changed=True,
    )
    assert not leaked


def test_content_assessment() -> None:
    ok, _ = assess_content_case(expect_violated=True, violated=True)
    assert ok
    clean, _ = assess_content_case(expect_violated=False, violated=True)
    assert not clean

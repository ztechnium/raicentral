"""Release 1 end-to-end smoke test.

This script exercises the first-release flow:

- Health check
- Create prompt + version (draft)
- Approve version (biz_approved -> active)
- Allowlist system/module
- Execute prompt via /v1/execute
- Query executions (admin)

Prereqs:
- Docker stack running (docker compose up -d)
- A development gateway with a shared admin token: set ADMIN_TOKEN in .env and
  export the same value here (it is disabled unless set; never use it in production)
- API_KEY set to a client API key for SYSTEM_ID (see docs/API_KEY_STORAGE.md);
  not needed if the gateway runs with REQUIRE_API_KEY=false

Run:
- python scripts/e2e_release1_smoke.py
"""

from __future__ import annotations

import os

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
SYSTEM_ID = os.environ.get("SYSTEM_ID", "test_system")
MODULE = os.environ.get("MODULE", "test_module")
PROMPT_ID = os.environ.get("PROMPT_ID", "release1_e2e_prompt")
PROMPT_VERSION = int(os.environ.get("PROMPT_VERSION", "1"))
API_KEY = os.environ.get("API_KEY", "")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    _assert(bool(ADMIN_TOKEN), "Export ADMIN_TOKEN to match the development gateway")
    admin_headers = {"X-Admin-Token": ADMIN_TOKEN}

    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{BASE_URL}/health")
        print("health", r.status_code, r.text)
        _assert(r.status_code == 200, "Health check failed")

        # Create prompt (idempotent-ish)
        r = client.post(
            f"{BASE_URL}/admin/api/prompts",
            headers=admin_headers,
            json={
                "prompt_id": PROMPT_ID,
                "name": "Release 1 E2E Prompt",
                "domain": "demo",
                "description": "Created by e2e_release1_smoke.py",
                "owner_system_id": SYSTEM_ID,
            },
        )
        if r.status_code not in (200, 409):
            print("create prompt failed", r.status_code, r.text)
            return 2
        print("create prompt", r.status_code)

        # Create version as draft
        r = client.post(
            f"{BASE_URL}/admin/api/prompt-versions",
            headers=admin_headers,
            json={
                "prompt_id": PROMPT_ID,
                "version": PROMPT_VERSION,
                "status": "draft",
                "template": "Return a short greeting to {name}.",
                "required_vars_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                "model_policy": {"default_model": "gpt-5.2-codex"},
                "policy_version": "v1.0",
                "created_by": "e2e",
            },
        )
        if r.status_code not in (200, 409):
            print("create prompt version failed", r.status_code, r.text)
            return 3
        print("create prompt version", r.status_code)

        # Approve: draft -> biz_approved
        r = client.post(
            f"{BASE_URL}/admin/api/prompt-versions/approve",
            headers=admin_headers,
            json={
                "prompt_id": PROMPT_ID,
                "version": PROMPT_VERSION,
                "approval_type": "biz_approved",
                "approved_by": "e2e",
            },
        )
        if r.status_code not in (200, 409):
            print("biz approve failed", r.status_code, r.text)
            return 4
        print("biz approve", r.status_code, r.text)

        # Approve: biz_approved -> active
        r = client.post(
            f"{BASE_URL}/admin/api/prompt-versions/approve",
            headers=admin_headers,
            json={
                "prompt_id": PROMPT_ID,
                "version": PROMPT_VERSION,
                "approval_type": "rai_approved",
                "approved_by": "e2e",
            },
        )
        if r.status_code not in (200, 409):
            print("rai approve failed", r.status_code, r.text)
            return 5
        print("rai approve", r.status_code, r.text)

        # Allowlist
        r = client.post(
            f"{BASE_URL}/admin/api/allowlist",
            headers=admin_headers,
            json={
                "prompt_id": PROMPT_ID,
                "version": PROMPT_VERSION,
                "system_id": SYSTEM_ID,
                "module": MODULE,
                "allowed": True,
            },
        )
        _assert(r.status_code == 200, f"allowlist failed: {r.status_code} {r.text}")
        print("allowlist ok")

        # Execute
        payload = {
            "prompt_id": PROMPT_ID,
            "prompt_version": PROMPT_VERSION,
            "system_id": SYSTEM_ID,
            "module": MODULE,
            "variables": {"name": "World"},
        }
        r = client.post(
            f"{BASE_URL}/v1/execute", json=payload, headers={"X-API-Key": API_KEY}
        )
        _assert(r.status_code == 200, f"execute failed: {r.status_code} {r.text}")
        data = r.json()
        print(
            "execute ok",
            "execution_id",
            data.get("execution_id"),
            "model",
            data.get("metadata", {}).get("llm_model"),
        )

        # Query executions
        r = client.post(
            f"{BASE_URL}/admin/api/executions/query",
            headers=admin_headers,
            json={"limit": 10, "system_id": SYSTEM_ID, "prompt_id": PROMPT_ID},
        )
        _assert(
            r.status_code == 200, f"query executions failed: {r.status_code} {r.text}"
        )
        rows = r.json()
        print("executions returned", len(rows))
        _assert(len(rows) >= 1, "expected at least one execution")

        print("E2E PASS")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print("E2E FAIL:", e)
        raise

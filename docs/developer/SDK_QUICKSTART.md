# Developer quickstart — RAI SDK

This edition has one organization. Sign in with the bootstrap admin. Do not create an organization and do not send `organization_id`.

## 1. Provision

1. Start the API. See the [README](../../README.md).
2. Register an AI system, an agent, an agent version, and tools. The [developer lab](../../examples/developer_lab/README.md) does this for the reference agents.
3. Create a runtime identity and issue an API key (`X-RAI-Agent-Key`).
4. Create an SDK integration:

```http
POST /v1/productization/integrations
Authorization: Bearer <access-token>

{
  "integration_key": "sales-prod",
  "name": "Sales AI",
  "runtime_identity_id": "...",
  "enforcement_mode": "HOOK_ENFORCED",
  "fail_closed": true
}
```

## 2. Install an SDK

The clients live in their own repositories. Install one of them into the agent project. Do not install the copies under `packages/` in this tree.

Python, from [github.com/ztechnium/rai-python-sdk](https://github.com/ztechnium/rai-python-sdk):

```bash
pip install rai-control-plane-sdk
```

```bash
pip install "rai-control-plane-sdk @ git+https://github.com/ztechnium/rai-python-sdk.git"
```

TypeScript, from [github.com/ztechnium/rai-typescript-sdk](https://github.com/ztechnium/rai-typescript-sdk):

```bash
npm install @ztechnium/rai-sdk
```

```bash
npm install github:ztechnium/rai-typescript-sdk
```

## 3. Integrate

```python
from rai_sdk import RAIClient

client = RAIClient(base_url, api_key=key, integration_id=integration_id)
client.heartbeat()
session = client.start_session(channel="app")
session.observe_input(user_text)
decision = session.authorize_action(tool="create_order", parameters={...})
if decision.allowed:
    result = do_side_effect()
    decision.report_execution(client, status="SUCCEEDED")
session.end()
```

Never execute a consequential side effect unless `decision.allowed` is true. On `RAIError(code="RAI_UNAVAILABLE")`, fail closed.

## 4. Reference

See `examples/sales_ai_reference/main.py` and `docs/api/sdk-v1-openapi.yaml`.

# Python SDK

The Python client is published from [github.com/ztechnium/rai-python-sdk](https://github.com/ztechnium/rai-python-sdk). That repository is the source of truth. Install it into the environment that runs your agent. Do not import the mirror under `packages/python-sdk` in this repository.

## Installation

From PyPI:

```bash
pip install rai-control-plane-sdk
```

From the source repository:

```bash
pip install "rai-control-plane-sdk @ git+https://github.com/ztechnium/rai-python-sdk.git"
```

Python 3.10 or newer is required.

## Configuration

```python
import os
from rai_sdk import RAIClient

client = RAIClient(
    os.environ["RAI_BASE_URL"],
    api_key=os.environ["RAI_AGENT_KEY"],
    integration_id=os.environ["RAI_INTEGRATION_ID"],
)
```

`api_key` is the runtime credential sent as `X-RAI-Agent-Key`. `integration_id` is the SDK integration created for this instance.

## Authorize before acting

```python
client.heartbeat()
session = client.start_session(channel="application")
session.observe_input("Create an order")
decision = session.authorize_action(
    tool="create_order",
    parameters={"sku": "abc", "qty": 1},
)
if decision.allowed:
    result = create_order()
    decision.report_execution(client, status="SUCCEEDED")
session.end()
```

Do not run a consequential side effect unless `decision.allowed` is true. If RAI cannot be reached, `RAIError` with code `RAI_UNAVAILABLE` is raised and the agent should fail closed.

Admin sign-in for the console and for provisioning stays on `POST /v1/auth/login`. That token is not the SDK agent key.

# Developer lab

These examples register a catalog in a running community-edition API, then ask RAI to execute tools. The local tool process writes tickets, orders, documents, and audit rows. RAI calls that process only when the decision is ALLOW. A deny or an approval hold does not change `state/records.json`.

The catalog covers:

- AI systems: Support AI, Sales AI, Knowledge assistant, Coding agent, Citizen assistant
- Models bound to each agent version
- One MCP server, `developer-lab-mcp`, whose endpoint is the local tool runtime
- Tools on that server
- Policy Library sources, with each RAI rule linked to the source obligation
- Tool rules that ALLOW, DENY, or REQUIRE_APPROVAL
- A Sales AI runtime amount budget that denies budgeted orders over 1000 per 24-hour window
- A Sales AI `MARK_AND_MONITOR` rule that creates an active monitor after a budgeted-order action
- Two clearly labeled, synthetic estimated model-call spans for FinOps (USD 1.50 total)
- Keyword content rules evaluated by the mock content evaluator

There is one instance. The scripts sign in as the bootstrap admin. They do not create an organization and they do not send `X-Organization-ID`.

## Before you run an example

1. Start the community edition and wait until http://localhost:8001/health is healthy. See the [README](../../README.md).
2. Use the bootstrap admin unless you have changed it: `admin@raigateway.local` / `ChangeMe123!`.

## Run Support AI

From the repository root. This starts the tool process on port 8765, registers Support AI, and runs its cases.

**Windows (PowerShell)**

```powershell
$env:RAI_BASE_URL = "http://localhost:8001"
$env:RAI_ADMIN_EMAIL = "admin@raigateway.local"
$env:RAI_ADMIN_PASSWORD = "ChangeMe123!"
python examples/support_ai_reference/main.py
```

**macOS or Linux**

```bash
export RAI_BASE_URL=http://localhost:8001
export RAI_ADMIN_EMAIL=admin@raigateway.local
export RAI_ADMIN_PASSWORD='ChangeMe123!'
python examples/support_ai_reference/main.py
```

If the API is inside Docker, set the tool URL so the container can reach your machine:

```text
RAI_TOOL_URL=http://host.docker.internal:8765/invoke
```

`support_ai_reference` uses that Docker URL unless `RAI_TOOL_URL` is already set. If you started the API on the host with uvicorn, set `RAI_TOOL_URL=http://127.0.0.1:8765/invoke`.

## Register every lab system

```text
python examples/developer_lab/provision.py
```

The script is idempotent. A new runtime credential is issued only when `examples/developer_lab/.rai-lab.json` does not already hold one for that system. Do not commit `.rai-lab.json`.

Then run one reference:

```text
python examples/sales_ai_reference/main.py
python examples/knowledge_assistant_reference/main.py
python examples/coding_agent_reference/main.py
python examples/government_assistant_reference/main.py
```

Or run cases directly:

```text
python examples/developer_lab/run_case.py --all
python examples/developer_lab/run_case.py sales-ai small-order
python examples/developer_lab/run_case.py sales-ai budget-over-limit
```

The tool process must already be listening on port 8765. `support_ai_reference` starts it. `provision.py` and the other reference scripts do not.

Workspace changes land in `examples/developer_lab/state/records.json`. A passing ALLOW case adds an audit row there. A passing DENY or REQUIRE_APPROVAL case leaves the file unchanged.

The Sales AI `budget-over-limit` case must be denied with reason `BUDGET_EXCEEDED` before the MCP tool runs. Its budget is created or updated when you provision the lab.
Running that case also triggers the Sales AI monitor rule, so an active monitor appears on `/risk/monitoring`. Provisioning records two synthetic FinOps spans; their USD 1.50 total is explicitly estimated sample data, not billed provider usage.

The coding-agent sandbox notes are in [sandbox/README.md](sandbox/README.md).

# TypeScript SDK

The TypeScript client is published from [github.com/ztechnium/rai-typescript-sdk](https://github.com/ztechnium/rai-typescript-sdk). That repository is the source of truth. Install it into the Node project that runs your agent. Do not import the mirror under `packages/typescript-sdk` in this repository.

## Installation

From npm:

```bash
npm install @ztechnium/rai-sdk
```

From the source repository:

```bash
npm install github:ztechnium/rai-typescript-sdk
```

Node.js 18 or newer is required.

## Configuration

```typescript
import { RAIClient } from "@ztechnium/rai-sdk";

const client = new RAIClient({
  baseUrl: process.env.RAI_BASE_URL!,
  apiKey: process.env.RAI_AGENT_KEY!,
  integrationId: process.env.RAI_INTEGRATION_ID!,
});
```

`apiKey` is the runtime credential sent as `X-RAI-Agent-Key`. Keep it on the server. `integrationId` is the SDK integration created for this instance.

## Authorize before acting

```typescript
await client.heartbeat();
const session = await client.startSession({ channel: "application" });
await session.observeInput({ content: "Send the receipt" });
const decision = await session.authorizeAction({
  tool: "send_email",
  parameters: { to: "user@example.com", subject: "Hello" },
});
if (decision.allowed) {
  await sendEmail();
  await decision.reportExecution(client, { status: "SUCCEEDED" });
}
await session.end();
```

HTTP failures and fail-closed transport errors throw `RAIError`. A policy denial is a decision object, not an exception. Do not run a consequential side effect unless `decision.allowed` is true.

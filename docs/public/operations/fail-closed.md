# Fail-Closed Behavior

RAI Control Plane denies actions by default when governance cannot confidently allow them.

## When RAI Denies

- Unknown or unregistered tool/integration
- Expired, revoked, or missing runtime credentials
- Policy evaluation errors (unless explicitly configured otherwise)
- Delegation scope exceeded
- Runtime budget exceeded
- Content evaluator fail-closed trigger on high-confidence risk

## When RAI Requires Approval

- Tool rules configured with `require_approval` effect
- Risk tier triggers mandatory human review
- Deployment gate failure without approved waiver

Approving a request does not run it. The caller sends the same action again, and
RAI allows it once: same runtime identity, same action and parameters, before the
approval expires. Provider proxy callers must resend an identical request body.
The next identical action needs a new approval.

## Configuration

Content evaluator supports explicit `fail_closed: true` for high-risk AI systems. This causes content evaluation errors to deny rather than pass through.

## Operational Implication

Plan for deny scenarios in your agent logic. Agents should handle `deny` and `require_approval` decisions gracefully rather than retrying indefinitely.

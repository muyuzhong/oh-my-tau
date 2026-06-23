---
name: project-conventions
description: NanoAgent self-review checklist for mechanism-vs-policy and layering before finishing an edit. Background guidance for any change under src/nanoagent.
user-invocable: false
---

# NanoAgent change checklist

The full rules live in `AGENTS.md` (always in context). This is the actionable
self-review to run **before claiming a change is done** — it covers the violations
a linter cannot catch.

## Layering (import direction: `agent -> ai -> utils`)

- [ ] `utils` imports neither `ai` nor `agent`.
- [ ] `ai` does not import `agent`.
- [ ] Provider-specific code is under `nanoagent.ai.providers` only.
- [ ] `lint-imports` passes.

## Mechanism, not policy (the part the linter misses)

Did this change smuggle harness policy into the framework? Reject if it adds any of:

- [ ] API-key discovery, default provider/model selection.
- [ ] Approval/permission rules (e.g. "allow read, deny write").
- [ ] Concrete filesystem tools, token-budget numbers, CLI/UI/session lifecycle.
- [ ] Business rules in `agent.loop`, `agent.tools`, or `ai.provider`.

If a requirement needs customization, prefer an **injected hook, protocol, or
wrapper** over baking the behavior in.

## Contracts

- [ ] Tool failures are returned as `ToolResultMessage(is_error=True)`, not raised.
- [ ] Every run ends with exactly one `AgentEnd` carrying a `RunResult`.
- [ ] The two stop-reason levels stay distinct: `ai.StopReason` (wire) vs
      `agent.StopReason` (whole run).
- [ ] Tool arguments validated with pydantic.

## Verify

```bash
lint-imports
pytest -q
```

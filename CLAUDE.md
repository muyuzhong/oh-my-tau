# CLAUDE.md

This repository is developed by the project owner and AI agents. Treat this file as the project-specific operating guide for future coding sessions.

## Project Intent

NanoAgent is a framework kernel, not a product. Keep the code focused on reusable agent runtime mechanisms:

- model/provider abstraction
- wire messages and stream events
- agent loop
- tool execution
- context assembly
- control hooks
- stateful `Agent`

Harness/application concerns come later and should stay out of the framework core.

## Layering Rules

The most important point: find the simplest solution, and only add complexity when necessary. Don't architect for the sake of frameworks—frameworks only mask problems.

The import direction is:

```text
nanoagent.agent -> nanoagent.ai -> nanoagent.utils
```

Follow these boundaries:

- `utils` must not import `ai` or `agent`.
- `ai` must not import `agent`.
- `agent` may depend on `ai` abstractions, but must not choose a provider, model, API key, or product policy.
- Provider-specific code belongs under `nanoagent.ai.providers`.
- Harness policy must not be smuggled into `agent.loop`, `agent.tools`, or `ai.provider`.

Keep `.importlinter` passing.

## Mechanism vs Policy

Framework code provides mechanisms. Harness code will provide policy.

Allowed in the framework:

- hooks
- protocols
- abstract configuration
- structured events
- structured results
- mock-driven tests

Keep out of the framework:

- concrete filesystem tools
- approval rules such as "allow read but deny write"
- API key discovery
- default provider selection
- token budget numbers
- CLI lifecycle
- UI behavior
- business rules

When a new requirement needs customization, prefer an injected hook, protocol, or wrapper. Add concrete policy only when building a harness layer.

## Code Style

- Keep modules small and responsibility-based.
- Prefer existing dataclass and protocol patterns.
- Use pydantic for tool argument validation.
- Encode tool failures as `ToolResultMessage(is_error=True)` instead of letting tool exceptions escape the loop.
- End every agent run with one `AgentEnd` carrying a `RunResult`.
- Keep the two stop-reason levels distinct:
  - `nanoagent.ai.StopReason`: provider/wire message stop reason.
  - `nanoagent.agent.StopReason`: whole-run terminal reason.

## Testing Expectations

Before claiming work is complete, run:

```bash
pytest -q
```

For changes that touch imports or module boundaries, also make sure:

```bash
pytest tests/test_import_contract.py -q
```

Use the mock provider for framework tests. Tests should not require real API keys or network access.

## Current Known Gaps

- No harness/application layer yet.
- No CLI/TUI/web UI.
- Provider adapters are minimal.
- Streaming accumulation is intentionally small and may need richer delta behavior later.
- Docs under `docs/superpowers` may contain process artifacts; README should remain the concise project map.

Do not treat these gaps as reasons to add product policy to the framework. Add the missing seam first, then let harness code decide the behavior.

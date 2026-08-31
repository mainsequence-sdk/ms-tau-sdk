# ADR: Interactive Provider Signin Attempts

Status: superseded by ADR 51

Superseded by [ADR 51: Consume Django Provider Control for Tau Execution](../adrs/adr-51-consume-django-provider-control-for-tau-execution.md).

Date: 2026-04-15

## Context

Astro already exposes provider auth state and provider-level signin/signoff controls for
auth-backed model providers.

That is enough for immediate signin paths such as env-backed API-key sync, but it is not enough for
interactive providers such as `openai-codex` and `github-copilot`.

The missing problem is not provider discovery.
The missing problem is orchestration for signin flows that may need one or more of these steps:

- open a browser URL
- wait for OAuth/device-code progress
- accept manual pasted callback input
- cancel an in-flight attempt
- finish asynchronously after the initial HTTP request returns

Pi already ships provider-specific OAuth helpers, including `openai-codex` and
`github-copilot`, but Astro did not have a provider-agnostic HTTP contract for interactive signin
lifecycle.

Without that lifecycle:

- the frontend cannot drive remote OAuth handoff flows
- `POST /api/model-providers/:provider/signin` cannot represent "signin started, but not finished"
- future interactive providers would require one-off route logic instead of a reusable framework

## Decision

Astro will add a generic interactive signin-attempt framework for model providers.

The accepted contract is:

- keep provider auth state separate from signin-attempt state
- keep `POST /api/model-providers/:provider/signin` as the entry point
- return `200` for immediate signin success
- return `202` plus an `attempt` object for interactive signin
- expose attempt lifecycle over dedicated status/manual/cancel endpoints
- translate provider-specific OAuth callbacks into a provider-agnostic attempt state machine

`openai-codex` and `github-copilot` are implemented on this framework.

## API

### Provider auth state

- `GET /api/model-providers`

This remains the provider-level source of truth for:

- `signInAvailable`
- `authenticated`
- `knownModelCount`
- `usableModelCount`

### Start signin

- `POST /api/model-providers/:provider/signin`

Success cases:

- `200` for immediate providers such as API-key sync
- `202` with `attempt` for interactive providers such as `openai-codex` and `github-copilot`

### Read attempt state

- `GET /api/model-providers/:provider/signin/:attemptId`

### Submit manual input

- `POST /api/model-providers/:provider/signin/:attemptId/manual`

Body:

```json
{
  "input": "http://localhost:1455/auth/callback?code=...&state=..."
}
```

### Cancel attempt

- `POST /api/model-providers/:provider/signin/:attemptId/cancel`

### Sign off provider

- `POST /api/model-providers/:provider/signoff`

Signoff remains provider-level and must cancel any active signin attempt for that provider.

## Attempt Model

Interactive attempts expose a normalized, provider-agnostic state machine:

- `pending`
- `awaiting_browser`
- `awaiting_manual_input`
- `running`
- `completed`
- `failed`
- `cancelled`

Each attempt also exposes `nextAction` so the frontend can respond without hardcoding provider
names.

Each attempt also preserves:

- `authUrl`
- `authInstructions`

so the frontend can continue showing the original sign-in link even after the attempt moves to
manual callback input.

Accepted `nextAction.type` values for the first implementation:

- `none`
- `wait`
- `open_url`
- `prompt_input`
- `enter_callback_url`

The attempt payload also includes:

- `progress[]`
- `createdAt`
- `updatedAt`
- `completedAt`
- `error`

## Provider Adapter Model

The framework is intentionally generic.
Provider-specific behavior is translated into the generic attempt model rather than being exposed
directly on the route surface.

The first implementation uses Pi's OAuth runtime helpers for `openai-codex` and
`github-copilot`.

The accepted adapter behavior is:

- `onAuth` updates the attempt to `awaiting_browser`
- `onPrompt` can update the attempt to `prompt_input` for provider-specific text questions
- `onManualCodeInput` updates the attempt to `awaiting_manual_input`
- `onProgress` appends progress entries and keeps the attempt in `running`
- successful completion persists runtime auth through Pi `AuthStorage`

## OpenAI Codex

`openai-codex` is the first interactive provider on this framework.

Important constraint:

- Pi's built-in Codex OAuth helper uses `http://localhost:1455/auth/callback`

That means a fully remote Astro deployment cannot assume the callback always lands inside the Astro
runtime.

The accepted first-step UX is therefore:

1. Astro starts the provider signin flow.
2. Astro returns an auth URL through `nextAction.type = "open_url"`.
3. The frontend opens the URL.
4. If the localhost callback cannot finish the flow, Astro moves the attempt to
   `awaiting_manual_input`.
5. The user pastes either the authorization code or the full redirect URL back into Astro through
   the manual endpoint.

This is good enough for a remote deployment while keeping the architecture reusable for future
providers.

## GitHub Copilot

`github-copilot` is also supported through the same interactive framework.

Its built-in Pi OAuth helper uses a device-code flow:

1. Astro defaults the optional GitHub Enterprise domain prompt to blank, which means `github.com` unless a future caller explicitly provides an enterprise domain override.
2. Astro then returns the verification URL through `nextAction.type = "open_url"`.
3. The verification code is carried in `authInstructions`.
4. The user completes the device-code flow in the browser while Astro polls for completion.

## Consequences

### Positive

- one reusable signin-attempt lifecycle works for OAuth and future device-code style providers
- frontend code can key off `status` and `nextAction`, not provider-specific branches
- `openai-codex` can now expose `signInAvailable: true`
- `github-copilot` can now expose `signInAvailable: true`
- signoff remains provider-level and does not delete model knowledge

### Negative

- interactive signin now requires state persistence for attempts in addition to provider auth state
- interrupted attempts can become stale if Astro restarts mid-flow
- the first Codex remote UX may still require manual callback handoff

## Tasks

- [x] Add generic attempt persistence and lifecycle endpoints.
- [x] Keep `POST /signin` as the entry point while supporting both `200` and `202`.
- [x] Implement `openai-codex` on the generic interactive attempt framework.
- [x] Implement `github-copilot` on the generic interactive attempt framework.
- [x] Cancel active signin attempts when a provider is signed off.
- [x] Add richer `nextAction` types for device-code style providers.
- [ ] Add seamless remote callback relay for providers that currently depend on localhost callbacks.

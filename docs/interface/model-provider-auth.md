# Model Provider Auth

Astro exposes auth-backed model-provider state separately from chat itself.

These endpoints are for providers such as `openai`, `anthropic`, `openai-codex`, and `github-copilot` whose models
depend on backend-owned user provider credential state.

They do not accept raw secrets from the client. Provider status, sign-in, and sign-off are
user-scoped, so callers must pass the public user uid as `created_by_user_uid`.
Astro sends `created_by_user_uid` to the backend credential API.

Astro resolves that user identity from, in order:

- JSON body or query string: `created_by_user_uid` or `createdByUserUid`
- request headers: `x-mainsequence-user-uid`, `x-ms-user-uid`, `x-user-uid`, or
  `x-created-by-user-uid`
- Bearer JWT claims: `created_by_user_uid`, `createdByUserUid`, or `mainsequence_user_uid`

If no user identity can be resolved, provider status and provider auth actions return `400`.

## Endpoints

### `GET /api/model-providers?created_by_user_uid=<user_uid>`

Returns provider-level auth state for managed auth-backed model providers Astro currently exposes in
the UI.

Example response:

```json
{
  "version": 1,
  "providers": [
    {
      "provider": "openai",
      "authKind": "api_key",
      "signInAvailable": true,
      "authenticated": false,
      "authSource": null,
      "knownModelCount": 8,
      "usableModelCount": 0,
      "lastValidatedAt": "2026-04-15T12:00:00.000Z",
      "version": null,
      "credentialHash": null
    },
    {
      "provider": "openai-codex",
      "authKind": "oauth",
      "signInAvailable": true,
      "authenticated": false,
      "authSource": null,
      "knownModelCount": 8,
      "usableModelCount": 0,
      "lastValidatedAt": "2026-04-15T12:00:00.000Z",
      "version": null,
      "credentialHash": null
    },
    {
      "provider": "github-copilot",
      "authKind": "oauth",
      "signInAvailable": true,
      "authenticated": false,
      "authSource": null,
      "knownModelCount": 20,
      "usableModelCount": 0,
      "lastValidatedAt": "2026-04-15T12:00:00.000Z",
      "version": null,
      "credentialHash": null
    }
  ],
  "backendCredentialStatus": {
    "ok": true
  }
}
```

Meaning:

- `authKind`: the provider uses either API-key auth or OAuth-style runtime auth
- `signInAvailable`: the frontend-friendly flag for whether `POST .../signin` can start right now
- `authenticated`: Astro currently allows that provider to be used for this user
- `authSource`: `backend` when the provider is backed by an active backend credential, otherwise `null`
- `version`: backend credential version, or `null` when unauthenticated
- `credentialHash`: backend credential hash, or `null` when unauthenticated
- `knownModelCount`: how many models Astro knows for that provider
- `usableModelCount`: how many of those models are executable right now
- providers outside Astro's current supported surface are intentionally omitted from this endpoint

### `POST /api/model-providers/:provider/signin`

Starts Astro-managed signin for the provider.

There are two successful response shapes:

- `200` for immediate providers, such as env-backed API-key sync
- `202` for interactive providers, such as OAuth flows

Immediate example:

```bash
curl -X POST http://localhost:8787/api/model-providers/openai/signin \\
  -H 'Content-Type: application/json' \\
  -d '{"created_by_user_uid":"00000000-0000-4000-8000-000000000123"}'
```

```json
{
  "ok": true,
  "statusCode": 200,
  "provider": "openai",
  "authenticated": true,
  "updatedAt": "2026-04-15T12:05:00.000Z"
}
```

Interactive example:

```bash
curl -X POST http://localhost:8787/api/model-providers/openai-codex/signin \\
  -H 'Content-Type: application/json' \\
  -d '{"created_by_user_uid":"00000000-0000-4000-8000-000000000123"}'
```

```json
{
  "ok": true,
  "statusCode": 202,
  "provider": "openai-codex",
  "attempt": {
    "id": "1b6d9f46-9f85-4b8d-bf53-8f72515e5e7b",
    "provider": "openai-codex",
    "status": "awaiting_browser",
    "authUrl": "https://auth.openai.com/oauth/authorize?...",
    "authInstructions": "A browser window should open. Complete login to finish.",
    "nextAction": {
      "type": "open_url",
      "url": "https://auth.openai.com/oauth/authorize?...",
      "instructions": "A browser window should open. Complete login to finish."
    },
    "progress": [
      {
        "message": "Opened provider authorization flow.",
        "at": "2026-04-15T12:06:00.000Z"
      }
    ],
    "authKind": "oauth",
    "createdAt": "2026-04-15T12:06:00.000Z",
    "updatedAt": "2026-04-15T12:06:00.000Z",
    "completedAt": null,
    "error": null
  }
}
```

### `GET /api/model-providers/:provider/signin/:attemptId`

Returns the current state of an interactive signin attempt.

Example response:

```json
{
  "version": 1,
  "attempt": {
    "id": "1b6d9f46-9f85-4b8d-bf53-8f72515e5e7b",
    "provider": "openai-codex",
    "status": "awaiting_manual_input",
    "authUrl": "https://auth.openai.com/oauth/authorize?...",
    "authInstructions": "A browser window should open. Complete login to finish.",
    "nextAction": {
      "type": "enter_callback_url",
      "prompt": "Paste the authorization code or the full redirect URL.",
      "instructions": "After you finish sign-in in the browser, copy the full redirect URL from the browser address bar and paste it here."
    },
    "progress": [
      {
        "message": "Opened provider authorization flow.",
        "at": "2026-04-15T12:06:00.000Z"
      },
      {
        "message": "Waiting for manual callback input.",
        "at": "2026-04-15T12:06:18.000Z"
      }
    ],
    "authKind": "oauth",
    "createdAt": "2026-04-15T12:06:00.000Z",
    "updatedAt": "2026-04-15T12:06:18.000Z",
    "completedAt": null,
    "error": null
  }
}
```

### `POST /api/model-providers/:provider/signin/:attemptId/manual`

Submits provider-requested manual input for an interactive signin attempt.

Examples:

- for `openai-codex`, this is usually the full callback URL from the browser address bar
- for `github-copilot`, this is only needed when Astro explicitly asks for a GitHub Enterprise domain; the default flow now auto-uses blank input and continues with `github.com`

Example body:

```json
{
  "input": "http://localhost:1455/auth/callback?code=...&state=..."
}
```

Example response:

```json
{
  "ok": true,
  "provider": "openai-codex",
  "attempt": {
    "id": "1b6d9f46-9f85-4b8d-bf53-8f72515e5e7b",
    "provider": "openai-codex",
    "status": "running",
    "authUrl": "https://auth.openai.com/oauth/authorize?...",
    "authInstructions": "A browser window should open. Complete login to finish.",
    "nextAction": {
      "type": "wait",
      "message": "Manual input received. Waiting for provider confirmation."
    },
    "progress": [
      {
        "message": "Received manual callback input.",
        "at": "2026-04-15T12:06:34.000Z"
      }
    ],
    "authKind": "oauth",
    "createdAt": "2026-04-15T12:06:00.000Z",
    "updatedAt": "2026-04-15T12:06:34.000Z",
    "completedAt": null,
    "error": null
  }
}
```

### `POST /api/model-providers/:provider/signin/:attemptId/cancel`

Cancels an active interactive signin attempt.

Example response:

```json
{
  "ok": true,
  "provider": "openai-codex",
  "attempt": {
    "id": "1b6d9f46-9f85-4b8d-bf53-8f72515e5e7b",
    "provider": "openai-codex",
    "status": "cancelled",
    "authUrl": "https://auth.openai.com/oauth/authorize?...",
    "authInstructions": "A browser window should open. Complete login to finish.",
    "nextAction": {
      "type": "none"
    },
    "progress": [
      {
        "message": "Signin attempt cancelled by user.",
        "at": "2026-04-15T12:07:00.000Z"
      }
    ],
    "authKind": "oauth",
    "createdAt": "2026-04-15T12:06:00.000Z",
    "updatedAt": "2026-04-15T12:07:00.000Z",
    "completedAt": "2026-04-15T12:07:00.000Z",
    "error": "Signin attempt cancelled by user."
  }
}
```

### `POST /api/model-providers/:provider/signoff`

Revokes the provider credential in the backend and removes any scoped local credential for active
Astro sign-in attempts.

After signoff:

- known models from that provider can still appear in `GET /api/models/catalog`
- those models are marked as not authenticated and not usable
- chat execution using that provider fails clearly until it is signed in again
- any active interactive signin attempt for that provider is cancelled

Example:

```bash
curl -X POST http://localhost:8787/api/model-providers/openai-codex/signoff \\
  -H 'Content-Type: application/json' \\
  -d '{"created_by_user_uid":"00000000-0000-4000-8000-000000000123"}'
```

```json
{
  "ok": true,
  "statusCode": 200,
  "provider": "openai-codex",
  "authenticated": false,
  "updatedAt": "2026-04-15T12:08:00.000Z"
}
```

## Attempt Statuses

Interactive attempts can move through these statuses:

- `pending`
- `awaiting_browser`
- `awaiting_manual_input`
- `running`
- `completed`
- `failed`
- `cancelled`

`nextAction` is the frontend-facing hint for what to do next:

- `none`
- `wait`
- `open_url`
- `prompt_input`
- `enter_callback_url`

Each attempt also keeps:

- `authUrl`: the original provider sign-in URL, preserved even after the attempt moves to manual input
- `authInstructions`: provider-supplied copy for opening or completing the sign-in page

`prompt_input` is used for provider-specific text prompts such as:

- GitHub Enterprise domain input for `github-copilot`
- any future OAuth/device-code flow that needs one extra text response before or during signin

When `nextAction.type = "prompt_input"`:

- the frontend should render the prompt text exactly as returned
- blank input is allowed only when `nextAction.allowEmpty = true`
- the same `POST .../manual` endpoint is used to submit the response

## Frontend Sequence

1. Call `GET /api/model-providers?created_by_user_uid=<user_uid>`.
2. Call `GET /api/models/catalog`.
3. If a provider shows `signInAvailable: true`, allow `Sign in`.
4. `POST /api/model-providers/:provider/signin` with `created_by_user_uid` in the JSON body.
5. If response is `200`, refetch provider auth and model catalog.
6. If response is `202`, open or render the returned `attempt.nextAction` and keep `attempt.authUrl`
   available as the primary "Open sign-in page" action.
7. Poll `GET /api/model-providers/:provider/signin/:attemptId`.
8. If `nextAction.type === "enter_callback_url"`, submit with `POST .../manual`.
9. If `nextAction.type === "prompt_input"`, submit the requested text with `POST .../manual`.
10. On `completed`, refetch provider auth and model catalog.

## Notes

- provider auth is user/provider-level, not model-level
- signoff does not delete the provider from Astro's known model inventory
- `openai-codex` now uses the interactive flow because Pi's built-in OAuth helper relies on a localhost callback and may require manual callback handoff in a remote Astro deployment
- `github-copilot` now defaults its optional enterprise-domain prompt to blank, so the normal signin path should proceed directly to the verification URL on `github.com`

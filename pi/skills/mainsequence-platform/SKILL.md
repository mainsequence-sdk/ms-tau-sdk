---
name: mainsequence-platform
description: Guidance for helping users interact with the Main Sequence platform and CLI.
---

Use this skill when the user asks for help interacting with the Main Sequence platform, including CLI usage, authentication, project setup, and platform workflows.

## Core flow

1. Verify authentication with `mainsequence user`.
2. Ensure `MAINSEQUENCE_ACCESS_TOKEN`, `MAINSEQUENCE_REFRESH_TOKEN`, `MAINSEQUENCE_BACKEND`, and `MAINSEQUENCE_PROJECTS_BASE` are present in the environment.
   - If auth is missing or expired, run:
     `mainsequence login --access-token "$MAINSEQUENCE_ACCESS_TOKEN" --refresh-token "$MAINSEQUENCE_REFRESH_TOKEN" --backend "$MAINSEQUENCE_BACKEND" --projects-base "$MAINSEQUENCE_PROJECTS_BASE"`.
   - If `MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS` is set, keep refreshing tokens on that interval while the session is active.
   - Do not request username/password credentials.
   - If authentication fails or env vars are missing, ask the user to refresh them.
3. Translate the user’s intent into Main Sequence possibilities by consulting the official Main Sequence CLI documentation and the CLI’s built-in help output (for example, `mainsequence --help` and command-level help).
4. The official Main Sequence CLI documentation (from the upstream Main Sequence docs; fetch it when not already local) is the source of truth for available commands and usage.
5. If Python or `mainsequence` commands need isolation, the repo root `Dockerfile` runtime is required.

## Output expectations

- Provide exact CLI commands when possible.
- Be explicit about required auth environment variables and where they should be set.

---
name: mainsequence-platform
description: Guidance for helping users interact with the Main Sequence platform and CLI.
---

Use this skill when the user asks for help interacting with the Main Sequence platform, including CLI usage, authentication, project setup, and platform workflows.

## Core flow

1. Verify authentication with `mainsequence user`.
2. If login is needed, use `mainsequence login <email>`.
   - Retrieve credentials from `astro-mainsequence-email` and `astro-mainsequence-password`.
   - On macOS, prefer the `security` CLI to read secrets.
3. Prefer the official CLI commands documented in `AGENTS.md` and Astro docs.
4. If Python or `mainsequence` commands need isolation, prefer the repo root `Dockerfile` runtime.

## Output expectations

- Provide exact CLI commands when possible.
- Be explicit about required secrets and where they come from.

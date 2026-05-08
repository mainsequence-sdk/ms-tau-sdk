# Extensions

Astro's runtime extensions live under `pi/extensions/`. The detailed docs now mirror that source tree under `docs/extensions/`, with one page per hook, tool, and shared helper.

## Read by area

- [`../extensions/README.md`](../extensions/README.md)
- [`../extensions/hooks/README.md`](../extensions/hooks/README.md)
- [`../extensions/tools/README.md`](../extensions/tools/README.md)
- [`../extensions/shared/README.md`](../extensions/shared/README.md)

## Hooks

- [`agent-registration`](../extensions/hooks/agent-registration.md)
- [`project-policy`](../extensions/hooks/project-policy/README.md)
- [`telemetry`](../extensions/hooks/telemetry.md)

## Tools

- [`ensure_mainsequence_cli_auth`](../extensions/tools/mainsequence-cli-auth.md)
- [`list_recent_changes`](../extensions/tools/recent-changes.md)

## Shared helpers

- [`agent-registration`](../extensions/shared/agent-registration.md)
- [`repo`](../extensions/shared/repo.md)
- [`telemetry`](../extensions/shared/telemetry.md)

## Runtime notes

- launch scripts bootstrap deterministic Main Sequence CLI auth before agent work begins
- project implementation belongs on the dedicated executor runtime, not on a child session switch
- Docker Compose keeps the same extension surface while mounting the editable repo files into `/app`

## Related pages

- [`settings-and-system-prompt.md`](./settings-and-system-prompt.md)
- [`agents.md`](./agents.md)
- [`../getting-started/request-lifecycle.md`](../getting-started/request-lifecycle.md)

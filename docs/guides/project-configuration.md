# Project Tau Configuration and Extensions

The project `.tau` directory is the only agent-behavior configuration boundary. Main Sequence TAU
SDK passes its packaged defaults and workspace to Tau; Tau resolves one effective configuration.

## General behavior override

Without a project override, the packaged SDK `SYSTEM.md` supplies a concise coding-agent default.
To replace it, create:

```text
.tau/SYSTEM.md
```

The project file is the effective general system prompt. The SDK does not require a companion file
and does not maintain a separate prompt setting. Other Tau-native resource forms continue to follow
the Tau version pinned by the SDK.

## Project resources

Tau discovers project-owned resources from the workspace, including:

```text
.tau/
├── SYSTEM.md
├── prompts/
├── skills/
└── extensions/
```

Project resources participate in Tau diagnostics and reload. Extension session-start, tool hooks,
and session-shutdown callbacks use Tau's normal lifecycle.

## Ownership and trust

The SDK enables and trusts the selected project's Tau resources because the project already owns
arbitrary Python code in the same process. Project authors are responsible for:

- extension source and behavior;
- optional tools and their Python/system dependencies;
- outbound network access performed by extensions;
- compatibility with the pinned Tau and SDK versions; and
- testing reload and shutdown behavior.

The SDK does not sandbox project extensions. It continues to protect runtime credentials,
authenticated caller and lease proof, persistence ordering, secret redaction, and wire validation.

## Optional tools

Web, search, fetch, video, and runtime-information tools are not part of the base SDK. A project may
install its chosen implementation and register tools in `.tau/extensions`. Those tools do not alter
the SDK's own Main Sequence HTTP and MCP transports.

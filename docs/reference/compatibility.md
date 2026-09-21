# Compatibility Policy

Version `1.0.0` establishes the first stable Main Sequence TAU SDK contract. The project follows
semantic versioning for these supported surfaces:

- `ms_tau_sdk.create_app`, `ms_tau_sdk.TauSDKSettings`, and `ms_tau_sdk.__version__`;
- the `ms-tau` command and its workspace-bound startup behavior;
- documented `MAINSEQUENCE_TAU_*`, `MAINSEQUENCE_ENDPOINT`, `MAINSEQUENCE_AUTH_MODE`, and
  `MAINSEQUENCE_RUNTIME_CREDENTIAL_*`, `MAINSEQUENCE_ACCESS_TOKEN`,
  `MAINSEQUENCE_REFRESH_TOKEN`, and `TAU_LOCAL_*` settings;
- documented health, readiness, chat, response, session, and A2A HTTP/wire behavior;
- packaged Tau defaults and Tau-native project `.tau` precedence; and
- the documented absence of SDK-owned container/deployment artifacts and optional tool baggage.

A major release is required to remove or incompatibly change one of those surfaces. Minor releases
may add backward-compatible settings, APIs, routes, or behavior. Patch releases contain compatible
fixes. Every release records relevant changes in the changelog and passes the same distribution,
consumer, project-configuration, and protocol contracts.

Modules not exported by `ms_tau_sdk.__all__`, internal classes, logs beyond their documented stable
fields, and implementation-specific diagnostics are private. Importing them does not create a
compatibility promise.

Tau remains pinned exactly because its resource precedence, extension lifecycle, provider catalog,
thinking levels, and storage protocol directly affect SDK behavior. Updating Tau requires the full
configuration, provider, persistence, distribution, clean-install, and consumer-fixture gates.

Security or external-service requirements can force a breaking change. Such a change requires an
ADR, a major version, and migration documentation; silent fallback to a retired SDK mode is not
allowed.

# `@mainsequence/pi` Local Simulation

This directory simulates the portable Main Sequence Pi package inside the Astro repo.

It is intentionally temporary. The real source of truth should live outside Astro, preferably in the
Main Sequence SDK repo or a sibling Main Sequence-owned repo.

The current simulation delegates SDK-owned skill delivery to a Pi extension. The extension asks the
installed Main Sequence SDK/CLI to seed `.agents/skills` for a runtime cwd when that folder is
absent.

## What This Package Represents

This package contains portable Main Sequence Pi resources:

- SDK-owned skill discovery delegated to the installed Main Sequence SDK/CLI
- a Pi extension that discovers/seeds SDK-owned skills into `.agents/skills`
- simulated portable Main Sequence prompts that should eventually live in the SDK package
- simulated Pi extensions that belong with those skills/prompts because they reference them directly

It does not contain Astro runtime/backend behavior.

## What Is Intentionally Not Included

- Astro HTTP/SSE/REST server code
- checkpoint/session/provider credential code
- runtime credential exchange
- Astro runtime-context logic
- Astro A2A routing
- Astro-only repo maintenance skills
- Astro Core runtime extensions that do not reference Main Sequence package resources

## Current Simulated Resources

```text
pi/prompts/review-main-sequence-project.md
pi/prompts/verify-mainsequence-tutorial.md
pi/system/APPEND_SYSTEM.md
pi/extensions/hooks/scaffold-skill-discovery/index.ts
pi/extensions/hooks/project-policy/index.ts
pi/extensions/hooks/project-policy/child-policy.md
pi/extensions/tools/mainsequence-cli-auth/index.ts
pi/extensions/tools/mainsequence-runtime-info/index.ts
```

The `scaffold-skill-discovery` extension seeds SDK skills as soon as Pi loads the extension, then
returns the seeded path from Pi's `resources_discover` event. It does not maintain a hardcoded SDK
skill allowlist. It only checks whether `<cwd>/.agents/skills` already exists; if it does, the hook
leaves it alone. If it does not, the hook resolves the installed SDK skill root:

```text
mainsequence skills path
```

Then it copies that full SDK skill tree into `<cwd>/.agents/skills/mainsequence`. During
`resources_discover`, it returns the existing `.agents/skills` directory to Pi for the current
discovery pass.

## Usage In This Repo

This package is wired into local Main Sequence-style deployments through `ASTRO_PI_PACKAGE_PATHS`.

To test package discovery, point Astro at this package:

```text
ASTRO_PI_PACKAGE_PATHS=./tmp_ms_pi
```

Do not treat this folder as the final package location.

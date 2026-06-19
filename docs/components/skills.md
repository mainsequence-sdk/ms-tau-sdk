# Skills

Skills are local instruction bundles the agent can load when a task clearly matches them.

In this repo, skills are not the main runtime mechanism. They are optional deeper guidance.

## Main Sequence SDK Skills

Main Sequence product skills are no longer delivered from Astro root `pi/`, and they are not copied
into `tmp_ms_pi/pi/skills`.

The local package simulation owns the discovery extension:

- `tmp_ms_pi/pi/extensions/hooks/scaffold-skill-discovery/index.ts`

That extension delegates skill seeding to the installed Main Sequence SDK/CLI when Pi loads it:

```text
mainsequence skills path
```

The SDK remains the source of truth. At runtime, the copied skills live under the Pi working
directory's `.agents/skills/mainsequence` folder and are discovered by Pi through its normal skill
loader.

## How skills fit into Astro

Astro Core primarily depends on:

- system prompts
- extensions
- prompt templates

Main Sequence skills are composed by loading the Main Sequence Pi package through
`ASTRO_PI_PACKAGE_PATHS`; the package extension then asks the SDK to populate `.agents/skills`.

## Related pages

- [`prompts.md`](./prompts.md)

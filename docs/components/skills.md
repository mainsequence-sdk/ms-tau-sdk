# Skills

Skills are local instruction bundles the agent can load when a task clearly matches them.

In this repo, skills are not the main runtime mechanism. They are optional deeper guidance.

## Main Sequence Package Simulation Skills

Main Sequence product skills are no longer delivered from Astro root `pi/`.
During the local package split simulation they are delivered by `tmp_ms_pi`. The package files are
the source of truth.

Current package skill roots:

- `tmp_ms_pi/pi/skills/project_builder/SKILL.md`
- `tmp_ms_pi/pi/skills/mainsequence-sdk/SKILL.md`
- `tmp_ms_pi/pi/skills/command_center/workspace_analysis/SKILL.md`
- `tmp_ms_pi/pi/skills/a2a_communication/SKILL.md`

## How skills fit into Astro

Astro Core primarily depends on:

- system prompts
- extensions
- prompt templates

Main Sequence skills are now composed through the package path configured by
`ASTRO_PI_PACKAGE_PATHS`.

## Related pages

- [`knowledge.md`](./knowledge.md)
- [`prompts.md`](./prompts.md)

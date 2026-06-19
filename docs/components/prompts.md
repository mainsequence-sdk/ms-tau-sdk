# Prompts

Main Sequence reusable workflow prompts are currently delivered by the local `tmp_ms_pi` package
simulation. The package files are the source of truth.

## Package Sources

- `tmp_ms_pi/pi/prompts/review-main-sequence-project.md`
- `tmp_ms_pi/pi/prompts/verify-mainsequence-tutorial.md`

## Why prompts stay separate from the shared runtime contract

- prompts guide the agent through reusable workflows
- `.pi/APPEND_SYSTEM.md` defines only the Astro Core runtime contract.
- `tmp_ms_pi/pi/system/APPEND_SYSTEM.md` defines the Main Sequence package runtime contract.

## Related pages

- [`agents.md`](./agents.md)
- [`settings-and-system-prompt.md`](./settings-and-system-prompt.md)

# Prompts

Main Sequence reusable workflow prompts are currently delivered by the
`adapters/mainsequence/pi-overlay` adapter overlay. The overlay files are the Astro-side source of truth.

## Package Sources

- `adapters/mainsequence/pi-overlay/pi/prompts/review-main-sequence-project.md`
- `adapters/mainsequence/pi-overlay/pi/prompts/verify-mainsequence-tutorial.md`

## Why prompts stay separate from the shared runtime contract

- prompts guide the agent through reusable workflows
- `.pi/APPEND_SYSTEM.md` defines only the Astro Core runtime contract.
- `adapters/mainsequence/pi-overlay/pi/system/APPEND_SYSTEM.md` defines the Main Sequence package runtime contract.

## Related pages

- [`agents.md`](./agents.md)
- [`settings-and-system-prompt.md`](./settings-and-system-prompt.md)

# Extension hooks

These pages document Astro's repo-owned Pi hook extensions under `pi/extensions/hooks/`.

## Hooks

- [`agent-registration.md`](./agent-registration.md)
- [`project-policy/README.md`](./project-policy/README.md)
- [`telemetry.md`](./telemetry.md)

## Notes

- Hooks are loaded by Pi at runtime; Astro uses them for lifecycle policy, backend registration, and telemetry.
- Hook behavior is gated by environment where needed, especially for child-only and telemetry-only flows.

## Related pages

- [`../README.md`](../README.md)
- [`../../components/extensions.md`](../../components/extensions.md)

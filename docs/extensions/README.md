# Extensions

Astro's repo-owned runtime extensions live under `pi/extensions/` and are documented here with one page per hook, tool, or shared helper.

## Structure

- [`hooks/`](./hooks/README.md)
  - lifecycle hooks registered into Pi
- [`tools/`](./tools/README.md)
  - repo-owned tools the parent or child agents can call
- [`shared/`](./shared/README.md)
  - helper modules reused by hooks, tools, entrypoints, and the stream runtime

## Source layout

```text
pi/extensions/
├── hooks/
├── shared/
└── tools/
```

## Related pages

- [`../components/extensions.md`](../components/extensions.md)
- [`../components/runtime-entrypoints-and-tools.md`](../components/runtime-entrypoints-and-tools.md)
- [`../reference/folder-structure.md`](../reference/folder-structure.md)

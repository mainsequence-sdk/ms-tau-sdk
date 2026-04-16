# Repo helpers

Source: [`pi/extensions/shared/repo.ts`](../../../pi/extensions/shared/repo.ts)

## Purpose

Provide small filesystem helpers reused across Astro's hooks and tools.

## Exports

- `findRepoRoot(startCwd)`
  - walk upward until Astro-like repo markers are found
- `safeReadFile(filePath)`
  - read UTF-8 content without throwing
- `truncateMiddle(text, maxChars)`
  - shrink long text while preserving both ends
- `normalizeFilePath(repoRoot, rawPath)`
  - return repo-relative paths when possible, otherwise an absolute normalized path

## Common callers

- child policy injection
- recent change tracking
- other repo-relative path summaries

## Related files

- [`../hooks/project-policy/README.md`](../hooks/project-policy/README.md)
- [`../tools/recent-changes.md`](../tools/recent-changes.md)

# 2026-04-22 Checkpoint Lease Lifecycle Bugs

## Context

While testing backend-owned checkpoint storage, chat session `52` returned an SSE application error:

```json
{
  "type": "error",
  "error": "Checkpoint lease failed: An unexpired checkpoint lease is already held for this session.",
  "status": 409,
  "error_code": "checkpoint_lease_already_held"
}
```

This is a real runtime bug, not a frontend display issue. Astro attempted to acquire a checkpoint
lease while the backend still had an unexpired lease for the same `AgentSession`.

## Observed Timeline

- `2026-04-22T09:07:34Z`: Astro acquired lease for session `52`, expiring at `09:09:34`.
- `2026-04-22T09:09:08Z`: Astro renewed the lease, expiring at `09:11:08`.
- `2026-04-22T09:09:14Z`: Sidecar flushed checkpoint version `14`.
- `2026-04-22T09:10:02Z`: Sidecar skipped a periodic flush because the local manifest still said
  the lease expired at `09:09:34`.
- `2026-04-22T09:10:02Z`: Astro tried `checkpoint_lease/acquire/`; backend rejected it with
  `checkpoint_lease_already_held`.

## Suspected Root Cause

The sidecar writes checkpoint manifests using a manifest object captured before the flush started.
If Astro renews the lease while the sidecar is flushing, the sidecar can overwrite the newer
`lease_expires_at` with stale lease metadata from the earlier manifest.

The risky write path is:

- `scripts/session_checkpoint_sidecar.ts`
- `writeCheckpointManifest(sessionKey, manifest, updates)`

## Bugs To Fix

- [x] Preserve freshest lease metadata when the sidecar writes a manifest after flush.
      Re-read the latest manifest at write time and do not overwrite a newer `lease_token`,
      `lease_holder_id`, or `lease_expires_at` with stale fields from an older in-memory manifest.

- [x] Avoid renewing a previous turn lease for a new Pi child process.
      A new chat turn should not blindly reuse a prior turn's lease if the previous terminal
      checkpoint marker may still be pending sidecar flush/release.

- [x] Add per-session local lifecycle state.
      Track states such as `running`, `finalizing_checkpoint`, and `idle`. If a new chat arrives
      while a previous terminal checkpoint is still finalizing, Astro should wait briefly or return
      a clear retryable error such as `session_checkpoint_finalizing`.

- [x] Make checkpoint markers lease-aware.
      Sidecar should read marker `lease_holder_id`, `lease_token`, `checkpoint_version`, and
      `bundle_hash`, then verify the marker still matches the active manifest before terminal
      flush/release. Old markers must not flush or release a newer turn's lease.

- [x] Do not write terminal checkpoint markers for pre-launch checkpoint lease failures.
      If Astro never acquired a lease and Pi never launched, the error should be sent to SSE but no
      sidecar checkpoint marker should be created.

- [x] Clear or invalidate `ctx.checkpointLease` after releasing a lease on restore failure.
      Restore-failure error handling currently can still write an error marker with a released token.

- [x] Handle HTTP client aborts explicitly.
      If the browser aborts the SSE request, Astro should stop the active Pi child, stop lease
      renewal, and write a terminal checkpoint marker only when a lease exists.

- [x] Retry or clearly surface release failures.
      If `checkpoint_lease/release/` fails after a successful terminal flush, sidecar should retry
      with backoff or record a durable local state so the next scan can try release again.

- [ ] Add regression tests or an integration script for rapid consecutive chat turns.
      The test should cover: finish marker pending, next chat arrives, sidecar flush in progress,
      lease renewal/acquire behavior, and final release.

## Implementation Notes

- `interface/stream/server.ts` now writes local checkpoint lifecycle state under
  `checkpoint-lifecycle/`.
- New chat launches wait for `finalizing_checkpoint` to clear before lease acquisition.
- Terminal checkpoint markers are skipped when no checkpoint lease exists.
- Terminal checkpoint markers include lease identity, checkpoint version, and bundle hash.
- Client aborts stop the active Pi child and write a terminal checkpoint marker when a lease exists.
- `scripts/session_checkpoint_sidecar.ts` now preserves the freshest manifest lease metadata when
  persisting checkpoint versions.
- Sidecar skips stale terminal markers whose lease identity no longer matches the active manifest.
- Sidecar retries terminal lease release before leaving the session blocked by backend TTL.

## Current Operational Workaround

If the bug appears before the fix is complete, wait for the backend lease TTL to expire or manually
release/clear the lease in the backend for that `AgentSession`. Restarting Astro alone does not
guarantee recovery while the backend lease is still unexpired.

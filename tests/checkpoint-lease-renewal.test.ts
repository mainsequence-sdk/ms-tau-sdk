import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

test("checkpoint lease renewal suppresses stale terminal cleanup failures before fatal renewal errors", async () => {
	const source = await readFile("interface/stream/server.ts", "utf8");
	const helperStart = source.indexOf("function checkpointLeaseRenewFailureIsTerminalCleanup");
	const helperEnd = source.indexOf("function buildBackendFailureErrorEvent", helperStart);
	assert.notEqual(helperStart, -1);
	assert.notEqual(helperEnd, -1);
	const helperSource = source.slice(helperStart, helperEnd);

	assert.match(helperSource, /checkpointLeaseRenewCanFallbackToAcquire\(result\)/);
	assert.match(helperSource, /ctx\.cancellation\?\.requested/);
	assert.match(helperSource, /ctx\.terminalError\?\.errorCode/);
	assert.match(helperSource, /ctx\.lastAssistantFinishReason != null/);
	assert.match(helperSource, /state === "finalizing_checkpoint"/);
	assert.match(helperSource, /lifecycle\.lease_token === activeLease\.leaseToken/);

	const renewalStart = source.indexOf("function startCheckpointLeaseRenewal");
	const renewalEnd = source.indexOf("function extractCheckpointErrorCode", renewalStart);
	assert.notEqual(renewalStart, -1);
	assert.notEqual(renewalEnd, -1);
	const renewalSource = source.slice(renewalStart, renewalEnd);

	const staleCleanupIndex = renewalSource.indexOf("checkpoint_lease_renew_skipped_terminal_cleanup");
	const fatalRenewIndex = renewalSource.indexOf('event: "checkpoint_lease_renew_failed"');
	assert.notEqual(staleCleanupIndex, -1);
	assert.notEqual(fatalRenewIndex, -1);
	assert.ok(staleCleanupIndex < fatalRenewIndex);
	assert.match(renewalSource, /stopCheckpointLeaseRenewal\(ctx\);\n\s+return;/);
});

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

import { resolveWarmRunnerTimeoutConfig } from "../interface/stream/warm-runner-config.js";

test("warm runner timeout config resolves defaults", () => {
	assert.deepEqual(resolveWarmRunnerTimeoutConfig({}), {
		idleTtlMs: 3600000,
		rpcCommandTimeoutMs: 10000,
		startupTimeoutMs: 120000,
		turnTimeoutMs: 120000,
	});
});

test("warm runner timeout config accepts integer millisecond overrides", () => {
	assert.deepEqual(
		resolveWarmRunnerTimeoutConfig({
			ASTRO_A2A_WARM_RUNNER_IDLE_TTL_MS: "7200000",
			ASTRO_A2A_WARM_RUNNER_RPC_TIMEOUT_MS: "15000",
			ASTRO_A2A_WARM_RUNNER_STARTUP_TIMEOUT_MS: "180000",
			ASTRO_A2A_WARM_RUNNER_TURN_TIMEOUT_MS: "90000",
		}),
		{
			idleTtlMs: 7200000,
			rpcCommandTimeoutMs: 15000,
			startupTimeoutMs: 180000,
			turnTimeoutMs: 90000,
		},
	);
});

test("warm runner timeout config rejects invalid and sub-second values", () => {
	assert.deepEqual(
		resolveWarmRunnerTimeoutConfig({
			ASTRO_A2A_WARM_RUNNER_IDLE_TTL_MS: "abc",
			ASTRO_A2A_WARM_RUNNER_RPC_TIMEOUT_MS: "999",
			ASTRO_A2A_WARM_RUNNER_STARTUP_TIMEOUT_MS: "-1",
			ASTRO_A2A_WARM_RUNNER_TURN_TIMEOUT_MS: "0",
		}),
		{
			idleTtlMs: 3600000,
			rpcCommandTimeoutMs: 10000,
			startupTimeoutMs: 120000,
			turnTimeoutMs: 120000,
		},
	);
});

test("warm runner dispatch has a bounded turn timeout failure path", async () => {
	const source = await readFile("interface/stream/server.ts", "utf8");
	const dispatchStart = source.indexOf("async function dispatchWarmRunnerTurn");
	const dispatchEnd = source.indexOf("async function runWarmPiPrompt", dispatchStart);
	assert.notEqual(dispatchStart, -1);
	assert.notEqual(dispatchEnd, -1);
	const dispatchSource = source.slice(dispatchStart, dispatchEnd);

	assert.match(dispatchSource, /setTimeout\(\(\) => \{/);
	assert.match(dispatchSource, /warmRunnerTurnTimeoutMs/);
	assert.match(dispatchSource, /event: "warm_runner_turn_timeout"/);
	assert.match(dispatchSource, /error_code: "warm_runner_turn_timeout"/);
	assert.match(dispatchSource, /completeWarmRunnerTurn\(runner, "stream_error"\)/);
	assert.match(dispatchSource, /stopWarmRunner\(runner, "turn_timeout"\)/);
});

test("warm runner cancellation releases the active turn before waiting for process exit", async () => {
	const source = await readFile("interface/stream/server.ts", "utf8");
	const releaseStart = source.indexOf("function releaseWarmRunnerTurnForCancellation");
	const releaseEnd = source.indexOf("function beginActiveRunCancellation", releaseStart);
	assert.notEqual(releaseStart, -1);
	assert.notEqual(releaseEnd, -1);
	const releaseSource = source.slice(releaseStart, releaseEnd);

	const cancellationStart = source.indexOf("function beginActiveRunCancellation");
	const cancellationEnd = source.indexOf("function handleAssistantDelta", cancellationStart);
	assert.notEqual(cancellationStart, -1);
	assert.notEqual(cancellationEnd, -1);
	const cancellationSource = source.slice(cancellationStart, cancellationEnd);

	assert.match(releaseSource, /event: "warm_runner_turn_cancel_requested"/);
	assert.match(releaseSource, /abortWarmRunnerTurnForCancellation\(runner, turn, ctx\)/);
	assert.match(source, /await writeWarmRunnerCommand\(runner, \{ type: "abort" \}\)/);
	assert.match(source, /event: "warm_runner_turn_abort_acknowledged"/);
	assert.match(source, /stopWarmRunner\(runner, "turn_abort_failed"\)/);
	assert.doesNotMatch(releaseSource, /stopWarmRunner\(runner, "turn_cancelled"\)/);
	assert.match(cancellationSource, /releaseWarmRunnerTurnForCancellation\(ctx\)/);
	assert.match(cancellationSource, /if \(releaseWarmRunnerTurnForCancellation\(ctx\)\) return/);
});

test("same-session stale queue reaper treats aborting warm turns as live", async () => {
	const source = await readFile("interface/stream/server.ts", "utf8");
	const liveOwnerStart = source.indexOf("function activeWarmQueueHasLiveOwner");
	const liveOwnerEnd = source.indexOf("function reapStaleWarmSessionTurnQueue", liveOwnerStart);
	assert.notEqual(liveOwnerStart, -1);
	assert.notEqual(liveOwnerEnd, -1);
	const liveOwnerSource = source.slice(liveOwnerStart, liveOwnerEnd);

	assert.match(liveOwnerSource, /if \(runner\.currentTurn\) return true/);
});

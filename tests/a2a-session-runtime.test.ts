import assert from "node:assert/strict";
import { test } from "node:test";

import { A2ASessionRuntimeRegistry } from "../interface/stream/a2a-session-runtime.js";

function fixedRegistry() {
	let tick = 0;
	return new A2ASessionRuntimeRegistry(() => new Date(Date.UTC(2026, 5, 16, 12, 0, tick++)));
}

test("session runtime attach creates a starting attachment keyed by session uid", () => {
	const registry = fixedRegistry();
	const record = registry.attach({
		agentSessionUid: "session-a",
		threadId: "thread-a",
		agentType: "astro-orchestrator",
	});

	assert.equal(record.agentSessionUid, "session-a");
	assert.equal(record.threadId, "thread-a");
	assert.equal(record.agentType, "astro-orchestrator");
	assert.equal(record.state, "starting");
	assert.equal(registry.get("session-a"), record);
});

test("session runtime attach reuses compatible live attachment", () => {
	const registry = fixedRegistry();
	const first = registry.attach({
		agentSessionUid: "session-a",
		threadId: "thread-a",
		agentType: "astro-orchestrator",
	});
	const second = registry.attach({
		agentSessionUid: "session-a",
		threadId: "thread-b",
		agentType: "astro-orchestrator",
	});

	assert.equal(second.attachedAt, first.attachedAt);
	assert.equal(second.threadId, "thread-b");
	assert.equal(second.state, "starting");
});

test("session runtime attach replaces detached attachment", () => {
	const registry = fixedRegistry();
	const first = registry.attach({
		agentSessionUid: "session-a",
		threadId: "thread-a",
		agentType: "astro-orchestrator",
	});
	const detached = registry.detach("session-a");
	const second = registry.attach({
		agentSessionUid: "session-a",
		threadId: "thread-a",
		agentType: "astro-orchestrator",
	});

	assert.equal(detached?.state, "detached");
	assert.notEqual(second.attachedAt, first.attachedAt);
	assert.equal(second.detachedAt, null);
	assert.equal(second.state, "starting");
});

test("session runtime registry updates state and last error", () => {
	const registry = fixedRegistry();
	registry.attach({
		agentSessionUid: "session-a",
		threadId: null,
		agentType: "astro-orchestrator",
	});
	const updated = registry.update("session-a", {
		state: "failed",
		lastError: {
			code: "startup_failed",
			message: "Pi did not start.",
			at: "2026-06-16T12:01:00.000Z",
		},
	});

	assert.equal(updated?.state, "failed");
	assert.equal(updated?.lastError?.code, "startup_failed");
});

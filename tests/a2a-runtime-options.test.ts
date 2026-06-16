import assert from "node:assert/strict";
import { test } from "node:test";

import { normalizeA2ARuntimeOptions } from "../interface/stream/a2a-runtime-options.js";

test("A2A runtime turn timeout is disabled unless requested", () => {
	const options = normalizeA2ARuntimeOptions({
		body: {},
		a2aContext: {},
		context: {},
	});

	assert.equal(options.turnTimeoutMs, 0);
});

test("A2A runtime turn timeout resolves from request seconds", () => {
	const options = normalizeA2ARuntimeOptions({
		body: {
			runtime_turn_timeout_seconds: 12.5,
		},
	});

	assert.equal(options.turnTimeoutMs, 12500);
});

test("A2A runtime turn timeout resolves from context milliseconds", () => {
	const options = normalizeA2ARuntimeOptions({
		body: {},
		context: {
			runtimeTurnTimeoutMs: "3000",
		},
	});

	assert.equal(options.turnTimeoutMs, 3000);
});

test("A2A runtime turn timeout can be explicitly disabled per request", () => {
	const options = normalizeA2ARuntimeOptions({
		body: {
			runtime_turn_timeout_ms: 0,
		},
		a2aContext: {
			runtime_turn_timeout_seconds: 900,
		},
	});

	assert.equal(options.turnTimeoutMs, 0);
});

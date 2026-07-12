import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

import { classifyStreamErrorForLog } from "../interface/stream/stream-error-log-classification.js";

test("user stop stream errors are logged as cancellation instead of runtime errors", () => {
	const classification = classifyStreamErrorForLog({
		type: "error",
		error: "[astro] User pressed stop.",
		error_source: "runtime",
		status: 499,
		error_code: "session_cancelled_by_user",
		error_detail: "User pressed stop.",
		field_errors: null,
	});

	assert.deepEqual(classification, {
		event: "pi.cancellation",
		chunkType: "cancelled",
		severity: "INFO",
		cancellationReason: "user_requested",
	});
});

test("client disconnect stream errors are logged as cancellation instead of runtime errors", () => {
	const classification = classifyStreamErrorForLog({
		type: "error",
		error: "[astro] A2A client disconnected before the runtime turn completed.",
		error_source: "client",
		status: 499,
		error_code: "client_disconnected",
		error_detail: "A2A client disconnected before the runtime turn completed.",
		field_errors: null,
	});

	assert.deepEqual(classification, {
		event: "pi.cancellation",
		chunkType: "cancelled",
		severity: "INFO",
		cancellationReason: "client_disconnected",
	});
});

test("real runtime stream errors remain errors", () => {
	const classification = classifyStreamErrorForLog({
		type: "error",
		error: "Provider exploded.",
		error_source: "provider",
		status: 502,
		error_code: "provider_failed",
		error_detail: "Provider exploded.",
		field_errors: null,
	});

	assert.deepEqual(classification, {
		event: "pi.chunk",
		chunkType: "error",
		severity: "ERROR",
	});
});

test("HTTP access logging does not classify client-canceled streams as server errors", async () => {
	const source = await readFile("interface/stream/server.ts", "utf8");
	const start = source.indexOf("function registerHttpAccessLog");
	const end = source.indexOf("function notFound", start);
	assert.notEqual(start, -1);
	assert.notEqual(end, -1);
	const registerSource = source.slice(start, end);

	assert.match(registerSource, /const outcome = aborted \? "canceled"/);
	assert.match(registerSource, /severity: statusCode >= 500 \? "ERROR" : statusCode >= 400 \? "WARNING" : "INFO"/);
	assert.doesNotMatch(registerSource, /statusCode >= 500 \|\| aborted \? "ERROR"/);
});

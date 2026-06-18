import assert from "node:assert/strict";
import { test } from "node:test";

import {
	buildStructuredLogPayload,
	renderHumanLogLine,
	renderMachineLogLine,
} from "../pi/extensions/shared/structured-logging.js";

test("structured logging renders machine JSON with promoted fields", () => {
	const payload = buildStructuredLogPayload(
		{
			severity: "INFO",
			component: "astro-stream",
			event: "a2a.message.completed",
			message: "A2A message completed.",
			data: {
				requestId: "req_123",
				sessionKey: "0b2701a1-e777-4cfe-8437-b94025f00069",
				agentSessionUid: "0b2701a1-e777-4cfe-8437-b94025f00069",
				agentType: "astro-orchestrator",
				statusCode: 200,
				durationMs: 842,
			},
		},
		{ ASTRO_LOG_PAYLOADS: "0" } as NodeJS.ProcessEnv,
		new Date("2026-06-18T12:17:52.018Z"),
	);

	const rendered = JSON.parse(renderMachineLogLine(payload));

	assert.equal(rendered.severity, "INFO");
	assert.equal(rendered.event, "a2a.message.completed");
	assert.equal(rendered.request_id, "req_123");
	assert.equal(rendered.session_id, "0b2701a1-e777-4cfe-8437-b94025f00069");
	assert.equal(rendered.agent_session_uid, "0b2701a1-e777-4cfe-8437-b94025f00069");
	assert.equal(rendered.agent_type, "astro-orchestrator");
	assert.equal(rendered.status_code, 200);
	assert.equal(rendered.duration_ms, 842);
});

test("structured logging renders compact human lines", () => {
	const payload = buildStructuredLogPayload(
		{
			severity: "ERROR",
			component: "astro-stream",
			event: "backend.session.fetch.failed",
			message: "Backend session fetch failed.",
			data: {
				requestId: "req_123",
				sessionKey: "0b2701a1-e777-4cfe-8437-b94025f00069",
				phase: "auth_headers",
				errorCode: "timeout",
				durationMs: 120000,
			},
		},
		{} as NodeJS.ProcessEnv,
		new Date("2026-06-18T12:17:54.000Z"),
	);

	const rendered = renderHumanLogLine(payload);

	assert.match(rendered, /^12:17:54\.000 ERROR backend\.session\.fetch\.failed /);
	assert.match(rendered, /req=req_123/);
	assert.match(rendered, /session=0b2701\.\.\.0069/);
	assert.match(rendered, /phase=auth_headers/);
	assert.match(rendered, /error=timeout/);
	assert.match(rendered, /120000ms/);
	assert.doesNotMatch(rendered, /\{.*\}/);
});

test("structured logging summarizes large payload fields by default", () => {
	const payload = buildStructuredLogPayload(
		{
			severity: "DEBUG",
			component: "astro-stream",
			event: "tool.result.recorded",
			message: "Tool result recorded.",
			data: {
				toolName: "read",
				result: "x".repeat(100),
			},
		},
		{
			ASTRO_LOG_PAYLOADS: "0",
			ASTRO_LOG_MAX_FIELD_BYTES: "16",
		} as NodeJS.ProcessEnv,
		new Date("2026-06-18T12:17:52.000Z"),
	);

	const rendered = JSON.parse(renderMachineLogLine(payload));

	assert.equal(rendered.data.result.truncated, true);
	assert.equal(rendered.data.result.bytes, 100);
	assert.equal(rendered.data.result.preview, "xxxxxxxxxxxxxxxx");
	assert.match(rendered.data.result.hash, /^sha256:/);
});

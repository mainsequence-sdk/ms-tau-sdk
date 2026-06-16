import assert from "node:assert/strict";
import { test } from "node:test";

import {
	buildStrictJsonRepairPrompt,
	type A2AOutputContractChunk,
	normalizeA2AOutputOptions,
	runA2AOutputContractTurn,
	validateStrictJsonText,
} from "../interface/stream/a2a-output.js";
import { attachAgentUid, serializeSse } from "../interface/stream/protocol.js";

function chunkTypes(chunks: A2AOutputContractChunk[]): string[] {
	return chunks.map((chunk) => chunk.type);
}

function ssePayload(chunks: A2AOutputContractChunk[]): string {
	return chunks
		.map((chunk, index) => serializeSse(index + 1, attachAgentUid(chunk, "agent-1")))
		.join("");
}

test("A2A output options resolve omit reasoning, strict json_object, and disabled repair", () => {
	const options = normalizeA2AOutputOptions({
		enabled: true,
		body: {
			omit_reasoning: "true",
			response_format: {
				type: "json_object",
				strict: true,
			},
			json_repair: {
				attempts: 0,
			},
		},
	});

	assert.equal(options.omitReasoning, true);
	assert.equal(options.strictJson, true);
	assert.equal(options.jsonMode, "json_object");
	assert.equal(options.jsonRepair.attempts, 0);
});

test("A2A output options use camelCase aliases and default repair attempts to 3", () => {
	const options = normalizeA2AOutputOptions({
		enabled: true,
		body: {
			omitReasoning: true,
			responseFormat: "json",
		},
	});

	assert.equal(options.omitReasoning, true);
	assert.equal(options.strictJson, true);
	assert.equal(options.jsonMode, "json");
	assert.equal(options.jsonRepair.attempts, 3);
});

test("A2A output options use durable envelope responseFormat only when request omits it", () => {
	const fallback = normalizeA2AOutputOptions({
		enabled: true,
		body: {},
		a2aContext: {},
		envelopeResponseFormat: "json",
	});
	const requestWins = normalizeA2AOutputOptions({
		enabled: true,
		body: {
			response_format: "plain_text",
		},
		a2aContext: {},
		envelopeResponseFormat: "json",
	});

	assert.equal(fallback.strictJson, true);
	assert.equal(fallback.jsonMode, "json");
	assert.equal(requestWins.strictJson, false);
	assert.equal(requestWins.responseFormat, "plain_text");
});

test("strict JSON validation canonicalizes valid JSON objects", () => {
	const result = validateStrictJsonText(' { "ok" : true, "items" : [1, 2] } ', {
		jsonMode: "json_object",
	});

	assert.equal(result.ok, true);
	if (result.ok === true) {
		assert.deepEqual(result.value, { ok: true, items: [1, 2] });
		assert.equal(result.canonicalText, '{"ok":true,"items":[1,2]}');
	}
});

test("strict json_object validation rejects arrays and prose", () => {
	const arrayResult = validateStrictJsonText("[1,2,3]", { jsonMode: "json_object" });
	const proseResult = validateStrictJsonText("Here is the JSON: {\"ok\":true}", {
		jsonMode: "json_object",
	});

	assert.equal(arrayResult.ok, false);
	assert.equal(proseResult.ok, false);
});

test("strict JSON repair prompt carries original text, error, and format", () => {
	const prompt = buildStrictJsonRepairPrompt({
		invalidText: "```json\n{\"ok\": true,\n```",
		validationError: "Unexpected end of JSON input",
		responseFormat: { type: "json_object", strict: true },
		jsonMode: "json_object",
		jsonSchema: null,
		attempt: 2,
		maxAttempts: 3,
	});

	assert.match(prompt, /Repair attempt: 2 of 3/);
	assert.match(prompt, /Unexpected end of JSON input/);
	assert.match(prompt, /Return only corrected JSON/);
	assert.match(prompt, /"json_object"/);
	assert.match(prompt, /Original invalid response:/);
});

test("reasoning suppression removes outbound reasoning SSE events", async () => {
	const options = normalizeA2AOutputOptions({
		enabled: true,
		body: {
			omit_reasoning: true,
		},
	});
	const chunks = await runA2AOutputContractTurn({
		options,
		events: [
			{ type: "thinking_start" },
			{ type: "thinking_delta", delta: "private reasoning" },
			{ type: "thinking_end" },
			{ type: "text_start" },
			{ type: "text_delta", delta: "public answer" },
			{ type: "text_end" },
			{ type: "done" },
		],
	});
	const raw = ssePayload(chunks);

	assert.deepEqual(chunkTypes(chunks), ["text-start", "text-delta", "text-end", "finish"]);
	assert.doesNotMatch(raw, /reasoning-start|reasoning-delta|reasoning-end/);
	assert.match(raw, /public answer/);
});

test("non-strict A2A streams text deltas normally", async () => {
	const options = normalizeA2AOutputOptions({
		enabled: true,
		body: {},
	});
	const chunks = await runA2AOutputContractTurn({
		options,
		events: [
			{ type: "text_start" },
			{ type: "text_delta", delta: "hello " },
			{ type: "text_delta", delta: "world" },
			{ type: "text_end" },
			{ type: "done" },
		],
	});

	assert.deepEqual(chunkTypes(chunks), [
		"text-start",
		"text-delta",
		"text-delta",
		"text-end",
		"finish",
	]);
	assert.deepEqual(
		chunks.filter((chunk) => chunk.type === "text-delta").map((chunk) => chunk.textDelta),
		["hello ", "world"],
	);
});

test("strict json_object repair succeeds after invalid first output", async () => {
	const options = normalizeA2AOutputOptions({
		enabled: true,
		body: {
			response_format: {
				type: "json_object",
				strict: true,
			},
		},
	});
	const attempts: number[] = [];
	const chunks = await runA2AOutputContractTurn({
		options,
		events: [
			{ type: "text_start" },
			{ type: "text_delta", delta: "not json" },
			{ type: "text_end" },
			{ type: "done" },
		],
		repair: (input) => {
			attempts.push(input.attempt);
			return ' { "ok" : true } ';
		},
	});

	assert.deepEqual(attempts, [1]);
	assert.deepEqual(chunkTypes(chunks), ["text-start", "text-delta", "text-end", "finish"]);
	assert.equal(chunks[1]?.type === "text-delta" ? chunks[1].textDelta : null, '{"ok":true}');
});

test("strict json_object repair exhaustion emits a2a_invalid_json_response", async () => {
	const options = normalizeA2AOutputOptions({
		enabled: true,
		body: {
			response_format: {
				type: "json_object",
				strict: true,
			},
			json_repair: {
				attempts: 2,
			},
		},
	});
	const attempts: number[] = [];
	const chunks = await runA2AOutputContractTurn({
		options,
		events: [
			{ type: "text_start" },
			{ type: "text_delta", delta: "not json" },
			{ type: "text_end" },
			{ type: "done" },
		],
		repair: (input) => {
			attempts.push(input.attempt);
			return "still not json";
		},
	});

	assert.deepEqual(attempts, [1, 2]);
	assert.deepEqual(chunkTypes(chunks), ["error"]);
	assert.equal(chunks[0]?.type === "error" ? chunks[0].error_code : null, "a2a_invalid_json_response");
	assert.equal(chunks[0]?.type === "error" ? chunks[0].forensics.json_repair_attempts : null, 2);
});

test("json_repair.attempts 0 hard-fails without repair", async () => {
	const options = normalizeA2AOutputOptions({
		enabled: true,
		body: {
			response_format: {
				type: "json_object",
				strict: true,
			},
			json_repair: {
				attempts: 0,
			},
		},
	});
	let repairCalled = false;
	const chunks = await runA2AOutputContractTurn({
		options,
		events: [
			{ type: "text_start" },
			{ type: "text_delta", delta: "not json" },
			{ type: "text_end" },
			{ type: "done" },
		],
		repair: () => {
			repairCalled = true;
			return '{"ok":true}';
		},
	});

	assert.equal(repairCalled, false);
	assert.deepEqual(chunkTypes(chunks), ["error"]);
	assert.equal(chunks[0]?.type === "error" ? chunks[0].forensics.json_repair_attempts : null, 0);
});

test("strict JSON contract handles warm delta and cold message_end output paths", async () => {
	const options = normalizeA2AOutputOptions({
		enabled: true,
		body: {
			response_format: {
				type: "json_object",
				strict: true,
			},
		},
	});
	const warmChunks = await runA2AOutputContractTurn({
		options,
		events: [
			{ type: "text_start" },
			{ type: "text_delta", delta: '{ "source" : "warm" }' },
			{ type: "text_end" },
			{ type: "done" },
		],
	});
	const coldChunks = await runA2AOutputContractTurn({
		options,
		events: [
			{ type: "message_end_text", text: ' { "source" : "cold" } ' },
			{ type: "done" },
		],
	});

	assert.equal(warmChunks[1]?.type === "text-delta" ? warmChunks[1].textDelta : null, '{"source":"warm"}');
	assert.equal(coldChunks[1]?.type === "text-delta" ? coldChunks[1].textDelta : null, '{"source":"cold"}');
	assert.deepEqual(chunkTypes(warmChunks), ["text-start", "text-delta", "text-end", "finish"]);
	assert.deepEqual(chunkTypes(coldChunks), ["text-start", "text-delta", "text-end", "finish"]);
});

import assert from "node:assert/strict";
import { test } from "node:test";

import { resolveBackendAdapter } from "../adapters/backend.js";
import { requireBackendCapability } from "../adapters/types.js";
import { handleStatelessLlmChat } from "../interface/stream/llm-passthrough.js";

const adapter = resolveBackendAdapter({ ASTRO_BACKEND: "mainsequence" });
const llmAdapterOptions = {
	providerCredentials: requireBackendCapability(adapter, "providerCredentials", adapter.providerCredentials),
	modelCatalog: requireBackendCapability(adapter, "modelCatalog", adapter.modelCatalog),
};

function jsonResponse(body: unknown, init?: ResponseInit): Response {
	return new Response(JSON.stringify(body), {
		status: init?.status ?? 200,
		headers: {
			"Content-Type": "application/json",
			...(init?.headers ?? {}),
		},
	});
}

test("stateless LLM passthrough rejects session and runtime fields", async () => {
	let fetchCalled = false;
	const result = await handleStatelessLlmChat({
		...llmAdapterOptions,
		body: {
			agent_session_uid: "session-1",
			messages: [
				{
					role: "user",
					content: "hello",
				},
			],
		},
		env: {
			OPENAI_API_KEY: "sk-test",
		},
		fetchFn: (async () => {
			fetchCalled = true;
			return jsonResponse({});
		}) as typeof fetch,
	});

	assert.equal(result.statusCode, 400);
	assert.equal(result.body.ok, false);
	assert.equal(result.body.ok === false ? result.body.error : null, "invalid_llm_passthrough_request");
	assert.equal(fetchCalled, false);
});

test("stateless LLM passthrough rejects non-canonical request aliases", async () => {
	let fetchCalled = false;
	const result = await handleStatelessLlmChat({
		...llmAdapterOptions,
		body: {
			provider: "openai",
			model: "gpt-test",
			message: "hello",
			responseFormat: {
				type: "json_object",
				strict: true,
			},
			timeoutSeconds: 30,
		},
		env: {
			OPENAI_API_KEY: "sk-test",
		},
		fetchFn: (async () => {
			fetchCalled = true;
			return jsonResponse({});
		}) as typeof fetch,
	});

	assert.equal(result.statusCode, 400);
	assert.equal(result.body.ok, false);
	assert.equal(result.body.ok === false ? result.body.error : null, "invalid_llm_passthrough_request");
	if (result.body.ok === false) {
		assert.equal(result.body.field_errors?.message, "Use the canonical stateless LLM request shape.");
		assert.equal(result.body.field_errors?.responseFormat, "Use the canonical stateless LLM request shape.");
		assert.equal(result.body.field_errors?.timeoutSeconds, "Use the canonical stateless LLM request shape.");
	}
	assert.equal(fetchCalled, false);
});

test("stateless LLM passthrough returns application JSON with parsed strict JSON", async () => {
	const calls: Array<{ url: string; payload: Record<string, unknown>; authorization: string | null }> = [];
	const result = await handleStatelessLlmChat({
		...llmAdapterOptions,
		body: {
			provider: "openai",
			model: "gpt-test",
			messages: [
				{
					role: "user",
					content: "Return JSON with two keys.",
				},
			],
			response_format: {
				type: "json_object",
				strict: true,
			},
		},
		env: {
			OPENAI_API_KEY: "sk-test",
		},
		fetchFn: (async (url, init) => {
			calls.push({
				url: String(url),
				payload: JSON.parse(String(init?.body ?? "{}")),
				authorization: new Headers(init?.headers).get("authorization"),
			});
			return jsonResponse({
				choices: [
					{
						finish_reason: "stop",
						message: {
							content: ' { "key1" : "value1", "key2" : "value2" } ',
						},
					},
				],
				usage: {
					prompt_tokens: 10,
					completion_tokens: 5,
					total_tokens: 15,
				},
			});
		}) as typeof fetch,
	});

	assert.equal(result.statusCode, 200);
	assert.equal(result.body.ok, true);
	if (result.body.ok === true) {
		assert.equal(result.body.message.content, '{"key1":"value1","key2":"value2"}');
		assert.deepEqual(result.body.json, { key1: "value1", key2: "value2" });
		assert.deepEqual(result.body.usage, {
			input_tokens: 10,
			output_tokens: 5,
			total_tokens: 15,
		});
	}
	assert.equal(calls.length, 1);
	assert.equal(calls[0]?.url, "https://api.openai.com/v1/chat/completions");
	assert.equal(calls[0]?.authorization, "Bearer sk-test");
	assert.deepEqual(calls[0]?.payload.messages, [
		{
			role: "user",
			content: "Return JSON with two keys.",
		},
	]);
	assert.deepEqual(calls[0]?.payload.response_format, { type: "json_object" });
});

test("stateless LLM passthrough repairs invalid strict JSON before returning", async () => {
	let callCount = 0;
	const result = await handleStatelessLlmChat({
		...llmAdapterOptions,
		body: {
			provider: "openai",
			model: "gpt-test",
			messages: [
				{
					role: "user",
					content: "Return JSON.",
				},
			],
			response_format: {
				type: "json_object",
				strict: true,
			},
			json_repair: {
				attempts: 1,
			},
		},
		env: {
			OPENAI_API_KEY: "sk-test",
		},
		fetchFn: (async () => {
			callCount += 1;
			return jsonResponse({
				choices: [
					{
						finish_reason: "stop",
						message: {
							content: callCount === 1 ? "not json" : '{"repaired":true}',
						},
					},
				],
			});
		}) as typeof fetch,
	});

	assert.equal(callCount, 2);
	assert.equal(result.statusCode, 200);
	assert.equal(result.body.ok, true);
	if (result.body.ok === true) {
		assert.equal(result.body.message.content, '{"repaired":true}');
		assert.deepEqual(result.body.json, { repaired: true });
	}
});

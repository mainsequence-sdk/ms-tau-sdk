import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const FIXTURE_ROOT = path.resolve("tests/fixtures/a2a");

async function readFixture(name: string): Promise<Record<string, any>> {
	const text = await readFile(path.join(FIXTURE_ROOT, name), "utf8");
	const parsed = JSON.parse(text);
	assert.equal(typeof parsed, "object");
	assert.notEqual(parsed, null);
	return parsed;
}

function assertA2AMessage(message: Record<string, any>) {
	assert.equal(typeof message.messageId, "string");
	assert.ok(message.messageId.length > 0);
	assert.match(message.role, /^ROLE_(USER|AGENT)$/);
	assert.equal(typeof message.contextId, "string");
	assert.ok(message.contextId.length > 0);
	assert.ok(Array.isArray(message.parts));
	assert.ok(message.parts.length > 0);
}

function assertA2ATask(task: Record<string, any>) {
	assert.equal(typeof task.id, "string");
	assert.equal(typeof task.contextId, "string");
	assert.equal(typeof task.status, "object");
	assert.match(task.status.state, /^TASK_STATE_/);
	if (task.artifacts !== undefined) assert.ok(Array.isArray(task.artifacts));
}

test("A2A REST message:send fixture uses Message request and message response", async () => {
	const request = await readFixture("message-send-request.json");
	const response = await readFixture("message-send-response.json");

	assertA2AMessage(request.message);
	assert.deepEqual(Object.keys(response), ["message"]);
	assertA2AMessage(response.message);
	assert.equal(response.message.role, "ROLE_AGENT");
});

test("A2A task fixture uses Task response with artifact output", async () => {
	const response = await readFixture("task-response.json");

	assert.deepEqual(Object.keys(response), ["task"]);
	assertA2ATask(response.task);
	assert.ok(response.task.artifacts.length > 0);
});

test("A2A JSON-RPC fixture wraps SendMessage result in JSON-RPC 2.0 envelope", async () => {
	const request = await readFixture("json-rpc-send-message-request.json");
	const response = await readFixture("json-rpc-send-message-response.json");

	assert.equal(request.jsonrpc, "2.0");
	assert.equal(request.method, "SendMessage");
	assertA2AMessage(request.params.message);
	assert.equal(response.jsonrpc, "2.0");
	assert.equal(response.id, request.id);
	assert.deepEqual(Object.keys(response.result), ["message"]);
	assertA2AMessage(response.result.message);
});
